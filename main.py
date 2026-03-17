import pytz
import logging
from telegram import Update
from telegram.ext import Application, CallbackQueryHandler, MessageHandler, filters
from telegram.request import HTTPXRequest
from datetime import time as dt_time

from aiohttp import web
from config import (
    TOKEN, BOT_API_URL, LOCAL_MODE,
    CLEANUP_INTERVAL_HOURS, DISK_CHECK_INTERVAL_MINUTES,
    init_config,
)
from services.task_manager import TaskManager, IO_CHANNEL
from services.event_bus import bus
from repositories import TaskRepository
from services.container import services
from services.ratelimit import RateLimiter
from services.facades import _execute_download_task
from models.domain_models import DownloadStatus
from utils.logger import setup_logging
from database.db import init_db
from database.task_store import mark_stale_tasks_failed

from core.health import create_health_app
from core.filters import CookieFilter
from core.jobs import cleanup_job, storage_alert_job, daily_report_job
from core.bot_setup import set_bot_commands
from core.cookies import handle_cookie_file
from core.handler_registry import registry  # ← 唯一需要的 handler 导入

# ── 触发所有 handler 模块的 import，让 @command_handler 装饰器执行注册 ──────
import handlers.user.basic        # noqa: F401
import handlers.user.history      # noqa: F401
import handlers.admin.system      # noqa: F401
import handlers.admin.users       # noqa: F401
import handlers.admin.tasks       # noqa: F401
# ─────────────────────────────────────────────────────────────────────────────

from handlers.downloads.message_parser import handle_link
from handlers.downloads.inline_actions import handle_callback

logger = logging.getLogger(__name__)


def register_all_handlers(app: Application) -> None:
    # 所有命令 handler：一行搞定
    registry.apply(app)

    # 非命令 handler（无法用装饰器声明，保留在此）
    app.add_handler(CallbackQueryHandler(handle_callback))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_link))
    app.add_handler(MessageHandler(filters.Document.ALL & CookieFilter(), handle_cookie_file))


def register_jobs(app: Application) -> None:
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


def main() -> None:
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
        app = Application.builder().token(TOKEN).request(request).build()

    register_all_handlers(app)
    register_jobs(app)

    async def post_init_callback(app: Application) -> None:
        init_config()
        await init_db()
        await mark_stale_tasks_failed()

        from services.event_bus import bus as _bus
        services.bus = _bus

        services.task_manager = TaskManager(
            io_workers=3,
            cpu_workers=2,
            api_workers=5,
        )
        services.limiter = RateLimiter()
        services.task_manager.set_executor(_execute_download_task, IO_CHANNEL)
        await services.task_manager.start()

        task_repo = TaskRepository()
        services.bus.on("task_started",   task_repo.save)
        services.bus.on("task_retrying",  task_repo.save)
        services.bus.on("task_completed", task_repo.save)

        await set_bot_commands(app)

        runner = web.AppRunner(create_health_app())
        await runner.setup()
        site = web.TCPSite(runner, "0.0.0.0", 8080)
        await site.start()
        logger.info("Health check endpoint started on :8080/health")

    app.post_init = post_init_callback

    async def post_shutdown(context) -> None:
        await services.task_manager.stop()

    app.post_shutdown = post_shutdown

    logger.info("Bot started!")
    app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
