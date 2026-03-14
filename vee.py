import logging
import os
from telegram import Update, BotCommand
from telegram.ext import Application, CommandHandler, MessageHandler, CallbackQueryHandler, filters, JobQueue, PicklePersistence, CallbackContext
from telegram.request import HTTPXRequest

from config import TOKEN, BOT_API_URL, LOCAL_MODE, ADMIN_IDS, cleanup_temp_files, CLEANUP_INTERVAL_HOURS, persist_all_data
from app.commands import (
    start_command, help_command, stats_command, history_command, myid_command,
    allow_command, block_command, users_command, broadcast_command,
    userhistory_command, rateinfo_command, setrate_command,
    cleanup_command, status_command, queue_command, storage_command, failed_command,
    lang_command, cookie_command, refresh_command
)
from app.callbacks import handle_link, handle_callback
from core.queue import download_queue
from core.facades import _execute_download_task


async def set_bot_commands(app: Application):
    bot = app.bot
    
    user_commands = [
        BotCommand("start", "Start the bot"),
        BotCommand("help", "Show available commands"),
        BotCommand("history", "View your download history"),
        BotCommand("myid", "Get your user ID"),
        BotCommand("lang", "Change language"),
    ]
    
    admin_commands = [
        BotCommand("stats", "Show bot statistics"),
        BotCommand("allow", "Allow a user (admin)"),
        BotCommand("block", "Block a user (admin)"),
        BotCommand("users", "List allowed users"),
        BotCommand("broadcast", "Broadcast message"),
        BotCommand("userhistory", "View user history"),
        BotCommand("rateinfo", "Show rate limit info"),
        BotCommand("setrate", "Set rate limit"),
        BotCommand("cleanup", "Clean temp files"),
        BotCommand("status", "Show bot status"),
        BotCommand("queue", "Show download queue"),
        BotCommand("storage", "Show storage info"),
        BotCommand("failed", "Show failed downloads"),
        BotCommand("cookie", "Update cookies"),
        BotCommand("refresh", "Clear cached file"),
    ]
    
    await bot.set_my_commands(user_commands)
    if ADMIN_IDS:
        await bot.set_my_commands(user_commands + admin_commands, scope={"type": "chat", "chat_id": list(ADMIN_IDS)[0]})


class CookieFilter(filters.BaseFilter):
    def filter(self, message):
        if not message.document:
            return False
        if not ADMIN_IDS:
            return False
        return message.from_user.id in ADMIN_IDS


async def handle_cookie_file(update: Update, context: CallbackContext):
    from config import COOKIE_FILE, COOKIES_DIR
    document = update.message.document
    if not document:
        return
    
    if not document.file_name.endswith('.txt'):
        await update.message.reply_text("Please send a .txt file (cookies.txt)")
        return
    
    import re
    filename = document.file_name
    match = re.match(r'([^_]+)_cookies\.txt', filename)
    
    if match:
        site_domain = match.group(1)
        site_cookie_file = os.path.join(COOKIES_DIR, f"{site_domain}_cookies.txt")
        try:
            file = await document.get_file()
            await file.download_to_drive(custom_path=site_cookie_file)
            await update.message.reply_text(f"Cookies saved for {site_domain}!")
        except Exception as e:
            await update.message.reply_text(f"Error saving cookie file: {e}")
    else:
        if COOKIE_FILE:
            try:
                file = await document.get_file()
                await file.download_to_drive(custom_path=COOKIE_FILE)
                await update.message.reply_text(f"Cookies updated! Saved to {COOKIE_FILE}")
            except Exception as e:
                await update.message.reply_text(f"Error saving cookie file: {e}")
        else:
            await update.message.reply_text("Invalid filename. Use format: domain_cookies.txt (e.g., www.youtube.com_cookies.txt)")

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO
)
logger = logging.getLogger(__name__)


