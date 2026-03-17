import logging
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import CallbackContext

from config import ADMIN_IDS
from services.user_service import track_user, set_user_language
from utils.logger import log_user
from utils.i18n import t, LANGUAGES
from utils.utils import require_message

logger = logging.getLogger(__name__)


@require_message
async def start_command(update: Update, context: CallbackContext):
    track_user(update.message.from_user)
    log_user(update.message.from_user, "start")
    user = update.message.from_user
    await update.message.reply_text(t("welcome", user.id))


@require_message
async def myid_command(update: Update, context: CallbackContext):
    user = update.message.from_user
    await update.message.reply_text(
        t("your_id", user.id, username=user.username or "N/A", name=f"{user.first_name} {user.last_name or ''}"),
        parse_mode="Markdown"
    )


@require_message
async def help_command(update: Update, context: CallbackContext):
    user_id = update.message.from_user.id
    is_admin = user_id in ADMIN_IDS if ADMIN_IDS else False

    if is_admin:
        await update.message.reply_text(t("admin_commands", user_id))
    else:
        await update.message.reply_text(t("available_commands", user_id))


@require_message
async def lang_command(update: Update, context: CallbackContext):
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
        await update.message.reply_text(t("invalid_language_option", user_id))
        return

    await set_user_language(user_id, lang_code)
    await update.message.reply_text(t("language_changed", user_id))

