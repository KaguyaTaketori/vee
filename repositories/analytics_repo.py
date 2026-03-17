# repositories/analytics_repo.py
"""
AnalyticsRepository
-------------------
Owns every SQL statement used for reporting and statistics.
Previously this SQL lived inline inside services/analytics.py.
"""

import time
import logging
from typing import Optional

from .base import BaseRepository

logger = logging.getLogger(__name__)


class AnalyticsRepository(BaseRepository):

    async def get_daily_stats(self, days: int = 1) -> dict:
        """
        Return aggregated download statistics for the last *days* day(s).
        Replaces the inline SQL block in services/analytics.get_daily_stats().
        """
        since = time.time() - days * 86400

        async with self._db() as db:
            async with db.execute(
                "SELECT COUNT(*) FROM history WHERE timestamp > ?", (since,)
            ) as cur:
                total = (await cur.fetchone())[0]

            async with db.execute(
                "SELECT status, COUNT(*) FROM history WHERE timestamp > ? GROUP BY status",
                (since,),
            ) as cur:
                status_counts = {r[0]: r[1] for r in await cur.fetchall()}

            async with db.execute(
                "SELECT COUNT(DISTINCT user_id) FROM history WHERE timestamp > ?",
                (since,),
            ) as cur:
                active_users = (await cur.fetchone())[0]

            async with db.execute(
                "SELECT download_type, COUNT(*) FROM history WHERE timestamp > ? GROUP BY download_type",
                (since,),
            ) as cur:
                type_dist = {r[0]: r[1] for r in await cur.fetchall()}

            async with db.execute(
                """
                SELECT u.username, u.first_name, h.user_id, COUNT(*) AS cnt
                FROM history h
                LEFT JOIN users u ON h.user_id = u.user_id
                WHERE h.timestamp > ?
                GROUP BY h.user_id
                ORDER BY cnt DESC
                LIMIT 5
                """,
                (since,),
            ) as cur:
                top_users = await cur.fetchall()

            async with db.execute(
                "SELECT SUM(file_size) FROM history WHERE timestamp > ? AND status = 'success'",
                (since,),
            ) as cur:
                total_bytes = (await cur.fetchone())[0] or 0

        return {
            "total": total,
            "success": status_counts.get("success", 0),
            "failed": status_counts.get("failed", 0),
            "active_users": active_users,
            "type_dist": type_dist,
            "top_users": top_users,
            "total_bytes": total_bytes,
        }

    async def get_summary_stats(self) -> dict:
        """
        High-level totals used by the /stats admin command.
        Replaces the inline SQL in utils/logger.get_user_stats() and
        services/analytics.get_bot_stats().
        """
        async with self._db() as db:
            async with db.execute("SELECT COUNT(*) FROM users") as cur:
                total_users = (await cur.fetchone())[0]

            async with db.execute("SELECT COUNT(*) FROM history") as cur:
                total_downloads = (await cur.fetchone())[0]

            async with db.execute(
                "SELECT user_id, url, download_type, status, file_size, title, "
                "file_path, file_id, timestamp "
                "FROM history WHERE status = 'failed' ORDER BY timestamp DESC LIMIT 5"
            ) as cur:
                recent_failures = [dict(r) for r in await cur.fetchall()]

        return {
            "total_users": total_users,
            "total_downloads": total_downloads,
            "recent_failures": recent_failures,
        }
