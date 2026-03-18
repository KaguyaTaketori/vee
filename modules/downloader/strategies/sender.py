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

    def __init__(
        self,
        user: User,
        reply_target: Message,
        processing_msg: Message,
    ) -> None:
        self._user = user
        self._reply_target = reply_target
        self._processing_msg = processing_msg
        self.user_id: int = user.id

    # ------------------------------------------------------------------ 工厂方法

    @classmethod
    def from_callback(
        cls,
        query: CallbackQuery,
        processing_msg: Message,
    ) -> "TelegramSender":
        """从按钮回调构造。reply_target 是 query.message。"""
        return cls(
            user=query.from_user,
            reply_target=query.message,
            processing_msg=processing_msg,
        )

    @classmethod
    def from_message(
        cls,
        message: Message,
        processing_msg: Message,
    ) -> "TelegramSender":
        """从文本消息构造（包括批量模式）。reply_target 就是 message 本身。"""
        return cls(
            user=message.from_user,
            reply_target=message,
            processing_msg=processing_msg,
        )

    # ------------------------------------------------------------------ status

    async def edit_status(self, text: str) -> None:
        await self._processing_msg.edit_text(text)

    async def send_message(self, text: str) -> None:
        await self._reply_target.reply_text(text)

    # ------------------------------------------------------------------- media

    async def send_video(
        self,
        file: str | BinaryIO,
        caption: str | None = None,
    ) -> str | None:
        logger.info("send_video: file=%s, caption=%s", file, caption)
        sent = await self._reply_target.reply_video(video=file, caption=caption)
        logger.info(
            "send_video: file_id=%s",
            sent.video.file_id if sent and sent.video else None,
        )
        return sent.video.file_id if sent and sent.video else None

    async def send_audio(
        self,
        file: str | BinaryIO,
        title: str | None = None,
    ) -> str | None:
        logger.info("send_audio: file=%s, title=%s", file, title)
        sent = await self._reply_target.reply_audio(audio=file, title=title)
        logger.info(
            "send_audio: file_id=%s",
            sent.audio.file_id if sent and sent.audio else None,
        )
        return sent.audio.file_id if sent and sent.audio else None

    async def send_document(
        self,
        file: str | BinaryIO,
        caption: str | None = None,
    ) -> str | None:
        logger.info("send_document: file=%s, caption=%s", file, caption)
        sent = await self._reply_target.reply_document(document=file, caption=caption)
        logger.info(
            "send_document: file_id=%s",
            sent.document.file_id if sent and sent.document else None,
        )
        return sent.document.file_id if sent and sent.document else None

    async def send_photo(
        self,
        source: str,
        caption: str | None = None,
    ) -> str | None:
        logger.info("send_photo: source=%s, caption=%s", source, caption)
        sent = await self._reply_target.reply_photo(photo=source, caption=caption)
        logger.info(
            "send_photo: file_id=%s",
            sent.photo[-1].file_id if sent and sent.photo else None,
        )
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
        _log(
            user_id=self._user.id,
            username=self._user.username or "N/A",
            name=f"{self._user.first_name} {self._user.last_name or ''}".strip(),
            action=action,
            url=url,
            status=status,
            file_size=file_size,
        )
