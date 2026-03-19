"""
modules/downloader/services/facades.py
───────────────────────────────────────
DownloadFacade + _execute_download_task executor.

Previous version had:
  • process_download_request  — dead code, never called
  • enqueue_silent            — dead code, never called
  • enqueue / send_cached     — called everywhere but NOT DEFINED (runtime AttributeError!)

This version keeps only what is actually used.
"""
from __future__ import annotations

import asyncio
import logging
import uuid

from models.domain_models import DownloadTask, DownloadStatus
from shared.services.container import services
from shared.services.session import UserSession
from utils.i18n import t

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Task executor  (wired into TaskManager.io_queue at bootstrap)
# ---------------------------------------------------------------------------

async def _execute_download_task(task: DownloadTask) -> None:
    """
    Called by the IO-channel worker for each queued DownloadTask.

    Runs the appropriate strategy, handles cancel events, and updates
    task.status in-place so the queue's _finalize_task sees the result.
    """
    from modules.downloader.strategies.factory import StrategyFactory

    strategy = StrategyFactory.get(task.download_type)
    if not strategy:
        task.status = DownloadStatus.FAILED
        task.error = f"No strategy found: {task.download_type}"
        return

    task.status = DownloadStatus.PROCESSING

    ctx = services.task_manager.get_task_context(task.task_id)
    if not ctx:
        task.status = DownloadStatus.FAILED
        task.error = "Task context missing"
        return

    sender = ctx["sender"]
    cancel_event = services.task_manager.get_cancel_event(task.task_id)

    strategy_future = asyncio.ensure_future(strategy.execute(sender, task.url))
    cancel_future = (
        asyncio.ensure_future(cancel_event.wait())
        if cancel_event
        else asyncio.ensure_future(asyncio.sleep(float("inf")))
    )

    try:
        done, pending = await asyncio.wait(
            [strategy_future, cancel_future],
            return_when=asyncio.FIRST_COMPLETED,
        )
    except asyncio.CancelledError:
        strategy_future.cancel()
        cancel_future.cancel()
        task.status = DownloadStatus.CANCELLED
        task.error = "Queue stopped"
        try:
            await sender.edit_status(t("bot_shutting_down", task.user_id))
        except Exception:
            pass
        return
    finally:
        # Always clean up the non-winning future
        for f in locals().get("pending", []):
            f.cancel()
            try:
                await f
            except (asyncio.CancelledError, Exception):
                pass

    if cancel_future in done:
        task.status = DownloadStatus.CANCELLED
        logger.info("Task %s cancelled by user %s", task.task_id, task.user_id)
        try:
            await sender.edit_status(t("download_cancelled", task.user_id))
        except Exception:
            pass

    elif strategy_future in done:
        exc = strategy_future.exception()
        if exc:
            logger.error(
                "Strategy execution failed: %s: %s", type(exc).__name__, exc,
                exc_info=exc,
            )
            task.status = DownloadStatus.FAILED
            task.error = str(exc)
            try:
                if not getattr(exc, "_status_already_edited", False):
                    await sender.edit_status(
                        t("download_failed", task.user_id, error=str(exc))
                    )
            except Exception:
                pass
        else:
            task.status = DownloadStatus.COMPLETED
            logger.info("Task %s completed successfully", task.task_id)


# ---------------------------------------------------------------------------
# DownloadFacade  (public API used by handlers and inline_actions)
# ---------------------------------------------------------------------------

class DownloadFacade:
    """
    Thin facade between handler layer and task queue.

    Methods
    -------
    enqueue(session, download_type)
        Queue a new download for the given session.
    """

    @staticmethod
    async def enqueue(session: UserSession, download_type: str) -> None:
        """Queue a download task for *session*."""
        task = DownloadTask(
            task_id=uuid.uuid4().hex[:16],
            user_id=session.user_id,
            url=session.url,
            download_type=download_type,
            channel="io",
        )
        task_ctx: dict = {"sender": session.sender}
        await services.task_manager.add_task(task, task_ctx)

        position = services.task_manager.get_queue_position(session.user_id)
        active = services.task_manager.get_active_count()

        if position > 0 or active >= services.task_manager.max_concurrent:
            try:
                await session.sender.edit_status(
                    t("queued", session.user_id, position=position + 1)
                )
            except Exception:
                pass
        else:
            try:
                await session.sender.edit_status(t("downloading", session.user_id))
            except Exception:
                pass

        logger.info(
            "Task %s queued: user=%s type=%s position=%d",
            task.task_id, session.user_id, download_type, position,
        )
