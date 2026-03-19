from __future__ import annotations

import os
import asyncio
import logging
from abc import ABC, abstractmethod

from config import MAX_FILE_SIZE
from database.history import (
    get_file_id_and_title_by_url,
    add_history,
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

    @property
    def download_type(self) -> str:
        return self.task_type

    # ---------------------------------------------------------------- helpers

    def _get_caption(self, title: str, emoji: str = "🎬") -> str | None:
        return f"{emoji} {title}" if title else None

    async def _validate_file_size(
        self, filename: str, sender: BotSender
    ) -> bool:
        """Return False (and clean up) if the file exceeds MAX_FILE_SIZE."""
        if not filename or not os.path.isfile(filename):
            return True
        file_size = int(os.path.getsize(filename))
        max_size = int(MAX_FILE_SIZE)
        if file_size > max_size:
            await sender.edit_status(
                t("file_too_large", sender.user_id,
                  size=f"{file_size // (1024 * 1024)}MB")
            )
            os.remove(filename)
            return False
        return True

    # ---------------------------------------------------------------- abstract

    @abstractmethod
    async def _do_execute(
        self, url: str, progress_hook
    ) -> tuple[str, dict]:
        """Execute the task and return (local_filename, info_dict)."""
        ...

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

    async def execute(self, sender: BotSender, url: str) -> None:
            user_id = sender.user_id
            await sender.edit_status(t("processing", user_id))
            logger.info("execute: url=%s, task_type=%s", url, self.task_type)

            file_id_result = await get_file_id_and_title_by_url(url, download_type=self.task_type)
            if file_id_result:
                file_id, title = file_id_result
                caption = self._get_caption(title) if title else None
                logger.info("execute: file_id hit, sending directly")
                await sender.edit_status(t("uploading", user_id))
                try:
                    await self._send_from_file_id(sender, file_id, caption)
                    await sender.delete_status()
                except Exception as exc:
                    logger.error("file_id send failed: %s", exc, exc_info=True)
                    await sender.edit_status(t("upload_failed", user_id, error=str(exc)))
                    await sender.delete_status(delay=5.0)
                    raise
                return

            loop = asyncio.get_event_loop()
            processing_msg = getattr(sender, 'processing_msg', None)
            progress_hook = None
            if processing_msg:
                from utils.download_tracker import _make_progress_hook
                progress_hook = _make_progress_hook(processing_msg, loop)

            try:
                filename, info = await self._do_execute(url, progress_hook)
                logger.info("execute: _do_execute completed, filename=%s", filename)
            except Exception as exc:
                logger.error("execute: _do_execute failed: %s", exc, exc_info=True)
                await sender.edit_status(t("download_failed", user_id, error=str(exc)))
                raise

            if not await self._validate_file_size(filename, sender):
                await sender.delete_status(delay=5.0)
                return

            title = info.get("title") if info else None
            caption = self._get_caption(title) if title else None

            await sender.edit_status(t("uploading", user_id))
            try:
                new_id = await self._upload_new_file(sender, filename, caption)
                if new_id:
                    try:
                        file_size = int(os.path.getsize(filename)) if os.path.exists(filename) else 0
                    except (OSError, TypeError, ValueError):
                        file_size = 0
                    await add_history(
                        user_id, url, self.task_type,
                        file_size, title, "success", filename, new_id,
                    )
                await sender.delete_status()
                logger.info("execute: upload completed")
            except Exception as exc:
                logger.error("Upload failed: %s", exc, exc_info=True)
                await sender.edit_status(t("upload_failed", user_id, error=str(exc)))
                await sender.delete_status(delay=5.0)
                raise
            finally:
                if filename and os.path.exists(filename):
                    try:
                        os.remove(filename)
                    except OSError:
                        pass


DownloadStrategy = TaskStrategy

