import pytz
import logging
import asyncio
from telegram import Update
from telegram.ext import Application, CallbackQueryHandler, MessageHandler, CommandHandler, filters
from telegram.request import HTTPXRequest
from datetime import time as dt_time

from aiohttp import web
from config import (
    TOKEN, BOT_API_URL, LOCAL_MODE,
    CLEANUP_INTERVAL_HOURS, DISK_CHECK_INTERVAL_MINUTES,
    init_config, ADMIN_IDS,
)
from shared.services.task_manager import TaskManager, IO_CHANNEL
from shared.services.event_bus import bus
from repositories import TaskRepository
from shared.services.container import services
from shared.services.ratelimit import RateLimiter
from modules.downloader.services.facades import _execute_download_task
from shared.services.notifier import TelegramAdminNotifier
from models.domain_models import DownloadStatus
from utils.logger import setup_logging
from database.db import init_db
from shared.repositories.task_store import mark_stale_tasks_failed

from core.health import create_health_app
from core.filters import CookieFilter
from core.jobs import cleanup_job, storage_alert_job, daily_report_job
from core.bot_setup import set_bot_commands
from core.handler_registry import registry
from handlers.admin.cookies import handle_cookie_file
from modules.downloader.integrations.ptb_adapter import PtbCommandRegistrar

from shared.integrations.llm.manager import build_llm_manager_from_env
import shared.integrations.llm.manager as _llm_mod

import handlers.user.basic
import handlers.user.history
import handlers.admin.system
import handlers.admin.users
import handlers.admin.tasks
import handlers.admin.cookies

from modules.downloader import DownloaderModule
from modules.billing import BillingModule

logger = logging.getLogger(__name__)

MODULES = [
    DownloaderModule(),
    BillingModule(),
]

def register_all_handlers(app: Application) -> None:
   for module in MODULES:
        module.setup(app)

class _NotifierProxy:
    async def notify_admins(self, message: str, parse_mode=None) -> None:
        await services.notifier.notify_admins(message, parse_mode)

_notifier_proxy = _NotifierProxy()

def register_jobs(app: Application) -> None:
    job_data = {"notifier": _notifier_proxy}
    
    if CLEANUP_INTERVAL_HOURS > 0:
        app.job_queue.run_repeating(cleanup_job, interval=CLEANUP_INTERVAL_HOURS * 3600, first=60,)

    if DISK_CHECK_INTERVAL_MINUTES > 0:
        app.job_queue.run_repeating(
            storage_alert_job,
            interval=DISK_CHECK_INTERVAL_MINUTES * 60,
            first=300,
            name="storage_alert",
            data=job_data,
        )

    tz = pytz.timezone("Asia/Shanghai")
    app.job_queue.run_daily(
        daily_report_job,
        time=dt_time(hour=9, minute=0, tzinfo=tz),
        name="daily_report",
        data=job_data,
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
        for module in MODULES:
            if hasattr(module, 'init_db'):
                await module.init_db()
        await mark_stale_tasks_failed()
        _llm_mod.llm_manager = build_llm_manager_from_env()
        from shared.services.event_bus import bus as _bus
        services.bus = _bus

        services.task_manager = TaskManager(
            io_workers=3,
            cpu_workers=2,
            api_workers=5,
        )
        services.limiter = RateLimiter()
        services.notifier = TelegramAdminNotifier(app.bot, list(ADMIN_IDS))
        services.task_manager.set_executor(_execute_download_task, IO_CHANNEL)
        await services.task_manager.start()

        task_repo = TaskRepository()
        services.bus.on("task_started",   task_repo.save)
        services.bus.on("task_retrying",  task_repo.save)
        services.bus.on("task_completed", task_repo.save)

        await set_bot_commands(app, MODULES)

        runner = web.AppRunner(create_health_app())
        await runner.setup()
        site = web.TCPSite(runner, "0.0.0.0", 8080)
        await site.start()
        logger.info("Health check endpoint started on :8080/health")

    app.post_init = post_init_callback

    async def post_shutdown(context) -> None:
        if services.task_manager is not None:
            await services.task_manager.stop()

    app.post_shutdown = post_shutdown

    logger.info("Bot started!")
    app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
