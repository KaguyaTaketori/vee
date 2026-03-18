"""
services/middleware.py
──────────────────────
Middleware / 责任链 Pipeline.

用法
----
在 Handler 入口处，把 update.message（或 query）包装成 RequestContext，
然后让 Pipeline 顺序执行每个中间件。任何一个中间件返回 MiddlewareResult.stop()
都会短路后续逻辑，同时把错误的 i18n key 带回来。

    ctx = RequestContext(user=update.message.from_user, reply=update.message.reply_text)
    result = await pipeline.run(ctx)
    if not result.ok:
        await update.message.reply_text(t(result.error_key, ctx.user_id))
        return

架构
----
                ┌─────────────────────────────────┐
  Handler ──▶  │  Pipeline.run(RequestContext)    │
                │  ┌────────────────────────────┐  │
                │  │  AuthMiddleware            │  │
                │  ├────────────────────────────┤  │
                │  │  RateLimitMiddleware       │  │
                │  └────────────────────────────┘  │
                └────────────┬────────────────────┘
                             │ ok=True
                             ▼
                  Facade / Service 分发
"""

from __future__ import annotations

import logging
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Callable, Awaitable, Any

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Request context – 传递给每一层中间件的轻量载体
# ---------------------------------------------------------------------------

@dataclass
class RequestContext:
    """所有中间件共享的请求上下文。

    Parameters
    ----------
    user:
        telegram.User 对象（或任何具有 .id 属性的对象）。
    reply:
        一个 async callable，签名为 (text: str) -> None。
        中间件用它向用户回复错误；调用方可传入
        update.message.reply_text 或 query.edit_message_text 等。
    meta:
        可扩展的键值袋，供中间件之间传递额外信息。
    """

    user: Any
    reply: Callable[[str], Awaitable[None]]
    meta: dict = field(default_factory=dict)

    @property
    def user_id(self) -> int:
        return self.user.id


# ---------------------------------------------------------------------------
# Pipeline 结果
# ---------------------------------------------------------------------------

@dataclass
class MiddlewareResult:
    """中间件链执行结果。"""

    ok: bool
    error_key: str | None = None        # i18n key，仅当 ok=False 时有意义

    # ── 工厂方法，让调用方语义更清晰 ──────────────────────────────────────

    @classmethod
    def proceed(cls) -> "MiddlewareResult":
        """所有中间件通过，继续执行业务逻辑。"""
        return cls(ok=True)

    @classmethod
    def stop(cls, error_key: str) -> "MiddlewareResult":
        """中间件拦截请求，终止后续处理。"""
        return cls(ok=False, error_key=error_key)


# ---------------------------------------------------------------------------
# 抽象中间件基类
# ---------------------------------------------------------------------------

class Middleware(ABC):
    """责任链中的一个节点。

    子类实现 ``process()``：
    - 返回 ``MiddlewareResult.proceed()`` 表示放行；
    - 返回 ``MiddlewareResult.stop(key)`` 表示拦截并携带错误 key。
    """

    @abstractmethod
    async def process(self, ctx: RequestContext) -> MiddlewareResult:
        ...


# ---------------------------------------------------------------------------
# AuthMiddleware
# ---------------------------------------------------------------------------

class AuthMiddleware(Middleware):
    """鉴权中间件：检查用户是否在白名单内。

    依赖 ``utils.utils.is_user_allowed``（纯函数，无 Telegram 耦合）。
    """

    async def process(self, ctx: RequestContext) -> MiddlewareResult:
        from utils.utils import is_user_allowed

        if not is_user_allowed(ctx.user_id):
            logger.warning("Unauthorized access attempt by user %s", ctx.user_id)
            return MiddlewareResult.stop("not_authorized")

        return MiddlewareResult.proceed()


# ---------------------------------------------------------------------------
# RateLimitMiddleware
# ---------------------------------------------------------------------------

class RateLimitMiddleware(Middleware):
    """限流中间件：调用 services.limiter 检查用户配额。

    懒加载 services，避免循环导入。
    """

    async def process(self, ctx: RequestContext) -> MiddlewareResult:
        from shared.services.container import services

        can_proceed, reason = await services.limiter.check_limit(ctx.user_id)
        if not can_proceed:
            logger.warning(
                "User %s blocked by rate limit: %s", ctx.user_id, reason
            )
            return MiddlewareResult.stop("rate_limit_exceeded")

        return MiddlewareResult.proceed()


# ---------------------------------------------------------------------------
# Pipeline
# ---------------------------------------------------------------------------

class MiddlewarePipeline:
    """顺序执行中间件列表，任一节点返回 stop() 则短路。

    Example
    -------
    ::

        pipeline = MiddlewarePipeline([
            AuthMiddleware(),
            RateLimitMiddleware(),
        ])

        result = await pipeline.run(ctx)
        if not result.ok:
            await ctx.reply(t(result.error_key, ctx.user_id))
            return
    """

    def __init__(self, middlewares: list[Middleware] | None = None) -> None:
        self._middlewares: list[Middleware] = middlewares or []

    def add(self, middleware: Middleware) -> "MiddlewarePipeline":
        """链式注册，方便动态组装。"""
        self._middlewares.append(middleware)
        return self

    async def run(self, ctx: RequestContext) -> MiddlewareResult:
        """依次执行所有中间件，返回最终结果。"""
        for mw in self._middlewares:
            result = await mw.process(ctx)
            if not result.ok:
                return result
        return MiddlewareResult.proceed()


# ---------------------------------------------------------------------------
# 默认 Pipeline 单例（下载请求的标准管道）
# ---------------------------------------------------------------------------

#: 供 message_parser.py 和 inline_actions.py 直接 import 使用的默认管道。
#: 如需扩展（如添加 BanMiddleware），在 main.py 的 post_init 中 .add() 即可。
default_pipeline: MiddlewarePipeline = MiddlewarePipeline([
    AuthMiddleware(),
    RateLimitMiddleware(),
])
