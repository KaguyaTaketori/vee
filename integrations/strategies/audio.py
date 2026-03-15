import os
import logging

from .base import DownloadStrategy
from integrations.downloaders import ytdlp_client

logger = logging.getLogger(__name__)


class AudioStrategy(DownloadStrategy):
    @property
    def download_type(self) -> str:
        return "audio"
    
    @property
    def telegram_method(self) -> str:
        return "audio"
    
    def _get_caption(self, title: str, emoji: str = "🎵") -> str | None:
        return f"{emoji} {title}" if title else None
    
    async def _send_from_file_id(self, query, file_id: str, caption: str | None):
        await query.message.reply_audio(audio=file_id, title=caption)
        await query.message.reply_text("✅ Sent via file ID (no re-upload)")
    
    async def _upload_new_file(self, query, filename: str, caption: str | None, url: str, user_id: int):
        title = os.path.splitext(os.path.basename(filename))[0]
        logger.info(f"Opening file for upload: {filename}")
        with open(filename, "rb") as f:
            logger.info(f"Sending audio to Telegram...")
            sent_msg = await query.message.reply_audio(audio=f, title=title)
        logger.info(f"Audio upload response received")
        return sent_msg.audio.file_id if sent_msg.audio else None
    
    async def _do_download(self, url: str, progress_hook):
        return await ytdlp_client.download_audio(url, progress_hook)
