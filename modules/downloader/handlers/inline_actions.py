# modules/downloader/handlers/inline_actions.py
from __future__ import annotations

import logging

from config import ADMIN_IDS
from core.callback_bus import register          # ← 从 core 层导入，不再自己维护列表
from core.callback_bus import handle_callback   # re-export，让 DownloaderModule.setup() 仍可从这里取
from modules.downloader.strategies.sender import TelegramSender
from modules.downloader.services.facades import DownloadFacade
from shared.services.middleware import RequestContext, default_pipeline
from shared.services.container import services
from shared.services.user_service import set_user_language, warm_user_lang
from shared.services.session import UserSession
from database.history import get_user_history, clear_file_id_by_url
from utils.i18n import LANGUAGES, t
from utils.utils import format_history_list
from utils.auth import check_admin
from handlers.user.history import _send_history_page

logger = logging.getLogger(__name__)


@register(lambda d: d.startswith("lang_"))
async def _cb_lang(query, context):
    lang_code = query.data.replace("lang_", "")
    if lang_code in LANGUAGES:
        await set_user_language(query.from_user.id, lang_code)
        await query.edit_message_text(t("language_changed", query.from_user.id))


@register(lambda d: d.startswith("uh_"))
async def _cb_admin_history(query, context):
    user_id = query.from_user.id
    if not check_admin(user_id):
        await query.answer(t("admin_only", user_id), show_alert=True)
        return
    target_id = int(query.data.replace("uh_", ""))
    history = await get_user_history(target_id, limit=20)
    msg = format_history_list(history, f"Download history for user {target_id}:\n\n")
    await query.edit_message_text(msg)


@register(lambda d: d == "cancel_menu_close")
async def _cb_cancel_close(query, context):
    try:
        await query.delete_message()
    except Exception:
        await query.edit_message_text(t("closed", query.from_user.id))


@register(lambda d: d.startswith("cancel_task_"))
async def _cb_cancel_task(query, context):
    user_id = query.from_user.id
    task_id = query.data.replace("cancel_task_", "")
    task = services.queue.get_task(task_id)
    if not task:
        await query.edit_message_text(t("task_not_found", user_id))
        return
    is_admin = user_id in ADMIN_IDS if ADMIN_IDS else False
    if task.user_id != user_id and not is_admin:
        await query.answer(t("cancel_own_only", user_id), show_alert=True)
        return
    cancelled = await services.queue.cancel_task(task_id)
    if cancelled:
        await query.edit_message_text(t("task_cancelled", user_id))
    else:
        await query.edit_message_text(t("cancel_failed", user_id))


@register(lambda d: d.startswith("admcancel_task_"))
async def _cb_admcancel_task(query, context):
    user_id = query.from_user.id
    if not check_admin(user_id):
        await query.answer(t("admin_only", user_id), show_alert=True)
        return
    task_id = query.data.replace("admcancel_task_", "")
    cancelled = await services.queue.cancel_task(task_id)
    msg = t("task_cancel_confirm", user_id, task_id=task_id) if cancelled \
        else t("task_cancel_error", user_id, task_id=task_id)
    await query.edit_message_text(msg)


@register(lambda d: d.startswith("download_") or d.startswith("quality_"))
async def _cb_download(query, context):
    user_id = query.from_user.id
    url = UserSession.get_pending_url(context, user_id)
    if not url:
        await query.edit_message_text(t("session_expired", user_id))
        return

    async def _reply(text: str) -> None:
        try:
            await query.edit_message_text(text)
        except Exception:
            pass

    pipeline_ctx = RequestContext(user=query.from_user, reply=_reply)
    result = await default_pipeline.run(pipeline_ctx)
    if not result.ok:
        await _reply(t(result.error_key, user_id))
        return

    try:
        processing_msg = await query.edit_message_text(t("processing", user_id))
    except Exception:
        processing_msg = query.message

    sender = TelegramSender.from_callback(query, processing_msg)
    success, error_key = await DownloadFacade.process_download_request(
        sender=sender,
        url=url,
        callback_data=query.data,
        context=context,
    )
    if not success:
        try:
            await sender.edit_status(t(error_key, user_id))
        except Exception:
            pass


@register(lambda d: d.startswith("history_page_"))
async def _cb_history_page(query, context):
    parts = query.data.split("_")
    target_user_id = int(parts[2])
    page = int(parts[3])
    if query.from_user.id != target_user_id:
        await query.answer(t("not_your_history", query.from_user.id), show_alert=True)
        return
    await query.answer()
    await _send_history_page(query, target_user_id, page)


@register(lambda d: d.startswith("refresh_do_"))
async def _cb_refresh_do(query, context):
    user_id = query.from_user.id
    if not check_admin(user_id):
        await query.answer(t("admin_only", user_id), show_alert=True)
        return
    parts = query.data.split("_")
    admin_id = int(parts[2])
    index = int(parts[3])
    urls = context.bot_data.get(f"refresh_urls_{admin_id}")
    if not urls or index >= len(urls):
        await query.edit_message_text(t("refresh_session_expired", user_id))
        return
    url = urls[index]
    await clear_file_id_by_url(url)
    context.bot_data.pop(f"refresh_urls_{admin_id}", None)
    await query.edit_message_text(t("refresh_cleared", user_id, url=url))


@register(lambda d: d.startswith("refresh_page_"))
async def _cb_refresh_page(query, context):
    user_id = query.from_user.id
    if not check_admin(user_id):
        await query.answer(t("admin_only", user_id), show_alert=True)
        return
    parts = query.data.split("_")
    admin_id = int(parts[2])
    page = int(parts[3])
    from handlers.admin.tasks import _send_refresh_page
    await query.answer()
    await _send_refresh_page(query, admin_id, page, context)
