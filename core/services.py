"""
Download Service - Abstraction layer to decouple download logic from consumers.

This module provides a clean interface for downloading media, independent of
the underlying implementation (yt-dlp, aria2, spotdl, etc.).
"""

import os
import asyncio
from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Any

import logging

logger = logging.getLogger(__name__)


@dataclass
class DownloadResult:
    """Result of a download operation."""
    success: bool
    filename: str | None = None
    info: dict[str, Any] | None = None
    error: str | None = None
    file_size: int = 0
    file_id: str | None = None


@dataclass
class FormatInfo:
    """Information about available formats."""
    format_id: str
    height: int | None
    ext: str
    filesize: int | None
    has_audio: bool


class DownloadService(ABC):
    """Abstract base class for download services."""
    
    @property
    @abstractmethod
    def service_name(self) -> str:
        """Identifier for this download service."""
        pass
    
    @abstractmethod
    async def get_formats(self, url: str) -> tuple[list[FormatInfo], dict]:
        """Get available formats for a URL."""
        pass
    
    @abstractmethod
    async def download(
        self, 
        url: str, 
        format_id: str = "best",
        progress_hook: callable = None
    ) -> DownloadResult:
        """Download media from URL."""
        pass
    
    @abstractmethod
    async def get_thumbnail(self, url: str) -> tuple[str | None, dict]:
        """Get thumbnail URL and info."""
        pass
    
    @property
    def priority(self) -> int:
        """Service priority (lower = checked first)."""
        return 100


class YtDlpService(DownloadService):
    """YouTube-dl/yt-dlp based download service."""
    
    def __init__(self):
        import httpx
        from config import COOKIE_FILE, COOKIES_DIR
        
        self._cookie_file = COOKIE_FILE
        self._cookies_dir = COOKIES_DIR
        self._resolved_urls: dict[str, str] = {}
    
    @property
    def service_name(self) -> str:
        return "yt_dlp"
    
    @property
    def priority(self) -> int:
        return 10
    
    async def get_formats(self, url: str) -> tuple[list[FormatInfo], dict]:
        from core.downloader import get_formats as _get_formats
        formats, info = await _get_formats(url)
        
        format_list = []
        for f in formats:
            acodec = f.get("acodec", "none")
            has_audio = acodec not in (None, "none")
            format_list.append(FormatInfo(
                format_id=f.get("format_id", ""),
                height=f.get("height"),
                ext=f.get("ext", ""),
                filesize=f.get("filesize") or f.get("filesize_approx"),
                has_audio=has_audio
            ))
        
        return format_list, info
    
    async def download(
        self, 
        url: str, 
        format_id: str = "best",
        progress_hook: callable = None
    ) -> DownloadResult:
        from core.downloader import download_video as _download_video
        
        try:
            filename, info = await _download_video(url, format_id, progress_hook)
            file_size = os.path.getsize(filename) if filename and os.path.exists(filename) else 0
            
            return DownloadResult(
                success=True,
                filename=filename,
                info=info,
                file_size=file_size
            )
        except Exception as e:
            return DownloadResult(success=False, error=str(e))
    
    async def get_thumbnail(self, url: str) -> tuple[str | None, dict]:
        from core.downloader import get_thumbnail as _get_thumbnail
        return await _get_thumbnail(url)


class AudioService(DownloadService):
    """Audio download service."""
    
    @property
    def service_name(self) -> str:
        return "audio"
    
    @property
    def priority(self) -> int:
        return 20
    
    async def get_formats(self, url: str) -> tuple[list[FormatInfo], dict]:
        return [], {}
    
    async def download(
        self, 
        url: str, 
        format_id: str = "best",
        progress_hook: callable = None
    ) -> DownloadResult:
        from core.downloader import download_audio as _download_audio
        from core.downloader import is_spotify_url
        
        try:
            if is_spotify_url(url):
                from core.downloader import download_spotify
                filename, info = await download_spotify(url, progress_hook)
            else:
                filename, info = await _download_audio(url, progress_hook)
            
            file_size = os.path.getsize(filename) if filename and os.path.exists(filename) else 0
            
            return DownloadResult(
                success=True,
                filename=filename,
                info=info,
                file_size=file_size
            )
        except Exception as e:
            return DownloadResult(success=False, error=str(e))
    
    async def get_thumbnail(self, url: str) -> tuple[str | None, dict]:
        return None, {}


class ThumbnailService(DownloadService):
    """Thumbnail/image download service."""
    
    @property
    def service_name(self) -> str:
        return "thumbnail"
    
    @property
    def priority(self) -> int:
        return 30
    
    async def get_formats(self, url: str) -> tuple[list[FormatInfo], dict]:
        return [], {}
    
    async def download(
        self, 
        url: str, 
        format_id: str = "best",
        progress_hook: callable = None
    ) -> DownloadResult:
        from core.downloader import get_thumbnail as _get_thumbnail
        
        try:
            thumbnail_url, info = await _get_thumbnail(url)
            if not thumbnail_url:
                return DownloadResult(success=False, error="No thumbnail available")
            
            return DownloadResult(
                success=True,
                filename=thumbnail_url,
                info=info
            )
        except Exception as e:
            return DownloadResult(success=False, error=str(e))
    
    async def get_thumbnail(self, url: str) -> tuple[str | None, dict]:
        return await self.download(url).filename, {}


class DownloadServiceRegistry:
    """Registry for download services with auto-detection."""
    
    _services: dict[str, DownloadService] = {}
    _initialized = False
    
    @classmethod
    def register(cls, service: DownloadService):
        """Register a download service."""
        cls._services[service.service_name] = service
    
    @classmethod
    def get(cls, name: str) -> DownloadService | None:
        """Get service by name."""
        if not cls._initialized:
            cls._init_default_services()
        return cls._services.get(name)
    
    @classmethod
    def get_for_url(cls, url: str) -> DownloadService | None:
        """Auto-detect appropriate service for URL."""
        if not cls._initialized:
            cls._init_default_services()
        
        from core.downloader import is_spotify_url
        
        if is_spotify_url(url):
            return cls._services.get("audio")
        
        return cls._services.get("yt_dlp")
    
    @classmethod
    def _init_default_services(cls):
        """Initialize default services."""
        cls.register(YtDlpService())
        cls.register(AudioService())
        cls.register(ThumbnailService())
        cls._initialized = True
    
    @classmethod
    def list_services(cls) -> list[str]:
        """List all registered services."""
        if not cls._initialized:
            cls._init_default_services()
        return list(cls._services.keys())
