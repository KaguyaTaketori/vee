"""
handlers/admin/cookies.py
─────────────────────────
Admin command and file handler for cookie management.

Decoupling
──────────
The previous version called ``document.get_file()`` and
``tg_file.download_as_bytearray()`` directly — a hard Telegram dependency
buried in business logic.

This version accepts a ``BotFileReceiver`` so the download step is
platform-agnostic:

    cookie_command      — unchanged, only uses reply_text
    handle_cookie_file  — accepts optional receiver; defaults to
                          TelegramFileReceiver in production, but any
                          BotFileReceiver-conforming object works in tests.

Testing example
───────────────
::

    class MockReceiver:
        def file_meta(self):
            return IncomingFile(filename="youtube_cookies.txt")
        async def download(self):
            return b"# Netscape HTTP Cookie File\\n..."

    await handle_cookie_file(mock_update, mock_ctx, receiver=MockReceiver())

No Telegram object, no network call, test completes instantly.
"""

from __future__ import annotations

import logging

from telegram import Update
from telegram.ext import CallbackContext

from modules.downloader.services.cookie_service import save_cookie_bytes, resolve_cookie_path
from shared.services.file_receiver import BotFileReceiver
from core.handler_registry import command_handler
from utils.i18n import t
from utils.utils import require_admin, require_message

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# /cookie command — instruct admin how to upload cookie file
# ---------------------------------------------------------------------------

@command_handler("cookie", admin_only=True)
@require_admin
@require_message
async def cookie_command(update: Update, context: CallbackContext) -> None:
    user_id = update.message.from_user.id
    await update.message.reply_text(
        t("cookie_instruction_title", user_id) + "\n\n"
        + t("cookie_instruction_steps", user_id)
    )


# ---------------------------------------------------------------------------
# Document message handler — receives the actual cookie file
# ---------------------------------------------------------------------------

async def handle_cookie_file(
    update: Update,
    context: CallbackContext,
    *,
    receiver: BotFileReceiver | None = None,
) -> None:
    """Handle an uploaded cookie .txt file.

    Parameters
    ----------
    update, context:
        Standard PTB handler arguments.
    receiver:
        A ``BotFileReceiver`` instance.  When *None* (the normal production
        path), a ``TelegramFileReceiver`` is built from ``update.message``.
        Pass an explicit receiver in tests to avoid any Telegram I/O.
    """
    user_id = update.message.from_user.id

    # ── Resolve receiver ────────────────────────────────────────────────────
    if receiver is None:
        from modules.downloader.integrations.telegram_file_receiver import (
            TelegramFileReceiver,
        )
        receiver = TelegramFileReceiver.from_message(update.message)

    if receiver is None:
        # No document attached — should not happen given the MessageHandler
        # filter, but guard defensively.
        return

    # ── Validate filename ────────────────────────────────────────────────────
    meta = receiver.file_meta()
    if meta is None:
        return

    filename = meta.filename

    if not filename.endswith(".txt"):
        await update.message.reply_text(t("cookies_file_required", user_id))
        return

    if resolve_cookie_path(filename) is None:
        await update.message.reply_text(t("invalid_cookies_filename", user_id))
        return

    # ── Download via platform-agnostic receiver ──────────────────────────────
    try:
        data: bytes = await receiver.download()
    except Exception as exc:
        logger.error(
            "handle_cookie_file: download failed for '%s': %s",
            filename, exc, exc_info=True,
        )
        await update.message.reply_text(
            t("cookies_save_error", user_id, error=str(exc))
        )
        return

    # ── Persist ──────────────────────────────────────────────────────────────
    try:
        result = await save_cookie_bytes(filename, data)
    except (ValueError, OSError) as exc:
        logger.error(
            "handle_cookie_file: save failed for '%s': %s",
            filename, exc, exc_info=True,
        )
        await update.message.reply_text(
            t("cookies_save_error", user_id, error=str(exc))
        )
        return

    # ── Reply ─────────────────────────────────────────────────────────────────
    if result.domain:
        await update.message.reply_text(
            t("cookies_saved", user_id, domain=result.domain)
        )
    else:
        await update.message.reply_text(
            t("cookies_updated", user_id, path=result.path)
        )
