"""
handlers/admin/cookies.py
"""
from __future__ import annotations

import logging

from telegram import Update
from telegram.ext import CallbackContext

from modules.downloader.services.cookie_service import save_cookie_bytes, resolve_cookie_path
from shared.services.file_receiver import BotFileReceiver
from shared.services.platform_context import TelegramContext
from core.handler_registry import command_handler
from utils.i18n import t
from utils.utils import require_admin, require_message

logger = logging.getLogger(__name__)


# ── /cookie ───────────────────────────────────────────────────────────────

@command_handler("cookie", admin_only=True)
@require_admin
@require_message
async def cookie_command(update: Update, context: CallbackContext) -> None:
    ctx = TelegramContext.from_message(update, context)
    await ctx.send(
        t("cookie_instruction_title", ctx.user_id)
        + "\n\n"
        + t("cookie_instruction_steps", ctx.user_id)
    )


# ── Document handler ───────────────────────────────────────────────────────

async def handle_cookie_file(
    update: Update,
    context: CallbackContext,
    *,
    receiver: BotFileReceiver | None = None,
) -> None:
    """Handle an uploaded cookie .txt file.

    Parameters
    ----------
    receiver:
        A ``BotFileReceiver`` instance.  When *None* (production path),
        a ``TelegramFileReceiver`` is built from ``update.message``.
        Pass a mock receiver in tests to avoid any Telegram I/O.
    """
    ctx = TelegramContext.from_message(update, context)

    if receiver is None:
        from modules.downloader.integrations.telegram_file_receiver import TelegramFileReceiver
        receiver = TelegramFileReceiver.from_message(update.message)

    if receiver is None:
        return

    meta = receiver.file_meta()
    if meta is None:
        return

    filename = meta.filename

    if not filename.endswith(".txt"):
        await ctx.send(t("cookies_file_required", ctx.user_id))
        return

    if resolve_cookie_path(filename) is None:
        await ctx.send(t("invalid_cookies_filename", ctx.user_id))
        return

    try:
        data: bytes = await receiver.download()
    except Exception as exc:
        logger.error("handle_cookie_file: download failed for '%s': %s", filename, exc, exc_info=True)
        await ctx.send(t("cookies_save_error", ctx.user_id, error=str(exc)))
        return

    try:
        result = await save_cookie_bytes(filename, data)
    except (ValueError, OSError) as exc:
        logger.error("handle_cookie_file: save failed for '%s': %s", filename, exc, exc_info=True)
        await ctx.send(t("cookies_save_error", ctx.user_id, error=str(exc)))
        return

    if result.domain:
        await ctx.send(t("cookies_saved", ctx.user_id, domain=result.domain))
    else:
        await ctx.send(t("cookies_updated", ctx.user_id, path=result.path))
