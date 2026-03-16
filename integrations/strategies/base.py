from __future__ import annotations

import os
import asyncio
import logging
from abc import ABC, abstractmethod

from config import MAX_FILE_SIZE, MAX_CACHE_SIZE
from database.history import (
    get_file_id_by_url,
    add_history,
    check_recent_download,
)
from utils.i18n import t
from .sender import BotSender

logger = logging.getLogger(__name__)


class DownloadStrategy(ABC):
    """Base class for download strategies (Template Method pattern).

    Subclasses implement three methods:
      • download_type  — string identifier used for routing and history
      • _do_download   — calls the appropriate downloader; returns (path, info)
      • _send_from_file_id / _upload_new_file — delegate to sender

    Nothing in this file imports from telegram or discord.
    """

    # ---------------------------------------------------------------- identity

    @property
    @abstractmethod
    def download_type(self) -> str:
        """Unique string key for this strategy (e.g. "audio", "video_1080")."""
        ...

    # ---------------------------------------------------------------- helpers

    def _get_caption(self, title: str, emoji: str = "🎬") -> str | None:
        return f"{emoji} {title}" if title else None

    async def _check_cached_file(self, url: str, user_id: int) -> str | None:
        """Return a still-valid local file path from a previous download, or None."""
        recent = await check_recent_download(
            url,
            max_age_hours=24,
            download_type=self.download_type,
        )
        if recent:
            file_path = recent.get("file_path")
            if file_path and os.path.exists(file_path):
                if os.path.getsize(file_path) <= MAX_CACHE_SIZE:
                    logger.info(
                        "Using cached file (%s) for %s: %s",
                        self.download_type, url, file_path,
                    )
                    return file_path
        return None

    async def _validate_file_size(
        self, filename: str, sender: BotSender
    ) -> bool:
        """Return False (and clean up) if the file exceeds MAX_FILE_SIZE."""
        file_size = os.path.getsize(filename)
        if file_size > MAX_FILE_SIZE:
            await sender.edit_status(
                t("file_too_large", sender.user_id,
                  size=f"{file_size // (1024 * 1024)}MB")
            )
            os.remove(filename)
            return False
        return True

    def _cleanup_temp_file(self, filename: str, cached_file: str | None) -> None:
        """Delete temp file unless it came from the local cache."""
        if os.path.exists(filename) and not cached_file:
            os.remove(filename)

    # ---------------------------------------------------------------- sending

    async def _get_file_id_or_upload(
        self,
        sender: BotSender,
        url: str,
        filename: str,
        caption: str | None,
    ) -> str | None:
        """Re-use a cached file_id if available, otherwise upload fresh."""
        existing_id = await get_file_id_by_url(url, download_type=self.download_type)
        if existing_id:
            logger.info(
                "Using file_id (%s) for %s: %s",
                self.download_type, url, existing_id,
            )
            await self._send_from_file_id(sender, existing_id, caption)
            return existing_id

        return await self._upload_new_file(sender, filename, caption)

    async def _send_from_file_id(
        self,
        sender: BotSender,
        file_id: str,
        caption: str | None,
    ) -> None:
        """Re-send using a cached platform key.  Override in each subclass."""
        raise NotImplementedError

    async def _upload_new_file(
        self,
        sender: BotSender,
        filename: str,
        caption: str | None,
    ) -> str | None:
        """Upload the file and return a cacheable key (or None).  Override per subclass."""
        raise NotImplementedError

    # ------------------------------------------------------------ main template

    async def execute(self, sender: BotSender, url: str) -> None:
        """Orchestrate the full download → validate → upload flow.

        Args:
            sender: Platform-agnostic messenger.  Built by facades.py from the
                    platform-specific query/context objects stored in the queue.
            url:    The URL to download.
        """
        user_id = sender.user_id

        # ── 1. Check local cache ──────────────────────────────────────────────
        cached_file = await self._check_cached_file(url, user_id)

        if cached_file and os.path.exists(cached_file):
            filename = cached_file
            title = os.path.splitext(os.path.basename(filename))[0]
            info: dict = {"title": title}
        else:
            # ── 2. Download ───────────────────────────────────────────────────
            try:
                await sender.edit_status(t("downloading", user_id))
                loop = asyncio.get_running_loop()
                from utils.download_tracker import _make_progress_hook
                progress_hook = _make_progress_hook(sender._processing_msg, loop)
                filename, info = await self._do_download(url, progress_hook)
            except Exception as exc:
                logger.error("Download failed: %s: %s", type(exc).__name__, exc)
                await sender.edit_status(
                    t("download_failed", user_id, error=str(exc))
                )
                sender.log_download(
                    f"{self.download_type}_downloaded", url,
                    f"download_failed: {exc}",
                )
                await add_history(user_id, url, self.download_type, 0, None, "failed")
                return

            if not os.path.exists(filename):
                await sender.edit_status(
                    t("download_failed", user_id, error="File not found")
                )
                sender.log_download(
                    f"{self.download_type}_downloaded", url,
                    "download_failed: File not found",
                )
                await add_history(user_id, url, self.download_type, 0, None, "failed")
                return

        # ── 3. Validate size ─────────────────────────────────────────────────
        if not await self._validate_file_size(filename, sender):
            return

        title = info.get("title") if info else None
        caption = self._get_caption(title) if title else None

        # ── 4. Upload ─────────────────────────────────────────────────────────
        await sender.edit_status(t("uploading", user_id))
        try:
            file_size = os.path.getsize(filename)
            logger.info(
                "Starting upload: %s — file: %s, size: %d bytes",
                self.download_type, filename, file_size,
            )
            file_id = await self._get_file_id_or_upload(
                sender, url, filename, caption
            )
            logger.info("Upload completed: file_id=%s", file_id)
            sender.log_download(
                f"{self.download_type}_downloaded", url, "success", file_size
            )
            await add_history(
                user_id, url, self.download_type,
                file_size, title, "success", filename, file_id,
            )
        except Exception as exc:
            import traceback
            logger.error("Upload failed: %s: %s", type(exc).__name__, exc)
            logger.error("Traceback: %s", traceback.format_exc())
            await sender.edit_status(
                t("upload_failed", user_id, error=str(exc))
            )
            sender.log_download(
                f"{self.download_type}_downloaded", url,
                f"upload_failed: {exc}",
            )
            await add_history(
                user_id, url, self.download_type,
                os.path.getsize(filename) if os.path.exists(filename) else 0,
                title, "failed",
            )

        self._cleanup_temp_file(filename, cached_file)

    # ---------------------------------------------------------------- abstract

    @abstractmethod
    async def _do_download(
        self, url: str, progress_hook
    ) -> tuple[str, dict]:
        """Download the content and return (local_filename, info_dict)."""
        ...
