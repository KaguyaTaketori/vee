import os
import re
import logging
from telegram import Update
from telegram.ext import CallbackContext
from config import COOKIE_FILE, COOKIES_DIR
from utils.i18n import t

logger = logging.getLogger(__name__)


async def handle_cookie_file(update: Update, context: CallbackContext):
    document = update.message.document
    if not document:
        return
    
    user_id = update.message.from_user.id
    
    if not document.file_name.endswith('.txt'):
        await update.message.reply_text(t("cookies_file_required", user_id))
        return
    
    filename = document.file_name
    match = re.match(r'([^_]+)_cookies\.txt', filename)
    
    if match:
        site_domain = match.group(1)
        site_cookie_file = os.path.join(COOKIES_DIR, f"{site_domain}_cookies.txt")
        try:
            file = await document.get_file()
            await file.download_to_drive(custom_path=site_cookie_file)
            await update.message.reply_text(t("cookies_saved", user_id, domain=site_domain))
        except Exception as e:
            logger.error(
                f"Failed to save cookie file '{document.file_name}' "
                f"for user {user_id}: {e}",
                exc_info=True
            )
            await update.message.reply_text(
                t("cookies_save_error", user_id, error=str(e))
            )
    else:
        if COOKIE_FILE:
            try:
                file = await document.get_file()
                await file.download_to_drive(custom_path=COOKIE_FILE)
                await update.message.reply_text(t("cookies_updated", user_id, path=COOKIE_FILE))
            except Exception as e:
                await update.message.reply_text(t("cookies_save_error", user_id, error=str(e)))
        else:
            await update.message.reply_text(t("invalid_cookies_filename", user_id))
