# repositories/history_repo.py
"""
HistoryRepository
-----------------
Owns every SQL statement that touches the `history` table.
Services / handlers must not write SELECT / INSERT / UPDATE / DELETE
against `history` directly.
"""

import os
import time
import logging
from typing import Optional

from shared.repositories.base import BaseRepository

logger = logging.getLogger(__name__)

_MAX_ENTRIES_PER_USER = 100
_MAX_TOTAL_ENTRIES = 5000


class HistoryRepository(BaseRepository):

    # ------------------------------------------------------------------ write

    async def add(
        self,
        user_id: int,
        url: str,
        download_type: str,
        file_size: Optional[int] = None,
        title: Optional[str] = None,
        status: str = "success",
        file_path: Optional[str] = None,
        file_id: Optional[str] = None,
    ) -> None:
        logger.info("history_repo.add: user_id=%s, url=%s, download_type=%s, file_size=%s (type=%s), title=%s",
            user_id, url, download_type, file_size, type(file_size), title)
        now = time.time()
        async with self._db() as db:
            await db.execute(
                """
                INSERT INTO history
                    (user_id, url, download_type, status,
                     file_size, title, file_path, file_id, timestamp)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (user_id, url, download_type, status,
                 file_size, title, file_path, file_id, now),
            )
            # Trim per-user rows
            await db.execute(
                """
                DELETE FROM history
                WHERE user_id = ?
                  AND id NOT IN (
                      SELECT id FROM history
                      WHERE user_id = ?
                      ORDER BY timestamp DESC
                      LIMIT ?
                  )
                """,
                (user_id, user_id, _MAX_ENTRIES_PER_USER),
            )
            # Trim global rows
            async with db.execute("SELECT COUNT(*) FROM history") as cur:
                total = (await cur.fetchone())[0]
            try:
                total = int(total) if total else 0
            except (TypeError, ValueError):
                total = 0
            if total > _MAX_TOTAL_ENTRIES:
                await db.execute(
                    """
                    DELETE FROM history
                    WHERE id IN (
                        SELECT id FROM history
                        ORDER BY timestamp ASC
                        LIMIT ?
                    )
                    """,
                    (total - _MAX_TOTAL_ENTRIES,),
                )
            await db.commit()

    async def clear_file_id_by_url(
        self, url: str, download_type: Optional[str] = None
    ) -> None:
        sql = "UPDATE history SET file_id = NULL WHERE url = ? AND file_id IS NOT NULL"
        params: list = [url]
        if download_type:
            sql += " AND download_type = ?"
            params.append(download_type)
        async with self._db() as db:
            await db.execute(sql, params)
            await db.commit()

    async def clear_user_history(self, user_id: int) -> None:
        async with self._db() as db:
            await db.execute("DELETE FROM history WHERE user_id = ?", (user_id,))
            await db.commit()

    # ------------------------------------------------------------------- read

    async def get_by_user(self, user_id: int, limit: int = 10) -> list[dict]:
        async with self._db() as db:
            async with db.execute(
                """
                SELECT user_id, url, download_type, status, file_size,
                       title, file_path, file_id, timestamp
                FROM history
                WHERE user_id = ?
                ORDER BY timestamp DESC
                LIMIT ?
                """,
                (user_id, limit),
            ) as cur:
                return [dict(r) for r in await cur.fetchall()]

    async def get_by_user_paged(
        self, user_id: int, page: int = 0, page_size: int = 5
    ) -> tuple[list[dict], int]:
        offset = page * page_size
        async with self._db() as db:
            async with db.execute(
                "SELECT COUNT(*) FROM history WHERE user_id = ?", (user_id,)
            ) as cur:
                total = (await cur.fetchone())[0]
            async with db.execute(
                """
                SELECT user_id, url, download_type, status, file_size,
                       title, file_path, file_id, timestamp
                FROM history
                WHERE user_id = ?
                ORDER BY timestamp DESC
                LIMIT ? OFFSET ?
                """,
                (user_id, page_size, offset),
            ) as cur:
                rows = [dict(r) for r in await cur.fetchall()]
        return rows, total

    async def get_failed(
        self, user_id: Optional[int] = None, limit: int = 20
    ) -> list[dict]:
        sql = (
            "SELECT user_id, url, download_type, status, file_size, "
            "title, file_path, file_id, timestamp "
            "FROM history WHERE status = 'failed'"
        )
        params: list = []
        if user_id is not None:
            sql += " AND user_id = ?"
            params.append(user_id)
        sql += " ORDER BY timestamp DESC LIMIT ?"
        params.append(limit)
        async with self._db() as db:
            async with db.execute(sql, params) as cur:
                return [dict(r) for r in await cur.fetchall()]

    async def count_all(self) -> int:
        async with self._db() as db:
            async with db.execute("SELECT COUNT(*) FROM history") as cur:
                row = await cur.fetchone()
                return row[0] if row else 0

    async def get_recent_cached_urls(
        self, limit: int = 5, offset: int = 0
    ) -> tuple[list[dict], int]:
        async with self._db() as db:
            async with db.execute(
                """
                SELECT COUNT(DISTINCT url)
                FROM history
                WHERE status = 'success' AND file_id IS NOT NULL
                """
            ) as cur:
                total = (await cur.fetchone())[0]
            async with db.execute(
                """
                SELECT url, title, download_type, file_size,
                       MAX(timestamp) AS timestamp
                FROM history
                WHERE status = 'success' AND file_id IS NOT NULL
                GROUP BY url, download_type
                ORDER BY MAX(timestamp) DESC
                LIMIT ? OFFSET ?
                """,
                (limit, offset),
            ) as cur:
                rows = [dict(r) for r in await cur.fetchall()]
        return rows, total

    async def get_file_id_by_url(
        self,
        url: str,
        download_type: Optional[str] = None,
    ) -> Optional[str]:
        sql = """
            SELECT file_id FROM history
            WHERE url = ?
              AND status = 'success'
              AND file_id IS NOT NULL
        """
        params: list = [url]
        if download_type:
            sql += " AND download_type = ?"
            params.append(download_type)
        sql += " ORDER BY timestamp DESC LIMIT 1"
        async with self._db() as db:
            async with db.execute(sql, params) as cur:
                row = await cur.fetchone()
                return row[0] if row and row[0] else None

    async def get_file_id_and_title_by_url(
        self,
        url: str,
        download_type: Optional[str] = None,
    ) -> Optional[tuple[str, str | None]]:
        sql = """
            SELECT file_id, title FROM history
            WHERE url = ?
              AND status = 'success'
              AND file_id IS NOT NULL
        """
        params: list = [url]
        if download_type:
            sql += " AND download_type = ?"
            params.append(download_type)
        sql += " ORDER BY timestamp DESC LIMIT 1"
        async with self._db() as db:
            async with db.execute(sql, params) as cur:
                row = await cur.fetchone()
                if row and row[0]:
                    return row[0], row[1]
        return None
