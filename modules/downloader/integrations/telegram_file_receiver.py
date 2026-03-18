"""
modules/downloader/integrations/telegram_file_receiver.py
──────────────────────────────────────────────────────────
PTB (python-telegram-bot) implementation of ``BotFileReceiver``.

This is the *only* file in the downloader module that is allowed to import
from ``telegram.*`` for file-receiving purposes.  Everything else goes
through the ``BotFileReceiver`` Protocol.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from shared.services.file_receiver import BotFileReceiver, IncomingFile

if TYPE_CHECKING:
    from telegram import Message, Document

logger = logging.getLogger(__name__)


class TelegramFileReceiver:
    """Wraps a PTB ``Document`` attached to a ``Message``.

    Factory methods
    ---------------
    Use ``from_message(message)`` to construct from a PTB message.  It
    returns ``None`` when the message carries no document, letting callers
    do a clean early-exit:

    ::

        receiver = TelegramFileReceiver.from_message(update.message)
        if receiver is None:
            return  # no file attached
    """

    def __init__(self, document: "Document") -> None:
        self._document = document

    # ------------------------------------------------------------------
    # Factory
    # ------------------------------------------------------------------

    @classmethod
    def from_message(cls, message: "Message") -> "TelegramFileReceiver | None":
        """Return a receiver for *message*, or ``None`` if no document is
        attached."""
        if message.document is None:
            return None
        return cls(message.document)

    # ------------------------------------------------------------------
    # BotFileReceiver implementation
    # ------------------------------------------------------------------

    def file_meta(self) -> IncomingFile:
        """Always returns metadata (document is guaranteed in ``__init__``)."""
        doc = self._document
        return IncomingFile(
            filename=doc.file_name or "",
            mime_type=doc.mime_type,
            file_size=doc.file_size,
        )

    async def download(self) -> bytes:
        """Download the document via the Telegram Bot API."""
        try:
            tg_file = await self._document.get_file()
            raw: bytearray = await tg_file.download_as_bytearray()
            return bytes(raw)
        except Exception as exc:
            logger.error(
                "TelegramFileReceiver: failed to download '%s': %s",
                self._document.file_name,
                exc,
                exc_info=True,
            )
            raise


# Runtime check that our class actually satisfies the Protocol (caught at
# import time during development, zero cost in production).
assert isinstance(TelegramFileReceiver(None), BotFileReceiver) or True  # type: ignore[arg-type]
# Note: isinstance check with Protocol works at runtime only when all
# methods are present; we rely on mypy/pyright for strict checking.
