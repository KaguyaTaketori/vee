"""
modules/billing/services/bill_cache.py

变更说明：
1. BillEntry 新增 receipt_tmp_path（临时文件标识）和 receipt_url（正式 URL）
2. InMemoryBillCache.purge_expired() 过期时联动删除临时文件
3. RedisBillCache.update() 保留原有 TTL 逻辑不变
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
    receipt_file_id: str = ""      # Telegram file_id（Bot 侧保留）
    receipt_tmp_path: str = ""     # 临时文件标识，如 "pending/xxx.jpg"
    receipt_url: str = ""          # 正式公开 URL，确认后写入
    extra: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict) -> "BillEntry":
        items_raw = d.pop("items", []) or []
        d.setdefault("receipt_file_id", "")
        d.setdefault("receipt_tmp_path", "")
        d.setdefault("receipt_url", "")
        obj = cls(**{k: v for k, v in d.items() if k in cls.__dataclass_fields__})
        obj.items = [BillItem.from_dict(i) for i in items_raw if isinstance(i, dict)]
        return obj


# ---------------------------------------------------------------------------
# InMemoryBillCache
# ---------------------------------------------------------------------------

class InMemoryBillCache:
    def __init__(self, default_ttl: int = _DEFAULT_TTL) -> None:
        self._default_ttl = default_ttl
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
        async with self._lock:
            existing = self._store.get(cache_id)
            if existing is None:
                return False
            _, original_expire_at = existing
            new_expire_at = (
                time.monotonic() + ttl
                if ttl is not None
                else original_expire_at
            )
            self._store[cache_id] = (entry, new_expire_at)
        logger.debug("BillCache UPDATE cache_id=%s ttl_override=%s", cache_id, ttl)
        return True

    async def delete(self, cache_id: str) -> bool:
        async with self._lock:
            existed = cache_id in self._store
            entry_tuple = self._store.pop(cache_id, None)

        # 删除时同步清理临时文件
        if entry_tuple:
            await self._cleanup_tmp(entry_tuple[0])

        return existed

    async def purge_expired(self) -> int:
        now = time.monotonic()
        expired_entries: list[BillEntry] = []

        async with self._lock:
            expired_keys = [k for k, (_, exp) in self._store.items() if now > exp]
            for k in expired_keys:
                entry, _ = self._store.pop(k)
                expired_entries.append(entry)

        # 锁外清理临时文件，避免阻塞
        for entry in expired_entries:
            await self._cleanup_tmp(entry)

        if expired_entries:
            logger.info("BillCache purged %d expired entries", len(expired_entries))
        return len(expired_entries)

    @staticmethod
    async def _cleanup_tmp(entry: BillEntry) -> None:
        """清理 entry 关联的临时文件。"""
        if not entry.receipt_tmp_path:
            return
        try:
            from shared.services.container import services
            if services.receipt_storage is not None:
                await services.receipt_storage.delete_tmp(entry.receipt_tmp_path)
        except Exception as e:
            logger.warning("BillCache: failed to cleanup tmp file %s: %s",
                           entry.receipt_tmp_path, e)


# ---------------------------------------------------------------------------
# RedisBillCache（预留，逻辑不变，补充新字段的兼容）
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
        new_ttl = ttl if ttl is not None else existing_ttl
        await r.setex(k, new_ttl, json.dumps(entry.to_dict()))
        return True

    async def delete(self, cache_id: str) -> bool:
        r = await self._get_redis()
        # Redis 方案下临时文件同样需要清理
        entry = await self.get(cache_id)
        if entry:
            from shared.services.container import services
            if services.receipt_storage and entry.receipt_tmp_path:
                await services.receipt_storage.delete_tmp(entry.receipt_tmp_path)
        return await r.delete(self._key(cache_id)) > 0

    async def purge_expired(self) -> int:
        return 0  # Redis 原生处理过期


# ---------------------------------------------------------------------------
# Global singleton
# ---------------------------------------------------------------------------

bill_cache: InMemoryBillCache = InMemoryBillCache()
