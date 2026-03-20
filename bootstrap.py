from __future__ import annotations

import logging
from typing import Sequence

from shared.services.container import services
from shared.services.event_bus import bus as _bus
from shared.services.task_manager import TaskManager, IO_CHANNEL
from shared.services.ratelimit import RateLimiter
from shared.services.notifier import AdminNotifier
from shared.services.receipt_storage import LocalReceiptStorage
from repositories import TaskRepository
from database.db import get_db
from database.migrator import run_migrations
from database.migrations import ALL_MIGRATIONS
from shared.repositories.task_store import mark_stale_tasks_failed
from modules.downloader.services.facades import _execute_download_task
import shared.integrations.llm.manager as _llm_mod
from config.llm_config import build_llm_manager_from_yaml
from config import init_config
from config.settings import UPLOADS_DIR, PUBLIC_BASE_URL

logger = logging.getLogger(__name__)


async def init_services(
    modules: Sequence,
    notifier: AdminNotifier,
    *,
    io_workers: int = 3,
    cpu_workers: int = 2,
    api_workers: int = 5,
) -> None:

    # ── Config ────────────────────────────────────────────────────────────
    init_config()
    logger.info("Config initialised")

    # ── Database migrations ───────────────────────────────────────────────
    async with get_db() as db:
        await run_migrations(db, ALL_MIGRATIONS)

    # 各模块自己的表（billing 等）
    for module in modules:
        if hasattr(module, "init_db"):
            await module.init_db()

    await mark_stale_tasks_failed()
    logger.info("Database ready")

    # ── LLM ───────────────────────────────────────────────────────────────
    try:
        _llm_mod.llm_manager = build_llm_manager_from_yaml()
        logger.info(
            "LLM manager initialised (provider=%s)",
            _llm_mod.llm_manager._active_provider_name,
        )
    except Exception as e:
        logger.warning("LLM manager init failed (non-fatal): %s", e)

    # ── Receipt Storage ───────────────────────────────────────────────────
    services.receipt_storage = LocalReceiptStorage(
        base_dir=UPLOADS_DIR,
        public_base_url=PUBLIC_BASE_URL,
    )
    logger.info("ReceiptStorage initialised (dir=%s)", UPLOADS_DIR)

    # ── Event bus ─────────────────────────────────────────────────────────
    services.bus = _bus

    # ── Task manager ──────────────────────────────────────────────────────
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

    # ── Rate limiter ──────────────────────────────────────────────────────
    services.limiter = RateLimiter()

    # ── Notifier ──────────────────────────────────────────────────────────
    services.notifier = notifier

    # ── WebSocket Manager ────────────────────────────────────────────────
    from shared.services.ws_manager import ws_manager as _ws_manager
    services.ws_manager = _ws_manager
    logger.info("WebSocket Manager 已初始化")

    # ── Persistence via EventBus ──────────────────────────────────────────
    task_repo = TaskRepository()
    services.bus.on("task_started",   task_repo.save)
    services.bus.on("task_retrying",  task_repo.save)
    services.bus.on("task_completed", task_repo.save)
    logger.info("EventBus listeners registered")


async def shutdown_services() -> None:
    if services.task_manager is not None:
        await services.task_manager.stop()
        logger.info("TaskManager stopped")


async def start_health_endpoint(host: str = "0.0.0.0", port: int = 8080) -> None:
    from aiohttp import web
    from core.health import create_health_app

    runner = web.AppRunner(create_health_app())
    await runner.setup()
    site = web.TCPSite(runner, host, port)
    await site.start()
    logger.info("Health check endpoint started on %s:%d/health", host, port)
