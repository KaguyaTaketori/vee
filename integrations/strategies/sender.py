from __future__ import annotations

import logging
from typing import BinaryIO, Protocol, runtime_checkable

logger = logging.getLogger(__name__)


@runtime_checkable
class BotSender(Protocol):
    """Platform-agnostic interface for replying to a user during a download task.

    Every method maps to one user-facing action.  Platform adapters (Telegram,
    Discord, …) implement this; strategies depend only on this interface.

    Return values of send_* are an optional *cache key* (Telegram's file_id).
    Platforms that don't support caching just return None — the strategy
    will re-upload next time, which is perfectly correct behaviour.
    """

    user_id: int

    # ------------------------------------------------------------------ status

    async def edit_status(self, text: str) -> None:
        """Replace the in-progress status message with new text."""
        ...

    async def send_message(self, text: str) -> None:
        """Send a new plain-text message (separate from the status message)."""
        ...

    # ------------------------------------------------------------------- media

    async def send_video(
        self,
        file: str | BinaryIO,
        caption: str | None = None,
    ) -> str | None:
        """Send a video.  *file* may be a path, open file handle, or a
        platform-native cache key (e.g. Telegram file_id).
        Returns a cacheable key, or None.
        """
        ...

    async def send_audio(
        self,
        file: str | BinaryIO,
        title: str | None = None,
    ) -> str | None:
        """Send an audio track.  Returns a cacheable key, or None."""
        ...

    async def send_document(
        self,
        file: str | BinaryIO,
        caption: str | None = None,
    ) -> str | None:
        """Send a document / generic file.  Returns a cacheable key, or None."""
        ...

    async def send_photo(
        self,
        source: str,
        caption: str | None = None,
    ) -> str | None:
        """Send a photo from a URL, local path, or cache key.
        Returns a cacheable key, or None.
        """
        ...

    # ----------------------------------------------------------------- logging

    def log_download(
        self,
        action: str,
        url: str,
        status: str,
        file_size: int | None = None,
    ) -> None:
        """Write a platform-specific audit log entry for this download."""
        ...


# ---------------------------------------------------------------------------
# Telegram implementation
# ---------------------------------------------------------------------------

class TelegramSender:
    """BotSender backed by python-telegram-bot objects.

    Wraps the *query* (CallbackQuery or SilentMessageQuery) and the
    *processing_msg* (the editable status message) that were created by the
    handler layer.  Strategies receive this object; they never import anything
    from python-telegram-bot directly.
    """

    def __init__(self, query, processing_msg) -> None:
        self._query = query
        self._processing_msg = processing_msg
        self.user_id: int = query.from_user.id

    # ------------------------------------------------------------------ status

    async def edit_status(self, text: str) -> None:
        await self._processing_msg.edit_text(text)

    async def send_message(self, text: str) -> None:
        await self._query.message.reply_text(text)

    # ------------------------------------------------------------------- media

    async def send_video(
        self,
        file: str | BinaryIO,
        caption: str | None = None,
    ) -> str | None:
        logger.info("send_video: file=%s, caption=%s", file, caption)
        sent = await self._query.message.reply_video(video=file, caption=caption)
        logger.info("send_video: sent=%s, file_id=%s", sent, sent.video.file_id if sent and sent.video else None)
        return sent.video.file_id if sent and sent.video else None

    async def send_audio(
        self,
        file: str | BinaryIO,
        title: str | None = None,
    ) -> str | None:
        logger.info("send_audio: file=%s, title=%s", file, title)
        sent = await self._query.message.reply_audio(audio=file, title=title)
        logger.info("send_audio: sent=%s, file_id=%s", sent, sent.audio.file_id if sent and sent.audio else None)
        return sent.audio.file_id if sent and sent.audio else None

    async def send_document(
        self,
        file: str | BinaryIO,
        caption: str | None = None,
    ) -> str | None:
        logger.info("send_document: file=%s, caption=%s", file, caption)
        sent = await self._query.message.reply_document(document=file, caption=caption)
        logger.info("send_document: sent=%s, file_id=%s", sent, sent.document.file_id if sent and sent.document else None)
        return sent.document.file_id if sent and sent.document else None

    async def send_photo(
        self,
        source: str,
        caption: str | None = None,
    ) -> str | None:
        logger.info("send_photo: source=%s, caption=%s", source, caption)
        sent = await self._query.message.reply_photo(photo=source, caption=caption)
        logger.info("send_photo: sent=%s, file_id=%s", sent, sent.photo[-1].file_id if sent and sent.photo else None)
        return sent.photo[-1].file_id if sent and sent.photo else None

    # ----------------------------------------------------------------- logging

    def log_download(
        self,
        action: str,
        url: str,
        status: str,
        file_size: int | None = None,
    ) -> None:
        from utils.logger import log_download as _log
        _log(self._query.from_user, action, url, status, file_size)
