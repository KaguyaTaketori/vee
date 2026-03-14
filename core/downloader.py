import os
import logging
import asyncio
import subprocess
import time
import yt_dlp
import httpx
from functools import lru_cache
from config import MAX_FILE_SIZE, COOKIE_FILE, get_temp_template, TEMP_DIR, BOT_FILE_PREFIX, USE_ARIA2, ARIA2_CONNECTIONS, COOKIE_REFRESH_CMD, COOKIE_REFRESH_INTERVAL_HOURS, COOKIES_DIR

logger = logging.getLogger(__name__)

_cookie_last_refresh = 0


def _get_running_loop():
    try:
        return asyncio.get_running_loop()
    except RuntimeError:
        try:
            return asyncio.get_event_loop()
        except RuntimeError:
            return None


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
        if "youtube.com" in url:
            site_cookie = os.path.join(COOKIES_DIR, "www.youtube.com_cookies.txt")
            if os.path.exists(site_cookie):
                return site_cookie
        elif "bilibili.com" in url or "b23.tv" in url:
            site_cookie = os.path.join(COOKIES_DIR, "www.bilibili.com_cookies.txt")
            if os.path.exists(site_cookie):
                return site_cookie
        
        if COOKIE_FILE and os.path.exists(COOKIE_FILE):
            return COOKIE_FILE
        return ""
    
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
    def prepare_url(url: str) -> str:
        """Common URL preprocessing: resolve short URLs and refresh cookies."""
        loop = _get_running_loop()
        if loop and loop.is_running():
            asyncio.create_task(refresh_cookies_async())
        else:
            refresh_cookies()
        return resolve_short_url(url)


def resolve_short_url(url: str) -> str:
    """Resolve short URLs (b23.tv, youtu.be, etc.) to full URLs."""
    short_domains = {"b23.tv", "youtu.be"}
    try:
        domain = url.split("/")[2].lower() if "://" in url else url.split("/")[0].lower()
        if any(d in domain for d in short_domains):
            logger.info(f"Resolving short URL: {url}")
            response = httpx.head(url, follow_redirects=True, timeout=10)
            resolved = str(response.url)
            logger.info(f"Resolved to: {resolved}")
            return resolved
    except Exception as e:
        logger.warning(f"Failed to resolve short URL {url}: {e}")
    return url


def _get_bilibili_info(url: str) -> dict:
    """Get video info from Bilibili API directly."""
    import re
    
    bvid = None
    if "b23.tv" in url:
        resolved = resolve_short_url(url)
        match = re.search(r'BV[\w]+', resolved)
        if match:
            bvid = match.group(0)
    else:
        match = re.search(r'BV[\w]+', url)
        if match:
            bvid = match.group(0)
    
    if not bvid:
        return None
    
    api_url = f"https://api.bilibili.com/x/web-interface/view?bvid={bvid}"
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
        "Referer": "https://www.bilibili.com/",
    }
    
    try:
        response = httpx.get(api_url, headers=headers, timeout=10)
        data = response.json()
        if data.get("code") == 0:
            return data.get("data")
    except Exception as e:
        logger.error(f"Bilibili API error: {e}")
    
    return None


def refresh_cookies():
    global _cookie_last_refresh
    if not COOKIE_REFRESH_CMD or not COOKIE_FILE:
        return False
    
    if time.time() - _cookie_last_refresh < (COOKIE_REFRESH_INTERVAL_HOURS * 3600):
        return False
    
    try:
        logger.info("Refreshing cookies...")
        result = subprocess.run(
            COOKIE_REFRESH_CMD,
            shell=True,
            capture_output=True,
            text=True,
            check=True
        )
        _cookie_last_refresh = time.time()
        logger.info(f"Cookies refreshed successfully")
        return True
    except subprocess.CalledProcessError as e:
        logger.error(f"Failed to refresh cookies: {e.stderr}")
        return False


