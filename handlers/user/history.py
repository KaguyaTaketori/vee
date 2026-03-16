import logging
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import CallbackContext

from services.queue import download_queue
from models.domain_models import STATUS_EMOJI, DownloadStatus
from utils.i18n import t
from utils.utils import format_history_list, format_history_item, require_message
from database.history import get_user_history_page
from database.task_store import get_user_tasks

logger = logging.getLogger(__name__)

PAGE_SIZE = 5


@require_message
async def history_command(update: Update, context: CallbackContext):
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
        nav.append(InlineKeyboardButton(t("prev_page", user_id), callback_data=f"history_page_{user_id}_{page - 1}"))
    if page < total_pages - 1:
        nav.append(InlineKeyboardButton(t("next_page", user_id), callback_data=f"history_page_{user_id}_{page + 1}"))

    keyboard = [nav] if nav else []
    markup = InlineKeyboardMarkup(keyboard) if keyboard else None

    if hasattr(message_or_query, "reply_text"):
        await message_or_query.reply_text(text, reply_markup=markup)
    else:
        await message_or_query.edit_message_text(text, reply_markup=markup)


@require_message
async def tasks_command(update: Update, context: CallbackContext):
    user_id = update.message.from_user.id

    records = await get_user_tasks(user_id, limit=10)
    if not records:
        await update.message.reply_text(t("no_tasks", user_id))
        return

    lines = [f"📋 {t('recent_tasks', user_id)}\n"]
    for r in records:
        emoji = STATUS_EMOJI.get(DownloadStatus(r["status"]), "❓")
        title = (r.get("title") or r["url"])[:35]
        retry_count = r.get("retry_count", 0)
        retry_info = f" ({t('retry_count', user_id, count=retry_count)})" if retry_count else ""
        err_info = f"\n   ⚠️ {r['error'][:40]}" if r.get("error") else ""
        lines.append(f"{emoji} [{r['download_type']}] {title}{retry_info}{err_info}")

    await update.message.reply_text("\n".join(lines))


@require_message
async def cancel_command(update: Update, context: CallbackContext):
    user_id = update.message.from_user.id

    user_tasks = download_queue.get_user_tasks(user_id)
    active = [
        task for task in user_tasks
        if task.status in (DownloadStatus.QUEUED, DownloadStatus.DOWNLOADING, DownloadStatus.PROCESSING)
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

    keyboard.append([InlineKeyboardButton("❌ " + t("close_menu", user_id), callback_data="cancel_menu_close")])

    await update.message.reply_text(
        t("select_cancel_task", user_id),
        reply_markup=InlineKeyboardMarkup(keyboard)
    )
