import logging
from typing import Protocol, Any

_logger = logging.getLogger(__name__)

class QueryLike(Protocol):
    from_user: Any
    message: Any
    async def edit_message_text(self, text: str, **kwargs) -> None: ...


class SilentMessageQuery:
    def __init__(self, user, status_msg):
        self.from_user = user
        self.message = status_msg
    
    async def edit_message_text(self, text: str, **kwargs) -> None:
        try:
            await self.message.edit_text(text, **kwargs)
        except Exception as e:
            _logger.debug(
                "SilentMessageQuery 静默失败 (user=%s): %s",
                getattr(self.from_user, "id", "?"), e,
            )

