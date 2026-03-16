from __future__ import annotations

import logging

from database.history import add_history, get_file_id_by_url
from utils.i18n import t
from .base import DownloadStrategy
from .sender import BotSender
from integrations.downloaders import ytdlp_client

logger = logging.getLogger(__name__)


class ThumbnailStrategy(DownloadStrategy):
    """Downloads a video thumbnail and sends it as a photo.

    Thumbnails are special: _do_download returns a *URL* rather than a local
    file path (the image stays remote; Telegram fetches it directly).  This
    is why we override execute() — the base class assumes a local file.
    """

    @property
    def download_type(self) -> str:
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

    async def _do_download(
        self, url: str, progress_hook
    ) -> tuple[str, dict]:
        thumbnail_url, info = await ytdlp_client.get_thumbnail(url)
        if not thumbnail_url:
            raise RuntimeError("No thumbnail available for this URL.")
        return thumbnail_url, info

    # ---- override execute() because there is no local file to validate -----

    async def execute(self, sender: BotSender, url: str) -> None:
        user_id = sender.user_id

        await sender.edit_status(t("downloading", user_id))
        try:
            thumbnail_url, info = await self._do_download(url, None)
        except Exception as exc:
            logger.error("Thumbnail fetch failed: %s", exc)
            await sender.edit_status(
                t("download_failed", user_id, error=str(exc))
            )
            return

        title = info.get("title") if info else None
        caption = self._get_caption(title) if title else None

        await sender.edit_status(t("uploading", user_id))
        try:
            # Re-use cached file_id if we have one, otherwise send the URL
            existing_id = await get_file_id_by_url(
                url, download_type=self.download_type
            )
            if existing_id:
                await self._send_from_file_id(sender, existing_id, caption)
                file_id = existing_id
            else:
                file_id = await self._upload_new_file(sender, thumbnail_url, caption)

            sender.log_download(
                f"{self.download_type}_downloaded", url, "success", 0
            )
            await add_history(
                user_id, url, self.download_type,
                0, title, "success", thumbnail_url, file_id,
            )
        except Exception as exc:
            await sender.edit_status(
                t("upload_failed", user_id, error=str(exc))
            )
            sender.log_download(
                f"{self.download_type}_downloaded", url,
                f"upload_failed: {exc}",
            )
            await add_history(
                user_id, url, self.download_type, 0, title, "failed"
            )
