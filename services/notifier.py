from __future__ import annotations

import logging
from typing import Protocol, runtime_checkable

logger = logging.getLogger(__name__)


@runtime_checkable
class AdminNotifier(Protocol):
    async def notify_admins(self, message: str, parse_mode: str | None = None) -> None:
        ...


class TelegramAdminNotifier:
    """通过 PTB bot 向所有 ADMIN_IDS 推送消息。"""

    def __init__(self, bot, admin_ids: list[int]) -> None:
        self._bot = bot
        self._admin_ids = admin_ids

    async def notify_admins(self, message: str, parse_mode: str | None = None) -> None:
        if not self._admin_ids:
            return
        for admin_id in self._admin_ids:
            try:
                await self._bot.send_message(
                    chat_id=admin_id,
                    text=message,
                    parse_mode=parse_mode,
                )
            except Exception as e:
                logger.warning("TelegramAdminNotifier: failed to notify %s: %s", admin_id, e)
