import pytz
import logging
from telegram import Update
from telegram.ext import Application, CommandHandler, MessageHandler, CallbackQueryHandler, filters, JobQueue
from telegram.request import HTTPXRequest
from datetime import time as dt_time

from aiohttp import web
from config import (
    TOKEN, BOT_API_URL, LOCAL_MODE, ADMIN_IDS,
    CLEANUP_INTERVAL_HOURS, DISK_CHECK_INTERVAL_MINUTES,
    init_config,
)
from services.task_manager import TaskManager, IO_CHANNEL
from services.event_bus import bus
from repositories import TaskRepository
from services.container import services
from services.ratelimit import RateLimiter
from services.user_service import cleanup_temp_files
from services.facades import _execute_download_task
from models.domain_models import DownloadStatus
from utils.logger import setup_logging
from database.db import init_db
from database.task_store import mark_stale_tasks_failed

from core.health import create_health_app
from core.filters import AdminFilter, CookieFilter
from core.jobs import cleanup_job, storage_alert_job, daily_report_job
from core.bot_setup import set_bot_commands
from core.cookies import handle_cookie_file

from handlers.user.basic import start_command, help_command, myid_command, lang_command
from handlers.user.history import history_command, tasks_command, cancel_command
from handlers.admin.system import stats_command, status_command, storage_command, setdisk_command, cleanup_command, report_command
from handlers.admin.users import allow_command, block_command, users_command, broadcast_command, userhistory_command, settier_command
from handlers.admin.tasks import queue_command, failed_command, cookie_command, refresh_command, admin_cancel_command, rateinfo_command, setrate_command
from handlers.downloads.message_parser import handle_link
from handlers.downloads.inline_actions import handle_callback

logger = logging.getLogger(__name__)


def register_all_handlers(app: Application):
    app.add_handler(CommandHandler("start",   start_command))
    app.add_handler(CommandHandler("help",    help_command))
    app.add_handler(CommandHandler("history", history_command))
    app.add_handler(CommandHandler("myid",    myid_command))
    app.add_handler(CommandHandler("lang",    lang_command))
    app.add_handler(CommandHandler("cancel",  cancel_command))
    app.add_handler(CommandHandler("tasks",   tasks_command))

    if ADMIN_IDS:
        app.add_handler(CommandHandler("stats",       stats_command,       filters=AdminFilter()))
        app.add_handler(CommandHandler("allow",       allow_command,       filters=AdminFilter()))
        app.add_handler(CommandHandler("block",       block_command,       filters=AdminFilter()))
        app.add_handler(CommandHandler("users",       users_command,       filters=AdminFilter()))
        app.add_handler(CommandHandler("broadcast",   broadcast_command,   filters=AdminFilter()))
        app.add_handler(CommandHandler("userhistory", userhistory_command, filters=AdminFilter()))
        app.add_handler(CommandHandler("rateinfo",    rateinfo_command,    filters=AdminFilter()))
        app.add_handler(CommandHandler("setrate",     setrate_command,     filters=AdminFilter()))
        app.add_handler(CommandHandler("cleanup",     cleanup_command,     filters=AdminFilter()))
        app.add_handler(CommandHandler("status",      status_command,      filters=AdminFilter()))
        app.add_handler(CommandHandler("queue",       queue_command,       filters=AdminFilter()))
        app.add_handler(CommandHandler("storage",     storage_command,     filters=AdminFilter()))
        app.add_handler(CommandHandler("failed",      failed_command,      filters=AdminFilter()))
        app.add_handler(CommandHandler("cookie",      cookie_command,      filters=AdminFilter()))
        app.add_handler(CommandHandler("refresh",     refresh_command,     filters=AdminFilter()))
        app.add_handler(CommandHandler("admcancel",   admin_cancel_command,filters=AdminFilter()))
        app.add_handler(CommandHandler("report",      report_command,      filters=AdminFilter()))
        app.add_handler(CommandHandler("setdisk",     setdisk_command,     filters=AdminFilter()))
        app.add_handler(CommandHandler("settier",     settier_command,     filters=AdminFilter()))

    app.add_handler(CallbackQueryHandler(handle_callback))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_link))
    app.add_handler(MessageHandler(filters.Document.ALL & CookieFilter(), handle_cookie_file))


