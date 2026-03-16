import os
import logging

from .base import DownloadStrategy
from integrations.downloaders import ytdlp_client
from utils.i18n import t

logger = logging.getLogger(__name__)


class VideoStrategy(DownloadStrategy):
    @property
    def download_type(self) -> str:
        return "video"
    
    @property
    def telegram_method(self) -> str:
        return "video"
    
    async def _send_from_file_id(self, query, file_id: str, caption: str | None):
        user_id = query.from_user.id
        await query.message.reply_video(video=file_id, caption=caption)
        await query.message.reply_text(t("sent_via_file_id_no_reupload", user_id))
    
    async def _upload_new_file(self, query, filename: str, caption: str | None, url: str, user_id: int):
        logger.info(f"Opening file for upload: {filename}")
        with open(filename, "rb") as f:
            logger.info(f"Sending video to Telegram...")
            sent_msg = await query.message.reply_video(video=f, caption=caption)
        logger.info(f"Video upload response received")
        return sent_msg.video.file_id if sent_msg.video else None
    
    async def _do_download(self, url: str, progress_hook):
        return await ytdlp_client.download_video(url, "best", progress_hook)


class VideoFormatStrategy(VideoStrategy):
    """Video strategy with specific format selection."""
    
    def __init__(self, format_id: str = "best"):
        self._format_id = format_id
        super().__init__()
    
    @property
    def download_type(self) -> str:
        return f"video_{self._format_id}"
    
    async def _do_download(self, url: str, progress_hook):
        return await ytdlp_client.download_video(url, self._format_id, progress_hook)
