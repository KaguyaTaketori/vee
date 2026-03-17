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


class TaskStrategy(ABC):
    """Base class for task strategies (Template Method pattern).

    「Task」比「Download」更通用——未来此类可以承载转码、通知推送等
    与下载无关的任务，无需再次改名。

    Subclasses implement:
      • task_type        — 唯一字符串标识，用于路由与历史记录
      • _do_execute      — 原 _do_download；执行具体任务，返回 (path, info)
      • _send_from_file_id / _upload_new_file — 委托给 BotSender
    """

    # ---------------------------------------------------------------- identity

    @property
    @abstractmethod
    def task_type(self) -> str:
        """Unique string key for this strategy (e.g. "audio", "video_1080")."""
        ...

    # ── backward-compat shim ────────────────────────────────────────────────
    # 保留 download_type 作为只读别名，方便渐进迁移期间已有代码不报错。
    # 所有新代码请使用 task_type。
    @property
    def download_type(self) -> str:
        return self.task_type

    # ---------------------------------------------------------------- helpers

    def _get_caption(self, title: str, emoji: str = "🎬") -> str | None:
        return f"{emoji} {title}" if title else None

    async def _check_cached_file(self, url: str, user_id: int) -> str | None:
        """Return a still-valid local file path from a previous download, or None."""
        recent = await check_recent_download(
            url,
            max_age_hours=24,
            download_type=self.task_type,
        )
        if recent:
            file_path = recent.get("file_path")
            if file_path and os.path.exists(file_path):
                if os.path.getsize(file_path) <= MAX_CACHE_SIZE:
                    logger.info(
                        "Using cached file (%s) for %s: %s",
                        self.task_type, url, file_path,
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
        if filename and filename != cached_file and os.path.exists(filename):
            try:
                os.remove(filename)
            except OSError:
                pass

    # ---------------------------------------------------------------- abstract

    @abstractmethod
    async def _do_execute(
        self, url: str, progress_hook
    ) -> tuple[str, dict]:
        """Execute the task and return (local_filename, info_dict)."""
        ...

    # ── backward-compat shim ────────────────────────────────────────────────
    async def _do_download(self, url: str, progress_hook) -> tuple[str, dict]:
        """Deprecated alias — delegates to _do_execute."""
        return await self._do_execute(url, progress_hook)

    # ---------------------------------------------------------------- template

    @abstractmethod
    async def _send_from_file_id(
        self,
        sender: BotSender,
        file_id: str,
        caption: str | None,
    ) -> None: ...

    @abstractmethod
    async def _upload_new_file(
        self,
        sender: BotSender,
        filename: str,
        caption: str | None,
    ) -> str | None: ...

    async def _get_file_id_or_upload(
        self,
        sender: BotSender,
        url: str,
        filename: str,
        caption: str | None,
    ) -> str | None:
        file_id = await get_file_id_by_url(url, self.task_type)
        if file_id:
            await self._send_from_file_id(sender, file_id, caption)
            return file_id
        new_id = await self._upload_new_file(sender, filename, caption)
        if new_id:
            await add_history(
                sender.user_id, url, self.task_type,
                os.path.getsize(filename) if os.path.exists(filename) else 0,
                caption, "success", filename, new_id,
            )
        return new_id

    async def execute(self, sender: BotSender, url: str) -> None:
        """Main entry point called by the task runner."""
        user_id = sender.user_id
        await sender.edit_status(t("processing", user_id))

        cached_file = await self._check_cached_file(url, user_id)
        progress_hook = None  # injected by caller if needed

        if cached_file:
            filename, info = cached_file, {}
        else:
            try:
                filename, info = await self._do_execute(url, progress_hook)
            except Exception as exc:
                logger.error("Task execution failed (%s): %s", self.task_type, exc)
                await sender.edit_status(t("download_failed", user_id))
                raise

        if not await self._validate_file_size(filename, sender):
            return

        title = info.get("title") if info else None
        caption = self._get_caption(title) if title else None

        await sender.edit_status(t("uploading", user_id))
        try:
            await self._get_file_id_or_upload(sender, url, filename, caption)
        except Exception as exc:
            logger.error("Upload failed: %s", exc)
            await sender.edit_status(t("upload_failed", user_id, error=str(exc)))
            raise
        finally:
            self._cleanup_temp_file(filename, cached_file)


# ---------------------------------------------------------------------------
# Backward-compat alias — 允许旧代码 `from .base import DownloadStrategy` 不崩
# ---------------------------------------------------------------------------
DownloadStrategy = TaskStrategy
