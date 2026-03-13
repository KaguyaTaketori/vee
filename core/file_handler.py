"""
File Handler Service - Decoupled file operations for Telegram uploads.

Handles all file-related operations: caching, uploading, cleanup.
This separates file I/O from download logic.
"""

import os
import logging
from dataclasses import dataclass
from typing import Any

from telegram import Update
from telegram.ext import CallbackContext

from config import MAX_FILE_SIZE, MAX_CACHE_SIZE
from core.history import check_recent_download, get_file_id_by_url, add_history
from core.logger import log_download

logger = logging.getLogger(__name__)


@dataclass
class FileUploadResult:
    """Result of a file upload operation."""
    success: bool
    file_id: str | None = None
    error: str | None = None


class FileHandler:
    """Handles file caching, upload, and cleanup operations."""
    
    def __init__(self):
        pass
    
    def check_cache(self, url: str, max_age_hours: int = 24) -> tuple[str | None, int]:
        """
        Check if there's a cached file for this URL.
        Returns: (file_path, file_size) or (None, 0)
        """
        recent = check_recent_download(url, max_age_hours=max_age_hours)
        if recent:
            file_path = recent.get("file_path")
            if file_path and os.path.exists(file_path):
                try:
                    size = os.path.getsize(file_path)
                    if size <= MAX_CACHE_SIZE:
                        logger.info(f"Cache hit for {url}: {file_path}")
                        return file_path, size
                except OSError:
                    pass
        return None, 0
    
    def check_file_id(self, url: str) -> str | None:
        """Check if there's a cached file_id for this URL."""
        return get_file_id_by_url(url)
    
    async def upload_video(
        self,
        query,
        filename: str,
        caption: str | None = None,
        file_id: str | None = None
    ) -> FileUploadResult:
        """Upload video file to Telegram."""
        try:
            if file_id:
                await query.message.reply_video(video=file_id, caption=caption)
                await query.message.reply_text("✅ Sent via file ID (no re-upload)")
            else:
                with open(filename, "rb") as f:
                    sent_msg = await query.message.reply_video(video=f, caption=caption)
                file_id = sent_msg.video.file_id if sent_msg.video else None
            
            return FileUploadResult(success=True, file_id=file_id)
        except Exception as e:
            logger.error(f"Video upload failed: {e}")
            return FileUploadResult(success=False, error=str(e))
    
    async def upload_audio(
        self,
        query,
        filename: str,
        title: str | None = None,
        caption: str | None = None,
        file_id: str | None = None
    ) -> FileUploadResult:
        """Upload audio file to Telegram."""
        try:
            if file_id:
                await query.message.reply_audio(audio=file_id, title=title, caption=caption)
                await query.message.reply_text("✅ Sent via file ID (no re-upload)")
            else:
                with open(filename, "rb") as f:
                    sent_msg = await query.message.reply_audio(audio=f, title=title, caption=caption)
                file_id = sent_msg.audio.file_id if sent_msg.audio else None
            
            return FileUploadResult(success=True, file_id=file_id)
        except Exception as e:
            logger.error(f"Audio upload failed: {e}")
            return FileUploadResult(success=False, error=str(e))
    
    async def upload_photo(
        self,
        query,
        filename: str | None = None,
        photo_url: str | None = None,
        caption: str | None = None,
        file_id: str | None = None
    ) -> FileUploadResult:
        """Upload photo to Telegram (from file or URL)."""
        try:
            if file_id:
                await query.message.reply_photo(photo=file_id, caption=caption)
            elif photo_url:
                await query.message.reply_photo(photo=photo_url, caption=caption)
            elif filename:
                with open(filename, "rb") as f:
                    sent_msg = await query.message.reply_photo(photo=f, caption=caption)
                file_id = sent_msg.photo[-1].file_id if sent_msg.photo else None
            
            return FileUploadResult(success=True, file_id=file_id)
        except Exception as e:
            logger.error(f"Photo upload failed: {e}")
            return FileUploadResult(success=False, error=str(e))
    
    def cleanup(self, filename: str | None, cached_file: str | None = None):
        """Remove temp file if not cached."""
        if filename and os.path.exists(filename) and filename != cached_file:
            try:
                os.remove(filename)
                logger.info(f"Cleaned up temp file: {filename}")
            except OSError as e:
                logger.warning(f"Failed to cleanup {filename}: {e}")
    
    def log_download(
        self,
        user,
        url: str,
        download_type: str,
        status: str,
        file_size: int = 0,
        format_id: str = None,
        title: str = None,
        filename: str = None,
        file_id: str = None
    ):
        """Log download and add to history."""
        log_download(user, f"{download_type}_downloaded", url, status, file_size, format_id)
        
        add_history(
            user.id,
            url,
            download_type,
            file_size,
            title,
            status,
            filename,
            file_id
        )


file_handler = FileHandler()
