# services/ratelimit.py
"""
Rate-limit service
------------------
Business logic only.  All SQL now lives in:
  - repositories.RateLimitRepository  (rate_limit table)
  - repositories.UserRepository       (user_rate_tiers table)

This module never imports get_db or writes SELECT / INSERT directly.
"""

import os
import json
import logging
from typing import Optional

from config import RATE_TIER_LIMITS, ADMIN_IDS, BASE_DIR
from repositories import RateLimitRepository, UserRepository

logger = logging.getLogger(__name__)

_RATE_LIMIT_CONFIG_FILE = os.path.join(BASE_DIR, "rate_limit_config.json")
RATE_UNLIMITED = -1

# ---------------------------------------------------------------------------
# Config file helpers (JSON, unchanged)
# ---------------------------------------------------------------------------

def load_rate_limit() -> dict:
    if os.path.exists(_RATE_LIMIT_CONFIG_FILE):
        with open(_RATE_LIMIT_CONFIG_FILE, "r") as f:
            return json.load(f)
    return {"max_downloads_per_hour": 10, "enabled": True}


def save_rate_limit(max_downloads: int, enabled: bool) -> None:
    with open(_RATE_LIMIT_CONFIG_FILE, "w") as f:
        json.dump({"max_downloads_per_hour": max_downloads, "enabled": enabled}, f)


# ---------------------------------------------------------------------------
# Thin helpers – delegate DB work to repositories
# ---------------------------------------------------------------------------

async def get_user_tier(user_id: int) -> str:
    """Return the user's tier string (default: 'normal')."""
    return await UserRepository().get_tier(user_id)


async def get_user_limit(user_id: int) -> int:
    """
    Return the effective per-hour download cap for *user_id*.
    RATE_UNLIMITED (-1) means no cap; 0 means suspended.
    """
    if ADMIN_IDS and user_id in ADMIN_IDS:
        return RATE_UNLIMITED

    row = await UserRepository().get_tier_and_limit(user_id)
    if row:
        tier, custom_max = row
        if custom_max is not None:
            return custom_max
        return RATE_TIER_LIMITS.get(tier, RATE_TIER_LIMITS["normal"])

    return RATE_TIER_LIMITS["normal"]


async def set_user_tier(
    user_id: int,
    tier: str,
    note: str = "",
    set_by: Optional[int] = None,
    custom_max: Optional[int] = None,
) -> None:
    await UserRepository().set_tier(
        user_id, tier, note=note, set_by=set_by, custom_max=custom_max
    )


# ---------------------------------------------------------------------------
# RateLimiter class
# ---------------------------------------------------------------------------

class RateLimiter:
    def __init__(self) -> None:
        self._rl_repo = RateLimitRepository()
        self._user_repo = UserRepository()
        config = load_rate_limit()
        self.max_downloads_per_hour: int = config.get("max_downloads_per_hour", 10)
        self.enabled: bool = config.get("enabled", True)

    def reload(self) -> None:
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

        return await self._rl_repo.record_and_check(user_id, user_max)

    async def reset(self, user_id: int) -> None:
        await self._rl_repo.reset(user_id)

    def get_status(self) -> dict:
        return {
            "max_downloads_per_hour": self.max_downloads_per_hour,
            "enabled": self.enabled,
            "active_users": 0,
        }

    async def get_remaining(self, user_id: int) -> int:
        user_max = await get_user_limit(user_id)
        if user_max == RATE_UNLIMITED:
            return 999
        if user_max == 0:
            return 0
        return await self._rl_repo.remaining(user_id, user_max)


# ---------------------------------------------------------------------------
# NOTE: The module-level singleton `rate_limiter = RateLimiter()` that
# previously lived here has been removed.  A single instance is created in
# main.py and stored in services.container.services.limiter.
# ---------------------------------------------------------------------------
