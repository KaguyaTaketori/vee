"""
modules/downloader/handlers/inline_actions.py

InlineKeyboard callback handlers for the downloader module.

All handler business logic now operates on PlatformContext rather than
raw PTB query objects.  The PTB-specific wiring (query.answer,
query.edit_message_text, etc.) is confined to TelegramContext.
"""
from __future__ import annotations

import logging

from config import ADMIN_IDS
from core.callback_bus import register
from core.callback_bus import handle_callback  # re-export for DownloaderModule.setup()
from modules.downloader.strategies.sender import TelegramSender
from modules.downloader.services.facades import DownloadFacade
from shared.services.middleware import RequestContext, default_pipeline
from shared.services.container import services
from shared.services.platform_context import TelegramContext
from shared.services.user_service import set_user_language, warm_user_lang
from shared.services.session import UserSession
from database.history import get_user_history, clear_file_id_by_url
from utils.i18n import LANGUAGES, t
from utils.utils import format_history_list
from utils.auth import check_admin
from handlers.user.history import _send_history_page

logger = logging.getLogger(__name__)


# ── Language picker ────────────────────────────────────────────────────────

@register(lambda d: d.startswith("lang_"))
async def _cb_lang(query, context) -> None:
    lang_code = query.data.replace("lang_", "")
    if lang_code in LANGUAGES:
        await set_user_language(query.from_user.id, lang_code)
        ctx = TelegramContext.from_callback_query(query, context)
        await ctx.edit(t("language_changed", query.from_user.id))


# ── Admin: view user history ───────────────────────────────────────────────

@register(lambda d: d.startswith("uh_"))
async def _cb_admin_history(query, context) -> None:
    user_id = query.from_user.id
    if not check_admin(user_id):
        await query.answer(t("admin_only", user_id), show_alert=True)
        return
    target_id = int(query.data.replace("uh_", ""))
    history = await get_user_history(target_id, limit=20)
    msg = format_history_list(history, f"Download history for user {target_id}:\n\n")
    ctx = TelegramContext.from_callback_query(query, context)
    await ctx.edit(msg)


# ── History pagination ─────────────────────────────────────────────────────

@register(lambda d: d.startswith("history_page_"))
async def _cb_history_page(query, context) -> None:
    parts = query.data.split("_")
    target_user_id = int(parts[2])
    page = int(parts[3])
    if query.from_user.id != target_user_id:
        await query.answer(t("not_your_history", query.from_user.id), show_alert=True)
        return
    await query.answer()
    await _send_history_page(query, target_user_id, page)


# ── Close / cancel menu ────────────────────────────────────────────────────

@register(lambda d: d == "cancel_menu_close")
async def _cb_cancel_close(query, context) -> None:
    try:
        await query.delete_message()
    except Exception:
        ctx = TelegramContext.from_callback_query(query, context)
        await ctx.edit(t("closed", query.from_user.id))


# ── Cancel own task ────────────────────────────────────────────────────────

@register(lambda d: d.startswith("cancel_task_"))
async def _cb_cancel_task(query, context) -> None:
    user_id = query.from_user.id
    task_id = query.data.replace("cancel_task_", "")
    task = services.queue.get_task(task_id)
    ctx = TelegramContext.from_callback_query(query, context)

    if not task:
        await ctx.edit(t("task_not_found", user_id))
        return

    is_admin = user_id in ADMIN_IDS if ADMIN_IDS else False
    if task.user_id != user_id and not is_admin:
        await query.answer(t("cancel_own_only", user_id), show_alert=True)
        return

    cancelled = await services.queue.cancel_task(task_id)
    if cancelled:
        await ctx.edit(t("task_cancelled", user_id))
    else:
        await ctx.edit(t("cancel_failed", user_id))


# ── Admin cancel task ──────────────────────────────────────────────────────

@register(lambda d: d.startswith("admcancel_task_"))
async def _cb_admcancel_task(query, context) -> None:
    user_id = query.from_user.id
    if not check_admin(user_id):
        await query.answer(t("admin_only", user_id), show_alert=True)
        return
    task_id = query.data.replace("admcancel_task_", "")
    ctx = TelegramContext.from_callback_query(query, context)
    cancelled = await services.queue.cancel_task(task_id)
    if cancelled:
        await ctx.edit(t("task_cancelled", user_id))
    else:
        await ctx.edit(t("cancel_failed", user_id))


# ── Download: video / audio / thumbnail selection ──────────────────────────

@register(lambda d: d.startswith("dl_video_") or d.startswith("dl_audio_") or
          d.startswith("dl_thumb_") or d.startswith("dl_spotify_"))
async def _cb_download_select(query, context) -> None:
    user_id = query.from_user.id
    data = query.data

    if data.startswith("dl_video_"):
        session_key = data[len("dl_video_"):]
        download_type = "video"
    elif data.startswith("dl_audio_"):
        session_key = data[len("dl_audio_"):]
        download_type = "audio"
    elif data.startswith("dl_thumb_"):
        session_key = data[len("dl_thumb_"):]
        download_type = "thumbnail"
    else:
        session_key = data[len("dl_spotify_"):]
        download_type = "spotify"

    session = UserSession.load(session_key)
    if session is None:
        ctx = TelegramContext.from_callback_query(query, context)
        await ctx.edit(t("session_expired", user_id))
        return

    mw_ctx = RequestContext(user=query.from_user, reply=query.message.reply_text)
    result = await default_pipeline.run(mw_ctx)
    if not result.ok:
        await query.answer(t(result.error_key, user_id), show_alert=True)
        return

    processing_msg = await query.message.reply_text(t("downloading", user_id))
    sender = TelegramSender.from_callback(query, processing_msg)
    await DownloadFacade.enqueue(
        UserSession(url=session.url, user_id=user_id, sender=sender),
        download_type,
    )
    await query.answer()


# ── Refresh cache: pick URL ────────────────────────────────────────────────

@register(lambda d: d.startswith("refresh_do_"))
async def _cb_refresh_do(query, context) -> None:
    user_id = query.from_user.id
    if not check_admin(user_id):
        await query.answer(t("admin_only", user_id), show_alert=True)
        return
    parts = query.data.split("_")
    admin_id = int(parts[2])
    index = int(parts[3])
    urls = context.bot_data.get(f"refresh_urls_{admin_id}")
    ctx = TelegramContext.from_callback_query(query, context)
    if not urls or index >= len(urls):
        await ctx.edit(t("refresh_session_expired", user_id))
        return
    url = urls[index]
    await clear_file_id_by_url(url)
    context.bot_data.pop(f"refresh_urls_{admin_id}", None)
    await ctx.edit(t("refresh_cleared", user_id, url=url))


@register(lambda d: d.startswith("refresh_page_"))
async def _cb_refresh_page(query, context) -> None:
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
