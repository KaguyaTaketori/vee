import time
import logging
from typing import Optional
from database.db import get_db
from models.domain_models import DownloadTask, DownloadStatus

logger = logging.getLogger(__name__)


async def persist_task(task: DownloadTask):
    async with get_db() as db:
        await db.execute(
            """
            INSERT INTO tasks (
                task_id, user_id, url, download_type, format_id,
                status, progress, error, file_path, file_size,
                retry_count, created_at, started_at, completed_at
            ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            ON CONFLICT(task_id) DO UPDATE SET
                status       = excluded.status,
                progress     = excluded.progress,
                error        = excluded.error,
                file_path    = excluded.file_path,
                file_size    = excluded.file_size,
                retry_count  = excluded.retry_count,
                started_at   = excluded.started_at,
                completed_at = excluded.completed_at
            """,
            (
                task.task_id, task.user_id, task.url, task.download_type,
                task.format_id, task.status.value, task.progress,
                task.error, task.file_path, task.file_size,
                task.retry_count, task.created_at,
                task.started_at, task.completed_at,
            ),
        )
        await db.commit()


async def get_task_record(task_id: str) -> Optional[dict]:
    async with get_db() as db:
        async with db.execute(
            "SELECT * FROM tasks WHERE task_id = ?", (task_id,)
        ) as cur:
            row = await cur.fetchone()
            return dict(row) if row else None


async def get_user_tasks(user_id: int, limit: int = 10) -> list[dict]:
    async with get_db() as db:
        async with db.execute(
            """
            SELECT * FROM tasks
            WHERE user_id = ?
            ORDER BY created_at DESC
            LIMIT ?
            """,
            (user_id, limit),
        ) as cur:
            return [dict(r) for r in await cur.fetchall()]


async def get_incomplete_tasks(max_age_hours: int = 24) -> list[dict]:
    since = time.time() - max_age_hours * 3600
    async with get_db() as db:
        async with db.execute(
            """
            SELECT * FROM tasks
            WHERE status IN ('queued', 'downloading', 'processing')
              AND created_at > ?
            ORDER BY created_at ASC
            """,
            (since,),
        ) as cur:
            return [dict(r) for r in await cur.fetchall()]


async def mark_stale_tasks_failed():
    async with get_db() as db:
        await db.execute(
            """
            UPDATE tasks
            SET status = 'failed',
                error  = 'Bot restarted, task interrupted',
                completed_at = ?
            WHERE status IN ('queued', 'downloading', 'processing')
            """,
            (time.time(),),
        )
        result = await db.execute("SELECT changes()")
        row = await result.fetchone()
        stale_count = row[0] if row else 0
        await db.commit()

    if stale_count:
        logger.info(f"Marked {stale_count} stale tasks as failed on startup")
    return stale_count

