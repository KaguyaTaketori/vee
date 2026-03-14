import logging
from logging.handlers import RotatingFileHandler
import os
from telegram import Update, BotCommand
from telegram.ext import Application, CommandHandler, MessageHandler, CallbackQueryHandler, filters, JobQueue, PicklePersistence, CallbackContext
from telegram.request import HTTPXRequest

from config import TOKEN, BOT_API_URL, LOCAL_MODE, ADMIN_IDS, cleanup_temp_files, CLEANUP_INTERVAL_HOURS, persist_all_data

LOG_DIR = "/home/ubuntu/vee"
LOG_FILE = os.path.join(LOG_DIR, "bot.log")
LOG_MAX_BYTES = 10 * 1024 * 1024
LOG_BACKUP_COUNT = 5

def setup_logging():
    os.makedirs(LOG_DIR, exist_ok=True)
    
    formatter = logging.Formatter(
        "%(asctime)s | %(levelname)-8s | %(name)s:%(lineno)d | %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S"
    )
    
    root_logger = logging.getLogger()
    root_logger.setLevel(logging.INFO)
    
    for handler in root_logger.handlers[:]:
        root_logger.removeHandler(handler)
    
    console_handler = logging.StreamHandler()
    console_handler.setFormatter(formatter)
    root_logger.addHandler(console_handler)
    
    file_handler = RotatingFileHandler(
        LOG_FILE,
        maxBytes=LOG_MAX_BYTES,
        backupCount=LOG_BACKUP_COUNT,
        encoding="utf-8"
    )
    file_handler.setFormatter(formatter)
    root_logger.addHandler(file_handler)
    
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("telegram").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)

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
    setup_logging()
    
    request = HTTPXRequest(
        write_timeout=1200,
        connect_timeout=60,
        read_timeout=1200,
        pool_timeout=120,
        connection_pool_size=10
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
        await set_bot_commands(app)

    app.post_init = post_init_callback
    
    async def post_shutdown(context):
        persist_all_data()

    app.post_shutdown = post_shutdown
    
    logger.info("Bot started!")
    app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
