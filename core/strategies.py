import os
import asyncio
import logging
from abc import ABC, abstractmethod

from telegram import Update
from telegram.ext import CallbackContext

from config import MAX_FILE_SIZE, MAX_CACHE_SIZE
from database.history import get_file_id_by_url, add_history
from utils.logger import log_download
from core.downloader import get_formats
from bot.download import _make_progress_hook

logger = logging.getLogger(__name__)


class DownloadStrategy(ABC):
    """Base class for download strategies using Template Method pattern."""
    
    @property
    @abstractmethod
    def download_type(self) -> str:
        """Return the type identifier for this strategy."""
        pass
    
    @property
    @abstractmethod
    def telegram_method(self) -> str:
        """Return 'video', 'audio', or 'photo' for Telegram sending."""
        pass
    
    def _get_caption(self, title: str, emoji: str = "🎬") -> str | None:
        """Build caption from title."""
        return f"{emoji} {title}" if title else None
    
    async def _check_cached_file(self, url: str, context: CallbackContext, user_id: int) -> str | None:
        """Check for cached file from recent downloads."""
        from database.history import check_recent_download
        
        recent = await check_recent_download(url, max_age_hours=24)
        if recent:
            file_path = recent.get("file_path")
            if file_path and os.path.exists(file_path):
                size = os.path.getsize(file_path)
                if size <= MAX_CACHE_SIZE:
                    logger.info(f"Using cached file for {url}: {file_path}")
                    return file_path
        return None
    
    async def _get_file_id_or_upload(self, query, url: str, filename: str, caption: str | None, user_id: int):
        """Get existing file_id or upload new file. Template method."""
        existing_id = await get_file_id_by_url(url)
        
        if existing_id:
            logger.info(f"Using file_id for {url}: {existing_id}")
            await self._send_from_file_id(query, existing_id, caption)
            return existing_id
        
        return await self._upload_new_file(query, filename, caption, url, user_id)
    
    async def _send_from_file_id(self, query, file_id: str, caption: str | None):
        """Override in subclass for type-specific sending."""
        raise NotImplementedError
    
    async def _upload_new_file(self, query, filename: str, caption: str | None, url: str, user_id: int):
        """Upload new file and save to history."""
        raise NotImplementedError
    
    async def _validate_file_size(self, filename: str, processing_msg, user_id: int) -> bool:
        """Check file size and delete if too large."""
        from utils.i18n import t
        
        file_size = os.path.getsize(filename)
        if file_size > MAX_FILE_SIZE:
            await processing_msg.edit_text(
                t("file_too_large", user_id, size=f"{file_size // (1024*1024)}MB")
            )
            os.remove(filename)
            return False
        return True
    
    def _cleanup_temp_file(self, filename: str, cached_file: str | None):
        """Delete temp file if not cached."""
        if os.path.exists(filename) and not cached_file:
            os.remove(filename)
    
    async def execute(self, query, url: str, processing_msg, context: CallbackContext):
        """Main execution template - orchestrates the download flow."""
        user_id = query.from_user.id
        from utils.i18n import t

        existing_id = await get_file_id_by_url(url)
        if existing_id:
            logger.info(f"Using existing file_id for {url}: {existing_id}")
            try:
                await self._send_from_file_id(query, existing_id, caption=None)
                log_download(query.from_user, f"{self.download_type}_cached_sent", url, "success")
                return
            except Exception as e:
                logger.warning(f"Failed to send via file_id (maybe expired): {e}. Proceeding to download.")
                from database.history import clear_file_id_by_url
                await clear_file_id_by_url(url)
        
        cached_file = await self._check_cached_file(url, context, user_id)
        
        if cached_file and os.path.exists(cached_file):
            filename = cached_file
            title = os.path.splitext(os.path.basename(filename))[0]
            info = {"title": title}
        else:
            try:
                await processing_msg.edit_text(t("downloading", user_id))
                loop = asyncio.get_running_loop()
                progress_hook = _make_progress_hook(processing_msg, loop)
                filename, info = await self._do_download(url, progress_hook)
            except Exception as e:
                logger.error(f"Download failed: {type(e).__name__}: {e}")
                await processing_msg.edit_text(t("download_failed", user_id, error=str(e)))
                log_download(query.from_user, f"{self.download_type}_downloaded", url, f"download_failed: {e}")
                await add_history(query.from_user.id, url, self.download_type, 0, None, "failed")
                return
            
            if not os.path.exists(filename):
                await processing_msg.edit_text(t("download_failed", user_id, error="File not found"))
                log_download(query.from_user, f"{self.download_type}_downloaded", url, "download_failed: File not found")
                await add_history(query.from_user.id, url, self.download_type, 0, None, "failed")
                return
        
        if not await self._validate_file_size(filename, processing_msg, user_id):
            return
        
        title = info.get("title") if info else None
        caption = self._get_caption(title) if title else None
        
        await processing_msg.edit_text(t("uploading", user_id))
        
        try:
            file_size = os.path.getsize(filename)
            logger.info(f"Starting upload: {self.download_type} - file: {filename}, size: {file_size} bytes")
            
            file_id = await self._get_file_id_or_upload(query, url, filename, caption, user_id)
            
            logger.info(f"Upload completed successfully: file_id={file_id}")
            log_download(query.from_user, f"{self.download_type}_downloaded", url, "success", file_size)
            await add_history(query.from_user.id, url, self.download_type, file_size, title, "success", filename, file_id)
        except Exception as e:
            import traceback
            logger.error(f"Upload failed: {type(e).__name__}: {e}")
            logger.error(f"Traceback: {traceback.format_exc()}")
            await processing_msg.edit_text(t("upload_failed", user_id, error=str(e)))
            log_download(query.from_user, f"{self.download_type}_downloaded", url, f"upload_failed: {e}")
            await add_history(query.from_user.id, url, self.download_type, os.path.getsize(filename) if os.path.exists(filename) else 0, title, "failed")
        
        self._cleanup_temp_file(filename, cached_file)
    
    @abstractmethod
    async def _do_download(self, url: str, progress_hook) -> tuple[str, dict]:
        """Implement actual download logic in subclass. Returns (filename, info_dict)."""
        pass


