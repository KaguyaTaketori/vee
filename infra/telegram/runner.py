"""
infra/telegram/runner.py
────────────────────────
Everything Telegram-specific, in one file.

Responsibilities
────────────────
• Build the PTB ``Application`` object (token, HTTPXRequest timeouts,
  LOCAL_MODE / custom bot API server).
• Construct a ``PtbHandlerRegistrar`` and call ``module.setup(registrar)``
  for every BotModule — the only place ``Application`` is ever passed down.
• Register scheduled jobs.
• Wire ``TelegramAdminNotifier`` and inject it into ``bootstrap.init_services``.
• Register bot commands (BotCommand menus) after the bot is connected.
• Launch ``app.run_polling``.
"""
from __future__ import annotations

import logging
import pytz
from datetime import time as dt_time
from typing import Sequence

from telegram import Update
from telegram.ext import Application
from telegram.request import HTTPXRequest

from bootstrap import init_services, shutdown_services, start_health_endpoint
from config import (
    TOKEN, BOT_API_URL, LOCAL_MODE,
    CLEANUP_INTERVAL_HOURS, DISK_CHECK_INTERVAL_MINUTES,
    ADMIN_IDS,
)
from core.bot_setup import set_bot_commands
from core.jobs import cleanup_job, storage_alert_job, daily_report_job, bill_cache_gc_job
from modules.downloader.integrations.ptb_registrar import PtbHandlerRegistrar
from shared.services.notifier import TelegramAdminNotifier
from shared.services.container import services

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Handler registration
# ---------------------------------------------------------------------------

def _register_handlers(app: Application, modules: Sequence) -> None:
    """
    Construct a single ``PtbHandlerRegistrar`` and call every module's
    ``setup()`` with it.

    The registrar is the only object that knows about ``app``; modules
    only ever see the platform-agnostic ``HandlerRegistrar`` interface.
    """
    registrar = PtbHandlerRegistrar(app, admin_ids=frozenset(ADMIN_IDS))
    for module in modules:
        module.setup(registrar)
        logger.debug("Module %r set up via PtbHandlerRegistrar", module.name)


# ---------------------------------------------------------------------------
# Job scheduling
# ---------------------------------------------------------------------------

class _NotifierProxy:
    """Deferred proxy so jobs always reach the live notifier in services."""

    async def notify_admins(self, message: str, parse_mode: str | None = None) -> None:
        await services.notifier.notify_admins(message, parse_mode)


def _register_jobs(app: Application) -> None:
    proxy = _NotifierProxy()
    job_data = {"notifier": proxy}

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
            data=job_data,
        )

    tz = pytz.timezone("Asia/Shanghai")
    app.job_queue.run_daily(
        daily_report_job,
        time=dt_time(hour=9, minute=0, tzinfo=tz),
        name="daily_report",
        data=job_data,
    )

    app.job_queue.run_repeating(
        bill_cache_gc_job,
        interval=300,
        first=300,
        name="bill_cache_gc",
    )


# ---------------------------------------------------------------------------
# Application factory
# ---------------------------------------------------------------------------

def _build_application() -> Application:
    """Construct the PTB Application (handles LOCAL_MODE / standard mode)."""
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
        return Application.builder().bot(bot).build()

    return Application.builder().token(TOKEN).request(request).build()


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------

def run(modules: Sequence) -> None:
    """
    Build the Telegram bot and start long-polling.

    This is the only function main.py needs to call.
    """
    app = _build_application()
    _register_handlers(app, modules)
    _register_jobs(app)

    async def _post_init(application: Application) -> None:
        notifier = TelegramAdminNotifier(application.bot, list(ADMIN_IDS))
        await init_services(modules=modules, notifier=notifier)
        await set_bot_commands(application, modules)
        await start_health_endpoint()

    async def _post_shutdown(_context) -> None:
        await shutdown_services()

    app.post_init = _post_init
    app.post_shutdown = _post_shutdown

    logger.info("Starting Telegram bot (polling)…")
    app.run_polling(allowed_updates=Update.ALL_TYPES)
