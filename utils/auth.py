import logging
from functools import wraps
from telegram import Update
from telegram.ext import CallbackContext
from config import ADMIN_IDS
from utils.i18n import t

logger = logging.getLogger(__name__)

def is_user_allowed(user_id: int) -> bool:
    from shared.services.user_service import get_allowed_users
    allowed = get_allowed_users()
    return not allowed or user_id in allowed

def check_admin(user_id: int) -> bool:
    return bool(ADMIN_IDS) and user_id in ADMIN_IDS

def require_admin(func):
    @wraps(func)
    async def wrapper(update: Update, context: CallbackContext):
        if not update.message:
            return
        user_id = update.message.from_user.id
        if not check_admin(user_id):
            logger.warning("未授权管理员命令: user_id=%s, cmd=%s",
                           user_id, update.message.text)
            await update.message.reply_text(t("not_authorized", user_id))
            return
        return await func(update, context)
    return wrapper

def require_message(func):
    @wraps(func)
    async def wrapper(update: Update, context: CallbackContext):
        if not update.message:
            return
        return await func(update, context)
    return wrapper
