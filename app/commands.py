import os
import psutil
from telegram import Update
from telegram.ext import CallbackContext

from config import ADMIN_IDS, get_allowed_users, save_allowed_users, track_user, get_all_users_info, get_user_display_name, get_config, cleanup_temp_files, TEMP_DIR, BOT_FILE_PREFIX
from core.logger import log_user, get_user_stats
from core.history import get_user_history, get_all_users_count, get_total_downloads, get_failed_downloads
from core.ratelimit import rate_limiter, save_rate_limit
from core.i18n import t, set_user_lang, LANGUAGES


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


async def stats_command(update: Update, context: CallbackContext):
    if not update.message:
        return
    from core.utils import check_admin
    user_id = update.message.from_user.id
    if not check_admin(user_id):
        return
    stats = get_user_stats()
    users_count = get_all_users_count()
    total_downloads = get_total_downloads()
    msg = f"{stats}\n\nTotal registered users: {users_count}\nTotal downloads: {total_downloads}"
    await update.message.reply_text(msg)


async def history_command(update: Update, context: CallbackContext):
    if not update.message:
        return
    user_id = update.message.from_user.id
    history = get_user_history(user_id, limit=10)
    
    if not history:
        await update.message.reply_text("No download history.")
        return
    
    msg = "Your download history:\n\n"
    for item in history:
        from datetime import datetime
        dt = datetime.fromtimestamp(item["timestamp"])
        status = "✅" if item.get("status") == "success" else "❌"
        msg += f"{status} {item['type']} - {item.get('title', 'Unknown')[:30]}...\n"
        msg += f"   {dt.strftime('%Y-%m-%d %H:%M')}\n\n"
    
    await update.message.reply_text(msg)


async def allow_command(update: Update, context: CallbackContext):
    if not update.message:
        return
    from core.utils import check_admin
    user_id = update.message.from_user.id
    if not check_admin(user_id):
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


async def block_command(update: Update, context: CallbackContext):
    if not update.message:
        return
    from core.utils import check_admin
    user_id = update.message.from_user.id
    if not check_admin(user_id):
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


async def users_command(update: Update, context: CallbackContext):
    if not update.message:
        return
    user_id = update.message.from_user.id
    if ADMIN_IDS and user_id not in ADMIN_IDS:
        return
    
    users_info = get_all_users_info()
    allowed = get_allowed_users()
    
    if not users_info:
        await update.message.reply_text("No users yet.")
        return
    
    msg = "Allowed users:\n\n"
    for user in sorted(users_info, key=lambda x: x.get("last_seen", 0), reverse=True):
        if user["id"] in allowed:
            name = get_user_display_name(user["id"])
            msg += f"• {name}\n"
    
    await update.message.reply_text(msg, parse_mode="Markdown")


async def broadcast_command(update: Update, context: CallbackContext):
    if not update.message:
        return
    user_id = update.message.from_user.id
    if ADMIN_IDS and user_id not in ADMIN_IDS:
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
        except Exception:
            failed += 1
    
    await update.message.reply_text(f"Broadcast sent to {success} users. Failed: {failed}")


async def userhistory_command(update: Update, context: CallbackContext):
    if not update.message:
        return
    user_id = update.message.from_user.id
    if ADMIN_IDS and user_id not in ADMIN_IDS:
        return
    
    users = get_allowed_users() - ADMIN_IDS
    
    if not users:
        await update.message.reply_text("No users to show.")
        return
    
    from telegram import InlineKeyboardButton, InlineKeyboardMarkup
    
    keyboard = []
    for uid in sorted(users):
        name = get_user_display_name(uid)
        keyboard.append([InlineKeyboardButton(name, callback_data=f"uh_{uid}")])
    
    reply_markup = InlineKeyboardMarkup(keyboard)
    await update.message.reply_text("Select a user to view history:", reply_markup=reply_markup)


async def rateinfo_command(update: Update, context: CallbackContext):
    if not update.message:
        return
    user_id = update.message.from_user.id
    if ADMIN_IDS and user_id not in ADMIN_IDS:
        return
    
    status = rate_limiter.get_status()
    status_text = "Enabled" if status["enabled"] else "Disabled"
    msg = f"Rate Limit Status:\n"
    msg += f"- Status: {status_text}\n"
    msg += f"- Max downloads/hour: {status['max_downloads_per_hour']}\n"
    msg += f"- Active users tracked: {status['active_users']}"
    await update.message.reply_text(msg)


async def setrate_command(update: Update, context: CallbackContext):
    if not update.message:
        return
    user_id = update.message.from_user.id
    if ADMIN_IDS and user_id not in ADMIN_IDS:
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


