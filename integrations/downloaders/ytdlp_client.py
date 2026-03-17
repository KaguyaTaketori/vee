import os
import time
import yt_dlp
import logging
import asyncio
from cachetools import TTLCache
from typing import NamedTuple
from yt_dlp.utils import ExtractorError
from urllib.parse import urlparse
from utils.utils import get_running_loop as _get_running_loop
from config import MAX_FILE_SIZE, get_temp_template, TEMP_DIR, BOT_FILE_PREFIX
from .helpers import get_cookie_file, refresh_cookies_async, resolve_short_url, mask_url

logger = logging.getLogger(__name__)

_FORMATS_CACHE_TTL = 300
_FORMATS_CACHE_MAX = 100
_formats_cache_lock: asyncio.Lock | None = None
_FORMATS_CACHE: TTLCache = TTLCache(maxsize=_FORMATS_CACHE_MAX, ttl=_FORMATS_CACHE_TTL)
_inflight: dict[str, asyncio.Future] = {}

def _in_executor(sync_fn):
    async def wrapper(url: str, *args, **kwargs):
        url = await YtDlpHelper.prepare_url(url)
        loop = _get_running_loop()
        return await loop.run_in_executor(None, lambda: sync_fn(url, *args, **kwargs))
    wrapper.__name__ = sync_fn.__name__
    return wrapper

def _get_formats_lock() -> asyncio.Lock:
    global _formats_cache_lock
    if _formats_cache_lock is None:
        _formats_cache_lock = asyncio.Lock()
    return _formats_cache_lock


class CookieExpiredError(Exception):
    """Raised when yt-dlp fails due to expired or missing cookies."""
    pass

class YtDlpHelper:
    """Helper class to reduce duplicate yt-dlp configuration code."""
    
    def __init__(self, url: str = None):
        self.url = url
        self.opts = self._build_opts()
    
    def _build_opts(self):
        opts = {
            "quiet": True,
            "no_warnings": True,
            "extractor_retries": 3,
            "fragment_retries": 3,
            "js_runtimes": {"node": {}},
        }
        if self.url:
            cookie_file = self._get_cookie_file(self.url)
            if cookie_file:
                opts["cookiefile"] = cookie_file
            opts = self._add_extractor_headers(self.url, opts)
        return opts
    
    def _get_cookie_file(self, url: str) -> str:
        return get_cookie_file(url)

    def _add_extractor_headers(self, url, opts):
        if "bilibili.com" in url:
            opts["http_headers"] = {
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
                "Referer": "https://www.bilibili.com/",
                "Origin": "https://www.bilibili.com",
            }
        return opts
    
    def get_opts(self):
        return self.opts
    
    def merge_opts(self, **kwargs):
        self.opts.update(kwargs)
        return self.opts
    
    @staticmethod
    async def prepare_url(url: str) -> str:
        """Common URL preprocessing: resolve short URLs and refresh cookies."""
        await refresh_cookies_async()
        return await resolve_short_url(url)


def _is_cookie_error(e: Exception) -> bool:
    msg = str(e).lower()
    return any(kw in msg for kw in [
        "sign in", "log in", "login required",
        "confirm you're not a bot", "cookies",
        "not a bot", "private video",
    ])


async def get_formats_cached(url: str) -> tuple:
    lock = _get_formats_lock()

    async with lock:
        cached = _FORMATS_CACHE.get(url)
        if cached is not None:
            return cached

        if url in _inflight:
            fut = _inflight[url]
        else:
            fut = asyncio.get_event_loop().create_future()
            _inflight[url] = fut

    if not fut.done():
        try:
            result = await get_formats(url)
            async with lock:
                _FORMATS_CACHE[url] = result
                _inflight.pop(url, None)
            fut.set_result(result)
        except Exception as exc:
            async with lock:
                _inflight.pop(url, None)
            if not fut.done():
                fut.set_exception(exc)
            raise

    return await asyncio.shield(fut)


async def get_formats(url):
    url = await YtDlpHelper.prepare_url(url)

    loop = _get_running_loop()
    def _get():
        helper = YtDlpHelper(url)
        ydl_opts = helper.get_opts()
        ydl_opts['logger'] = logging.getLogger('yt_dlp')
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            try:
                info = ydl.extract_info(url, download=False)
            except ExtractorError as e:
                if _is_cookie_error(e):
                    raise CookieExpiredError(str(e)) from e
                raise
            formats = info.get("formats") or []
            logger.info(
                f"URL: {mask_url(url)}, Format count: {len(formats)}, "
                f"First 10: {[(f.get('format_id'), f.get('height'), f.get('ext')) for f in formats[:10]]}"
            )
            logger.debug(f"Full URL (debug only): {url}")
            return formats, info
    return await loop.run_in_executor(None, _get)


