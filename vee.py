import logging
import os
from telegram import Update, BotCommand
from telegram.ext import Application, CommandHandler, MessageHandler, CallbackQueryHandler, filters, JobQueue, PicklePersistence, CallbackContext
from telegram.request import HTTPXRequest
from telegram.error import TelegramError

from config import TOKEN, BOT_API_URL, LOCAL_MODE, ADMIN_IDS, CLEANUP_INTERVAL_HOURS, DISK_WARN_THRESHOLD, DISK_CRIT_THRESHOLD, DISK_CHECK_INTERVAL_MINUTES
from services.user_service import cleanup_temp_files
from bot.commands import (
    start_command, help_command, stats_command, history_command, myid_command,
    allow_command, block_command, users_command, broadcast_command,
    userhistory_command, rateinfo_command, setrate_command,
    cleanup_command, status_command, queue_command, storage_command, failed_command,
    lang_command, cookie_command, refresh_command, cancel_command, admin_cancel_command,
    report_command, setdisk_command, tasks_command, settier_command,
)
from bot.callbacks import handle_link, handle_callback
from services.queue import download_queue
from models.domain_models import DownloadStatus
from services.facades import _execute_download_task
from utils.logger import setup_logging
from database.db import init_db
from database.task_store import mark_stale_tasks_failed
from services.analytics import get_daily_stats, format_daily_report
from datetime import time as dt_time
import pytz

logger = logging.getLogger(__name__)

async def set_bot_commands(app: Application):
    bot = app.bot
    
    user_commands = [
        BotCommand("start", "Start the bot"),
        BotCommand("cancel", "取消进行中的下载"),
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
        BotCommand("admcancel", "取消任意下载任务"),
        BotCommand("settier",  "设置用户速率等级 [user_id] [tier]"),
        BotCommand("setdisk",  "设置磁盘告警阈值 [warn%] [crit%]"),
        BotCommand("report",   "查看下载统计报告 [天数]"),
        BotCommand("tasks",    "查看自己的任务记录"),
    ]
        
    await bot.set_my_commands(user_commands)
    
    if ADMIN_IDS:
        for admin_id in ADMIN_IDS:
            try:
                await bot.set_my_commands(
                    user_commands + admin_commands,
                    scope={"type": "chat", "chat_id": admin_id}
                )
            except Exception as e:
                logger.warning(f"Could not set commands for admin {admin_id}: {e}")


class AdminFilter(filters.BaseFilter):
    def filter(self, message):
        return bool(ADMIN_IDS) and message.from_user.id in ADMIN_IDS


class CookieFilter(AdminFilter):
    def filter(self, message):
        return message.document is not None and super().filter(message)


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
            logger.error(
                f"Failed to save cookie file '{document.file_name}' "
                f"for user {update.message.from_user.id}: {e}",
                exc_info=True
            )
            await update.message.reply_text(
                "❌ Failed to save the cookie file. Please try again or contact the admin."
            )
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


async def cleanup_job(context):
    cleanup_temp_files(max_age_hours=24)

_last_alert_level: str = "ok"   
async def storage_alert_job(context):
    global _last_alert_level

    if not ADMIN_IDS:
        return

    disk = psutil.disk_usage("/")
    percent = disk.percent

    if percent >= DISK_CRIT_THRESHOLD:
        current_level = "critical"
    elif percent >= DISK_WARN_THRESHOLD:
        current_level = "warn"
    else:
        current_level = "ok"

    if current_level == _last_alert_level:
        return
    if current_level == "ok" and _last_alert_level == "ok":
        return

    _last_alert_level = current_level

    if current_level == "critical":
        msg = (
            f"🚨 CRITICAL: 磁盘使用率 {percent:.1f}%，已超过 {DISK_CRIT_THRESHOLD}%！\n"
            f"剩余空间：{disk.free // (1024**3):.1f} GB\n"
            f"请立即清理，否则下载功能将失败。"
        )
    elif current_level == "warn":
        msg = (
            f"⚠️ WARNING: 磁盘使用率 {percent:.1f}%，已超过 {DISK_WARN_THRESHOLD}%\n"
            f"剩余空间：{disk.free // (1024**3):.1f} GB"
        )
    else:  # 从告警恢复
        msg = f"✅ 磁盘已恢复正常，当前使用率 {percent:.1f}%"

    for admin_id in ADMIN_IDS:
        try:
            await context.bot.send_message(chat_id=admin_id, text=msg)
        except Exception as e:
            logger.warning(f"Failed to send storage alert to {admin_id}: {e}")


async def daily_report_job(context):
    if not ADMIN_IDS:
        return
    stats = await get_daily_stats(days=1)
    msg = format_daily_report(stats, period="昨日")
    for admin_id in ADMIN_IDS:
        try:
            await context.bot.send_message(chat_id=admin_id, text=msg)
        except Exception as e:
            logger.warning(f"Failed to send daily report to {admin_id}: {e}")


def main():
    setup_logging()
    
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
    app.add_handler(CommandHandler("cancel", cancel_command))
    app.add_handler(CommandHandler("tasks", tasks_command))
    
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
        app.add_handler(CommandHandler("admcancel", admin_cancel_command, filters=AdminFilter()))
        app.add_handler(CommandHandler("report", report_command, filters=AdminFilter()))
        app.add_handler(CommandHandler("setdisk", setdisk_command, filters=AdminFilter()))
        app.add_handler(CommandHandler("settier",  settier_command,  filters=AdminFilter()))

    
    app.add_handler(CallbackQueryHandler(handle_callback))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_link))
    app.add_handler(MessageHandler(filters.Document.ALL & CookieFilter(), handle_cookie_file))

    if CLEANUP_INTERVAL_HOURS > 0:
        app.job_queue.run_repeating(cleanup_job, interval=CLEANUP_INTERVAL_HOURS * 3600, first=60)
    
    if DISK_CHECK_INTERVAL_MINUTES > 0:
        app.job_queue.run_repeating(storage_alert_job,interval=DISK_CHECK_INTERVAL_MINUTES * 60,first=300,name="storage_alert",)

    tz = pytz.timezone("Asia/Shanghai")
    app.job_queue.run_daily(daily_report_job, time=dt_time(hour=9, minute=0, tzinfo=tz),name="daily_report",)

    async def post_init_callback(app: Application):
        await init_db()
        await mark_stale_tasks_failed()
        download_queue.set_executor(_execute_download_task)
        await download_queue.start()
        await set_bot_commands(app)

    app.post_init = post_init_callback
    
    async def post_shutdown(context):
        await download_queue.stop()

    app.post_shutdown = post_shutdown
    
    logger.info("Bot started!")
    app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