async def cleanup_command(update: Update, context: CallbackContext):
    if not update.message:
        return
    user_id = update.message.from_user.id
    if ADMIN_IDS and user_id not in ADMIN_IDS:
        return
    
    cleanup_temp_files()
    await update.message.reply_text("Temp files cleaned up.")


async def status_command(update: Update, context: CallbackContext):
    if not update.message:
        return
    from core.utils import check_admin, scan_temp_files
    user_id = update.message.from_user.id
    if not check_admin(user_id):
        return
    
    import psutil
    
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


async def queue_command(update: Update, context: CallbackContext):
    if not update.message:
        return
    user_id = update.message.from_user.id
    if ADMIN_IDS and user_id not in ADMIN_IDS:
        return
    
    from core.queue import download_queue
    
    active = download_queue.active_tasks
    queued = download_queue.queue.qsize()
    
    msg = "📥 Download Queue\n\n"
    msg += f"Active downloads: {len(active)}\n"
    msg += f"Queued: {queued}\n\n"
    
    if active:
        msg += "Active:\n"
        for tid, task in list(active.items())[:10]:
            user_name = get_user_display_name(task.user_id)
            status_emoji = {
                "downloading": "⬇️",
                "processing": "⚙️",
                "uploading": "📤",
            }.get(task.status.value, "⏳")
            msg += f"{status_emoji} {task.download_type} - {user_name}\n"
    
    await update.message.reply_text(msg)


async def storage_command(update: Update, context: CallbackContext):
    if not update.message:
        return
    from core.utils import check_admin, scan_temp_files, format_bytes
    user_id = update.message.from_user.id
    if not check_admin(user_id):
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


async def failed_command(update: Update, context: CallbackContext):
    if not update.message:
        return
    user_id = update.message.from_user.id
    if ADMIN_IDS and user_id not in ADMIN_IDS:
        return
    
    if not context.args:
        await update.message.reply_text("Usage: /failed [user_id]")
        return
    
    try:
        target_id = int(context.args[0])
    except ValueError:
        await update.message.reply_text("Invalid user ID.")
        return
    
    history = get_user_history(target_id, limit=50)
    failed = [h for h in history if h.get("status") == "failed"]
    
    if not failed:
        await update.message.reply_text(f"No failed downloads for user {target_id}.")
        return
    
    msg = f"❌ Failed downloads for user {target_id}:\n\n"
    for item in failed[:20]:
        from datetime import datetime
        dt = datetime.fromtimestamp(item["timestamp"])
        msg += f"• {item['type']} - {item.get('title', 'N/A')[:30]}\n"
        if item.get("error"):
            msg += f"  Error: {item['error'][:50]}\n"
        msg += f"  {dt.strftime('%Y-%m-%d %H:%M')}\n\n"
    
    await update.message.reply_text(msg)


def _format_bytes(bytes_val):
    """Deprecated: Use core.utils.format_bytes instead."""
    from core.utils import format_bytes
    return format_bytes(bytes_val)


async def lang_command(update: Update, context: CallbackContext):
    if not update.message:
        return
    user_id = update.message.from_user.id
    
    if not context.args:
        from telegram import InlineKeyboardButton, InlineKeyboardMarkup
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
    
    set_user_lang(user_id, lang_code)
    await update.message.reply_text(t("language_changed", user_id))


async def cookie_command(update: Update, context: CallbackContext):
    if not update.message:
        return
    user_id = update.message.from_user.id
    if ADMIN_IDS and user_id not in ADMIN_IDS:
        return
    
    await update.message.reply_text(
        "Send me your cookies.txt file to update the bot's cookies.\n\n"
        "To generate cookies:\n"
        "1. On your PC: `yt-dlp --cookies-from-browser chrome https://www.youtube.com --skip-download -o cookies.txt`\n"
        "2. Send the file here"
    )


async def refresh_command(update: Update, context: CallbackContext):
    """Force re-download by clearing cached file_id for a URL."""
    if not update.message:
        return
    from core.utils import check_admin
    user_id = update.message.from_user.id
    if not check_admin(user_id):
        return
    
    if not context.args:
        await update.message.reply_text("Usage: /refresh <url>\n\nClears cached file ID so the video will be re-downloaded.")
        return
    
    url = " ".join(context.args)
    from core.history import clear_file_id_by_url
    clear_file_id_by_url(url)
    await update.message.reply_text(f"✅ Cleared cached file ID for:\n{url}\n\nThe next download will fetch a fresh file.")