async def download_video(url: str, format_id: str, progress_hook=None) -> tuple[str, dict]:
    url = await YtDlpHelper.prepare_url(url)

    from .aria2_client import is_aria2_available, download_video_aria2
    from config import USE_ARIA2

    needs_merging = format_id == "best" or "+" in str(format_id)
    if USE_ARIA2 and is_aria2_available() and format_id == "best" and not needs_merging:
        try:
            return await download_video_aria2(url, format_id, progress_hook)
        except Exception as e:
            logger.warning("aria2 失败，回退到 yt-dlp: %s", e)

    loop = _get_running_loop()

    def _download():
        helper = YtDlpHelper(url)
        ydl_opts = helper.get_opts()

        if "twitter.com" in url or "x.com" in url:
            ydl_opts["extractor_args"] = {"twitter": {"lang": "en"}}

        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(url, download=False)

        formats = info.get("formats", [])
        selected = next(
            (f for f in formats if str(f.get("format_id")) == str(format_id)), None
        )
        acodec = selected.get("acodec") if selected else None
        has_audio = acodec not in (None, "none")

        if has_audio:
            ydl_opts["format"] = format_id
        elif format_id == "best":
            ydl_opts["format"] = "bestvideo+bestaudio/best"
            ydl_opts["merge_output_format"] = "mp4"
        else:
            ydl_opts["format"] = f"{format_id}+bestaudio/best"
            ydl_opts["merge_output_format"] = "mp4"

        ydl_opts["outtmpl"] = get_temp_template()
        if progress_hook:
            ydl_opts["progress_hooks"] = [progress_hook]

        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            ydl.process_ie_result(info, download=True)
            filename = ydl.prepare_filename(info)

        return filename, info

    return await loop.run_in_executor(None, _download)


@_in_executor
def download_audio(url: str, progress_hook=None) -> tuple[str, dict]:
    helper = YtDlpHelper(url)
    ydl_opts = helper.merge_opts(
        format="bestaudio/best",
        outtmpl=get_temp_template(),
        postprocessors=[{"key": "FFmpegExtractAudio", "preferredcodec": "mp3"}],
        **({"progress_hooks": [progress_hook]} if progress_hook else {}),
    )
    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
        info = ydl.extract_info(url, download=True)
        filename = os.path.splitext(ydl.prepare_filename(info))[0] + ".mp3"
    return filename, info


@_in_executor
def get_thumbnail(url: str) -> tuple[str | None, dict]:
    with yt_dlp.YoutubeDL(YtDlpHelper(url).get_opts()) as ydl:
        info = ydl.extract_info(url, download=False)
    return info.get("thumbnail"), info


@_in_executor
def get_images(url: str) -> tuple[list[str], str]:
    with yt_dlp.YoutubeDL(YtDlpHelper(url).get_opts()) as ydl:
        info = ydl.extract_info(url, download=False)
    images = info.get("thumbnail") or []
    if isinstance(images, str):
        images = [images]
    for thumb in info.get("additional_thumbnails") or []:
        if u := thumb.get("url"):
            images.append(u)
    return images, info.get("title", "Image")


@_in_executor
def download_subtitle(url: str, preferred_langs: list[str] | None = None) -> tuple[str, dict]:
    helper = YtDlpHelper(url)
    ydl_opts = helper.merge_opts(
        skip_download=True, writesubtitles=True, writeautomaticsub=True,
        subtitlesformat="srt/vtt/best", outtmpl=get_temp_template(),
        **({"subtitleslangs": preferred_langs} if preferred_langs else {}),
    )
    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
        info = ydl.extract_info(url, download=True)
    for ext in ("srt", "vtt", "ass"):
        for fname in os.listdir(TEMP_DIR):
            if fname.startswith(BOT_FILE_PREFIX) and fname.endswith(f".{ext}"):
                full_path = os.path.join(TEMP_DIR, fname)
                if time.time() - os.path.getmtime(full_path) < 60:
                    return full_path, info
    raise RuntimeError("未找到字幕文件，该视频可能没有字幕。")


def is_spotify_url(url: str) -> bool:
    return "spotify.com" in url.lower()
