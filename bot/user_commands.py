import logging
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import CallbackContext

from config import ADMIN_IDS
from services.user_service import track_user
from services.queue import download_queue
from models.domain_models import DownloadStatus
from utils.logger import log_user
from utils.i18n import t, set_user_lang, LANGUAGES
from utils.utils import format_history_list, format_history_item
from database.history import get_user_history_page
from database.task_store import get_user_tasks

logger = logging.getLogger(__name__)

PAGE_SIZE = 5


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
    await update.message.reply_text(
        t("your_id", user.id, username=user.username or "N/A", name=f"{user.first_name} {user.last_name or ''}"),
        parse_mode="Markdown"
    )


async def help_command(update: Update, context: CallbackContext):
    if not update.message:
        return
    user_id = update.message.from_user.id
    is_admin = user_id in ADMIN_IDS if ADMIN_IDS else False
    
    if is_admin:
        await update.message.reply_text(t("admin_commands", user_id))
    else:
        await update.message.reply_text(t("available_commands", user_id))


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


async def tasks_command(update: Update, context: CallbackContext):
    if not update.message:
        return
    user_id = update.message.from_user.id

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
