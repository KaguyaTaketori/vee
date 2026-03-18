import logging
from datetime import datetime
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import CallbackContext

from core.handler_registry import command_handler
from shared.services.container import services
from shared.services.ratelimit import save_rate_limit, get_user_tier, get_user_limit
from shared.services.user_service import get_user_display_name
from database.history import clear_file_id_by_url, get_user_history, get_recent_cached_urls
from utils.i18n import t
from utils.utils import require_admin, require_message, format_history_list

logger = logging.getLogger(__name__)

_FORMAT_LABEL: dict[str, str] = {
    # YouTube
    "137": "1080p", "248": "1080p",
    "136": "720p",  "247": "720p",
    "135": "480p",  "244": "480p",
    "134": "360p",  "243": "360p",
    "133": "240p",  "242": "240p",
    "160": "144p",  "278": "144p",
    "271": "1440p", "308": "1440p",
    "313": "2160p", "315": "2160p",
    "best": "最佳画质",
    # Bilibili
    "127": "8K",
    "126": "Dolby 视界",
    "125": "HDR 真彩",
    "120": "4K 超清",
    "116": "1080p60",
    "112": "1080p+",
    "80":  "1080p",
    "74":  "720p60",
    "64":  "720p",
    "32":  "480p",
    "16":  "360p",
    # Bilibili 旧版 qn 编号
    "30280": "8K",
    "30250": "Dolby 视界",
    "30251": "Dolby 全景声",
    "30240": "HDR 真彩",
    "30232": "1080p60",
    "30080": "1080p+",
    "30064": "1080p",
    "30032": "480p",
    "30016": "360p",
}


def _format_download_type(download_type: str, file_size: int | None = None) -> str:
    size_str = ""
    if file_size:
        mb = file_size / (1024 * 1024)
        size_str = f" {mb:.0f}MB"

    if download_type == "audio":
        return f"🎵 音频{size_str}"
    if download_type == "spotify":
        return f"🎵 Spotify{size_str}"
    if download_type == "subtitle":
        return f"📝 字幕{size_str}"
    if download_type == "thumbnail":
        return f"🖼️ 封面{size_str}"
    if download_type == "video":
        return f"🎬 视频{size_str}"
    if download_type.startswith("video_"):
        fmt_id = download_type.removeprefix("video_")
        label = _FORMAT_LABEL.get(fmt_id, fmt_id)
        return f"🎬 {label}{size_str}"

    return download_type


@command_handler("queue", admin_only=True)
@require_admin
@require_message
async def queue_command(update: Update, context: CallbackContext):
    user_id = update.message.from_user.id
    active = services.task_manager.get_all_active_tasks()
    queued = services.task_manager.get_total_queued()

    msg = t("queue_title", user_id, active=len(active), queued=queued)

    if active:
        msg += "\n" + t("active_downloads", user_id) + "\n"
        for task in list(active)[:10]:
            user_name = await get_user_display_name(task.user_id)
            status_emoji = {
                "downloading": "⬇️",
                "processing":  "⚙️",
                "uploading":   "📤",
            }.get(task.status.value, "⏳")
            msg += f"{status_emoji} {task.download_type} - {user_name}\n"

    await update.message.reply_text(msg)


@command_handler("failed", admin_only=True)
@require_admin
@require_message
async def failed_command(update: Update, context: CallbackContext):
    user_id = update.message.from_user.id
    if not context.args:
        await update.message.reply_text(t("usage_failed", user_id))
        return

    try:
        target_id = int(context.args[0])
    except ValueError:
        await update.message.reply_text(t("invalid_user_id", user_id))
        return

    history = await get_user_history(target_id, limit=50)
    failed = [h for h in history if h.get("status") == "failed"]

    if not failed:
        await update.message.reply_text(t("failed_for_user", user_id, target_id=target_id))
        return

    msg = format_history_list(
        failed[:20],
        t("failed_title_simple", user_id, target_id=target_id) + "\n",
    )
    await update.message.reply_text(msg)


REFRESH_PAGE_SIZE = 5


@command_handler("refresh", admin_only=True)
@require_admin
@require_message
async def refresh_command(update: Update, context: CallbackContext):
    """Force re-download by clearing cached file_id for a URL."""
    user_id = update.message.from_user.id

    if context.args:
        url = " ".join(context.args)
        await clear_file_id_by_url(url)
        await update.message.reply_text(t("refresh_cleared", user_id, url=url))
        return

    await _send_refresh_page(update.message, user_id, page=0, context=context)


