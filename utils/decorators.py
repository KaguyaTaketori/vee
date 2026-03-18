# utils/decorators.py
"""
通用 PTB handler 装饰器。
"""
from __future__ import annotations

import asyncio
import logging
from functools import wraps
from typing import Callable

logger = logging.getLogger(__name__)


async def _delete_after(message, delay: float) -> None:
    """等待 delay 秒后删除消息，失败时静默忽略（消息已过期/无权限等）。"""
    await asyncio.sleep(delay)
    try:
        await message.delete()
    except Exception as exc:
        logger.debug(
            "auto_delete: could not delete message_id=%s: %s",
            getattr(message, "message_id", "?"), exc,
        )


def auto_delete(delay: float = 3.0, *, also_reply_to: bool = True) -> Callable:
    """
    PTB MessageHandler 装饰器：handler 执行完毕后，异步删除相关消息。

    删除目标：
    - update.message          — 用户发送的消息（如 ForceReply 的回复内容）
    - update.message.reply_to_message — bot 发出的 ForceReply 提示消息
      （仅当 also_reply_to=True 且该消息存在时）

    参数：
    - delay: 删除前等待的秒数，默认 3 秒（给用户时间看到确认卡）
    - also_reply_to: 是否同时删除 ForceReply 提示消息，默认 True

    用法：
        @auto_delete(delay=3.0)
        async def handle_bill_edit_reply(update, context): ...
    """
    def decorator(func: Callable) -> Callable:
        @wraps(func)
        async def wrapper(update, context):
            result = await func(update, context)
            msg = getattr(update, "message", None)
            if msg:
                asyncio.create_task(_delete_after(msg, delay))
                if also_reply_to and msg.reply_to_message:
                    asyncio.create_task(_delete_after(msg.reply_to_message, delay))
            return result
        return wrapper
    return decorator
