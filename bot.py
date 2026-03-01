import logging
import asyncio
from telegram import Update
from telegram.ext import Application, CommandHandler, MessageHandler, CallbackQueryHandler, filters, JobQueue
from telegram.request import HTTPXRequest

from config import TOKEN, BOT_API_URL, LOCAL_MODE, ADMIN_IDS, cleanup_temp_files, CLEANUP_INTERVAL_HOURS
from app.commands import (
    start_command, help_command, stats_command, history_command, myid_command,
    allow_command, block_command, users_command, broadcast_command,
    userhistory_command, rateinfo_command, setrate_command,
    cleanup_command, status_command
)
from app.callbacks import handle_link, handle_callback

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


def main():
    request = HTTPXRequest(
        write_timeout=600,
        connect_timeout=30,
        read_timeout=600,
        pool_timeout=30
    )
    
    if LOCAL_MODE:
        from telegram import Bot
        bot = Bot(
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
    
    app.add_handler(CallbackQueryHandler(handle_callback))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_link))

    if CLEANUP_INTERVAL_HOURS > 0:
        app.job_queue.run_repeating(cleanup_job, interval=CLEANUP_INTERVAL_HOURS * 3600, first=60)

    logger.info("Bot started!")
    app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
