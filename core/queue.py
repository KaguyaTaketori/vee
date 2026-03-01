import asyncio
import time
from dataclasses import dataclass
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
    download_type: str  # video, audio, thumbnail
    format_id: Optional[str] = None
    status: DownloadStatus = DownloadStatus.QUEUED
    progress: float = 0.0
    created_at: float = None
    started_at: Optional[float] = None
    completed_at: Optional[float] = None
    error: Optional[str] = None
    file_path: Optional[str] = None
    file_size: Optional[int] = None

    def __post_init__(self):
        if self.created_at is None:
            self.created_at = time.time()


class DownloadQueue:
    def __init__(self, max_concurrent: int = 3):
        self.max_concurrent = max_concurrent
        self.queue: asyncio.Queue = asyncio.Queue()
        self.active_tasks: dict[str, DownloadTask] = {}
        self.completed_tasks: dict[str, DownloadTask] = {}
        self.user_downloads: dict[int, set[str]] = {}
        self._workers: list[asyncio.Task] = []
        self._running = False
        self._cancel_event: asyncio.Event = None

    async def start(self):
        self._running = True
        self._cancel_event = asyncio.Event()
        self._workers = [
            asyncio.create_task(self._worker(i))
            for i in range(self.max_concurrent)
        ]

    async def stop(self):
        self._running = False
        self._cancel_event = None
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

    async def add_task(self, task: DownloadTask) -> str:
        await self.queue.put(task)
        if task.user_id not in self.user_downloads:
            self.user_downloads[task.user_id] = set()
        self.user_downloads[task.user_id].add(task.task_id)
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

    def cancel_task(self, task_id: str) -> bool:
        task = self.active_tasks.get(task_id)
        if task and task.status == DownloadStatus.QUEUED:
            task.status = DownloadStatus.CANCELLED
            task.completed_at = time.time()
            self.completed_tasks[task_id] = task
            del self.active_tasks[task_id]
            return True
        return False

    def complete_task(self, task: DownloadTask):
        task.status = DownloadStatus.COMPLETED
        task.completed_at = time.time()
        self.completed_tasks[task.task_id] = task
        if task.task_id in self.active_tasks:
            del self.active_tasks[task.task_id]

    def fail_task(self, task: DownloadTask, error: str):
        task.status = DownloadStatus.FAILED
        task.error = error
        task.completed_at = time.time()
        self.completed_tasks[task.task_id] = task
        if task.task_id in self.active_tasks:
            del self.active_tasks[task.task_id]


download_queue = DownloadQueue(max_workers=3)