async def refresh_cookies_async():
    global _cookie_last_refresh
    if not COOKIE_REFRESH_CMD or not COOKIE_FILE:
        return False
    
    if time.time() - _cookie_last_refresh < (COOKIE_REFRESH_INTERVAL_HOURS * 3600):
        return False
    
    try:
        logger.info("Refreshing cookies (async)...")
        process = await asyncio.create_subprocess_shell(
            COOKIE_REFRESH_CMD,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE
        )
        stdout, stderr = await process.communicate()
        
        if process.returncode != 0:
            logger.error(f"Failed to refresh cookies: {stderr.decode()}")
            return False
        
        _cookie_last_refresh = time.time()
        logger.info(f"Cookies refreshed successfully")
        return True
    except Exception as e:
        logger.error(f"Failed to refresh cookies: {e}")
        return False


def _use_intl_api(url):
    """Convert Bilibili URL to use international API."""
    if "bilibili.com" in url and "b23.tv" not in url:
        return url
    return url


_FORMATS_CACHE = {}

async def get_formats_cached(url):
    """Get formats with caching for improved performance."""
    if url in _FORMATS_CACHE:
        return _FORMATS_CACHE[url]
    
    res = await get_formats(url)
    _FORMATS_CACHE[url] = res
    
    if len(_FORMATS_CACHE) > 100:
        _FORMATS_CACHE.clear()
        
    return res


async def get_formats_cached(url):
    """Get formats with caching for improved performance."""
    try:
        return _cached_get_formats(url)
    except Exception:
        return await get_formats(url)


async def get_formats(url):
    url = YtDlpHelper.prepare_url(url)
    
    loop = _get_running_loop()
    def _get():
        helper = YtDlpHelper(url)
        ydl_opts = helper.get_opts()
        ydl_opts['logger'] = logging.getLogger('yt_dlp')
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(url, download=False)
            formats = info.get("formats") or []
            logger.info(f"URL: {url}, Format count: {len(formats)}, First 10: {[(f.get('format_id'), f.get('height'), f.get('ext')) for f in formats[:10]]}")
            return formats, info
    return await loop.run_in_executor(None, _get)


async def download_video(url, format_id, progress_hook=None):
    url = YtDlpHelper.prepare_url(url)
    
    needs_merging = format_id == "best" or "+" in str(format_id)
    
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
            
            logger.error(f"Format {format_id} selected: acodec={acodec}, has_audio={has_audio}")
            
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


async def download_audio(url, progress_hook=None):
    url = YtDlpHelper.prepare_url(url)
    
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
    url = YtDlpHelper.prepare_url(url)
    loop = _get_running_loop()
    def _get():
        helper = YtDlpHelper(url)
        with yt_dlp.YoutubeDL(helper.get_opts()) as ydl:
            info = ydl.extract_info(url, download=False)
            thumbnail_url = info.get("thumbnail")
        return thumbnail_url, info
    return await loop.run_in_executor(None, _get)


async def get_images(url):
    url = YtDlpHelper.prepare_url(url)
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


def is_aria2_available():
    try:
        subprocess.run(["aria2c", "--version"], capture_output=True, check=True)
        return True
    except (subprocess.CalledProcessError, FileNotFoundError):
        return False


async def download_with_aria2(url, filename, progress_hook=None, connections=16):
    if not is_aria2_available():
        raise RuntimeError("aria2c is not installed")

    cmd = [
        "aria2c",
        "-x", str(connections),
        "-s", str(connections),
        "-d", os.path.dirname(filename),
        "-o", os.path.basename(filename),
        "--continue=true",
        "--retry-wait=3",
        "--max-tries=5",
        url
    ]

    process = await asyncio.create_subprocess_exec(
        *cmd,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE
    )

    stdout, stderr = await process.communicate()

    if process.returncode != 0:
        logger.error(f"aria2c failed: {stderr.decode()}")
        raise RuntimeError(f"aria2c download failed: {stderr.decode()}")

    return filename


async def get_direct_url(url, format_id=None):
    url = YtDlpHelper.prepare_url(url)
    
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


