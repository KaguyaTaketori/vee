import os
import logging
import yt_dlp
from urllib.parse import urlparse

from utils.utils import get_running_loop as _get_running_loop
from config import MAX_FILE_SIZE, get_temp_template, TEMP_DIR, BOT_FILE_PREFIX
from .helpers import get_cookie_file, refresh_cookies_async, resolve_short_url, mask_url

logger = logging.getLogger(__name__)

_FORMATS_CACHE: dict[str, tuple] = {}
_FORMATS_CACHE_MAX = 100


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


async def get_formats_cached(url: str) -> tuple:
    """Get formats with simple LRU-style cache."""
    if url in _FORMATS_CACHE:
        return _FORMATS_CACHE[url]
    
    result = await get_formats(url)
    _FORMATS_CACHE[url] = result
    
    if len(_FORMATS_CACHE) > _FORMATS_CACHE_MAX:
        keys_to_remove = list(_FORMATS_CACHE.keys())[:_FORMATS_CACHE_MAX // 2]
        for k in keys_to_remove:
            del _FORMATS_CACHE[k]
    
    return result


async def get_formats(url):
    url = await YtDlpHelper.prepare_url(url)
    
    loop = _get_running_loop()
    def _get():
        helper = YtDlpHelper(url)
        ydl_opts = helper.get_opts()
        ydl_opts['logger'] = logging.getLogger('yt_dlp')
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(url, download=False)
            formats = info.get("formats") or []
            logger.info(f"URL: {mask_url(url)}, Format count: {len(formats)}, First 10: {[(f.get('format_id'), f.get('height'), f.get('ext')) for f in formats[:10]]}")
            logger.debug(f"Full URL (debug only): {url}")
            return formats, info
    return await loop.run_in_executor(None, _get)


async def download_video(url, format_id, progress_hook=None):
    url = await YtDlpHelper.prepare_url(url)
    
    needs_merging = format_id == "best" or "+" in str(format_id)
    
    from .aria2_client import is_aria2_available, download_video_aria2
    from config import USE_ARIA2
    
    if USE_ARIA2 and is_aria2_available() and format_id == "best" and not needs_merging:
        try:
            return await download_video_aria2(url, format_id, progress_hook)
        except Exception as e:
            logger.warning(f"aria2 failed, falling back to yt-dlp: {e}")
    
    loop = _get_running_loop()
    def _download():
        helper = YtDlpHelper(url)
        ydl_opts = helper.get_opts()
        
        if "twitter.com" in url or "x.com" in url:
            ydl_opts["extractor_args"] = {
                "twitter": {"lang": "en"}
            }
        
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(url, download=False)
            formats = info.get("formats", [])
            
            selected_format = None
            if format_id != "best":
                for f in formats:
                    if str(f.get("format_id")) == str(format_id):
                        selected_format = f
                        break
            
            acodec = selected_format.get("acodec") if selected_format else None
            has_audio = acodec not in (None, "none") if acodec else False
            
            logger.debug(f"Format {format_id} selected: acodec={acodec}, has_audio={has_audio}")
            
            if has_audio:
                ydl_opts["format"] = format_id
                ydl_opts.pop("merge_output_format", None)
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
        info = ydl.extract_info(url, download=True)
        filename = ydl.prepare_filename(info)
    return filename, info
    return await loop.run_in_executor(None, _download)

async def download_subtitle(url: str, preferred_langs: list[str] | None = None) -> tuple[str, dict]:
    url = await YtDlpHelper.prepare_url(url)
    loop = _get_running_loop()

    def _download():
        helper = YtDlpHelper(url)
        ydl_opts = helper.get_opts()
        ydl_opts.update({
            "skip_download": True,
            "writesubtitles": True,
            "writeautomaticsub": True,
            "subtitlesformat": "srt/vtt/best",
            "outtmpl": get_temp_template(),
        })

        if preferred_langs:
            ydl_opts["subtitleslangs"] = preferred_langs

        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(url, download=True)
            title = info.get("title", "subtitle")

        for ext in ("srt", "vtt", "ass"):
            for fname in os.listdir(TEMP_DIR):
                if fname.startswith(BOT_FILE_PREFIX) and fname.endswith(f".{ext}"):
                    full_path = os.path.join(TEMP_DIR, fname)
                    if time.time() - os.path.getmtime(full_path) < 60:
                        return full_path, info

        raise RuntimeError("No subtitle file found. The video may not have subtitles.")

    import time
    return await loop.run_in_executor(None, _download)


async def download_audio(url, progress_hook=None):
    url = await YtDlpHelper.prepare_url(url)
    
    loop = _get_running_loop()
    def _download():
        helper = YtDlpHelper(url)
        ydl_opts = helper.get_opts()
        ydl_opts.update({
            "format": "bestaudio/best",
            "outtmpl": get_temp_template(),
            "postprocessors": [{
                "key": "FFmpegExtractAudio",
                "preferredcodec": "mp3",
            }],
            "progress_hooks": [progress_hook] if progress_hook else None,
        })

        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(url, download=True)
            filename = ydl.prepare_filename(info)
            filename = os.path.splitext(filename)[0] + ".mp3"
        return filename, info
    return await loop.run_in_executor(None, _download)


async def get_thumbnail(url):
    url = await YtDlpHelper.prepare_url(url)
    loop = _get_running_loop()
    def _get():
        helper = YtDlpHelper(url)
        with yt_dlp.YoutubeDL(helper.get_opts()) as ydl:
            info = ydl.extract_info(url, download=False)
            thumbnail_url = info.get("thumbnail")
        return thumbnail_url, info
    return await loop.run_in_executor(None, _get)


async def get_images(url):
    url = await YtDlpHelper.prepare_url(url)
    loop = _get_running_loop()
    def _get():
        helper = YtDlpHelper(url)
        with yt_dlp.YoutubeDL(helper.get_opts()) as ydl:
            info = ydl.extract_info(url, download=False)
            images = info.get("thumbnail") or []
            if isinstance(images, str):
                images = [images]
            
            additional_thumbnails = info.get("additional_thumbnails") or []
            if additional_thumbnails:
                for t in additional_thumbnails:
                    if t.get("url"):
                        images.append(t.get("url"))
            
            title = info.get("title", "Image")
        return images, title
    return await loop.run_in_executor(None, _get)


async def get_direct_url(url, format_id=None):
    url = await YtDlpHelper.prepare_url(url)
    
    loop = _get_running_loop()
    def _get():
        helper = YtDlpHelper(url)
        ydl_opts = helper.get_opts()
        if format_id:
            ydl_opts["format"] = f"{format_id}+bestaudio/best"
        
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(url, download=False)
            formats = info.get("formats") or []
            
            if format_id:
                for f in formats:
                    if str(f.get("format_id")) == str(format_id):
                        return f.get("url"), info
                return formats[0].get("url"), info if formats else (None, info)
            else:
                best = formats[-1] if formats else None
                return best.get("url") if best else None, info
    return await loop.run_in_executor(None, _get)


def is_spotify_url(url: str) -> bool:
    return "spotify.com" in url.lower()