class AdminFilter(filters.BaseFilter):
    def filter(self, message):
        return message.from_user.id in ADMIN_IDS


async def cleanup_job(context):
    cleanup_temp_files(max_age_hours=24)


async def persist_job(context):
    persist_all_data()


async def storage_alert_job(context):
    import psutil
    from config import ADMIN_IDS
    
    if not ADMIN_IDS:
        return
    
    disk = psutil.disk_usage("/")
    total, used, free, percent = disk.total, disk.used, disk.free, disk.percent
    
    if percent > 90:
        msg = f"⚠️ CRITICAL: Disk usage at {percent:.1f}%"
        for admin_id in ADMIN_IDS:
            try:
                await context.bot.send_message(chat_id=admin_id, text=msg)
            except:
                pass


def main():
    request = HTTPXRequest(
        write_timeout=600,
        connect_timeout=30,
        read_timeout=600,
        pool_timeout=30
    )
    
    if LOCAL_MODE:
        from telegram.ext import ExtBot
        bot = ExtBot(
            token=TOKEN,
            local_mode=LOCAL_MODE,
            base_url=BOT_API_URL,
            request=request
        )
        app = Application.builder().bot(bot).build()
    else:
        app = (
            Application.builder()
            .token(TOKEN)
            .request(request)
            .build()
        )

    app.add_handler(CommandHandler("start", start_command))
    app.add_handler(CommandHandler("help", help_command))
    app.add_handler(CommandHandler("history", history_command))
    app.add_handler(CommandHandler("myid", myid_command))
    app.add_handler(CommandHandler("lang", lang_command))
    
    if ADMIN_IDS:
        app.add_handler(CommandHandler("stats", stats_command, filters=AdminFilter()))
        app.add_handler(CommandHandler("allow", allow_command, filters=AdminFilter()))
        app.add_handler(CommandHandler("block", block_command, filters=AdminFilter()))
        app.add_handler(CommandHandler("users", users_command, filters=AdminFilter()))
        app.add_handler(CommandHandler("broadcast", broadcast_command, filters=AdminFilter()))
        app.add_handler(CommandHandler("userhistory", userhistory_command, filters=AdminFilter()))
        app.add_handler(CommandHandler("rateinfo", rateinfo_command, filters=AdminFilter()))
        app.add_handler(CommandHandler("setrate", setrate_command, filters=AdminFilter()))
        app.add_handler(CommandHandler("cleanup", cleanup_command, filters=AdminFilter()))
        app.add_handler(CommandHandler("status", status_command, filters=AdminFilter()))
        app.add_handler(CommandHandler("queue", queue_command, filters=AdminFilter()))
        app.add_handler(CommandHandler("storage", storage_command, filters=AdminFilter()))
        app.add_handler(CommandHandler("failed", failed_command, filters=AdminFilter()))
        app.add_handler(CommandHandler("cookie", cookie_command, filters=AdminFilter()))
        app.add_handler(CommandHandler("refresh", refresh_command, filters=AdminFilter()))
    
    app.add_handler(CallbackQueryHandler(handle_callback))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_link))
    app.add_handler(MessageHandler(filters.Document.ALL & CookieFilter(), handle_cookie_file))

    if CLEANUP_INTERVAL_HOURS > 0:
        app.job_queue.run_repeating(cleanup_job, interval=CLEANUP_INTERVAL_HOURS * 3600, first=60)

    app.job_queue.run_repeating(persist_job, interval=60, first=30)
    
    if ADMIN_IDS:
        app.job_queue.run_repeating(storage_alert_job, interval=3600, first=300)

    async def post_init_callback(app: Application):
        download_queue.set_executor(_execute_download_task)
        await download_queue.start()
        await set_bot_commands(app)

    app.post_init = post_init_callback
    
    async def post_shutdown(context):
        await download_queue.stop()
        persist_all_data()

    app.post_shutdown = post_shutdown
    
    logger.info("Bot started!")
    app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
