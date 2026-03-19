from __future__ import annotations

import logging

from database.history import add_history, get_file_id_and_title_by_url
from utils.i18n import t
from .base import TaskStrategy
from .sender import BotSender
from modules.downloader.integrations.downloaders import ytdlp_client

logger = logging.getLogger(__name__)


class ThumbnailStrategy(TaskStrategy):
    """Downloads a video thumbnail and sends it as a photo.

    Thumbnails are special: _do_execute returns a *URL* rather than a local
    file path (the image stays remote; Telegram fetches it directly).  This
    is why we override execute() — the base class assumes a local file.
    """

    @property
    def task_type(self) -> str:
        return "thumbnail"

    def _get_caption(self, title: str, emoji: str = "🖼️") -> str | None:
        return f"{emoji} {title}" if title else None

    async def _send_from_file_id(
        self,
        sender: BotSender,
        file_id: str,
        caption: str | None,
    ) -> None:
        await sender.send_photo(file_id, caption=caption)

    async def _upload_new_file(
        self,
        sender: BotSender,
        filename: str,   # thumbnail_url in practice
        caption: str | None,
    ) -> str | None:
        return await sender.send_photo(filename, caption=caption)

    async def _do_execute(
        self, url: str, progress_hook
    ) -> tuple[str, dict]:
        thumbnail_url, info = await ytdlp_client.get_thumbnail(url)
        if not thumbnail_url:
            raise RuntimeError("No thumbnail available for this URL.")
        return thumbnail_url, info
    
    async def execute(self, sender: BotSender, url: str) -> None:
            user_id = sender.user_id
            await sender.edit_status(t("downloading", user_id))

            file_id_result = await get_file_id_and_title_by_url(url, download_type=self.task_type)
            if file_id_result:
                file_id, title = file_id_result
                caption = self._get_caption(title) if title else None
                await sender.edit_status(t("uploading", user_id))
                try:
                    await self._send_from_file_id(sender, file_id, caption)
                    await sender.delete_status()
                except Exception as exc:
                    await sender.edit_status(t("upload_failed", user_id, error=str(exc)))
                    await sender.delete_status(delay=5.0)
                    raise
                return

            try:
                thumbnail_url, info = await self._do_execute(url, None)
            except Exception as exc:
                logger.error("Thumbnail fetch failed: %s", exc)
                await sender.edit_status(t("download_failed", user_id))
                await sender.delete_status(delay=5.0)
                return

            title = info.get("title") if info else None
            caption = self._get_caption(title) if title else None

            await sender.edit_status(t("uploading", user_id))
            try:
                file_id = await self._upload_new_file(sender, thumbnail_url, caption)
                await add_history(user_id, url, self.task_type, 0, title, "success", thumbnail_url, file_id)
                await sender.delete_status()
            except Exception as exc:
                await sender.edit_status(t("upload_failed", user_id, error=str(exc)))
                await add_history(user_id, url, self.task_type, 0, title, "failed")
                await sender.delete_status(delay=5.0)
