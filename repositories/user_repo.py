# repositories/user_repo.py
"""
UserRepository
--------------
Owns every SQL statement for the `users` and `user_rate_tiers` tables.
"""

import time
import logging
from typing import Optional

from shared.repositories.base import BaseRepository

logger = logging.getLogger(__name__)


class UserRepository(BaseRepository):

    # ------------------------------------------------------------------ users

    async def get(self, user_id: int) -> dict:
        async with self._db() as db:
            async with db.execute(
                """
                SELECT user_id, username, first_name, last_name,
                       lang, added_at, last_seen
                FROM users WHERE user_id = ?
                """,
                (user_id,),
            ) as cur:
                row = await cur.fetchone()
                return dict(row) if row else {}

    async def get_lang(self, user_id: int) -> str:
        async with self._db() as db:
            async with db.execute(
                "SELECT lang FROM users WHERE user_id = ?", (user_id,)
            ) as cur:
                row = await cur.fetchone()
                return row[0] if row and row[0] else "en"

    async def upsert(
        self,
        user_id: int,
        username: Optional[str] = None,
        first_name: Optional[str] = None,
        last_name: Optional[str] = None,
        lang: str = "en",
    ) -> None:
        now = time.time()
        async with self._db() as db:
            await db.execute(
                """
                INSERT INTO users (user_id, username, first_name, last_name, lang, added_at, last_seen)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(user_id) DO UPDATE SET
                    username   = excluded.username,
                    first_name = excluded.first_name,
                    last_name  = excluded.last_name,
                    last_seen  = excluded.last_seen
                """,
                (user_id, username, first_name, last_name, lang, now, now),
            )
            await db.commit()

    async def set_lang(self, user_id: int, lang: str) -> None:
        now = time.time()
        async with self._db() as db:
            await db.execute(
                """
                INSERT INTO users (user_id, lang, added_at, last_seen)
                VALUES (?, ?, ?, ?)
                ON CONFLICT(user_id) DO UPDATE SET
                    lang      = excluded.lang,
                    last_seen = excluded.last_seen
                """,
                (user_id, lang, now, now),
            )
            await db.commit()

    async def touch(self, user_id: int) -> None:
        """Update last_seen to now without touching other columns."""
        async with self._db() as db:
            await db.execute(
                "UPDATE users SET last_seen = ? WHERE user_id = ?",
                (time.time(), user_id),
            )
            await db.commit()

    async def count_all(self) -> int:
        async with self._db() as db:
            async with db.execute("SELECT COUNT(*) FROM users") as cur:
                row = await cur.fetchone()
                return row[0] if row else 0

    async def get_all(self) -> list[dict]:
        async with self._db() as db:
            async with db.execute(
                "SELECT user_id, username, first_name, last_name, lang, added_at, last_seen FROM users"
            ) as cur:
                return [dict(r) for r in await cur.fetchall()]

    # -------------------------------------------------------- user_rate_tiers

    async def get_tier_row(self, user_id: int) -> Optional[dict]:
        """Return the full tier row for *user_id*, or None."""
        async with self._db() as db:
            async with db.execute(
                "SELECT user_id, tier, max_per_hour, note, set_by, set_at FROM user_rate_tiers WHERE user_id = ?",
                (user_id,),
            ) as cur:
                row = await cur.fetchone()
                return dict(row) if row else None

    async def get_tier(self, user_id: int) -> str:
        async with self._db() as db:
            async with db.execute(
                "SELECT tier FROM user_rate_tiers WHERE user_id = ?", (user_id,)
            ) as cur:
                row = await cur.fetchone()
                return row[0] if row else "normal"

    async def get_tier_and_limit(self, user_id: int) -> Optional[tuple[str, Optional[int]]]:
        """Return (tier, max_per_hour) or None if no row exists."""
        async with self._db() as db:
            async with db.execute(
                "SELECT tier, max_per_hour FROM user_rate_tiers WHERE user_id = ?",
                (user_id,),
            ) as cur:
                row = await cur.fetchone()
                return (row[0], row[1]) if row else None

    async def set_tier(
        self,
        user_id: int,
        tier: str,
        note: str = "",
        set_by: Optional[int] = None,
        custom_max: Optional[int] = None,
    ) -> None:
        async with self._db() as db:
            await db.execute(
                """
                INSERT INTO user_rate_tiers
                    (user_id, tier, max_per_hour, note, set_by, set_at)
                VALUES (?, ?, ?, ?, ?, ?)
                ON CONFLICT(user_id) DO UPDATE SET
                    tier         = excluded.tier,
                    max_per_hour = excluded.max_per_hour,
                    note         = excluded.note,
                    set_by       = excluded.set_by,
                    set_at       = excluded.set_at
                """,
                (user_id, tier, custom_max, note, set_by, time.time()),
            )
            await db.commit()
