"""
modules/billing/services/bill_cache.py
───────────────────────────────────────
In-memory (and optional Redis) cache for pending bill confirmations.

Fix: InMemoryBillCache.update() previously always reset TTL to
default_ttl, meaning a user who spent 14 minutes editing a bill would
suddenly lose their work at the 15-minute mark on the next edit.

New behaviour:
  - update(cache_id, entry)            → preserve remaining TTL
  - update(cache_id, entry, ttl=N)     → explicitly set new TTL
"""
from __future__ import annotations

import asyncio
import json
import logging
import time
import uuid
from dataclasses import dataclass, field, asdict
from typing import Optional

logger = logging.getLogger(__name__)

_DEFAULT_TTL = 900  # 15 minutes


# ---------------------------------------------------------------------------
# BillItem
# ---------------------------------------------------------------------------

@dataclass
class BillItem:
    name: str
    amount: float
    name_raw: str = ""
    quantity: float = 1.0
    unit_price: Optional[float] = None
    item_type: str = "item"
    sort_order: int = 0

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict) -> "BillItem":
        return cls(**{k: v for k, v in d.items() if k in cls.__dataclass_fields__})


# ---------------------------------------------------------------------------
# BillEntry
# ---------------------------------------------------------------------------

@dataclass
class BillEntry:
    user_id: int
    amount: float
    currency: str
    category: str
    description: str
    merchant: str
    bill_date: str
    raw_text: str = ""
    items: list[BillItem] = field(default_factory=list)
    receipt_file_id: str = ""
    extra: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict) -> "BillEntry":
        items_raw = d.pop("items", []) or []
        d.setdefault("receipt_file_id", "")
        obj = cls(**{k: v for k, v in d.items() if k in cls.__dataclass_fields__})
        obj.items = [BillItem.from_dict(i) for i in items_raw if isinstance(i, dict)]
        return obj


# ---------------------------------------------------------------------------
# InMemoryBillCache
# ---------------------------------------------------------------------------

class InMemoryBillCache:
    def __init__(self, default_ttl: int = _DEFAULT_TTL) -> None:
        self._default_ttl = default_ttl
        # value: (entry, absolute_expire_monotonic)
        self._store: dict[str, tuple[BillEntry, float]] = {}
        self._lock = asyncio.Lock()

    async def set(self, entry: BillEntry, ttl: Optional[int] = None) -> str:
        cache_id = str(uuid.uuid4())
        expire_at = time.monotonic() + (ttl or self._default_ttl)
        async with self._lock:
            self._store[cache_id] = (entry, expire_at)
        logger.debug("BillCache SET cache_id=%s user_id=%s", cache_id, entry.user_id)
        return cache_id

    async def get(self, cache_id: str) -> Optional[BillEntry]:
        async with self._lock:
            item = self._store.get(cache_id)
            if not item:
                return None
            entry, expire_at = item
            if time.monotonic() > expire_at:
                del self._store[cache_id]
                logger.debug("BillCache EXPIRED cache_id=%s", cache_id)
                return None
            return entry

    async def update(
        self,
        cache_id: str,
        entry: BillEntry,
        ttl: Optional[int] = None,
    ) -> bool:
        """
        Update the entry in the cache.

        TTL behaviour:
          - ttl=None  → preserve the original expiry time (don't punish
                        users for editing their bill)
          - ttl=N     → set a brand-new TTL of N seconds from now
        """
        async with self._lock:
            existing = self._store.get(cache_id)
            if existing is None:
                return False
            _, original_expire_at = existing
            new_expire_at = (
                time.monotonic() + ttl      # explicit override
                if ttl is not None
                else original_expire_at     # ← preserve remaining time
            )
            self._store[cache_id] = (entry, new_expire_at)
        logger.debug(
            "BillCache UPDATE cache_id=%s ttl_override=%s", cache_id, ttl
        )
        return True

    async def delete(self, cache_id: str) -> bool:
        async with self._lock:
            existed = cache_id in self._store
            self._store.pop(cache_id, None)
        return existed

    async def purge_expired(self) -> int:
        now = time.monotonic()
        async with self._lock:
            expired = [k for k, (_, exp) in self._store.items() if now > exp]
            for k in expired:
                del self._store[k]
        if expired:
            logger.info("BillCache purged %d expired entries", len(expired))
        return len(expired)


# ---------------------------------------------------------------------------
# RedisBillCache  (unchanged — Redis TTL is always explicit via SETEX)
# ---------------------------------------------------------------------------

class RedisBillCache:
    _KEY_PREFIX = "bill_cache:"

    def __init__(self, redis_url: str, default_ttl: int = _DEFAULT_TTL) -> None:
        self._redis_url = redis_url
        self._default_ttl = default_ttl
        self._redis = None

    async def _get_redis(self):
        if self._redis is None:
            import redis.asyncio as aioredis
            self._redis = await aioredis.from_url(self._redis_url, decode_responses=True)
        return self._redis

    def _key(self, cache_id: str) -> str:
        return f"{self._KEY_PREFIX}{cache_id}"

    async def set(self, entry: BillEntry, ttl: Optional[int] = None) -> str:
        cache_id = str(uuid.uuid4())
        r = await self._get_redis()
        await r.setex(self._key(cache_id), ttl or self._default_ttl, json.dumps(entry.to_dict()))
        return cache_id

    async def get(self, cache_id: str) -> Optional[BillEntry]:
        r = await self._get_redis()
        raw = await r.get(self._key(cache_id))
        if not raw:
            return None
        try:
            return BillEntry.from_dict(json.loads(raw))
        except Exception as e:
            logger.error("RedisBillCache GET parse error cache_id=%s: %s", cache_id, e)
            return None

    async def update(
        self,
        cache_id: str,
        entry: BillEntry,
        ttl: Optional[int] = None,
    ) -> bool:
        r = await self._get_redis()
        k = self._key(cache_id)
        existing_ttl = await r.ttl(k)
        if existing_ttl < 0:
            return False
        # Preserve remaining TTL unless explicitly overridden
        new_ttl = ttl if ttl is not None else existing_ttl
        await r.setex(k, new_ttl, json.dumps(entry.to_dict()))
        return True

    async def delete(self, cache_id: str) -> bool:
        r = await self._get_redis()
        return await r.delete(self._key(cache_id)) > 0

    async def purge_expired(self) -> int:
        return 0  # Redis handles expiry natively


# ---------------------------------------------------------------------------
# Global singleton
# ---------------------------------------------------------------------------

bill_cache: InMemoryBillCache = InMemoryBillCache()
