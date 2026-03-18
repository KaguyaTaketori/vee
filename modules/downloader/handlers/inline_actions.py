"""
modules/downloader/handlers/inline_actions.py

InlineKeyboard callback handlers for the downloader module.

Decoupling
──────────
All handlers now receive a ``CallbackContext`` (from core.callback_bus)
instead of a raw PTB CallbackQuery + context pair.

• ctx.data            — callback_data string
• ctx.user_id         — sender's user ID
• ctx.user            — user object (for RequestContext)
• ctx.platform_ctx    — PlatformContext (send / edit / send_keyboard / …)
• ctx.answer()        — silent ACK
• ctx.answer_alert()  — alert popup ACK
• ctx.delete_message()
• ctx.raw_context     — PTB CallbackContext, only where bot_data is needed
                        (_cb_refresh_page, _cb_refresh_do)

No handler imports telegram.* directly.
"""
from __future__ import annotations

import logging

from config import ADMIN_IDS
from core.callback_bus import register, CallbackContext, TelegramCallbackContext
from core.callback_bus import handle_callback  # re-export for DownloaderModule.setup()
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


# ── Language picker ────────────────────────────────────────────────────────

@register(lambda d: d.startswith("lang_"))
async def _cb_lang(ctx: CallbackContext) -> None:
    lang_code = ctx.data.replace("lang_", "")
    if lang_code in LANGUAGES:
        await set_user_language(ctx.user_id, lang_code)
        await ctx.platform_ctx.edit(t("language_changed", ctx.user_id))
    await ctx.answer()


# ── Admin: view user history ───────────────────────────────────────────────

@register(lambda d: d.startswith("uh_"))
async def _cb_admin_history(ctx: CallbackContext) -> None:
    if not check_admin(ctx.user_id):
        await ctx.answer_alert(t("admin_only", ctx.user_id))
        return
    target_id = int(ctx.data.replace("uh_", ""))
    history = await get_user_history(target_id, limit=20)
    msg = format_history_list(history, f"Download history for user {target_id}:\n\n")
    await ctx.platform_ctx.edit(msg)
    await ctx.answer()


# ── History pagination ─────────────────────────────────────────────────────

@register(lambda d: d.startswith("history_page_"))
async def _cb_history_page(ctx: CallbackContext) -> None:
    parts = ctx.data.split("_")
    target_user_id = int(parts[2])
    page = int(parts[3])
    if ctx.user_id != target_user_id:
        await ctx.answer_alert(t("not_your_history", ctx.user_id))
        return
    await ctx.answer()
    # _send_history_page now accepts PlatformContext directly — no PTB raw
    # query needed.  edit=True → edit_keyboard (mutate existing message).
    await _send_history_page(ctx.platform_ctx, target_user_id, page, edit=True)


# ── Close / cancel menu ────────────────────────────────────────────────────

@register(lambda d: d == "cancel_menu_close")
async def _cb_cancel_close(ctx: CallbackContext) -> None:
    try:
        await ctx.delete_message()
    except Exception:
        await ctx.platform_ctx.edit(t("closed", ctx.user_id))


# ── Cancel own task ────────────────────────────────────────────────────────

@register(lambda d: d.startswith("cancel_task_"))
async def _cb_cancel_task(ctx: CallbackContext) -> None:
    task_id = ctx.data.replace("cancel_task_", "")
    task = services.queue.get_task(task_id)

    if not task:
        await ctx.platform_ctx.edit(t("task_not_found", ctx.user_id))
        return

    is_admin = ctx.user_id in ADMIN_IDS if ADMIN_IDS else False
    if task.user_id != ctx.user_id and not is_admin:
        await ctx.answer_alert(t("cancel_own_only", ctx.user_id))
        return

    cancelled = await services.queue.cancel_task(task_id)
    await ctx.platform_ctx.edit(
        t("task_cancelled", ctx.user_id) if cancelled else t("cancel_failed", ctx.user_id)
    )


# ── Admin cancel task ──────────────────────────────────────────────────────

@register(lambda d: d.startswith("admcancel_task_"))
async def _cb_admcancel_task(ctx: CallbackContext) -> None:
    if not check_admin(ctx.user_id):
        await ctx.answer_alert(t("admin_only", ctx.user_id))
        return
    task_id = ctx.data.replace("admcancel_task_", "")
    cancelled = await services.queue.cancel_task(task_id)
    await ctx.platform_ctx.edit(
        t("task_cancelled", ctx.user_id) if cancelled else t("cancel_failed", ctx.user_id)
    )


# ── Download: video / audio / thumbnail selection ──────────────────────────

@register(lambda d: d.startswith("dl_video_") or d.startswith("dl_audio_") or
          d.startswith("dl_thumb_") or d.startswith("dl_spotify_"))
async def _cb_download_select(ctx: CallbackContext) -> None:
    data = ctx.data

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
        await ctx.platform_ctx.edit(t("session_expired", ctx.user_id))
        return

    # Middleware check (auth + rate limit) — RequestContext needs the user object
    mw_ctx = RequestContext(
        user=ctx.user,
        reply=ctx.platform_ctx.send,
    )
    result = await default_pipeline.run(mw_ctx)
    if not result.ok:
        await ctx.answer_alert(t(result.error_key, ctx.user_id))
        return

    # TelegramSender still requires the raw PTB query to build its reply target.
    # Reach through TelegramCallbackContext to get the underlying query.
    processing_msg = await ctx.platform_ctx.send(t("downloading", ctx.user_id))

    if isinstance(ctx, TelegramCallbackContext):
        sender = TelegramSender.from_callback(ctx._query, processing_msg)
    else:
        sender = None  # type: ignore[assignment]

    await DownloadFacade.enqueue(
        UserSession(url=session.url, user_id=ctx.user_id, sender=sender),
        download_type,
    )
    await ctx.answer()


# ── Refresh cache: pick URL ────────────────────────────────────────────────

@register(lambda d: d.startswith("refresh_do_"))
async def _cb_refresh_do(ctx: CallbackContext) -> None:
    if not check_admin(ctx.user_id):
        await ctx.answer_alert(t("admin_only", ctx.user_id))
        return
    parts = ctx.data.split("_")
    admin_id = int(parts[2])
    index = int(parts[3])
    # bot_data is PTB-specific — accessed through raw_context
    urls = ctx.raw_context.bot_data.get(f"refresh_urls_{admin_id}")
    if not urls or index >= len(urls):
        await ctx.platform_ctx.edit(t("refresh_session_expired", ctx.user_id))
        return
    url = urls[index]
    await clear_file_id_by_url(url)
    ctx.raw_context.bot_data.pop(f"refresh_urls_{admin_id}", None)
    await ctx.platform_ctx.edit(t("refresh_cleared", ctx.user_id, url=url))


@register(lambda d: d.startswith("refresh_page_"))
async def _cb_refresh_page(ctx: CallbackContext) -> None:
    if not check_admin(ctx.user_id):
        await ctx.answer_alert(t("admin_only", ctx.user_id))
        return
    parts = ctx.data.split("_")
    admin_id = int(parts[2])
    page = int(parts[3])
    from handlers.admin.tasks import _send_refresh_page
    await ctx.answer()
    if isinstance(ctx, TelegramCallbackContext):
        await _send_refresh_page(ctx._query, admin_id, page, ctx.raw_context)
