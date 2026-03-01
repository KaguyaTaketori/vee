from telegram import Update
from telegram.ext import CallbackContext

from config import ADMIN_IDS, get_allowed_users, save_allowed_users
from core.logger import log_user, get_user_stats


async def start_command(update: Update, context: CallbackContext):
    if not update.message:
        return
    log_user(update.message.from_user, "start")
    await update.message.reply_text(
        "Welcome! Send me a link from YouTube, TikTok, Instagram, Twitter, or other supported platforms.\n\n"
        "I can download:\n"
        "• Videos (up to 2GB with local Bot API)\n"
        "• Thumbnails\n"
        "• Audio (MP3)\n\n"
        "Just send a link and choose what to download!"
    )


async def help_command(update: Update, context: CallbackContext):
    if not update.message:
        return
    user_id = update.message.from_user.id
    is_admin = user_id in ADMIN_IDS if ADMIN_IDS else False
    
    if is_admin:
        await update.message.reply_text(
            "Available commands:\n"
            "/start - Start the bot\n"
            "/help - Show this help\n\n"
            "Admin commands:\n"
            "/allow <user_id> - Allow a user\n"
            "/block <user_id> - Block a user\n"
            "/users - List allowed users\n"
            "/stats - Bot usage stats"
        )
    else:
        await update.message.reply_text(
            "Available commands:\n"
            "/start - Start the bot\n"
            "/help - Show this help\n\n"
            "Just send a link to download!"
        )


async def stats_command(update: Update, context: CallbackContext):
    if not update.message:
        return
    user_id = update.message.from_user.id
    if ADMIN_IDS and user_id not in ADMIN_IDS:
        return
    stats = get_user_stats()
    await update.message.reply_text(stats)


async def allow_command(update: Update, context: CallbackContext):
    if not update.message:
        return
    user_id = update.message.from_user.id
    if ADMIN_IDS and user_id not in ADMIN_IDS:
        return
    
    if not context.args:
        await update.message.reply_text("Usage: /allow <user_id>")
        return
    
    try:
        target_id = int(context.args[0])
    except ValueError:
        await update.message.reply_text("Invalid user ID. Must be a number.")
        return
    
    users = get_allowed_users()
    users.add(target_id)
    save_allowed_users(users)
    
    await update.message.reply_text(f"User {target_id} has been allowed.")


async def block_command(update: Update, context: CallbackContext):
    if not update.message:
        return
    user_id = update.message.from_user.id
    if ADMIN_IDS and user_id not in ADMIN_IDS:
        return
    
    if not context.args:
        await update.message.reply_text("Usage: /block <user_id>")
        return
    
    try:
        target_id = int(context.args[0])
    except ValueError:
        await update.message.reply_text("Invalid user ID. Must be a number.")
        return
    
    users = get_allowed_users()
    users.discard(target_id)
    save_allowed_users(users)
    
    await update.message.reply_text(f"User {target_id} has been blocked.")


async def users_command(update: Update, context: CallbackContext):
    if not update.message:
        return
    user_id = update.message.from_user.id
    if ADMIN_IDS and user_id not in ADMIN_IDS:
        return
    
    users = get_allowed_users()
    if not users:
        await update.message.reply_text("No allowed users.")
        return
    
    msg = "Allowed users:\n" + "\n".join(f"- {uid}" for uid in sorted(users))
    await update.message.reply_text(msg)
