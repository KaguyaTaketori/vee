from telegram import Update
from telegram.ext import CallbackContext

from config import ADMIN_IDS, get_allowed_users, save_allowed_users
from core.logger import log_user, get_user_stats
from core.history import get_user_history, get_all_users_count, get_total_downloads
from core.ratelimit import rate_limiter, save_rate_limit


async def start_command(update: Update, context: CallbackContext):
    if not update.message:
        return
    log_user(update.message.from_user, "start")
    user = update.message.from_user
    await update.message.reply_text(
        f"Welcome! Your ID: {user.id}\n\n"
        "Send me a link from YouTube, TikTok, Instagram, Twitter, or other supported platforms.\n\n"
        "I can download:\n"
        "• Videos (up to 2GB with local Bot API)\n"
        "• Thumbnails\n"
        "• Audio (MP3)\n\n"
        "Just send a link and choose what to download!"
    )


async def myid_command(update: Update, context: CallbackContext):
    if not update.message:
        return
    user = update.message.from_user
    msg = f"Your Telegram ID: `{user.id}`\n"
    msg += f"Username: @{user.username or 'N/A'}\n"
    msg += f"Name: {user.first_name} {user.last_name or ''}"
    await update.message.reply_text(msg, parse_mode="Markdown")


async def help_command(update: Update, context: CallbackContext):
    if not update.message:
        return
    user_id = update.message.from_user.id
    is_admin = user_id in ADMIN_IDS if ADMIN_IDS else False
    
    if is_admin:
        await update.message.reply_text(
            "Available commands:\n"
            "/start - Start the bot\n"
            "/help - Show this help\n"
            "/history - Your download history\n\n"
            "Admin commands:\n"
            "/allow <user_id> - Allow a user\n"
            "/block <user_id> - Block a user\n"
            "/users - List allowed users\n"
            "/stats - Bot usage stats\n"
            "/broadcast <message> - Broadcast to all users\n"
            "/userhistory <user_id> - View user history\n"
            "/rateinfo - View rate limit info\n"
            "/setrate <max> [on/off] - Set rate limit"
        )
    else:
        await update.message.reply_text(
            "Available commands:\n"
            "/start - Start the bot\n"
            "/help - Show this help\n"
            "/history - Your download history\n\n"
            "Just send a link to download!"
        )


async def stats_command(update: Update, context: CallbackContext):
    if not update.message:
        return
    user_id = update.message.from_user.id
    if ADMIN_IDS and user_id not in ADMIN_IDS:
        return
    stats = get_user_stats()
    users_count = get_all_users_count()
    total_downloads = get_total_downloads()
    msg = f"{stats}\n\nTotal registered users: {users_count}\nTotal downloads: {total_downloads}"
    await update.message.reply_text(msg)


async def history_command(update: Update, context: CallbackContext):
    if not update.message:
        return
    user_id = update.message.from_user.id
    history = get_user_history(user_id, limit=10)
    
    if not history:
        await update.message.reply_text("No download history.")
        return
    
    msg = "Your download history:\n\n"
    for item in history:
        from datetime import datetime
        dt = datetime.fromtimestamp(item["timestamp"])
        status = "✅" if item.get("status") == "success" else "❌"
        msg += f"{status} {item['type']} - {item.get('title', 'Unknown')[:30]}...\n"
        msg += f"   {dt.strftime('%Y-%m-%d %H:%M')}\n\n"
    
    await update.message.reply_text(msg)


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


async def broadcast_command(update: Update, context: CallbackContext):
    if not update.message:
        return
    user_id = update.message.from_user.id
    if ADMIN_IDS and user_id not in ADMIN_IDS:
        return
    
    if not context.args:
        await update.message.reply_text("Usage: /broadcast <message>")
        return
    
    message = " ".join(context.args)
    users = get_allowed_users() - ADMIN_IDS
    
    bot = context.bot
    success = 0
    failed = 0
    
    for uid in users:
        try:
            await bot.send_message(chat_id=uid, text=message)
            success += 1
        except Exception:
            failed += 1
    
    await update.message.reply_text(f"Broadcast sent to {success} users. Failed: {failed}")


async def userhistory_command(update: Update, context: CallbackContext):
    if not update.message:
        return
    user_id = update.message.from_user.id
    if ADMIN_IDS and user_id not in ADMIN_IDS:
        return
    
    users = get_allowed_users() - ADMIN_IDS
    
    if not users:
        await update.message.reply_text("No users to show.")
        return
    
    from telegram import InlineKeyboardButton, InlineKeyboardMarkup
    
    keyboard = []
    for uid in sorted(users):
        keyboard.append([InlineKeyboardButton(f"User {uid}", callback_data=f"uh_{uid}")])
    
    reply_markup = InlineKeyboardMarkup(keyboard)
    await update.message.reply_text("Select a user to view history:", reply_markup=reply_markup)


async def rateinfo_command(update: Update, context: CallbackContext):
    if not update.message:
        return
    user_id = update.message.from_user.id
    if ADMIN_IDS and user_id not in ADMIN_IDS:
        return
    
    status = rate_limiter.get_status()
    status_text = "Enabled" if status["enabled"] else "Disabled"
    msg = f"Rate Limit Status:\n"
    msg += f"- Status: {status_text}\n"
    msg += f"- Max downloads/hour: {status['max_downloads_per_hour']}\n"
    msg += f"- Active users tracked: {status['active_users']}"
    await update.message.reply_text(msg)


async def setrate_command(update: Update, context: CallbackContext):
    if not update.message:
        return
    user_id = update.message.from_user.id
    if ADMIN_IDS and user_id not in ADMIN_IDS:
        return
    
    if not context.args:
        await update.message.reply_text("Usage: /setrate <max_per_hour> [on/off]")
        return
    
    try:
        max_per_hour = int(context.args[0])
        if max_per_hour < 1 or max_per_hour > 100:
            await update.message.reply_text("Value must be between 1-100")
            return
    except ValueError:
        await update.message.reply_text("Invalid number.")
        return
    
    enabled = True
    if len(context.args) > 1:
        enabled = context.args[1].lower() in ["on", "true", "1"]
    
    save_rate_limit(max_per_hour, enabled)
    rate_limiter.reload()
    
    status = "enabled" if enabled else "disabled"
    await update.message.reply_text(f"Rate limit updated: {max_per_hour}/hour, {status}")
