# repositories/rate_limit_repo.py
"""
RateLimitRepository
-------------------
Owns every SQL statement for the `rate_limit` table.
The check-and-insert operation is kept atomic via BEGIN IMMEDIATE so that
two concurrent coroutines cannot both pass the limit check.
"""

import time
import logging
from typing import Optional

from shared.repositories.base import BaseRepository

logger = logging.getLogger(__name__)

_WINDOW_SECONDS = 3600  # 1 hour sliding window


class RateLimitRepository(BaseRepository):

    async def count_in_window(self, user_id: int) -> int:
        """Return how many events the user has in the current window."""
        since = time.time() - _WINDOW_SECONDS
        async with self._db() as db:
            async with db.execute(
                "SELECT COUNT(*) FROM rate_limit WHERE user_id = ? AND timestamp > ?",
                (user_id, since),
            ) as cur:
                row = await cur.fetchone()
                return row[0] if row else 0

    async def oldest_in_window(self, user_id: int) -> Optional[float]:
        """Return the timestamp of the oldest event in the current window."""
        since = time.time() - _WINDOW_SECONDS
        async with self._db() as db:
            async with db.execute(
                "SELECT MIN(timestamp) FROM rate_limit WHERE user_id = ? AND timestamp > ?",
                (user_id, since),
            ) as cur:
                row = await cur.fetchone()
                return row[0] if row else None

    async def purge_expired(self) -> None:
        """Delete all events older than the window (maintenance helper)."""
        since = time.time() - _WINDOW_SECONDS
        async with self._db() as db:
            await db.execute(
                "DELETE FROM rate_limit WHERE timestamp < ?", (since,)
            )
            await db.commit()

    async def record_and_check(
        self, user_id: int, user_max: int
    ) -> tuple[bool, Optional[str]]:
        """
        Atomically:
          1. Purge expired events.
          2. Count current events for user.
          3. If under limit → insert event, return (True, None).
          4. If at/over limit → return (False, wait_message) without inserting.

        Must be called only when user_max > 0 and user is not unlimited.
        """
        now = time.time()
        since = now - _WINDOW_SECONDS

        async with self._db() as db:
            await db.execute("BEGIN IMMEDIATE")
            try:
                # 1. Purge
                await db.execute(
                    "DELETE FROM rate_limit WHERE timestamp < ?", (since,)
                )
                # 2. Count
                async with db.execute(
                    "SELECT COUNT(*) FROM rate_limit WHERE user_id = ? AND timestamp > ?",
                    (user_id, since),
                ) as cur:
                    count = (await cur.fetchone())[0]

                if count >= user_max:
                    # Find when the oldest slot opens up
                    async with db.execute(
                        "SELECT MIN(timestamp) FROM rate_limit WHERE user_id = ? AND timestamp > ?",
                        (user_id, since),
                    ) as cur:
                        oldest = (await cur.fetchone())[0] or now
                    await db.execute("ROLLBACK")
                    remaining = int(oldest + _WINDOW_SECONDS - now)
                    return False, f"Rate limit exceeded. Try again in {remaining}s."

                # 3. Insert
                await db.execute(
                    "INSERT INTO rate_limit (user_id, timestamp) VALUES (?, ?)",
                    (user_id, now),
                )
                await db.commit()
                return True, None

            except Exception:
                await db.execute("ROLLBACK")
                raise

    async def remaining(self, user_id: int, user_max: int) -> int:
        count = await self.count_in_window(user_id)
        return max(0, user_max - count)

    async def reset(self, user_id: int) -> None:
        async with self._db() as db:
            await db.execute(
                "DELETE FROM rate_limit WHERE user_id = ?", (user_id,)
            )
            await db.commit()
