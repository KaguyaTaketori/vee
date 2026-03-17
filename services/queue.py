# services/queue.py
"""
Single-channel async priority queue for one resource class of tasks.

Changes from previous version
------------------------------
* Removed direct dependency on ``database.task_store.persist_task``.
  The queue no longer knows the DB exists.
* _finalize_task now emits ``bus.emit("task_completed", task)`` so that
  any registered listener (e.g. task_repo.save) can persist the record.
* _process_task still calls ``persist_task`` at the *start* of a task
  (status = DOWNLOADING) through the bus event "task_started", keeping
  the startup-persist behaviour while remaining decoupled.

The public API is unchanged – callers use TaskManager, not this class
directly. DownloadQueue is kept as a building block.
"""
from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass, field
from typing import Callable, Optional

from models.domain_models import DownloadTask, DownloadStatus, TaskContext
from services.event_bus import bus

logger = logging.getLogger(__name__)


@dataclass(order=True)
class PrioritizedTask:
    priority: int
    task: "DownloadTask" = field(compare=False)


class DownloadQueue:
    """
    Async worker pool backed by an asyncio.PriorityQueue.

    Parameters
    ----------
    max_concurrent:
        Number of worker coroutines running in parallel.
    max_completed_tasks:
        Maximum entries kept in the in-memory completed-task cache.
    channel_name:
        Human-readable label used in log messages (e.g. "io", "cpu").
    """

    def __init__(
        self,
        max_concurrent: int = 3,
        max_completed_tasks: int = 100,
        channel_name: str = "default",
    ) -> None:
        self.max_concurrent = max_concurrent
        self.max_completed_tasks = max_completed_tasks
        self.channel_name = channel_name

        self.queue: asyncio.PriorityQueue = asyncio.PriorityQueue()
        self.active_tasks: dict[str, DownloadTask] = {}
        self.completed_tasks: dict[str, DownloadTask] = {}
        self.user_downloads: dict[int, set[str]] = {}

        self._workers: list[asyncio.Task] = []
        self._running = False
        self._cancel_event: Optional[asyncio.Event] = None
        self._cancel_events: dict[str, asyncio.Event] = {}
        self._executor: Optional[Callable] = None
        self._pending_user_tasks: dict[int, list[str]] = {}
        self._task_contexts: dict[str, TaskContext] = {}
        self._cancelled_ids: set[str] = set()

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    def set_executor(self, executor: Callable) -> None:
        """Assign the coroutine function that processes each task."""
        self._executor = executor

    async def start(self) -> None:
        self._running = True
        self._cancel_event = asyncio.Event()
        self._workers = [
            asyncio.create_task(self._worker(i), name=f"queue-{self.channel_name}-{i}")
            for i in range(self.max_concurrent)
        ]
        logger.info(
            "[%s] Queue started with %d workers", self.channel_name, self.max_concurrent
        )

    async def stop(self) -> None:
        self._running = False
        if self._cancel_event:
            self._cancel_event.set()
        for worker in self._workers:
            worker.cancel()
        await asyncio.gather(*self._workers, return_exceptions=True)
        logger.info("[%s] Queue stopped", self.channel_name)

    # ------------------------------------------------------------------
    # Task management
    # ------------------------------------------------------------------

    async def add_task(
        self,
        task: DownloadTask,
        telegram_ctx: TaskContext = None,
        priority: int = 10,
    ) -> str:
        await self.queue.put(PrioritizedTask(priority=priority, task=task))

        self.user_downloads.setdefault(task.user_id, set()).add(task.task_id)
        self._pending_user_tasks.setdefault(task.user_id, []).append(task.task_id)

        if telegram_ctx is not None:
            self._task_contexts[task.task_id] = telegram_ctx

        self._cancel_events[task.task_id] = asyncio.Event()
        return task.task_id

    def get_task(self, task_id: str) -> Optional[DownloadTask]:
        return self.active_tasks.get(task_id) or self.completed_tasks.get(task_id)

    def get_task_context(self, task_id: str) -> Optional[TaskContext]:
        return self._task_contexts.get(task_id)

    def get_cancel_event(self, task_id: str) -> Optional[asyncio.Event]:
        return self._cancel_events.get(task_id)

    def get_user_tasks(self, user_id: int) -> list[DownloadTask]:
        task_ids = self.user_downloads.get(user_id, set())
        tasks = [self.get_task(tid) for tid in task_ids]
        return sorted(
            (t for t in tasks if t is not None),
            key=lambda t: t.created_at,
            reverse=True,
        )

    def get_queue_position(self, user_id: int) -> int:
        return len(self._pending_user_tasks.get(user_id, []))

    def get_total_queued(self) -> int:
        return self.queue.qsize()

    def get_active_count(self) -> int:
        return len(self.active_tasks)

    def get_all_active_tasks(self) -> list[DownloadTask]:
        """Return all currently active tasks across all channels."""
        return list(self.active_tasks.values())

    # ------------------------------------------------------------------
    # Cancellation
    # ------------------------------------------------------------------

    async def cancel_active_task(self, task_id: str) -> bool:
        task = self.active_tasks.get(task_id)
        if not task:
            return False
        event = self._cancel_events.get(task_id)
        if event:
            event.set()
        return True

    async def cancel_queued_task(self, task_id: str) -> bool:
        for pending_list in self._pending_user_tasks.values():
            if task_id in pending_list:
                self._cancelled_ids.add(task_id)
                pending_list.remove(task_id)
                fake_task = DownloadTask(
                    task_id=task_id,
                    user_id=0,
                    url="",
                    download_type="",
                    status=DownloadStatus.CANCELLED,
                )
                self.completed_tasks[task_id] = fake_task
                return True
        return False

    async def cancel_task(self, task_id: str) -> bool:
        if task_id in self.active_tasks:
            return await self.cancel_active_task(task_id)
        return await self.cancel_queued_task(task_id)

    def complete_task(self, task: DownloadTask) -> None:
        self._finalize_task(task, DownloadStatus.COMPLETED)

    def fail_task(self, task: DownloadTask, error: str) -> None:
        self._finalize_task(task, DownloadStatus.FAILED, error)

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _finalize_task(
        self,
        task: DownloadTask,
        status: DownloadStatus,
        error: str = None,
    ) -> None:
        """
        Mark a task as terminal and emit an event so listeners can react.

        Previously this method called persist_task(task) directly.
        Now it fires the event bus – the queue has zero knowledge of
        where (or whether) the task gets persisted.
        """
        task.status = status
        task.completed_at = time.time()
        if error:
            task.error = error

        self.completed_tasks[task.task_id] = task
        self.active_tasks.pop(task.task_id, None)
        self._task_contexts.pop(task.task_id, None)
        self._cancel_events.pop(task.task_id, None)
        self._cleanup_completed()

        # ── Decouple: emit event instead of calling persist_task() ──────
        bus.emit("task_completed", task)

    def _cleanup_completed(self) -> None:
        if len(self.completed_tasks) > self.max_completed_tasks:
            sorted_tasks = sorted(
                self.completed_tasks.items(),
                key=lambda x: x[1].completed_at or 0,
            )
            to_remove = sorted_tasks[: len(sorted_tasks) - self.max_completed_tasks]
            for task_id, _ in to_remove:
                del self.completed_tasks[task_id]
                for user_tasks in self.user_downloads.values():
                    user_tasks.discard(task_id)

    # ------------------------------------------------------------------
    # Worker loop
    # ------------------------------------------------------------------

    async def _worker(self, worker_id: int) -> None:
        while self._running:
            try:
                item = await asyncio.wait_for(self.queue.get(), timeout=1.0)
                task = item.task
                if task.task_id in self._cancelled_ids:
                    self._cancelled_ids.discard(task.task_id)
                    logger.info(
                        "[%s] Task %s cancelled before execution, skipping.",
                        self.channel_name, task.task_id,
                    )
                    continue
                await self._process_task(task)
            except asyncio.TimeoutError:
                continue
            except asyncio.CancelledError:
                break

    async def _process_task(self, task: DownloadTask) -> None:
        task.status = DownloadStatus.DOWNLOADING
        task.started_at = time.time()
        self.active_tasks[task.task_id] = task

        bus.emit("task_started", task)

        user_pending = self._pending_user_tasks.get(task.user_id, [])
        if task.task_id in user_pending:
            user_pending.remove(task.task_id)

        if not self._executor:
            self._finalize_task(task, DownloadStatus.FAILED, "No executor configured")
            return

        last_error = None
        for attempt in range(task.max_retries + 1):
            if attempt > 0:
                logger.info(
                    "[%s] Retrying task %s (attempt %d/%d, delay=%.1fs)",
                    self.channel_name, task.task_id, attempt, task.max_retries,
                    task.retry_delay,
                )
                await asyncio.sleep(task.retry_delay * attempt)
                task.retry_count = attempt
                bus.emit("task_retrying", task)

                ctx = self.get_task_context(task.task_id)
                if ctx:
                    sender = ctx.get("sender")
                    if sender:
                        try:
                            await sender.edit_status(
                                f"🔄 Retrying… (attempt {attempt}/{task.max_retries})"
                            )
                        except Exception:
                            pass
            try:
                await self._executor(task)
                if task.status not in (
                    DownloadStatus.COMPLETED,
                    DownloadStatus.FAILED,
                    DownloadStatus.CANCELLED,
                ):
                    self._finalize_task(task, DownloadStatus.COMPLETED)
                elif task.status == DownloadStatus.FAILED and attempt < task.max_retries:
                    last_error = task.error
                    task.status = DownloadStatus.DOWNLOADING
                    continue
                else:
                    self._finalize_task(task, task.status, task.error)
                return
            except Exception as exc:
                last_error = str(exc)
                logger.warning(
                    "[%s] Task %s attempt %d failed: %s",
                    self.channel_name, task.task_id, attempt, exc,
                )
                if attempt >= task.max_retries:
                    break

        logger.error(
            "[%s] Task %s failed after %d retries: %s",
            self.channel_name, task.task_id, task.max_retries, last_error,
        )
        self._finalize_task(
            task,
            DownloadStatus.FAILED,
            f"Failed after {task.max_retries} retries: {last_error}",
        )


