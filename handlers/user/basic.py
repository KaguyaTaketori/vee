import logging
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import CallbackContext

from config import ADMIN_IDS
from core.handler_registry import command_handler
from services.user_service import track_user, set_user_language
from utils.logger import log_user
from utils.i18n import t, LANGUAGES
from utils.utils import require_message

logger = logging.getLogger(__name__)


@command_handler("start")
@require_message
async def start_command(update: Update, context: CallbackContext):
    track_user(update.message.from_user)
    log_user(update.message.from_user, "start")
    await update.message.reply_text(t("welcome", update.message.from_user.id))


@command_handler("help")
@require_message
async def help_command(update: Update, context: CallbackContext):
    user_id = update.message.from_user.id
    is_admin = user_id in ADMIN_IDS if ADMIN_IDS else False
    key = "admin_commands" if is_admin else "available_commands"
    await update.message.reply_text(t(key, user_id))


@command_handler("myid")
@require_message
async def myid_command(update: Update, context: CallbackContext):
    user = update.message.from_user
    await update.message.reply_text(
        t("your_id", user.id, username=user.username or "N/A",
          name=f"{user.first_name} {user.last_name or ''}"),
        parse_mode="Markdown",
    )


@command_handler("lang")
@require_message
async def lang_command(update: Update, context: CallbackContext):
    user_id = update.message.from_user.id
    if not context.args:
        keyboard = [
            [InlineKeyboardButton(name, callback_data=f"lang_{code}")]
            for code, name in LANGUAGES.items()
        ]
        await update.message.reply_text(
            t("select_language", user_id),
            reply_markup=InlineKeyboardMarkup(keyboard),
        )
        return
    lang_code = context.args[0].lower()
    if lang_code not in LANGUAGES:
        await update.message.reply_text(t("invalid_language_option", user_id))
        return
    await set_user_language(user_id, lang_code)
    await update.message.reply_text(t("language_changed", user_id))
