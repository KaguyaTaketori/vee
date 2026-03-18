"""
services/bill_cache.py

变更说明（items 支持版本）：
1. BillEntry 新增 items: list[BillItem] 字段，用于存储收据商品明细
2. BillItem dataclass：name / name_raw / quantity / unit_price / amount / item_type / sort_order
3. to_dict / from_dict 完整支持 items 序列化，兼容旧数据（无 items 字段时默认 []）
4. 缓存逻辑不变，InMemoryBillCache / RedisBillCache 均透明支持
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

_DEFAULT_TTL = 900  # 15 分钟


# ---------------------------------------------------------------------------
# 商品明细模型
# ---------------------------------------------------------------------------

@dataclass
class BillItem:
    """
    收据中的单行商品/折扣/税费。

    item_type 枚举：
        'item'     — 普通商品
        'discount' — 折扣（amount 为负数）
        'tax'      — 税费
        'subtotal' — 小计行（一般不入库，仅供展示）
    """
    name: str                          # 商品名（翻译为中文）
    amount: float                      # 该行金额；折扣为负数
    name_raw: str = ""                 # 原始文字（日文等）
    quantity: float = 1.0
    unit_price: Optional[float] = None
    item_type: str = "item"            # item | discount | tax | subtotal
    sort_order: int = 0                # 与收据顺序对应

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict) -> "BillItem":
        return cls(**{k: v for k, v in d.items() if k in cls.__dataclass_fields__})


# ---------------------------------------------------------------------------
# 账单主模型
# ---------------------------------------------------------------------------

@dataclass
class BillEntry:
    """AI 解析出的账单临时数据。"""
    user_id: int
    amount: float
    currency: str
    category: str
    description: str
    merchant: str
    bill_date: str                              # ISO 格式 YYYY-MM-DD
    raw_text: str = ""                                    # 原始用户输入（审计用）
    items: list[BillItem] = field(default_factory=list)   # 商品明细
    receipt_file_id: str = ""                             # Telegram file_id（图片凭证）
    extra: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict) -> "BillEntry":
        items_raw = d.pop("items", []) or []
        d.setdefault("receipt_file_id", "")               # 兼容旧数据
        obj = cls(**{k: v for k, v in d.items() if k in cls.__dataclass_fields__})
        obj.items = [BillItem.from_dict(i) for i in items_raw if isinstance(i, dict)]
        return obj


# ---------------------------------------------------------------------------
# 内存缓存（默认，单进程）
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

    async def update(self, cache_id: str, entry: BillEntry, ttl: Optional[int] = None) -> bool:
        async with self._lock:
            if cache_id not in self._store:
                return False
            new_expire = time.monotonic() + (ttl if ttl is not None else self._default_ttl)
            self._store[cache_id] = (entry, new_expire)
        logger.debug("BillCache UPDATE cache_id=%s (TTL renewed)", cache_id)
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
# Redis 后端（多实例部署时使用）
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
        await r.setex(
            self._key(cache_id),
            ttl or self._default_ttl,
            json.dumps(entry.to_dict()),
        )
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

    async def update(self, cache_id: str, entry: BillEntry, ttl: Optional[int] = None) -> bool:
        r = await self._get_redis()
        k = self._key(cache_id)
        existing_ttl = await r.ttl(k)
        if existing_ttl < 0:
            return False
        new_ttl = ttl if ttl is not None else self._default_ttl
        await r.setex(k, new_ttl, json.dumps(entry.to_dict()))
        return True

    async def delete(self, cache_id: str) -> bool:
        r = await self._get_redis()
        deleted = await r.delete(self._key(cache_id))
        return deleted > 0

    async def purge_expired(self) -> int:
        return 0


# ---------------------------------------------------------------------------
# 全局单例
# ---------------------------------------------------------------------------

bill_cache: InMemoryBillCache = InMemoryBillCache()
