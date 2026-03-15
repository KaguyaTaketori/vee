import os
import psutil
import logging
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import CallbackContext
from functools import wraps

from config import ADMIN_IDS, get_config, TEMP_DIR, BOT_FILE_PREFIX
from services.user_service import get_allowed_users, save_allowed_users, track_user, get_all_users_info, get_user_display_name, cleanup_temp_files
from database.db import get_db
from utils.logger import log_user, get_user_stats
from database.history import clear_file_id_by_url,  get_user_history, get_user_history_page, get_all_users_count, get_total_downloads, get_failed_downloads
from services.queue import download_queue
from models.domain_models import DownloadStatus, STATUS_EMOJI
from services.ratelimit import rate_limiter, save_rate_limit
from utils.i18n import t, set_user_lang, LANGUAGES
from utils.utils import require_admin, check_admin, scan_temp_files, format_bytes, format_history_list, format_history_item

logger = logging.getLogger(__name__)


async def start_command(update: Update, context: CallbackContext):
    if not update.message:
        return
    track_user(update.message.from_user)
    log_user(update.message.from_user, "start")
    user = update.message.from_user
    await update.message.reply_text(t("welcome", user.id))


async def myid_command(update: Update, context: CallbackContext):
    if not update.message:
        return
    user = update.message.from_user
    await update.message.reply_text(t("your_id", user.id, username=user.username or "N/A", name=f"{user.first_name} {user.last_name or ''}"), parse_mode="Markdown")


async def help_command(update: Update, context: CallbackContext):
    if not update.message:
        return
    user_id = update.message.from_user.id
    is_admin = user_id in ADMIN_IDS if ADMIN_IDS else False
    
    if is_admin:
        await update.message.reply_text(t("admin_commands", user_id))
    else:
        await update.message.reply_text(t("available_commands", user_id))


async def tasks_command(update: Update, context: CallbackContext):
    if not update.message:
        return
    user_id = update.message.from_user.id
    from database.task_store import get_user_tasks

    records = await get_user_tasks(user_id, limit=10)
    if not records:
        await update.message.reply_text(t("no_tasks", user_id))
        return

    STATUS_EMOJI = {
        "queued":      "⏳", "downloading": "⬇️",
        "processing":  "⚙️", "uploading":   "📤",
        "completed":   "✅", "failed":      "❌",
        "cancelled":   "🚫",
    }

    lines = [f"📋 {t('recent_tasks', user_id)}\n"]
    for r in records:
        emoji = STATUS_EMOJI.get(r["status"], "❓")
        title = (r.get("title") or r["url"])[:35]
        retry_info = f" (重试 {r['retry_count']} 次)" if r.get("retry_count") else ""
        err_info = f"\n   ⚠️ {r['error'][:40]}" if r.get("error") else ""
        lines.append(f"{emoji} [{r['download_type']}] {title}{retry_info}{err_info}")

    await update.message.reply_text("\n".join(lines))


async def cancel_command(update: Update, context: CallbackContext):
    if not update.message:
        return

    user_id = update.message.from_user.id

    user_tasks = download_queue.get_user_tasks(user_id)
    active = [
        t for t in user_tasks
        if t.status in (DownloadStatus.QUEUED, DownloadStatus.DOWNLOADING, DownloadStatus.PROCESSING)
    ]

    if not active:
        await update.message.reply_text(t("no_active_tasks", user_id))
        return

    keyboard = []
    for task in active:
        short_url = task.url[:30] + "..." if len(task.url) > 30 else task.url
        status_emoji = {
            DownloadStatus.QUEUED: "⏳",
            DownloadStatus.DOWNLOADING: "⬇️",
            DownloadStatus.PROCESSING: "⚙️",
        }.get(task.status, "❓")

        label = f"{status_emoji} {task.download_type} | {short_url}"
        keyboard.append([
            InlineKeyboardButton(label, callback_data=f"cancel_task_{task.task_id}")
        ])

    keyboard.append([InlineKeyboardButton("❌ 关闭", callback_data="cancel_menu_close")])

    await update.message.reply_text(
        "选择要取消的任务：",
        reply_markup=InlineKeyboardMarkup(keyboard)
    )

