from __future__ import annotations

import logging

from .base import DownloadStrategy
from .sender import BotSender
from integrations.downloaders import ytdlp_client

logger = logging.getLogger(__name__)


class SubtitleStrategy(DownloadStrategy):

    SUPPORTED_LANGS = ["zh-Hans", "zh-Hant", "zh", "en", "ja", "ko"]

    @property
    def download_type(self) -> str:
        return "subtitle"

    def _get_caption(self, title: str, emoji: str = "📝") -> str | None:
        return f"{emoji} {title}" if title else None

    async def _send_from_file_id(
        self,
        sender: BotSender,
        file_id: str,
        caption: str | None,
    ) -> None:
        await sender.send_document(file_id, caption=caption)

    async def _upload_new_file(
        self,
        sender: BotSender,
        filename: str,
        caption: str | None,
    ) -> str | None:
        with open(filename, "rb") as f:
            file_id = await sender.send_document(f, caption=caption)
        return file_id

    async def _do_download(self, url: str, progress_hook) -> tuple[str, dict]:
        return await ytdlp_client.download_subtitle(
            url, preferred_langs=self.SUPPORTED_LANGS
        )
