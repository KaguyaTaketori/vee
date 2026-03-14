import asyncio
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Optional, Callable, Any
from telegram import Bot


class DownloadStatus(Enum):
    QUEUED = "queued"
    DOWNLOADING = "downloading"
    PROCESSING = "processing"
    UPLOADING = "uploading"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


@dataclass
class DownloadTask:
    task_id: str
    user_id: int
    url: str
    download_type: str
    format_id: Optional[str] = None
    status: DownloadStatus = DownloadStatus.QUEUED
    progress: float = 0.0
    created_at: float = field(default_factory=time.time)
    started_at: Optional[float] = None
    completed_at: Optional[float] = None
    error: Optional[str] = None
    file_path: Optional[str] = None
    file_size: Optional[int] = None


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
        self._cancel_event: Optional[asyncio.Event] = None
        self._executor: Optional[Callable] = None
        self._pending_user_tasks: dict[int, list[str]] = {}
    
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

    async def _process_task(self, task: DownloadTask):
        task.status = DownloadStatus.DOWNLOADING
        task.started_at = time.time()
        self.active_tasks[task.task_id] = task
        
        if task.user_id in self._pending_user_tasks and task.task_id in self._pending_user_tasks[task.user_id]:
            self._pending_user_tasks[task.user_id].remove(task.task_id)
        
        if self._executor:
            try:
                await self._executor(task)
            except Exception as e:
                task.status = DownloadStatus.FAILED
                task.error = str(e)
                task.completed_at = time.time()
                self.completed_tasks[task.task_id] = task
                if task.task_id in self.active_tasks:
                    del self.active_tasks[task.task_id]
                self._cleanup_completed()
        else:
            task.status = DownloadStatus.FAILED
            task.error = "No executor configured"
            task.completed_at = time.time()
            self.completed_tasks[task.task_id] = task
            if task.task_id in self.active_tasks:
                del self.active_tasks[task.task_id]
            self._cleanup_completed()

    async def add_task(self, task: DownloadTask) -> str:
        await self.queue.put(task)
        if task.user_id not in self.user_downloads:
            self.user_downloads[task.user_id] = set()
        self.user_downloads[task.user_id].add(task.task_id)
        if task.user_id not in self._pending_user_tasks:
            self._pending_user_tasks[task.user_id] = []
        self._pending_user_tasks[task.user_id].append(task.task_id)
        return task.task_id

    def get_task(self, task_id: str) -> Optional[DownloadTask]:
        return self.active_tasks.get(task_id) or self.completed_tasks.get(task_id)

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

    def cancel_task(self, task_id: str) -> bool:
        task = self.active_tasks.get(task_id)
        if task and task.status == DownloadStatus.QUEUED:
            task.status = DownloadStatus.CANCELLED
            task.completed_at = time.time()
            self.completed_tasks[task_id] = task
            del self.active_tasks[task_id]
            self._cleanup_completed()
            return True
        return False

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