class VideoStrategy(DownloadStrategy):
    @property
    def download_type(self) -> str:
        return "video"
    
    @property
    def telegram_method(self) -> str:
        return "video"
    
    async def _send_from_file_id(self, query, file_id: str, caption: str | None):
        await query.message.reply_video(video=file_id, caption=caption)
        await query.message.reply_text("✅ Sent via file ID (no re-upload)")
    
    async def _upload_new_file(self, query, filename: str, caption: str | None, url: str, user_id: int):
        logger.info(f"Opening file for upload: {filename}")
        with open(filename, "rb") as f:
            logger.info(f"Sending video to Telegram...")
            sent_msg = await query.message.reply_video(video=f, caption=caption)
        logger.info(f"Video upload response received")
        return sent_msg.video.file_id if sent_msg.video else None
    
    async def _do_download(self, url: str, progress_hook):
        from core.downloader import download_video
        format_id = "best"
        return await download_video(url, format_id, progress_hook)


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
        from core.downloader import download_audio
        return await download_audio(url, progress_hook)


class SpotifyStrategy(AudioStrategy):
    @property
    def download_type(self) -> str:
        return "spotify"
    
    async def _do_download(self, url: str, progress_hook) -> tuple[str, dict]:
        from core.downloader import download_spotify
        return await download_spotify(url, progress_hook=progress_hook)

class ThumbnailStrategy(DownloadStrategy):
    @property
    def download_type(self) -> str:
        return "thumbnail"
    
    @property
    def telegram_method(self) -> str:
        return "photo"
    
    async def execute(self, query, url: str, processing_msg, context: CallbackContext):
        user_id = query.from_user.id
        from utils.i18n import t
        
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
        from core.downloader import get_thumbnail
        thumbnail_url, info = await get_thumbnail(url)
        if not thumbnail_url:
            raise RuntimeError("No thumbnail available")
        return thumbnail_url, info


class VideoFormatStrategy(VideoStrategy):
    """Video strategy with specific format selection."""
    
    def __init__(self, format_id: str = "best"):
        self._format_id = format_id
        super().__init__()
    
    @property
    def download_type(self) -> str:
        return f"video_{self._format_id}"
    
    async def _do_download(self, url: str, progress_hook):
        from core.downloader import download_video
        return await download_video(url, self._format_id, progress_hook)


# core/strategies.py — 新增 SubtitleStrategy

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
        from core.downloader import download_subtitle
        return await download_subtitle(url, preferred_langs=self.SUPPORTED_LANGS)


class StrategyFactory:
    """Factory for creating download strategies with lazy loading."""
    
    _strategies: dict[str, DownloadStrategy] = {}
    _strategy_classes = {
        "download_video": VideoStrategy,
        "download_audio": AudioStrategy,
        "download_thumbnail": ThumbnailStrategy,
        "spotify": SpotifyStrategy,
        "subtitle": SubtitleStrategy,
    }
    
    @classmethod
    def get(cls, key: str) -> DownloadStrategy | None:
        if key not in cls._strategies:
            if key in cls._strategy_classes:
                cls._strategies[key] = cls._strategy_classes[key]()
            elif key.startswith("video_"):
                format_id = key.replace("video_", "")
                cls._strategies[key] = VideoFormatStrategy(format_id)
            else:
                return None
        return cls._strategies.get(key)
    
    @classmethod
    def register(cls, key: str, strategy_class: type[DownloadStrategy]):
        cls._strategy_classes[key] = strategy_class
        if key in cls._strategies:
            del cls._strategies[key]
    
    @classmethod
    def clear_cache(cls):
        """Clear cached strategy instances (useful for testing)."""
        cls._strategies.clear()
