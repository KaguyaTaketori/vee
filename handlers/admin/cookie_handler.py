from __future__ import annotations

import logging

from telegram import Update
from telegram.ext import CallbackContext

from core.handler_registry import command_handler
from modules.downloader.services.cookie_service import save_cookie_bytes
from utils.i18n import t
from utils.utils import require_admin, require_message

logger = logging.getLogger(__name__)


async def handle_cookie_file(update: Update, context: CallbackContext) -> None:
    document = update.message.document
    if not document:
        return

    user_id = update.message.from_user.id

    if not document.file_name.endswith(".txt"):
        await update.message.reply_text(t("cookies_file_required", user_id))
        return

    filename = document.file_name

    from modules.downloader.services.cookie_service import resolve_cookie_path
    if resolve_cookie_path(filename) is None:
        await update.message.reply_text(t("invalid_cookies_filename", user_id))
        return

    try:
        tg_file = await document.get_file()
        raw: bytearray = await tg_file.download_as_bytearray()
    except Exception as e:
        logger.error("Failed to download cookie file from Telegram: %s", e, exc_info=True)
        await update.message.reply_text(t("cookies_save_error", user_id, error=str(e)))
        return

    try:
        result = await save_cookie_bytes(filename, bytes(raw))
    except (ValueError, OSError) as e:
        logger.error("Failed to save cookie file '%s': %s", filename, e, exc_info=True)
        await update.message.reply_text(t("cookies_save_error", user_id, error=str(e)))
        return

    if result.domain:
        await update.message.reply_text(t("cookies_saved", user_id, domain=result.domain))
    else:
        await update.message.reply_text(t("cookies_updated", user_id, path=result.path))
