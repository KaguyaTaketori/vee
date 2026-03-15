import os
import logging

from .base import DownloadStrategy
from integrations.downloaders import ytdlp_client

logger = logging.getLogger(__name__)


class SubtitleStrategy(DownloadStrategy):

    SUPPORTED_LANGS = ["zh-Hans", "zh-Hant", "zh", "en", "ja", "ko"]

    @property
    def download_type(self) -> str:
        return "subtitle"

    @property
    def telegram_method(self) -> str:
        return "document"

    def _get_caption(self, title: str, emoji: str = "📝") -> str | None:
        return f"{emoji} {title}" if title else None

    async def _send_from_file_id(self, query, file_id: str, caption: str | None):
        await query.message.reply_document(document=file_id, caption=caption)

    async def _upload_new_file(self, query, filename: str, caption: str | None, url: str, user_id: int):
        with open(filename, "rb") as f:
            sent = await query.message.reply_document(document=f, caption=caption)
        return sent.document.file_id if sent.document else None

    async def _do_download(self, url: str, progress_hook) -> tuple[str, dict]:
        return await ytdlp_client.download_subtitle(url, preferred_langs=self.SUPPORTED_LANGS)
