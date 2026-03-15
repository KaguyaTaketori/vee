import logging
import asyncio
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Optional, Callable, TypedDict, Any
from telegram import Bot
from core.task_store import persist_task
from core.models import DownloadTask, DownloadStatus, STATUS_EMOJI, TaskContext

logger = logging.getLogger(__name__)

class DownloadQueue:
    def __init__(self, max_concurrent: int = 3, max_completed_tasks: int = 100):
        self.max_concurrent = max_concurrent
        self.max_completed_tasks = max_completed_tasks
        self.queue: asyncio.Queue = asyncio.Queue()
        self.active_tasks: dict[str, DownloadTask] = {}
        self.completed_tasks: dict[str, DownloadTask] = {}
        self.user_downloads: dict[int, set[str]] = {}
        self._workers: list[asyncio.Task] = []
        self._running = False
        self._cancel_events: dict[str, asyncio.Event] = {}
        self._executor: Optional[Callable] = None
        self._pending_user_tasks: dict[int, list[str]] = {}
        self._task_contexts: dict[str, TaskContext] = {}
    
    def set_executor(self, executor: Callable):
        """Set the async function to execute when processing a task.
        
        The executor should accept: (task: DownloadTask, processing_msg, context)
        """
        self._executor = executor
    
    async def start(self):
        self._running = True
        self._cancel_event = asyncio.Event()
        self._workers = [
            asyncio.create_task(self._worker(i))
            for i in range(self.max_concurrent)
        ]

    async def stop(self):
        self._running = False
        if self._cancel_event:
            self._cancel_event.set()
        for worker in self._workers:
            worker.cancel()
        await asyncio.gather(*self._workers, return_exceptions=True)

    async def _worker(self, worker_id: int):
        while self._running:
            try:
                task = await asyncio.wait_for(self.queue.get(), timeout=1.0)
                await self._process_task(task)
            except asyncio.TimeoutError:
                continue
            except asyncio.CancelledError:
                break

    def get_task_context(self, task_id: str) -> Optional[TaskContext]:
        return self._task_contexts.get(task_id)

    def _finalize_task(self, task: DownloadTask, status: DownloadStatus, error: str = None):
        task.status = status
        task.completed_at = time.time()
        if error:
            task.error = error
        self.completed_tasks[task.task_id] = task
        self.active_tasks.pop(task.task_id, None)
        self._task_contexts.pop(task.task_id, None)
        self._cleanup_completed()
        self._cancel_events.pop(task.task_id, None)

        try:
            loop = asyncio.get_running_loop()
            loop.create_task(persist_task(task))
        except RuntimeError:
            pass

    async def _process_task(self, task: DownloadTask):
        task.status = DownloadStatus.DOWNLOADING
        task.started_at = time.time()
        self.active_tasks[task.task_id] = task
        await persist_task(task)

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
                    f"Retrying task {task.task_id} "
                    f"(attempt {attempt}/{task.max_retries}, delay={task.retry_delay}s)"
                )
                await asyncio.sleep(task.retry_delay * attempt)
                task.retry_count = attempt
                await persist_task(task)

                ctx = self.get_task_context(task.task_id)
                if ctx:
                    try:
                        await ctx["processing_msg"].edit_text(
                            f"🔄 Retrying... (attempt {attempt}/{task.max_retries})"
                        )
                    except Exception:
                        pass

            try:
                await self._executor(task)
                if task.status not in (DownloadStatus.COMPLETED, DownloadStatus.FAILED, DownloadStatus.CANCELLED):
                    self._finalize_task(task, DownloadStatus.COMPLETED)
                elif task.status == DownloadStatus.FAILED and attempt < task.max_retries:
                    last_error = task.error
                    task.status = DownloadStatus.DOWNLOADING
                    continue
                else:
                    self._finalize_task(task, task.status, task.error)
                return
            except Exception as e:
                last_error = str(e)
                logger.warning(f"Task {task.task_id} attempt {attempt} failed: {e}")
                if attempt >= task.max_retries:
                    break

        logger.error(f"Task {task.task_id} failed after {task.max_retries} retries: {last_error}")
        self._finalize_task(task, DownloadStatus.FAILED, f"Failed after {task.max_retries} retries: {last_error}")

    async def add_task(self, task: DownloadTask, telegram_ctx: TaskContext = None) -> str:
        await self.queue.put(task)
        if task.user_id not in self.user_downloads:
            self.user_downloads[task.user_id] = set()
        self.user_downloads[task.user_id].add(task.task_id)

        if task.user_id not in self._pending_user_tasks:
            self._pending_user_tasks[task.user_id] = []
        self._pending_user_tasks[task.user_id].append(task.task_id)

        if telegram_ctx is not None:
            self._task_contexts[task.task_id] = telegram_ctx
        
        self._cancel_events[task.task_id] = asyncio.Event()
        return task.task_id

    def get_task(self, task_id: str) -> Optional[DownloadTask]:
        return self.active_tasks.get(task_id) or self.completed_tasks.get(task_id)

    def get_cancel_event(self, task_id: str) -> Optional[asyncio.Event]:
        return self._cancel_events.get(task_id)

    def get_user_tasks(self, user_id: int) -> list[DownloadTask]:
        task_ids = self.user_downloads.get(user_id, set())
        tasks = []
        for tid in task_ids:
            task = self.get_task(tid)
            if task:
                tasks.append(task)
        return sorted(tasks, key=lambda t: t.created_at, reverse=True)
    
    def get_queue_position(self, user_id: int) -> int:
        """Get the number of queued tasks for a user (not including active)."""
        return len(self._pending_user_tasks.get(user_id, []))
    
    def get_total_queued(self) -> int:
        """Get total number of queued tasks."""
        return self.queue.qsize()
    
    def get_active_count(self) -> int:
        """Get number of currently active tasks."""
        return len(self.active_tasks)

    async def cancel_active_task(self, task_id: str) -> bool:
        task = self.active_tasks.get(task_id)
        if not task:
            return False
        
        event = self._cancel_events.get(task_id)
        if event:
            event.set()
        return True

    async def cancel_queued_task(self, task_id: str) -> bool:
        remaining = []
        cancelled = False
        
        while not self.queue.empty():
            try:
                task = self.queue.get_nowait()
                if task.task_id == task_id:
                    task.status = DownloadStatus.CANCELLED
                    task.completed_at = time.time()
                    self.completed_tasks[task_id] = task
                    self._task_contexts.pop(task_id, None)
                    cancelled = True
                else:
                    remaining.append(task)
            except asyncio.QueueEmpty:
                break
    
        for t in remaining:
            await self.queue.put(t)
    
        if cancelled:
            user_pending = self._pending_user_tasks.get(task.user_id, [])
            if task_id in user_pending:
                user_pending.remove(task_id)
    
        return cancelled

    async def cancel_task(self, task_id: str) -> bool:
        if task_id in self.active_tasks:
            return await self.cancel_active_task(task_id)
        
        return await self.cancel_queued_task(task_id)

    def complete_task(self, task: DownloadTask):
        task.status = DownloadStatus.COMPLETED
        task.completed_at = time.time()
        self.completed_tasks[task.task_id] = task
        if task.task_id in self.active_tasks:
            del self.active_tasks[task.task_id]
        self._cleanup_completed()

    def fail_task(self, task: DownloadTask, error: str):
        task.status = DownloadStatus.FAILED
        task.error = error
        task.completed_at = time.time()
        self.completed_tasks[task.task_id] = task
        if task.task_id in self.active_tasks:
            del self.active_tasks[task.task_id]
        self._cleanup_completed()

    def _cleanup_completed(self):
        if len(self.completed_tasks) > self.max_completed_tasks:
            sorted_tasks = sorted(
                self.completed_tasks.items(),
                key=lambda x: x[1].completed_at or 0
            )
            to_remove = sorted_tasks[:len(sorted_tasks) - self.max_completed_tasks]
            for task_id, _ in to_remove:
                del self.completed_tasks[task_id]
                for user_tasks in self.user_downloads.values():
                    user_tasks.discard(task_id)


download_queue = DownloadQueue(max_concurrent=3)