async def download_video_aria2(url, format_id, progress_hook=None):
    url = YtDlpHelper.prepare_url(url)
    
    if not is_aria2_available():
        raise RuntimeError("aria2c is not installed")

    loop = _get_running_loop()
    
    def _get_info():
        helper = YtDlpHelper(url)
        ydl_opts = helper.get_opts()
        
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(url, download=False)
            formats = info.get("formats") or []
            
            target_format = None
            
            if format_id == "best":
                ydl_opts["format"] = "bestvideo+bestaudio/best"
                for f in reversed(formats):
                    if f.get("url") and f.get("ext") in ("mp4", "m4a", "webm"):
                        target_format = f
                        break
            else:
                for f in formats:
                    if str(f.get("format_id")) == str(format_id):
                        target_format = f
                        break
                
                if target_format:
                    acodec = target_format.get("acodec")
                    has_audio = acodec not in (None, "none") if acodec else False
                    logger.info(f"ARIA2 Format {format_id}: acodec={acodec}, has_audio={has_audio}")
                    if has_audio:
                        ydl_opts["format"] = format_id
                    else:
                        ydl_opts["format"] = f"{format_id}+bestaudio/best"
                else:
                    ydl_opts["format"] = f"{format_id}+bestaudio/best"
            
            if not target_format and formats:
                target_format = formats[-1]
            
            direct_url = target_format.get("url") if target_format else None
            
            title = info.get("title", "video")
            ext = target_format.get("ext", "mp4") if target_format else "mp4"
            safe_title = "".join(c for c in title if c.isalnum() or c in " -_").strip()[:50]
            
            filename = os.path.join(TEMP_DIR, f"{BOT_FILE_PREFIX}{safe_title}.{ext}")
            
            return direct_url, filename, info
    
    direct_url, filename, info = await loop.run_in_executor(None, _get_info)
    
    if not direct_url:
        raise RuntimeError("Could not extract direct URL from video")

    cmd = [
        "aria2c",
        "-x", str(ARIA2_CONNECTIONS),
        "-s", str(ARIA2_CONNECTIONS),
        "-d", os.path.dirname(filename),
        "-o", os.path.basename(filename),
        "--continue=true",
        "--retry-wait=3",
        "--max-tries=5",
        direct_url
    ]

    process = await asyncio.create_subprocess_exec(
        *cmd,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE
    )

    stdout, stderr = await process.communicate()

    if process.returncode != 0:
        logger.error(f"aria2c failed: {stderr.decode()}")
        raise RuntimeError(f"aria2c download failed: {stderr.decode()}")

    return filename, info


def is_spotify_url(url: str) -> bool:
    return "spotify.com" in url.lower()


async def download_spotify(url, progress_hook=None):
    """Download Spotify track using spotDL."""
    loop = _get_running_loop()
    
    def _download():
        output_template = os.path.join(TEMP_DIR, f"{BOT_FILE_PREFIX}%(title)s.%(ext)s")
        
        cmd = [
            "spotdl",
            url,
            "--output", TEMP_DIR,
            "--format", "mp3",
            "--bitrate", "320k",
            "--overwrite", "force",
        ]
        
        logger.info(f"Running spotdl: {' '.join(cmd)}")
        
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            cwd=TEMP_DIR
        )
        
        if result.returncode != 0:
            logger.error(f"spotdl failed: {result.stderr}")
            raise RuntimeError(f"spotdl failed: {result.stderr}")
        
        import glob
        pattern = os.path.join(TEMP_DIR, f"{BOT_FILE_PREFIX}*.mp3")
        files = glob.glob(pattern)
        
        if not files:
            raise RuntimeError("spotdl didn't produce any output files")
        
        filename = max(files, key=os.path.getmtime)
        
        with open(filename, "rb") as f:
            pass
        
        title = os.path.splitext(os.path.basename(filename))[0]
        info = {"title": title, "url": url}
        
        return filename, info
    
    return await loop.run_in_executor(None, _download)
