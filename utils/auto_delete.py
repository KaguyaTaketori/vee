from __future__ import annotations

import asyncio
import logging
from functools import wraps
from typing import Callable

logger = logging.getLogger(__name__)


async def _delete_after(message, delay: float) -> None:
    await asyncio.sleep(delay)
    try:
        await message.delete()
    except Exception as exc:
        logger.debug(
            "auto_delete: could not delete message_id=%s: %s",
            getattr(message, "message_id", "?"), exc,
        )


def auto_delete(delay: float = 3.0, *, also_reply_to: bool = True) -> Callable:
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
