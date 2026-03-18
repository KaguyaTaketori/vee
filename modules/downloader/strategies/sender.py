"""
modules/downloader/strategies/sender.py
────────────────────────────────────────
BotSender Protocol + TelegramSender implementation.

All telegram.* imports are confined to TelegramSender; the Protocol
itself is fully platform-agnostic.
"""
from __future__ import annotations

import logging
from typing import TYPE_CHECKING, BinaryIO, Protocol, runtime_checkable

if TYPE_CHECKING:
    # These imports are only evaluated by type checkers, never at runtime,
    # so there is zero PTB dependency in the Protocol layer.
    from telegram import CallbackQuery, Message, User

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Platform-agnostic BotSender Protocol
# ---------------------------------------------------------------------------

@runtime_checkable
class BotSender(Protocol):
    """Platform-agnostic interface for replying to a user during a download.

    Return values of send_* are an optional *cache key* (Telegram file_id).
    Platforms that don't support caching return None — the strategy will
    re-upload next time, which is correct behaviour.
    """

    user_id: int

    async def edit_status(self, text: str) -> None:
        """Replace the in-progress status message with new text."""
        ...

    async def delete_status(self, delay: float = 0.0) -> None: ...

    async def send_message(self, text: str) -> None:
        """Send a new plain-text message (separate from the status message)."""
        ...

    async def send_video(
        self,
        file: "str | BinaryIO",
        caption: str | None = None,
    ) -> str | None: ...

    async def send_audio(
        self,
        file: "str | BinaryIO",
        title: str | None = None,
    ) -> str | None: ...

    async def send_document(
        self,
        file: "str | BinaryIO",
        caption: str | None = None,
    ) -> str | None: ...

    async def send_photo(
        self,
        source: str,
        caption: str | None = None,
    ) -> str | None: ...

    def log_download(
        self,
        action: str,
        url: str,
        status: str,
        file_size: int | None = None,
    ) -> None: ...


# ---------------------------------------------------------------------------
# Telegram implementation
# ---------------------------------------------------------------------------

class TelegramSender:
    """PTB implementation of BotSender.

    Constructed via factory methods; never instantiate directly.
    All telegram.* objects are imported lazily inside methods so that
    importing this module does not force a PTB dependency on non-PTB code.
    """

    def __init__(
        self,
        user: "User",
        reply_target: "Message",
        processing_msg: "Message",
    ) -> None:
        self._user = user
        self._reply_target = reply_target
        self._processing_msg = processing_msg
        self.user_id: int = user.id

    # ── Factories ──────────────────────────────────────────────────────────

    @classmethod
    def from_callback(
        cls,
        query: "CallbackQuery",
        processing_msg: "Message",
    ) -> "TelegramSender":
        """Build from an inline-button callback. reply_target = query.message."""
        return cls(
            user=query.from_user,
            reply_target=query.message,
            processing_msg=processing_msg,
        )

    @classmethod
    def from_message(
        cls,
        message: "Message",
        processing_msg: "Message",
    ) -> "TelegramSender":
        """Build from a plain message (text link, batch mode)."""
        return cls(
            user=message.from_user,
            reply_target=message,
            processing_msg=processing_msg,
        )

    # ── Status ─────────────────────────────────────────────────────────────
    @property
    def processing_msg(self):
        return self._processing_msg

    async def edit_status(self, text: str) -> None:
        if self._processing_msg is None:
            return
        await self._processing_msg.edit_text(text)

    async def send_message(self, text: str) -> None:
        await self._reply_target.reply_text(text)

    async def delete_status(self, delay: float = 0.0) -> None:
        """下载完成后删除状态消息，防止聊天记录堆积。"""
        if self._processing_msg is None:
            return
        if delay > 0:
            import asyncio
            await asyncio.sleep(delay)
        try:
            await self._processing_msg.delete()
        except Exception as exc:
            logger.debug("delete_status: could not delete: %s", exc)

    # ── Media ──────────────────────────────────────────────────────────────

    async def send_video(
        self,
        file: "str | BinaryIO",
        caption: str | None = None,
    ) -> str | None:
        logger.info("send_video: caption=%s", caption)
        sent = await self._reply_target.reply_video(video=file, caption=caption)
        file_id = sent.video.file_id if sent and sent.video else None
        logger.info("send_video: file_id=%s", file_id)
        return file_id

    async def send_audio(
        self,
        file: "str | BinaryIO",
        title: str | None = None,
    ) -> str | None:
        logger.info("send_audio: title=%s", title)
        sent = await self._reply_target.reply_audio(audio=file, title=title)
        file_id = sent.audio.file_id if sent and sent.audio else None
        logger.info("send_audio: file_id=%s", file_id)
        return file_id

    async def send_document(
        self,
        file: "str | BinaryIO",
        caption: str | None = None,
    ) -> str | None:
        logger.info("send_document: caption=%s", caption)
        sent = await self._reply_target.reply_document(document=file, caption=caption)
        file_id = sent.document.file_id if sent and sent.document else None
        logger.info("send_document: file_id=%s", file_id)
        return file_id

    async def send_photo(
        self,
        source: str,
        caption: str | None = None,
    ) -> str | None:
        logger.info("send_photo: caption=%s", caption)
        sent = await self._reply_target.reply_photo(photo=source, caption=caption)
        file_id = sent.photo[-1].file_id if sent and sent.photo else None
        logger.info("send_photo: file_id=%s", file_id)
        return file_id

    # ── Logging ────────────────────────────────────────────────────────────

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
