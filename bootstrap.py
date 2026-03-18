"""
bootstrap.py
────────────
Platform-agnostic application bootstrap.

Responsibility
──────────────
Wire together every *platform-independent* service:

    • Config initialisation
    • Database (SQLite via aiosqlite)
    • Module DB tables
    • LLM manager
    • EventBus  →  services.bus
    • TaskManager (io / cpu / api channels)
    • RateLimiter
    • TaskRepository (EventBus listener)
    • Health-check HTTP endpoint (aiohttp, port 8080)

What is NOT here
────────────────
• Telegram Application / bot object
• PTB-specific notifier (TelegramAdminNotifier)
• bot_commands setup (requires app.bot)
• run_polling

Those live in ``infra/telegram/runner.py``.

The single exported coroutine ``init_services`` accepts an
``AdminNotifier`` so the caller (the Telegram runner, or a future CLI
runner) can inject its platform-specific notifier:

    await init_services(
        modules=MODULES,
        notifier=TelegramAdminNotifier(app.bot, list(ADMIN_IDS)),
    )
"""

from __future__ import annotations

import logging
from typing import Sequence

from shared.services.container import services
from shared.services.event_bus import bus as _bus
from shared.services.task_manager import TaskManager, IO_CHANNEL
from shared.services.ratelimit import RateLimiter
from shared.services.notifier import AdminNotifier
from repositories import TaskRepository
from database.db import init_db
from shared.repositories.task_store import mark_stale_tasks_failed
from modules.downloader.services.facades import _execute_download_task
import shared.integrations.llm.manager as _llm_mod
from shared.integrations.llm.manager import build_llm_manager_from_env
from config import init_config

logger = logging.getLogger(__name__)


async def init_services(
    modules: Sequence,
    notifier: AdminNotifier,
    *,
    io_workers: int = 3,
    cpu_workers: int = 2,
    api_workers: int = 5,
) -> None:
    """
    Initialise all platform-independent services.

    Parameters
    ----------
    modules:
        The BotModule list (used to call ``init_db()`` on each module).
    notifier:
        Platform-specific admin notifier — the only piece of platform
        state that leaks into this layer, kept as a typed Protocol.
    io_workers / cpu_workers / api_workers:
        TaskManager channel concurrency.  Exposed for testing overrides.
    """
    # ── Config ────────────────────────────────────────────────────────────────
    init_config()
    logger.info("Config initialised")

    # ── Database ──────────────────────────────────────────────────────────────
    await init_db()
    for module in modules:
        if hasattr(module, "init_db"):
            await module.init_db()
    await mark_stale_tasks_failed()
    logger.info("Database ready")

    # ── LLM ───────────────────────────────────────────────────────────────────
    _llm_mod.llm_manager = build_llm_manager_from_env()
    logger.info("LLM manager initialised (provider=%s)", _llm_mod.llm_manager._active_provider_name if _llm_mod.llm_manager else "none")

    # ── Event bus ─────────────────────────────────────────────────────────────
    services.bus = _bus

    # ── Task manager ──────────────────────────────────────────────────────────
    services.task_manager = TaskManager(
        io_workers=io_workers,
        cpu_workers=cpu_workers,
        api_workers=api_workers,
    )
    services.task_manager.set_executor(_execute_download_task, IO_CHANNEL)
    await services.task_manager.start()
    logger.info(
        "TaskManager started (io=%d cpu=%d api=%d)",
        io_workers, cpu_workers, api_workers,
    )

    # ── Rate limiter ──────────────────────────────────────────────────────────
    services.limiter = RateLimiter()

    # ── Notifier (platform-injected) ──────────────────────────────────────────
    services.notifier = notifier

    # ── Persistence via EventBus ─────────────────────────────────────────────
    task_repo = TaskRepository()
    services.bus.on("task_started",   task_repo.save)
    services.bus.on("task_retrying",  task_repo.save)
    services.bus.on("task_completed", task_repo.save)
    logger.info("EventBus listeners registered")


async def shutdown_services() -> None:
    """Graceful teardown — call from the platform runner's post_shutdown hook."""
    if services.task_manager is not None:
        await services.task_manager.stop()
        logger.info("TaskManager stopped")


async def start_health_endpoint(host: str = "0.0.0.0", port: int = 8080) -> None:
    """Start the aiohttp health-check endpoint.

    Kept here (not in the Telegram runner) because it is platform-neutral
    — it would exist for a Discord bot or a CLI runner equally.
    """
    from aiohttp import web
    from core.health import create_health_app

    runner = web.AppRunner(create_health_app())
    await runner.setup()
    site = web.TCPSite(runner, host, port)
    await site.start()
    logger.info("Health check endpoint started on %s:%d/health", host, port)