@require_admin
async def setdisk_command(update: Update, context: CallbackContext):
    if not update.message:
        return

    import main as _main

    if not context.args:
        from config import DISK_WARN_THRESHOLD, DISK_CRIT_THRESHOLD, DISK_CHECK_INTERVAL_MINUTES
        await update.message.reply_text(
            f"💾 当前磁盘告警配置：\n"
            f"  WARNING 阈值：{DISK_WARN_THRESHOLD}%\n"
            f"  CRITICAL 阈值：{DISK_CRIT_THRESHOLD}%\n"
            f"  检查间隔：{DISK_CHECK_INTERVAL_MINUTES} 分钟\n\n"
            f"用法：/setdisk <warn%> [crit%]\n"
            f"示例：/setdisk 75 85"
        )
        return

    try:
        warn = int(context.args[0])
        crit = int(context.args[1]) if len(context.args) > 1 else warn + 10

        if not (0 < warn < crit <= 100):
            raise ValueError("warn 必须 < crit，且均在 1-100 之间")
    except ValueError as e:
        await update.message.reply_text(f"❌ 参数错误：{e}")
        return

    import config
    config.DISK_WARN_THRESHOLD = warn
    config.DISK_CRIT_THRESHOLD = crit
    _main._last_alert_level = "ok"

    await update.message.reply_text(
        f"✅ 磁盘告警阈值已更新：\n"
        f"  WARNING：{warn}%\n"
        f"  CRITICAL：{crit}%"
    )

@require_admin
async def settier_command(update: Update, context: CallbackContext):
    if not update.message:
        return

    from config import RATE_TIER_LIMITS
    from services.ratelimit import get_user_tier, get_user_limit, set_user_tier

    VALID_TIERS = list(RATE_TIER_LIMITS.keys())

    if not context.args:
        async with get_db() as db:
            async with db.execute(
                "SELECT user_id, tier, max_per_hour, note, set_at FROM user_rate_tiers ORDER BY set_at DESC LIMIT 20"
            ) as cur:
                rows = await cur.fetchall()

        if not rows:
            await update.message.reply_text("当前没有自定义等级的用户。")
            return

        lines = ["👥 自定义等级用户列表：\n"]
        for r in rows:
            limit_str = f"(自定义: {r[2]}/h)" if r[2] is not None else f"({RATE_TIER_LIMITS.get(r[1], '?')}/h)"
            note_str  = f" — {r[3]}" if r[3] else ""
            lines.append(f"  UID {r[0]}: [{r[1]}] {limit_str}{note_str}")

        await update.message.reply_text("\n".join(lines))
        return

    if len(context.args) == 1:
        try:
            target_id = int(context.args[0])
        except ValueError:
            await update.message.reply_text("❌ 无效的用户 ID")
            return

        tier  = await get_user_tier(target_id)
        limit = await get_user_limit(target_id)
        remaining = await rate_limiter.get_remaining(target_id)
        await update.message.reply_text(
            f"👤 用户 {target_id}\n"
            f"  等级：{tier}\n"
            f"  限额：{limit}/小时\n"
            f"  剩余：{remaining} 次"
        )
        return

    try:
        target_id = int(context.args[0])
    except ValueError:
        await update.message.reply_text("❌ 无效的用户 ID")
        return

    tier_arg = context.args[1].lower()

    if tier_arg == "custom":
        if len(context.args) < 3:
            await update.message.reply_text("用法：/settier <user_id> custom <max_per_hour>")
            return
        try:
            custom_max = int(context.args[2])
            if custom_max < 0 or custom_max > 10000:
                raise ValueError
        except ValueError:
            await update.message.reply_text("❌ 自定义限额必须为 0–10000 的整数")
            return

        note = " ".join(context.args[3:]) if len(context.args) > 3 else ""
        await set_user_tier(target_id, "custom", note=note,
                            set_by=update.message.from_user.id, custom_max=custom_max)
        await update.message.reply_text(
            f"✅ 用户 {target_id} 自定义限额已设为 {custom_max}/小时"
        )

    elif tier_arg in VALID_TIERS:
        note = " ".join(context.args[2:]) if len(context.args) > 2 else ""
        await set_user_tier(target_id, tier_arg, note=note,
                            set_by=update.message.from_user.id)
        tier_limit = RATE_TIER_LIMITS[tier_arg]
        await update.message.reply_text(
            f"✅ 用户 {target_id} 等级已设为 [{tier_arg}]（{tier_limit}/小时）"
        )
    else:
        await update.message.reply_text(
            f"❌ 无效等级：{tier_arg}\n"
            f"可用选项：{' | '.join(VALID_TIERS)} | custom <数值>"
        )

@require_admin
async def report_command(update: Update, context: CallbackContext):
    if not update.message:
        return

    days = 1
    if context.args:
        try:
            days = max(1, min(90, int(context.args[0])))
        except ValueError:
            pass

    from services.analytics import get_daily_stats, format_daily_report
    stats = await get_daily_stats(days=days)
    period = f"近 {days} 天" if days > 1 else "今日"
    await update.message.reply_text(format_daily_report(stats, period=period))

