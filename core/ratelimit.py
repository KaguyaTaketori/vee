import os
import time
import logging
import aiosqlite
from dataclasses import dataclass
from typing import Optional

from core.db import DB_PATH

logger = logging.getLogger(__name__)


def load_rate_limit() -> dict:
    config_path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "rate_limit_config.json")
    if os.path.exists(config_path):
        import json
        with open(config_path, "r") as f:
            return json.load(f)
    return {"max_downloads_per_hour": 10, "enabled": True}


def save_rate_limit(max_downloads: int, enabled: bool):
    config_path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "rate_limit_config.json")
    import json
    with open(config_path, "w") as f:
        json.dump({"max_downloads_per_hour": max_downloads, "enabled": enabled}, f)


class RateLimiter:
    def __init__(self):
        config = load_rate_limit()
        self.max_downloads_per_hour = config.get("max_downloads_per_hour", 10)
        self.enabled = config.get("enabled", True)

    def reload(self):
        config = load_rate_limit()
        self.max_downloads_per_hour = config.get("max_downloads_per_hour", 10)
        self.enabled = config.get("enabled", True)

    async def check_limit_async(self, user_id: int) -> tuple[bool, Optional[str]]:
        if not self.enabled:
            return True, None
            
        now = time.time()
        window = 3600
        
        async with aiosqlite.connect(DB_PATH) as db:
            await db.execute(
                "INSERT INTO rate_limit (user_id, timestamp) VALUES (?, ?)",
                (user_id, now)
            )
            await db.commit()
            
            await db.execute(
                "DELETE FROM rate_limit WHERE timestamp < ?",
                (now - window,)
            )
            await db.commit()
            
            async with db.execute(
                "SELECT COUNT(*) FROM rate_limit WHERE user_id = ? AND timestamp > ?",
                (user_id, now - window)
            ) as cursor:
                row = await cursor.fetchone()
                count = row[0] if row else 0
            
            if count >= self.max_downloads_per_hour:
                async with db.execute(
                    "SELECT timestamp FROM rate_limit WHERE user_id = ? ORDER BY timestamp ASC LIMIT 1",
                    (user_id,)
                ) as cursor:
                    row = await cursor.fetchone()
                    if row:
                        wait_time = int(window - (now - row[0]) + 60)
                        return False, f"Rate limit exceeded. Try again in {wait_time // 60} minutes."
                return False, "Rate limit exceeded. Try again later."
            
            return True, None

    def check_limit(self, user_id: int) -> tuple[bool, Optional[str]]:
        if not self.enabled:
            return True, None
            
        now = time.time()
        window = 3600
        
        import asyncio
        try:
            loop = asyncio.get_running_loop()
            return loop.run_until_complete(self.check_limit_async(user_id))
        except RuntimeError:
            return asyncio.run(self.check_limit_async(user_id))

    async def get_remaining_async(self, user_id: int) -> int:
        if not self.enabled:
            return 999
        now = time.time()
        window = 3600
        
        async with aiosqlite.connect(DB_PATH) as db:
            async with db.execute(
                "SELECT COUNT(*) FROM rate_limit WHERE user_id = ? AND timestamp > ?",
                (user_id, now - window)
            ) as cursor:
                row = await cursor.fetchone()
                count = row[0] if row else 0
        
        return max(0, self.max_downloads_per_hour - count)

    def get_remaining(self, user_id: int) -> int:
        if not self.enabled:
            return 999
        now = time.time()
        window = 3600
        
        import asyncio
        try:
            loop = asyncio.get_running_loop()
            return loop.run_until_complete(self.get_remaining_async(user_id))
        except RuntimeError:
            return asyncio.run(self.get_remaining_async(user_id))

    async def reset_async(self, user_id: int):
        async with aiosqlite.connect(DB_PATH) as db:
            await db.execute("DELETE FROM rate_limit WHERE user_id = ?", (user_id,))
            await db.commit()

    def reset(self, user_id: int):
        import asyncio
        try:
            loop = asyncio.get_running_loop()
            return loop.run_until_complete(self.reset_async(user_id))
        except RuntimeError:
            return asyncio.run(self.reset_async(user_id))

    def get_status(self) -> dict:
        return {
            "max_downloads_per_hour": self.max_downloads_per_hour,
            "enabled": self.enabled,
            "active_users": 0
        }


rate_limiter = RateLimiter()
