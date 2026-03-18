# core/callback_bus.py
"""
全局 InlineKeyboard 回调路由总线。

设计目标
--------
将回调路由注册机制从 modules/downloader 中提升到 core 层，
使 downloader 和 billing 两个模块都能独立注册回调处理器，
而无需任何一方 import 另一方。

用法
----
在各模块的 handlers 中注册：

    from core.callback_bus import register

    @register(lambda d: d.startswith("bill_confirm:"))
    async def _cb_bill_confirm(query, context):
        ...

在 DownloaderModule.setup() 中挂载统一入口：

    from core.callback_bus import handle_callback
    from telegram.ext import CallbackQueryHandler
    app.add_handler(CallbackQueryHandler(handle_callback))

注册时机
--------
模块的 handlers 文件在 setup() 中被 import 时，模块级 @register
装饰器自动执行，副作用式地将 handler 追加到 _HANDLERS 列表。
只要 setup() 在 add_handler 之前完成，顺序就是安全的。
"""
from __future__ import annotations

import logging
from typing import Callable, Awaitable

logger = logging.getLogger(__name__)

# (matcher, handler) 列表，按注册顺序匹配
_HANDLERS: list[tuple[Callable[[str], bool], Callable]] = []


def register(matcher: Callable[[str], bool]) -> Callable:
    """
    装饰器：将一个 async handler 注册到全局回调路由表。

    参数
    ----
    matcher:
        接收 callback_data 字符串，返回 bool 的函数。
        第一个匹配的 handler 胜出（短路）。

    示例
    ----
    @register(lambda d: d.startswith("download_"))
    async def _handle_download(query, context):
        ...
    """
    def decorator(func: Callable) -> Callable:
        _HANDLERS.append((matcher, func))
        logger.debug("callback_bus: registered handler %s for matcher %s", func.__name__, matcher)
        return func
    return decorator


async def handle_callback(update, context) -> None:
    """
    统一的 CallbackQueryHandler 入口。

    在 DownloaderModule.setup()（或任意模块）中挂载：
        app.add_handler(CallbackQueryHandler(handle_callback))
    """
    query = update.callback_query
    if not query:
        return

    data = query.data or ""
    for matcher, handler in _HANDLERS:
        try:
            if matcher(data):
                await handler(query, context)
                return
        except Exception as exc:
            logger.error(
                "callback_bus: handler %s raised for data=%r: %s",
                handler.__name__, data, exc, exc_info=True,
            )
            try:
                await query.answer("❌ 处理出错，请重试。", show_alert=True)
            except Exception:
                pass
            return

    # 没有任何 handler 匹配时静默 ack，避免 Telegram 超时提示
    await query.answer()
    logger.warning("callback_bus: unhandled callback_data=%r", data)