async def _send_refresh_page(message_or_query, user_id: int, page: int, context):
    records, total = await get_recent_cached_urls(
        limit=REFRESH_PAGE_SIZE,
        offset=page * REFRESH_PAGE_SIZE,
    )
    total_pages = max(1, (total + REFRESH_PAGE_SIZE - 1) // REFRESH_PAGE_SIZE)

    if not records:
        text = t("refresh_no_cache", user_id)
        if hasattr(message_or_query, "reply_text"):
            await message_or_query.reply_text(text)
        else:
            await message_or_query.edit_message_text(text)
        return

    context.bot_data[f"refresh_urls_{user_id}"] = [r["url"] for r in records]

    base_index = page * REFRESH_PAGE_SIZE
    lines = [t("refresh_pick_prompt", user_id) + f"  ({page + 1}/{total_pages})\n"]
    for i, r in enumerate(records):
        title = (r.get("title") or r["url"])[:40]
        type_label = _format_download_type(r.get("download_type", ""), r.get("file_size"))
        dt = datetime.fromtimestamp(r["timestamp"]).strftime("%m-%d %H:%M")
        lines.append(f"{base_index + i + 1}. {title}\n   {type_label}  {dt}")
    text = "\n".join(lines)

    num_buttons = [
        InlineKeyboardButton(
            str(base_index + i + 1),
            callback_data=f"refresh_do_{user_id}_{i}",
        )
        for i in range(len(records))
    ]
    keyboard = [num_buttons]

    nav = []
    if page > 0:
        nav.append(InlineKeyboardButton("◀️", callback_data=f"refresh_page_{user_id}_{page - 1}"))
    nav.append(InlineKeyboardButton(t("btn_close", user_id), callback_data="cancel_menu_close"))
    if page < total_pages - 1:
        nav.append(InlineKeyboardButton("▶️", callback_data=f"refresh_page_{user_id}_{page + 1}"))
    keyboard.append(nav)

    markup = InlineKeyboardMarkup(keyboard)
    if hasattr(message_or_query, "reply_text"):
        await message_or_query.reply_text(text, reply_markup=markup)
    else:
        await message_or_query.edit_message_text(text, reply_markup=markup)


@command_handler("admcancel", admin_only=True)
@require_admin
@require_message
async def admin_cancel_command(update: Update, context: CallbackContext):
    user_id = update.message.from_user.id

    if not context.args:
        active = list(services.task_manager.get_all_active_tasks())
        queued_size = services.task_manager.get_total_queued()

        if not active and queued_size == 0:
            await update.message.reply_text(t("queue_empty_cn", user_id))
            return

        keyboard = []
        for task in active:
            short_url = task.url[:25] + "..." if len(task.url) > 25 else task.url
            label = f"[{task.task_id}] UID:{task.user_id} {short_url}"
            keyboard.append([
                InlineKeyboardButton(
                    t("btn_cancel_task", user_id, label=label),
                    callback_data=f"cancel_task_{task.task_id}",
                )
            ])

        keyboard.append([InlineKeyboardButton(t("btn_close", user_id), callback_data="cancel_menu_close")])

        msg = t("admin_tasks_title", user_id, active=len(active), queued=queued_size)
        await update.message.reply_text(msg, reply_markup=InlineKeyboardMarkup(keyboard))
        return

    task_id = context.args[0]
    success = await services.task_manager.cancel_task(task_id)

    if success:
        await update.message.reply_text(t("task_cancel_confirm", user_id, task_id=task_id))
    else:
        await update.message.reply_text(t("task_cancel_error", user_id, task_id=task_id))


@command_handler("rateinfo", admin_only=True)
@require_admin
@require_message
async def rateinfo_command(update: Update, context: CallbackContext):
    user_id = update.message.from_user.id
    status = services.limiter.get_status()
    status_text = "Enabled" if status["enabled"] else "Disabled"
    msg = t("rateinfo_status", user_id, status=status_text, max=status["max_downloads_per_hour"], users=status["active_users"])
    await update.message.reply_text(msg)


@command_handler("setrate", admin_only=True)
@require_admin
@require_message
async def setrate_command(update: Update, context: CallbackContext):
    user_id = update.message.from_user.id
    if not context.args:
        await update.message.reply_text(t("usage_setrate", user_id))
        return

    try:
        max_per_hour = int(context.args[0])
        if max_per_hour < 1 or max_per_hour > 100:
            await update.message.reply_text(t("value_range", user_id))
            return
    except ValueError:
        await update.message.reply_text(t("invalid_number", user_id))
        return

    enabled = True
    if len(context.args) > 1:
        enabled = context.args[1].lower() in ["on", "true", "1"]

    save_rate_limit(max_per_hour, enabled)
    services.limiter.reload()

    status = "enabled" if enabled else "disabled"
    await update.message.reply_text(
        t("rate_limit_updated_simple", user_id, max=max_per_hour, status=status)
    )
