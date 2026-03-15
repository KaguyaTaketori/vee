import logging
from telegram.ext import CallbackContext

from .base import DownloadStrategy
from database.history import add_history
from utils.logger import log_download
from utils.i18n import t
from integrations.downloaders import ytdlp_client

logger = logging.getLogger(__name__)


class ThumbnailStrategy(DownloadStrategy):
    @property
    def download_type(self) -> str:
        return "thumbnail"
    
    @property
    def telegram_method(self) -> str:
        return "photo"
    
    async def execute(self, query, url: str, processing_msg, context: CallbackContext):
        user_id = query.from_user.id
        
        await processing_msg.edit_text(t("downloading", user_id))
        try:
            thumbnail_url, info = await self._do_download(url, None)
        except Exception as e:
            logger.error(f"Thumbnail download failed: {e}")
            await processing_msg.edit_text(t("download_failed", user_id, error=str(e)))
            return
            
        title = info.get("title") if info else None
        caption = self._get_caption(title) if title else None
        
        await processing_msg.edit_text(t("uploading", user_id))
        
        try:
            logger.info(f"Starting upload: {self.download_type} - URL: {thumbnail_url}")
            file_id = await self._get_file_id_or_upload(query, url, thumbnail_url, caption, user_id)
            
            log_download(query.from_user, f"{self.download_type}_downloaded", url, "success", 0)
            await add_history(query.from_user.id, url, self.download_type, 0, title, "success", thumbnail_url, file_id)
        except Exception as e:
            await processing_msg.edit_text(t("upload_failed", user_id, error=str(e)))
            log_download(query.from_user, f"{self.download_type}_downloaded", url, f"upload_failed: {e}")
            await add_history(query.from_user.id, url, self.download_type, 0, title, "failed")
    
    async def _send_from_file_id(self, query, file_id: str, caption: str | None):
        await query.message.reply_photo(photo=file_id, caption=caption)
    
    async def _upload_new_file(self, query, filename: str, caption: str | None, url: str, user_id: int):
        sent_msg = await query.message.reply_photo(photo=filename, caption=caption)
        return sent_msg.photo[-1].file_id if sent_msg.photo else None
    
    async def _do_download(self, url: str, progress_hook):
        thumbnail_url, info = await ytdlp_client.get_thumbnail(url)
        if not thumbnail_url:
            raise RuntimeError("No thumbnail available")
        return thumbnail_url, info
