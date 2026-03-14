import os
import time
import logging
import aiosqlite
from typing import Optional
from core.db import DB_PATH

logger = logging.getLogger(__name__)

MAX_ENTRIES_PER_USER = 100
MAX_TOTAL_ENTRIES = 5000


async def add_history(user_id: int, url: str, download_type: str, file_size: Optional[int] = None, 
                     title: Optional[str] = None, status: str = "success", file_path: Optional[str] = None,
                     file_id: Optional[str] = None):
    now = time.time()
    
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            """
            INSERT INTO history (user_id, url, download_type, status, file_size, title, file_path, file_id, timestamp)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (user_id, url, download_type, status, file_size, title, file_path, file_id, now)
        )
        
        await db.execute(
            """
            DELETE FROM history WHERE user_id = ? AND id NOT IN (
                SELECT id FROM history WHERE user_id = ? ORDER BY timestamp DESC LIMIT ?
            )
            """,
            (user_id, user_id, MAX_ENTRIES_PER_USER)
        )
        
        total_count = await db.execute_fetchone("SELECT COUNT(*) FROM history")
        total = total_count[0] if total_count else 0
        
        if total > MAX_TOTAL_ENTRIES:
            delete_count = total - MAX_TOTAL_ENTRIES
            await db.execute(
                """
                DELETE FROM history WHERE id IN (
                    SELECT id FROM history ORDER BY timestamp ASC LIMIT ?
                )
                """,
                (delete_count,)
            )
        
        await db.commit()


async def get_user_history(user_id: int, limit: int = 10) -> list:
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            """
            SELECT user_id, url, download_type, status, file_size, title, file_path, file_id, timestamp
            FROM history WHERE user_id = ? ORDER BY timestamp DESC LIMIT ?
            """,
            (user_id, limit)
        ) as cursor:
            rows = await cursor.fetchall()
            return [dict(row) for row in rows]


async def clear_user_history(user_id: int):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("DELETE FROM history WHERE user_id = ?", (user_id,))
        await db.commit()


async def get_all_users_count() -> int:
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute("SELECT COUNT(DISTINCT user_id) FROM history") as cursor:
            row = await cursor.fetchone()
            return row[0] if row else 0


async def get_total_downloads() -> int:
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute("SELECT COUNT(*) FROM history") as cursor:
            row = await cursor.fetchone()
            return row[0] if row else 0


async def get_failed_downloads(user_id: Optional[int] = None, limit: int = 20) -> list:
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        if user_id:
            async with db.execute(
                """
                SELECT user_id, url, download_type, status, file_size, title, file_path, file_id, timestamp
                FROM history WHERE user_id = ? AND status = 'failed' ORDER BY timestamp DESC LIMIT ?
                """,
                (user_id, limit)
            ) as cursor:
                rows = await cursor.fetchall()
                return [dict(row) for row in rows]
        else:
            async with db.execute(
                """
                SELECT user_id, url, download_type, status, file_size, title, file_path, file_id, timestamp
                FROM history WHERE status = 'failed' ORDER BY timestamp DESC LIMIT ?
                """,
                (limit,)
            ) as cursor:
                rows = await cursor.fetchall()
                return [dict(row) for row in rows]


async def check_recent_download(url: str, max_age_hours: int = 24) -> Optional[dict]:
    """Check if URL was recently downloaded and file still exists."""
    now = time.time()
    max_age_seconds = max_age_hours * 3600
    
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            """
            SELECT user_id, url, download_type, status, file_size, title, file_path, file_id, timestamp
            FROM history 
            WHERE url = ? AND status = 'success' AND timestamp > ? AND (file_path IS NOT NULL OR file_id IS NOT NULL)
            ORDER BY timestamp DESC LIMIT 1
            """,
            (url, now - max_age_seconds)
        ) as cursor:
            row = await cursor.fetchone()
            if row:
                entry = dict(row)
                if entry.get("file_path") and os.path.exists(entry["file_path"]):
                    return entry
                if entry.get("file_id"):
                    return entry
    return None


async def get_file_id_by_url(url: str, max_age_hours: int = 168) -> Optional[str]:
    """Get file_id by URL (up to 7 days). Returns file_id if available."""
    now = time.time()
    max_age_seconds = max_age_hours * 3600
    
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute(
            """
            SELECT file_id FROM history 
            WHERE url = ? AND status = 'success' AND file_id IS NOT NULL AND timestamp > ?
            ORDER BY timestamp DESC LIMIT 1
            """,
            (url, now - max_age_seconds)
        ) as cursor:
            row = await cursor.fetchone()
            if row and row[0]:
                return row[0]
    return None


async def clear_file_id_by_url(url: str):
    """Remove file_id from history entries for a URL, forcing re-download."""
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "UPDATE history SET file_id = NULL WHERE url = ? AND file_id IS NOT NULL",
            (url,)
        )
        await db.commit()


async def force_persist():
    pass
