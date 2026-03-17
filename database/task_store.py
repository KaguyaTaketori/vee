# database/task_store.py
"""
Backward-compatible façade for task-related DB operations.

All SQL has been moved to repositories.TaskRepository.
Existing call sites continue to work unchanged.

If you are writing NEW code, import TaskRepository directly:
    from repositories import TaskRepository
"""

from typing import Optional
from repositories import TaskRepository
from models.domain_models import DownloadTask

_repo = TaskRepository()


async def persist_task(task: DownloadTask) -> None:
    await _repo.save(task)


async def get_task_record(task_id: str) -> Optional[dict]:
    return await _repo.get_by_id(task_id)


async def get_user_tasks(user_id: int, limit: int = 10) -> list[dict]:
    return await _repo.get_by_user(user_id, limit=limit)


async def get_incomplete_tasks(max_age_hours: int = 24) -> list[dict]:
    return await _repo.get_incomplete(max_age_hours=max_age_hours)


async def mark_stale_tasks_failed() -> int:
    return await _repo.mark_stale_failed()
