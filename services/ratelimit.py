import os
import time
import json
import asyncio
import logging
import aiosqlite
from dataclasses import dataclass
from typing import Optional
from config import RATE_TIER_LIMITS, ADMIN_IDS, BASE_DIR
from database.db import get_db

logger = logging.getLogger(__name__)

_RATE_LIMIT_CONFIG_FILE = os.path.join(BASE_DIR, "rate_limit_config.json")
RATE_UNLIMITED = -1

def load_rate_limit() -> dict:
    if os.path.exists(_RATE_LIMIT_CONFIG_FILE):
        with open(_RATE_LIMIT_CONFIG_FILE, "r") as f:
            return json.load(f)
    return {"max_downloads_per_hour": 10, "enabled": True}


def save_rate_limit(max_downloads: int, enabled: bool):
    with open(_RATE_LIMIT_CONFIG_FILE, "w") as f:
        json.dump({"max_downloads_per_hour": max_downloads, "enabled": enabled}, f)

async def get_user_tier(user_id: int) -> str:
    """查询用户等级，默认 normal。"""
    async with get_db() as db:
        async with db.execute(
            "SELECT tier FROM user_rate_tiers WHERE user_id = ?", (user_id,)
        ) as cur:
            row = await cur.fetchone()
            return row[0] if row else "normal"


async def get_user_limit(user_id: int) -> int:
    if ADMIN_IDS and user_id in ADMIN_IDS:
        return RATE_UNLIMITED

    async with get_db() as db:
        async with db.execute(
            "SELECT tier, max_per_hour FROM user_rate_tiers WHERE user_id = ?",
            (user_id,),
        ) as cur:
            row = await cur.fetchone()

    if row:
        tier, custom_max = row
        if custom_max is not None:
            return custom_max                       
        return RATE_TIER_LIMITS.get(tier, RATE_TIER_LIMITS["normal"])

    return RATE_TIER_LIMITS["normal"]


async def set_user_tier(user_id: int, tier: str, note: str = "", set_by: int = None, custom_max: int = None):
    async with get_db() as db:
        await db.execute(
            """
            INSERT INTO user_rate_tiers (user_id, tier, max_per_hour, note, set_by, set_at)
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


class RateLimiter:
    def __init__(self):
        config = load_rate_limit()
        self.max_downloads_per_hour = config.get("max_downloads_per_hour", 10)
        self.enabled = config.get("enabled", True)

    def reload(self):
        config = load_rate_limit()
        self.max_downloads_per_hour = config.get("max_downloads_per_hour", 10)
        self.enabled = config.get("enabled", True)

    async def check_limit(self, user_id: int) -> tuple[bool, Optional[str]]:
        if not self.enabled:
            return True, None

        user_max = await get_user_limit(user_id)

        if user_max == RATE_UNLIMITED:
            return True, None
        if user_max == 0:
            return False, "Your account has been suspended."

        now = time.time()
        window = 3600

        async with get_db() as db:
            await db.execute("BEGIN IMMEDIATE")
            try:
                await db.execute(
                    "DELETE FROM rate_limit WHERE timestamp < ?", (now - window,)
                )
                async with db.execute(
                    "SELECT COUNT(*) FROM rate_limit WHERE user_id = ? AND timestamp > ?",
                    (user_id, now - window),
                ) as cursor:
                    row = await cursor.fetchone()
                    count = row[0] if row else 0

                if count >= user_max:
                    await db.execute("ROLLBACK")
                    async with db.execute(
                        "SELECT MIN(timestamp) FROM rate_limit WHERE user_id = ? AND timestamp > ?",
                        (user_id, now - window)
                    ) as cur:
                        oldest = (await cur.fetchone())[0] or now
                    remaining_secs = int(oldest + window - now)
                    return False, f"Rate limit exceeded. Try again in {remaining_secs}s."

                await db.execute(
                    "INSERT INTO rate_limit (user_id, timestamp) VALUES (?, ?)",
                    (user_id, now),
                )
                await db.commit()
                return True, None
            except Exception:
                await db.execute("ROLLBACK")
                raise

    async def reset(self, user_id: int):
        async with get_db() as db:
            await db.execute("DELETE FROM rate_limit WHERE user_id = ?", (user_id,))
            await db.commit()

    def get_status(self) -> dict:
        return {
            "max_downloads_per_hour": self.max_downloads_per_hour,
            "enabled": self.enabled,
            "active_users": 0
        }

    async def get_remaining(self, user_id: int) -> int:
        user_max = await get_user_limit(user_id)
        if user_max == RATE_UNLIMITED:
            return 999
        if user_max == 0:
            return 0
        now = time.time()
        async with get_db() as db:
            async with db.execute(
                "SELECT COUNT(*) FROM rate_limit WHERE user_id = ? AND timestamp > ?",
                (user_id, now - 3600),
            ) as cur:
                row = await cur.fetchone()
                count = row[0] if row else 0
        return max(0, user_max - count)


rate_limiter = RateLimiter()