def register_jobs(app: Application):
    if CLEANUP_INTERVAL_HOURS > 0:
        app.job_queue.run_repeating(
            cleanup_job,
            interval=CLEANUP_INTERVAL_HOURS * 3600,
            first=60,
        )

    if DISK_CHECK_INTERVAL_MINUTES > 0:
        app.job_queue.run_repeating(
            storage_alert_job,
            interval=DISK_CHECK_INTERVAL_MINUTES * 60,
            first=300,
            name="storage_alert",
        )

    tz = pytz.timezone("Asia/Shanghai")
    app.job_queue.run_daily(
        daily_report_job,
        time=dt_time(hour=9, minute=0, tzinfo=tz),
        name="daily_report",
    )


def main():
    setup_logging()

    request = HTTPXRequest(
        write_timeout=600,
        connect_timeout=30,
        read_timeout=600,
        pool_timeout=30,
    )

    if LOCAL_MODE:
        from telegram.ext import ExtBot
        bot = ExtBot(
            token=TOKEN,
            local_mode=LOCAL_MODE,
            base_url=BOT_API_URL,
            request=request,
        )
        app = Application.builder().bot(bot).build()
    else:
        app = (
            Application.builder()
            .token(TOKEN)
            .request(request)
            .build()
        )

    register_all_handlers(app)
    register_jobs(app)

    async def post_init_callback(app: Application):
        # ── 1. Config & DB ────────────────────────────────────────────────
        init_config()
        await init_db()
        await mark_stale_tasks_failed()
     
        # ── 2. Instantiate EventBus (module-level singleton, re-exported) ─
        from services.event_bus import bus as _bus
        services.bus = _bus
     
        # ── 3. Instantiate TaskManager (replaces single DownloadQueue) ────
        services.task_manager = TaskManager(
            io_workers=3,   # bandwidth slots (same as before)
            cpu_workers=2,  # CPU-heavy tasks
            api_workers=5,  # fast API tasks
        )

        services.limiter = RateLimiter()
     
        # ── 4. Wire executors per channel ────────────────────────────────
        #   io_queue  → existing download executor (no change to the function)
        services.task_manager.set_executor(_execute_download_task, IO_CHANNEL)
        #
        #   cpu_queue / api_queue → wire up when those features are built:
        #   services.task_manager.set_executor(execute_cpu_task,  CPU_CHANNEL)
        #   services.task_manager.set_executor(execute_api_task,  API_CHANNEL)
     
        await services.task_manager.start()
     
        # ── 5. Register event listeners (the only place DB wiring lives) ──
        task_repo = TaskRepository()
     
        #   "task_started"    → persist the in-progress record
        services.bus.on("task_started",    task_repo.save)
     
        #   "task_retrying"   → update retry_count in DB
        services.bus.on("task_retrying",   task_repo.save)
     
        #   "task_completed"  → persist the terminal state (replaces the
        #                        old persist_task() call inside _finalize_task)
        services.bus.on("task_completed",  task_repo.save)
     
        #   Future listeners – add here, zero changes elsewhere:
        #   services.bus.on("task_completed", send_completion_notification)
        #   services.bus.on("task_completed", analytics_tracker.record)
     
        # ── 6. Bot-level setup ────────────────────────────────────────────
        await set_bot_commands(app)
     
        # ── 7. Health-check HTTP server ───────────────────────────────────
        runner = web.AppRunner(create_health_app())
        await runner.setup()
        site = web.TCPSite(runner, "0.0.0.0", 8080)
        await site.start()
        logger.info("Health check endpoint started on :8080/health")

    app.post_init = post_init_callback

    async def post_shutdown(context):
        await services.queue.stop()

    app.post_shutdown = post_shutdown

    logger.info("Bot started!")
    app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
