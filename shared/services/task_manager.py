# services/task_manager.py
"""
Multi-channel task manager.

Replaces the single DownloadQueue singleton with three purpose-built
channels, each with its own concurrency budget and executor:

    io_queue  – stream downloads + uploads          (bandwidth-bound)
    cpu_queue – GIF conversion, background removal,
                TTS synthesis                        (CPU-bound)
    api_queue – AI chat, OCR receipt scanning       (API-concurrency-bound)

Routing is driven by ``DownloadTask.channel``, a new optional field that
defaults to "io" so existing code keeps working without changes.

Public interface mirrors the old DownloadQueue where possible so that
facades.py and handlers only need minimal updates.
"""
from __future__ import annotations

import logging
from typing import Callable, Optional

from models.domain_models import DownloadTask, DownloadStatus, TaskContext
from shared.services._queue import DownloadQueue

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Channel tag constants – use these instead of raw strings in call sites.
# ---------------------------------------------------------------------------
IO_CHANNEL  = "io"    # bandwidth-heavy: download, upload
CPU_CHANNEL = "cpu"   # CPU-heavy: gif, matting, tts
API_CHANNEL = "api"   # fast API: AI Q&A, OCR


class TaskManager:
    """
    Façade over three DownloadQueue instances.

    Parameters
    ----------
    io_workers:
        Concurrent slots for I/O tasks (default 3 – same as before).
    cpu_workers:
        Concurrent slots for CPU tasks (default 2 – spare cores).
    api_workers:
        Concurrent slots for API tasks (default 5 – cheap to wait on).
    max_completed:
        Per-channel completed-task cache size.
    """

    def __init__(
        self,
        io_workers: int = 3,
        cpu_workers: int = 2,
        api_workers: int = 5,
        max_completed: int = 100,
    ) -> None:
        self.io_queue  = DownloadQueue(io_workers,  max_completed, IO_CHANNEL)
        self.cpu_queue = DownloadQueue(cpu_workers, max_completed, CPU_CHANNEL)
        self.api_queue = DownloadQueue(api_workers, max_completed, API_CHANNEL)

        self._channels: dict[str, DownloadQueue] = {
            IO_CHANNEL:  self.io_queue,
            CPU_CHANNEL: self.cpu_queue,
            API_CHANNEL: self.api_queue,
        }

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    def set_executor(self, executor: Callable, channel: str = IO_CHANNEL) -> None:
        """
        Wire an executor coroutine into one channel.

        Call once per channel during post_init:

            task_manager.set_executor(execute_download, IO_CHANNEL)
            task_manager.set_executor(execute_cpu_task, CPU_CHANNEL)
            task_manager.set_executor(execute_api_task, API_CHANNEL)
        """
        queue = self._channels.get(channel)
        if queue is None:
            raise ValueError(f"Unknown channel '{channel}'. Valid: {list(self._channels)}")
        queue.set_executor(executor)

    async def start(self) -> None:
        for queue in self._channels.values():
            await queue.start()
        logger.info("TaskManager: all channels started.")

    async def stop(self) -> None:
        for queue in self._channels.values():
            await queue.stop()
        logger.info("TaskManager: all channels stopped.")

    # ------------------------------------------------------------------
    # Task routing
    # ------------------------------------------------------------------

    async def add_task(
        self,
        task: DownloadTask,
        telegram_ctx: TaskContext = None,
        priority: int = 10,
    ) -> str:
        """
        Route *task* to the correct channel based on ``task.channel``.

        If ``task.channel`` is not set or unrecognised, it defaults to
        ``IO_CHANNEL`` so existing callers need no changes.
        """
        channel = getattr(task, "channel", IO_CHANNEL) or IO_CHANNEL
        queue = self._channels.get(channel, self.io_queue)

        if channel not in self._channels:
            logger.warning(
                "TaskManager: unknown channel '%s' for task %s, routing to io.",
                channel, task.task_id,
            )

        return await queue.add_task(task, telegram_ctx, priority)

    # ------------------------------------------------------------------
    # Query helpers (union across all channels)
    # ------------------------------------------------------------------

    def get_task(self, task_id: str) -> Optional[DownloadTask]:
        for queue in self._channels.values():
            task = queue.get_task(task_id)
            if task is not None:
                return task
        return None

    def get_task_context(self, task_id: str) -> Optional[TaskContext]:
        for queue in self._channels.values():
            ctx = queue.get_task_context(task_id)
            if ctx is not None:
                return ctx
        return None

    def get_user_tasks(self, user_id: int) -> list[DownloadTask]:
        tasks: list[DownloadTask] = []
        for queue in self._channels.values():
            tasks.extend(queue.get_user_tasks(user_id))
        return sorted(tasks, key=lambda t: t.created_at, reverse=True)

    def get_all_active_tasks(self) -> list[DownloadTask]:
        """Return all currently active tasks across all channels."""
        tasks: list[DownloadTask] = []
        for queue in self._channels.values():
            tasks.extend(queue.get_all_active_tasks())
        return sorted(tasks, key=lambda t: t.created_at, reverse=True)

    def get_queue_position(self, user_id: int) -> int:
        """Sum of pending positions across all channels for a user."""
        return sum(
            q.get_queue_position(user_id) for q in self._channels.values()
        )

    def get_active_count(self, channel: str = None) -> int:
        if channel:
            return self._channels[channel].get_active_count()
        return sum(q.get_active_count() for q in self._channels.values())

    def get_total_queued(self, channel: str = None) -> int:
        if channel:
            return self._channels[channel].get_total_queued()
        return sum(q.get_total_queued() for q in self._channels.values())

    # Backward-compat alias used by existing admin /queue handler
    @property
    def max_concurrent(self) -> int:
        """Total concurrency across all channels."""
        return sum(q.max_concurrent for q in self._channels.values())

    # ------------------------------------------------------------------
    # Cancellation
    # ------------------------------------------------------------------

    async def cancel_task(self, task_id: str) -> bool:
        for queue in self._channels.values():
            if task_id in queue.active_tasks or any(
                task_id in pl for pl in queue._pending_user_tasks.values()
            ):
                return await queue.cancel_task(task_id)
        return False

    def get_cancel_event(self, task_id: str) -> Optional[asyncio.Event]:
        """Return the cancel Event for *task_id*, searching all channels."""
        for queue in self._channels.values():
            event = queue.get_cancel_event(task_id)
            if event is not None:
                return event
        return None

    # ------------------------------------------------------------------
    # Channel introspection (for /queue admin command)
    # ------------------------------------------------------------------

    def stats(self) -> dict[str, dict]:
        return {
            name: {
                "active":  queue.get_active_count(),
                "queued":  queue.get_total_queued(),
                "workers": queue.max_concurrent,
            }
            for name, queue in self._channels.items()
        }