@require_admin
async def admin_cancel_command(update: Update, context: CallbackContext):
    if not update.message:
        return
      
    if not context.args:
        active = list(download_queue.active_tasks.values())
        queued_size = download_queue.get_total_queued()
        
        if not active and queued_size == 0:
            await update.message.reply_text("当前队列为空。")
            return
        
        keyboard = []
        for task in active:
            short_url = task.url[:25] + "..." if len(task.url) > 25 else task.url
            label = f"[{task.task_id}] UID:{task.user_id} {short_url}"
            keyboard.append([
                InlineKeyboardButton(
                    f"取消 {label}",
                    callback_data=f"cancel_task_{task.task_id}"
                )
            ])
        
        keyboard.append([InlineKeyboardButton("❌ 关闭", callback_data="cancel_menu_close")])
        
        msg = f"活跃任务：{len(active)} 个，排队中：{queued_size} 个\n\n选择要取消的任务："
        await update.message.reply_text(msg, reply_markup=InlineKeyboardMarkup(keyboard))
        return
    
    task_id = context.args[0]
    success = await download_queue.cancel_task(task_id)
    
    if success:
        await update.message.reply_text(f"✅ 任务 {task_id} 已取消。")
    else:
        await update.message.reply_text(
            f"❌ 取消失败。任务 {task_id} 不存在或已完成。\n"
            f"使用 /admcancel 查看当前活跃任务。"
        )

@require_admin
async def stats_command(update: Update, context: CallbackContext):
    if not update.message:
        return
    msg = await get_user_stats()
    await update.message.reply_text(msg)

PAGE_SIZE = 5

async def history_command(update: Update, context: CallbackContext):
    if not update.message:
        return
    user_id = update.message.from_user.id
    await _send_history_page(update.message, user_id, page=0)


