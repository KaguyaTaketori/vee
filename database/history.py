import os
import time
import logging
from typing import Optional
from database.db import get_db

logger = logging.getLogger(__name__)

MAX_ENTRIES_PER_USER = 100
MAX_TOTAL_ENTRIES = 5000

async def get_recent_cached_urls(
    limit: int = 5,
    offset: int = 0,
) -> tuple[list, int]:
    async with get_db() as db:
        async with db.execute(
            """
            SELECT COUNT(DISTINCT url) FROM history
            WHERE status = 'success' AND file_id IS NOT NULL
            """
        ) as cur:
            total = (await cur.fetchone())[0]

        async with db.execute(
            """
            SELECT url, title, download_type, file_size, MAX(timestamp) as timestamp
            FROM history
            WHERE status = 'success' AND file_id IS NOT NULL
            GROUP BY url, download_type
            ORDER BY MAX(timestamp) DESC
            LIMIT ? OFFSET ?
            """,
            (limit, offset),
        ) as cursor:
            rows = await cursor.fetchall()

    return [dict(row) for row in rows], total

async def add_history(user_id: int, url: str, download_type: str, file_size: Optional[int] = None, 
                     title: Optional[str] = None, status: str = "success", file_path: Optional[str] = None,
                     file_id: Optional[str] = None):
    now = time.time()
    
    async with get_db() as db:
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
        
        async with db.execute("SELECT COUNT(*) FROM history") as cursor:
            row = await cursor.fetchone()
            total = row[0] if row else 0
        
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
    async with get_db() as db:
        async with db.execute(
            """
            SELECT user_id, url, download_type, status, file_size, title, file_path, file_id, timestamp
            FROM history WHERE user_id = ? ORDER BY timestamp DESC LIMIT ?
            """,
            (user_id, limit)
        ) as cursor:
            rows = await cursor.fetchall()
            return [dict(row) for row in rows]


async def get_user_history_page(user_id: int, page: int = 0, page_size: int = 5) -> tuple[list, int]:
    offset = page * page_size
    async with get_db() as db:
        async with db.execute(
            "SELECT COUNT(*) FROM history WHERE user_id = ?", (user_id,)
        ) as cur:
            total = (await cur.fetchone())[0]

        async with db.execute(
            """
            SELECT user_id, url, download_type, status, file_size, title,
                   file_path, file_id, timestamp
            FROM history WHERE user_id = ?
            ORDER BY timestamp DESC LIMIT ? OFFSET ?
            """,
            (user_id, page_size, offset),
        ) as cur:
            rows = await cur.fetchall()

    return [dict(r) for r in rows], total


async def clear_user_history(user_id: int):
    async with get_db() as db:
        await db.execute("DELETE FROM history WHERE user_id = ?", (user_id,))
        await db.commit()


async def get_all_users_count() -> int:
    async with get_db() as db:
        async with db.execute("SELECT COUNT(*) FROM users") as cursor:
            row = await cursor.fetchone()
            return row[0] if row else 0

async def get_total_downloads() -> int:
    async with get_db() as db:
        async with db.execute("SELECT COUNT(*) FROM history") as cursor:
            row = await cursor.fetchone()
            return row[0] if row else 0


async def get_failed_downloads(user_id: Optional[int] = None, limit: int = 20) -> list:
    sql = "SELECT user_id, url, download_type, status, file_size, title, file_path, file_id, timestamp FROM history WHERE status = 'failed'"
    params: tuple = ()
    if user_id:
        sql += " AND user_id = ?"
        params = (user_id,)
    sql += " ORDER BY timestamp DESC LIMIT ?"
    params += (limit,)

    async with get_db() as db:
        async with db.execute(sql, params) as cursor:
            rows = await cursor.fetchall()
            return [dict(row) for row in rows]


async def check_recent_download(
    url: str,
    max_age_hours: int = 24,
    download_type: str | None = None,
) -> Optional[dict]:
    """Check if URL was recently downloaded and file still exists."""
    now = time.time()
    max_age_seconds = max_age_hours * 3600

    sql = """
        SELECT user_id, url, download_type, status, file_size, title, file_path, file_id, timestamp
        FROM history
        WHERE url = ?
          AND status = 'success'
          AND timestamp > ?
          AND (file_path IS NOT NULL OR file_id IS NOT NULL)
    """
    params: list = [url, now - max_age_seconds]

    if download_type:
        sql += " AND download_type = ?"
        params.append(download_type)

    sql += " ORDER BY timestamp DESC LIMIT 1"

    async with get_db() as db:
        async with db.execute(sql, params) as cursor:
            row = await cursor.fetchone()
            if row:
                entry = dict(row)
                if entry.get("file_path") and os.path.exists(entry["file_path"]):
                    return entry
                if entry.get("file_id"):
                    return entry
    return None


async def get_file_id_by_url(
    url: str,
    max_age_hours: int = 168,
    download_type: str | None = None,
) -> Optional[str]:
    """Get file_id by URL (up to 7 days). Returns file_id if available."""
    now = time.time()
    max_age_seconds = max_age_hours * 3600

    sql = """
        SELECT file_id FROM history
        WHERE url = ?
          AND status = 'success'
          AND file_id IS NOT NULL
          AND timestamp > ?
    """
    params: list = [url, now - max_age_seconds]

    if download_type:
        sql += " AND download_type = ?"
        params.append(download_type)

    sql += " ORDER BY timestamp DESC LIMIT 1"

    async with get_db() as db:
        async with db.execute(sql, params) as cursor:
            row = await cursor.fetchone()
            if row and row[0]:
                return row[0]
    return None


async def clear_file_id_by_url(url: str, download_type: str | None = None):
    """Clear cached file_id for a URL. If download_type given, only clears that type."""
    sql = "UPDATE history SET file_id = NULL WHERE url = ? AND file_id IS NOT NULL"
    params: list = [url]

    if download_type:
        sql += " AND download_type = ?"
        params.append(download_type)

    async with get_db() as db:
        await db.execute(sql, params)
        await db.commit()
