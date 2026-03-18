"""
services/bill_cache.py

账单临时缓存层。
默认使用带超时的内存字典（单进程）；
如需多进程/多实例共享，切换为 Redis 后端（见下方 RedisBillCache）。

GC 策略
-------
- 惰性删除：get() 时检查是否过期。
- 主动 GC：由 core/jobs.py 的 bill_cache_gc_job 通过 PTB job_queue 定期调用
  purge_expired()，与项目其他定时任务统一管理，无需自行维护后台协程。
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

# 缓存默认存活时间（秒）。
# 用户确认流程（解析 → 阅读 → 逐字段编辑）在网络慢或犹豫时容易超时，
# 15 分钟比原来的 5 分钟更宽松；每次用户编辑字段时会自动续期。
_DEFAULT_TTL = 900


# ---------------------------------------------------------------------------
# 账单数据模型
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
    bill_date: str                          # ISO 格式日期字符串
    raw_text: str = ""                      # 原始用户输入（用于审计）
    extra: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict) -> "BillEntry":
        return cls(**{k: v for k, v in d.items() if k in cls.__dataclass_fields__})


# ---------------------------------------------------------------------------
# 内存缓存（默认）
# ---------------------------------------------------------------------------

class InMemoryBillCache:
    """
    线程安全的内存缓存，带 TTL 自动过期。
    适用于单进程部署（PTB 的 asyncio 单线程模型）。
    """

    def __init__(self, default_ttl: int = _DEFAULT_TTL) -> None:
        """
        :param default_ttl: 缓存条目的默认存活时间（秒），默认 15 分钟。
        """
        self._default_ttl = default_ttl
        self._store: dict[str, tuple[BillEntry, float]] = {}  # cache_id -> (entry, expire_at)
        self._lock = asyncio.Lock()

    # ── CRUD ──────────────────────────────────────────────────────────────

    async def set(self, entry: BillEntry, ttl: Optional[int] = None) -> str:
        """存入账单，返回 cache_id。"""
        cache_id = str(uuid.uuid4())
        expire_at = time.monotonic() + (ttl or self._default_ttl)
        async with self._lock:
            self._store[cache_id] = (entry, expire_at)
        logger.debug("BillCache SET cache_id=%s user_id=%s", cache_id, entry.user_id)
        return cache_id

    async def get(self, cache_id: str) -> Optional[BillEntry]:
        """取出账单；过期或不存在返回 None。"""
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
        """
        更新现有条目（例如用户修改字段后）；返回是否成功。

        每次用户主动编辑都视为一次活跃操作，自动将 TTL 重置为 default_ttl，
        防止在反复编辑过程中因原始 TTL 耗尽而丢失账单。
        若调用方传入显式 ttl 则以该值为准。
        """
        async with self._lock:
            if cache_id not in self._store:
                return False
            # 用户有操作 → 续期；调用方可覆盖
            new_expire = time.monotonic() + (ttl if ttl is not None else self._default_ttl)
            self._store[cache_id] = (entry, new_expire)
        logger.debug("BillCache UPDATE cache_id=%s (TTL renewed)", cache_id)
        return True

    async def delete(self, cache_id: str) -> bool:
        """删除条目；返回是否实际删除了内容。"""
        async with self._lock:
            existed = cache_id in self._store
            self._store.pop(cache_id, None)
        logger.debug("BillCache DELETE cache_id=%s existed=%s", cache_id, existed)
        return existed

    async def purge_expired(self) -> int:
        """清理所有过期条目，返回清理数量（可作为定时任务调用）。"""
        now = time.monotonic()
        async with self._lock:
            expired = [k for k, (_, exp) in self._store.items() if now > exp]
            for k in expired:
                del self._store[k]
        if expired:
            logger.info("BillCache purged %d expired entries", len(expired))
        return len(expired)


# ---------------------------------------------------------------------------
# Redis 后端（多进程 / 多实例部署时使用）
# ---------------------------------------------------------------------------

class RedisBillCache:
    """
    基于 Redis 的账单缓存，支持多进程/多实例共享。

    依赖：pip install redis[asyncio]

    在 config/settings.py 中添加：
        REDIS_URL = os.getenv("REDIS_URL", "redis://localhost:6379/0")

    然后在 services/container.py 中初始化：
        from services.bill_cache import RedisBillCache
        bill_cache = RedisBillCache(redis_url=REDIS_URL)
    """

    _KEY_PREFIX = "bill_cache:"

    def __init__(self, redis_url: str, default_ttl: int = _DEFAULT_TTL) -> None:
        self._redis_url = redis_url
        self._default_ttl = default_ttl
        self._redis = None  # 延迟初始化

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
        logger.debug("RedisBillCache SET cache_id=%s", cache_id)
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
        if existing_ttl < 0:   # -2: key 不存在；-1: 无 TTL
            return False
        # 用户有操作 → 续期
        new_ttl = ttl if ttl is not None else self._default_ttl
        await r.setex(k, new_ttl, json.dumps(entry.to_dict()))
        logger.debug("RedisBillCache UPDATE cache_id=%s (TTL renewed)", cache_id)
        return True

    async def delete(self, cache_id: str) -> bool:
        r = await self._get_redis()
        deleted = await r.delete(self._key(cache_id))
        return deleted > 0

    async def purge_expired(self) -> int:
        # Redis 原生 TTL，无需手动清理
        return 0


# ---------------------------------------------------------------------------
# 全局单例（在 services/container.py 中初始化并注入）
# ---------------------------------------------------------------------------

# 默认使用内存缓存；多实例部署时替换为 RedisBillCache
bill_cache: InMemoryBillCache = InMemoryBillCache()