async def _send_history_page(message_or_query, user_id: int, page: int):
    records, total = await get_user_history_page(user_id, page=page, page_size=PAGE_SIZE)
    total_pages = max(1, (total + PAGE_SIZE - 1) // PAGE_SIZE)

    if not records:
        text = t("no_history", user_id)
    else:
        header = t("history_header", user_id, page=page + 1, total=total_pages, count=total)
        body = "".join(format_history_item(r) for r in records)
        text = header + body

    nav = []
    if page > 0:
        nav.append(InlineKeyboardButton("⬅️ 上一页", callback_data=f"history_page_{user_id}_{page - 1}"))
    if page < total_pages - 1:
        nav.append(InlineKeyboardButton("下一页 ➡️", callback_data=f"history_page_{user_id}_{page + 1}"))

    keyboard = [nav] if nav else []
    markup = InlineKeyboardMarkup(keyboard) if keyboard else None

    if hasattr(message_or_query, "reply_text"):
        await message_or_query.reply_text(text, reply_markup=markup)
    else:
        await message_or_query.edit_message_text(text, reply_markup=markup)

@require_admin
async def allow_command(update: Update, context: CallbackContext):
    if not update.message:
        return
    
    if not context.args:
        await update.message.reply_text("Usage: /allow <user_id>")
        return
    
    try:
        target_id = int(context.args[0])
    except ValueError:
        await update.message.reply_text("Invalid user ID. Must be a number.")
        return
    
    users = get_allowed_users()
    users.add(target_id)
    save_allowed_users(users)
    
    await update.message.reply_text(f"User {target_id} has been allowed.")


@require_admin
async def block_command(update: Update, context: CallbackContext):
    if not update.message:
        return
    
    if not context.args:
        await update.message.reply_text("Usage: /block <user_id>")
        return
    
    try:
        target_id = int(context.args[0])
    except ValueError:
        await update.message.reply_text("Invalid user ID. Must be a number.")
        return
    
    users = get_allowed_users()
    users.discard(target_id)
    save_allowed_users(users)
    
    await update.message.reply_text(f"User {target_id} has been blocked.")


@require_admin
async def users_command(update: Update, context: CallbackContext):
    if not update.message:
        return
    
    allowed = get_allowed_users()
    users_info = await get_all_users_info()
    
    if not allowed:
        await update.message.reply_text("No allowed users in the list.")
        return
    
    last_seen_map = {}
    if users_info:
        last_seen_map = {u.get("user_id"): u.get("last_seen", 0) for u in users_info}
    
    msg = "✅ Allowed users:\n\n"
    
    for uid in sorted(allowed, key=lambda x: last_seen_map.get(x, 0), reverse=True):
        name = await get_user_display_name(uid)
        
        if name == str(uid):
            msg += f"• `{uid}` *(Never used bot)*\n"
        else:
            safe_name = name.replace('_', '\\_')
            msg += f"• {safe_name} (`{uid}`)\n"
    
    await update.message.reply_text(msg, parse_mode="Markdown")


@require_admin
async def broadcast_command(update: Update, context: CallbackContext):
    if not update.message:
        return
    
    if not context.args:
        await update.message.reply_text("Usage: /broadcast <message>")
        return
    
    message = " ".join(context.args)
    users = get_allowed_users() - ADMIN_IDS
    
    bot = context.bot
    success = 0
    failed = 0
    
    for uid in users:
        try:
            await bot.send_message(chat_id=uid, text=message)
            success += 1
        except Exception as e:
            logger.warning(f"Failed to send broadcast to {uid}: {e}")
            failed += 1
    
    await update.message.reply_text(f"Broadcast sent to {success} users. Failed: {failed}")


@require_admin
async def userhistory_command(update: Update, context: CallbackContext):
    if not update.message:
        return
    
    users = get_allowed_users() - ADMIN_IDS
    
    if not users:
        await update.message.reply_text("No users to show.")
        return
    
    
    keyboard = []
    for uid in sorted(users):
        name = await get_user_display_name(uid)
        keyboard.append([InlineKeyboardButton(name, callback_data=f"uh_{uid}")])
    
    reply_markup = InlineKeyboardMarkup(keyboard)
    await update.message.reply_text("Select a user to view history:", reply_markup=reply_markup)


@require_admin
async def rateinfo_command(update: Update, context: CallbackContext):
    if not update.message:
        return
    
    status = rate_limiter.get_status()
    status_text = "Enabled" if status["enabled"] else "Disabled"
    msg = f"Rate Limit Status:\n"
    msg += f"- Status: {status_text}\n"
    msg += f"- Max downloads/hour: {status['max_downloads_per_hour']}\n"
    msg += f"- Active users tracked: {status['active_users']}"
    await update.message.reply_text(msg)


@require_admin
async def setrate_command(update: Update, context: CallbackContext):
    if not update.message:
        return
    
    if not context.args:
        await update.message.reply_text("Usage: /setrate <max_per_hour> [on/off]")
        return
    
    try:
        max_per_hour = int(context.args[0])
        if max_per_hour < 1 or max_per_hour > 100:
            await update.message.reply_text("Value must be between 1-100")
            return
    except ValueError:
        await update.message.reply_text("Invalid number.")
        return
    
    enabled = True
    if len(context.args) > 1:
        enabled = context.args[1].lower() in ["on", "true", "1"]
    
    save_rate_limit(max_per_hour, enabled)
    rate_limiter.reload()
    
    status = "enabled" if enabled else "disabled"
    await update.message.reply_text(f"Rate limit updated: {max_per_hour}/hour, {status}")


@require_admin
async def cleanup_command(update: Update, context: CallbackContext):
    if not update.message:
        return
    
    cleanup_temp_files()
    await update.message.reply_text("Temp files cleaned up.")


@require_admin
async def status_command(update: Update, context: CallbackContext):
    if not update.message:
        return
    
    config = get_config()
    rate_status = rate_limiter.get_status()
    
    temp_files, temp_size, _, _ = scan_temp_files(config["temp_dir"])
    
    msg = "📊 Bot Status\n\n"
    msg += f"CPU: {psutil.cpu_percent()}%\n"
    mem = psutil.virtual_memory()
    msg += f"Memory: {mem.percent}% ({mem.available // (1024*1024)}MB available)\n\n"
    msg += f"Temp files: {temp_files}\n"
    msg += f"Temp size: {temp_size // (1024*1024)}MB\n\n"
    msg += f"Rate limit: {rate_status['max_downloads_per_hour']}/hour\n"
    msg += f"Rate enabled: {rate_status['enabled']}\n"
    msg += f"Cleanup interval: {config['cleanup_interval_hours']}h\n"
    msg += f"Temp file max age: {config['temp_file_max_age_hours']}h\n"
    msg += f"Max cache size: {config.get('max_cache_size', 500*1024*1024) // (1024*1024)}MB"
    
    await update.message.reply_text(msg)


@require_admin
async def queue_command(update: Update, context: CallbackContext):
    if not update.message:
        return
     
    active = download_queue.active_tasks
    queued = download_queue.queue.qsize()
    
    msg = "📥 Download Queue\n\n"
    msg += f"Active downloads: {len(active)}\n"
    msg += f"Queued: {queued}\n\n"
    
    if active:
        msg += "Active:\n"
        for tid, task in list(active.items())[:10]:
            user_name = await get_user_display_name(task.user_id)
            status_emoji = {
                "downloading": "⬇️",
                "processing": "⚙️",
                "uploading": "📤",
            }.get(task.status.value, "⏳")
            msg += f"{status_emoji} {task.download_type} - {user_name}\n"
    
    await update.message.reply_text(msg)


@require_admin
async def storage_command(update: Update, context: CallbackContext):
    if not update.message:
        return
    
    config = get_config()
    temp_dir = config["temp_dir"]
    
    disk = psutil.disk_usage("/")
    total, used, free = disk.total, disk.used, disk.free
    temp_files, temp_size, oldest_file, oldest_time = scan_temp_files(temp_dir)
    
    disk_percent = (used / total) * 100
    alert = ""
    if disk_percent > 90:
        alert = "\n⚠️ WARNING: Disk usage above 90%!"
    elif disk_percent > 80:
        alert = "\n⚠️ CAUTION: Disk usage above 80%"
    
    msg = f"💾 Storage Status{alert}\n\n"
    msg += f"Total disk: {format_bytes(total)}\n"
    msg += f"Used: {format_bytes(used)} ({disk_percent:.1f}%)\n"
    msg += f"Free: {format_bytes(free)}\n\n"
    msg += f"Temp directory ({temp_dir}):\n"
    msg += f"Files: {temp_files}\n"
    msg += f"Size: {format_bytes(temp_size)}\n"
    if oldest_file:
        from datetime import datetime
        msg += f"Oldest: {oldest_file}\n"
        msg += f"   {datetime.fromtimestamp(oldest_time).strftime('%Y-%m-%d %H:%M')}"
    
    await update.message.reply_text(msg)


@require_admin
async def failed_command(update: Update, context: CallbackContext):
    if not update.message:
        return
    
    if not context.args:
        await update.message.reply_text("Usage: /failed [user_id]")
        return
    
    try:
        target_id = int(context.args[0])
    except ValueError:
        await update.message.reply_text("Invalid user ID.")
        return
    
    history = await get_user_history(target_id, limit=50)
    failed = [h for h in history if h.get("status") == "failed"]
    
    if not failed:
        await update.message.reply_text(f"No failed downloads for user {target_id}.")
        return
    
    msg = format_history_list(failed[:20], f"❌ Failed downloads for user {target_id}:\n\n")
    
    await update.message.reply_text(msg)


async def lang_command(update: Update, context: CallbackContext):
    if not update.message:
        return
    user_id = update.message.from_user.id
    
    if not context.args:
        keyboard = []
        for code, name in LANGUAGES.items():
            keyboard.append([InlineKeyboardButton(name, callback_data=f"lang_{code}")])
        reply_markup = InlineKeyboardMarkup(keyboard)
        await update.message.reply_text(t("select_language", user_id), reply_markup=reply_markup)
        return
    
    lang_code = context.args[0].lower()
    if lang_code not in LANGUAGES:
        await update.message.reply_text("Invalid language. Use: en, zh, or ja")
        return
    
    await set_user_lang(user_id, lang_code)
    await update.message.reply_text(t("language_changed", user_id))


@require_admin
async def cookie_command(update: Update, context: CallbackContext):
    if not update.message:
        return
    
    await update.message.reply_text(
        "Send me your cookies.txt file to update the bot's cookies.\n\n"
        "To generate cookies:\n"
        "1. On your PC: `yt-dlp --cookies-from-browser chrome https://www.youtube.com --skip-download -o cookies.txt`\n\n"
        "For site-specific cookies, use filename format:\n"
        "• `www.youtube.com_cookies.txt` for YouTube\n"
        "• `www.bilibili.com_cookies.txt` for Bilibili\n\n"
        "The bot will automatically use the appropriate cookie file based on the URL being downloaded."
    )


@require_admin
async def refresh_command(update: Update, context: CallbackContext):
    """Force re-download by clearing cached file_id for a URL."""
    if not update.message:
        return
    
    if not context.args:
        await update.message.reply_text("Usage: /refresh <url>\n\nClears cached file ID so the video will be re-downloaded.")
        return
    
    url = " ".join(context.args)
    await clear_file_id_by_url(url)
    await update.message.reply_text(f"✅ Cleared cached file ID for:\n{url}\n\nThe next download will fetch a fresh file.")
