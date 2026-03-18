"""
shared/services/file_receiver.py
─────────────────────────────────
Platform-agnostic interface for receiving user-uploaded files.

Previously the only way to receive a file was to call
``document.get_file()`` / ``tg_file.download_as_bytearray()`` directly
inside the handler — a hard Telegram dependency sitting in business logic.

This module introduces a thin Protocol so that:

    handler code  →  BotFileReceiver (Protocol)
                           ↑
                  TelegramFileReceiver (PTB adapter, lives in
                  modules/downloader/integrations/)

Any future platform (Discord, Slack, HTTP webhook) only needs to provide
its own BotFileReceiver implementation; the cookie handler stays unchanged.

Usage in handler
----------------
::

    async def handle_cookie_file(
        update: Update,
        context: CallbackContext,
        receiver: BotFileReceiver | None = None,
    ) -> None:
        receiver = receiver or TelegramFileReceiver.from_message(update.message)
        meta = receiver.file_meta()
        if meta is None:
            return
        ...
        data = await receiver.download()
        await save_cookie_bytes(meta.filename, data)
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol, runtime_checkable


# ---------------------------------------------------------------------------
# Value object describing an incoming file
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class IncomingFile:
    """Platform-agnostic description of an uploaded file.

    Attributes
    ----------
    filename:
        Original filename as reported by the sender (e.g. ``youtube_cookies.txt``).
    mime_type:
        MIME type string, or ``None`` when the platform does not provide one.
    file_size:
        File size in bytes, or ``None`` if unknown.
    """

    filename: str
    mime_type: str | None = None
    file_size: int | None = None


# ---------------------------------------------------------------------------
# Protocol
# ---------------------------------------------------------------------------

@runtime_checkable
class BotFileReceiver(Protocol):
    """Receive a file that a user uploaded inside a chat message.

    Implementors
    ------------
    - ``TelegramFileReceiver``  — wraps PTB ``Document``
    - ``DiscordFileReceiver``   — (future) wraps a discord.py attachment
    - ``MockFileReceiver``      — for unit tests (no network required)

    The protocol is intentionally minimal: one query method and one I/O
    method.  Filtering (is this a .txt file? is the user admin?) stays in
    the handler.
    """

    def file_meta(self) -> IncomingFile | None:
        """Return metadata for the attached file, or *None* if no file
        is present in this message.

        Platforms that guarantee a file is present (e.g. because the
        MessageHandler filter already checked) may always return a valid
        ``IncomingFile``.
        """
        ...

    async def download(self) -> bytes:
        """Download and return the full file contents.

        Raises
        ------
        RuntimeError
            If called when ``file_meta()`` is ``None``.
        IOError / httpx.HTTPError / telegram.error.TelegramError
            Platform-specific network errors bubble up as-is so the caller
            can log and report them.
        """
        ...
