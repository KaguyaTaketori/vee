import os
import shlex
import tempfile
import shutil
import logging
import asyncio
import subprocess
import time
import yt_dlp
import httpx
import re as _re
from functools import lru_cache
from urllib.parse import urlparse
from core.utils import get_running_loop as _get_running_loop
from config import MAX_FILE_SIZE, COOKIE_FILE, get_temp_template, TEMP_DIR, BOT_FILE_PREFIX, USE_ARIA2, ARIA2_CONNECTIONS, COOKIE_REFRESH_CMD, COOKIE_REFRESH_INTERVAL_HOURS, COOKIES_DIR

logger = logging.getLogger(__name__)

_cookie_last_refresh = 0

_DOMAIN_COOKIE_MAP = {
    "youtube.com":  "www.youtube.com_cookies.txt",
    "youtu.be":     "www.youtube.com_cookies.txt",
    "bilibili.com": "www.bilibili.com_cookies.txt",
    "b23.tv":       "www.bilibili.com_cookies.txt",
}

_SPOTDL_PROGRESS_RE = _re.compile(r"(\d+)%\|")
_SPOTDL_DONE_RE     = _re.compile(r'Downloaded\s+"(.+?)"')

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
        try:
            netloc = urlparse(url).netloc.lower().removeprefix("www.")
        except Exception:
            netloc = ""

        for domain, cookie_filename in _DOMAIN_COOKIE_MAP.items():
            if netloc == domain or netloc.endswith(f".{domain}"):
                site_cookie = os.path.join(COOKIES_DIR, cookie_filename)
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
    async def prepare_url(url: str) -> str:
        """Common URL preprocessing: resolve short URLs and refresh cookies."""
        await refresh_cookies_async()
        return await resolve_short_url(url)


def _mask_url(url: str, max_path_len: int = 20) -> str:
    try:
        p = urlparse(url)
        path = p.path[:max_path_len] + ("..." if len(p.path) > max_path_len else "")
        return f"{p.scheme}://{p.netloc}{path}"
    except Exception:
        return "[invalid url]"

def _build_aria2_cmd(
    url: str,
    output_dir: str,
    output_file: str,
    connections: int = ARIA2_CONNECTIONS,
) -> list[str]:
    return [
        "aria2c",
        "-x", str(connections),
        "-s", str(connections),
        "-d", output_dir,
        "-o", output_file,
        "--continue=true",
        "--retry-wait=3",
        "--max-tries=5",
        url,
    ]

async def _run_aria2(cmd: list[str]) -> None:
    process = await asyncio.create_subprocess_exec(
        *cmd,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    _, stderr = await process.communicate()
    if process.returncode != 0:
        raise RuntimeError(f"aria2c failed: {stderr.decode()}")

async def resolve_short_url(url: str) -> str:
    """Resolve short URLs (b23.tv, youtu.be, etc.) to full URLs."""
    short_domains = {"b23.tv", "youtu.be"}
    try:
        domain = url.split("/")[2].lower() if "://" in url else url.split("/")[0].lower()
        if any(d in domain for d in short_domains):
            logger.info(f"Resolving short URL: {url}")
            async with httpx.AsyncClient(follow_redirects=True, timeout=10) as client:
                response = await client.head(url)
            resolved = str(response.url)
            logger.info(f"Resolved to: {resolved}")
            return resolved
    except Exception as e:
        logger.warning(f"Failed to resolve short URL {url}: {e}")
    return url

def _should_refresh_cookies() -> bool:
    if not COOKIE_REFRESH_CMD or not COOKIE_FILE:
        return False
    return time.time() - _cookie_last_refresh >= (COOKIE_REFRESH_INTERVAL_HOURS * 3600)

def refresh_cookies() -> bool:
    global _cookie_last_refresh
    if not _should_refresh_cookies():
        return False

    try:
        logger.info("Refreshing cookies...")
        cmd = shlex.split(COOKIE_REFRESH_CMD)
        result = subprocess.run(
            cmd,
            shell=False,
            capture_output=True,
            text=True,
            check=True,
            timeout=120
        )
        _cookie_last_refresh = time.time()
        logger.info("Cookies refreshed successfully")
        return True
    except subprocess.CalledProcessError as e:
        logger.error(f"Cookie refresh failed (exit {e.returncode}): {e.stderr[:200]}")
        return False
    except subprocess.TimeoutExpired:
        logger.error("Cookie refresh timed out after 120s")
        return False
    except Exception as e:
        logger.error(f"Cookie refresh unexpected error: {e}")
        return False



async def refresh_cookies_async() -> bool:
    global _cookie_last_refresh
    if not _should_refresh_cookies():
        return False

    try:
        logger.info("Refreshing cookies (async)...")
        cmd = shlex.split(COOKIE_REFRESH_CMD)

        process = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE
        )

        try:
            stdout, stderr = await asyncio.wait_for(
                process.communicate(),
                timeout=120
            )
        except asyncio.TimeoutError:
            process.kill()
            await process.communicate()
            logger.error("Cookie refresh (async) timed out after 120s")
            return False

        if process.returncode != 0:
            logger.error(f"Cookie refresh failed (exit {process.returncode}): "
                         f"{stderr.decode()[:200]}")
            return False

        _cookie_last_refresh = time.time()
        logger.info("Cookies refreshed successfully (async)")
        return True

    except FileNotFoundError:
        logger.error(f"Cookie refresh command not found: {shlex.split(COOKIE_REFRESH_CMD)[0]}")
        return False
    except Exception as e:
        logger.error(f"Cookie refresh (async) unexpected error: {e}")
        return False


_FORMATS_CACHE: dict[str, tuple] = {}
_FORMATS_CACHE_MAX = 100

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
            logger.info(f"URL: {_mask_url(url)}, Format count: {len(formats)}, First 10: {[(f.get('format_id'), f.get('height'), f.get('ext')) for f in formats[:10]]}")
            logger.debug(f"Full URL (debug only): {url}")
            return formats, info
    return await loop.run_in_executor(None, _get)


async def download_video(url, format_id, progress_hook=None):
    url = await YtDlpHelper.prepare_url(url)
    
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


_aria2_available: bool | None = None
def is_aria2_available() -> bool:
    global _aria2_available
    if _aria2_available is None:
        _aria2_available = shutil.which("aria2c") is not None
    return _aria2_available


async def download_with_aria2(url, filename, progress_hook=None, connections=16):
    if not is_aria2_available():
        raise RuntimeError("aria2c is not installed")

    cmd = _build_aria2_cmd(direct_url, os.path.dirname(filename), os.path.basename(filename))
    await _run_aria2(cmd)

    return filename


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


async def download_video_aria2(url, format_id, progress_hook=None):
    url = await YtDlpHelper.prepare_url(url)
    
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

    cmd = _build_aria2_cmd(direct_url, os.path.dirname(filename), os.path.basename(filename))
    await _run_aria2(cmd)

    return filename, info


def is_spotify_url(url: str) -> bool:
    return "spotify.com" in url.lower()

async def download_spotify(url: str, progress_hook=None) -> tuple[str, dict]:
    tmp_dir = tempfile.mkdtemp(dir=TEMP_DIR, prefix=f"{BOT_FILE_PREFIX}spotify_")

    cmd = [
        "spotdl", url,
        "--output", tmp_dir,
        "--format", "mp3",
        "--bitrate", "320k",
        "--overwrite", "force",
        "--log-level", "INFO",
    ]

    logger.info(f"Running spotdl: {' '.join(cmd)}")

    try:
        process = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
            cwd=tmp_dir,
        )

        title = "Unknown"
        last_percent = -1

        async for raw_line in process.stdout:
            line = raw_line.decode("utf-8", errors="replace").strip()
            if not line:
                continue

            logger.debug(f"[spotdl] {line}")

            m_done = _SPOTDL_DONE_RE.search(line)
            if m_done:
                title = m_done.group(1)
                if progress_hook:
                    progress_hook({"status": "finished", "title": title})
                continue

            m_pct = _SPOTDL_PROGRESS_RE.search(line)
            if m_pct and progress_hook:
                percent = int(m_pct.group(1))
                if percent != last_percent:
                    last_percent = percent
                    progress_hook({
                        "status": "downloading",
                        "percent": float(percent),
                        "title": title,
                    })

        await process.wait()

        if process.returncode != 0:
            raise RuntimeError(f"spotdl exited with code {process.returncode}")

        files = [
            os.path.join(tmp_dir, f)
            for f in os.listdir(tmp_dir)
            if f.endswith(".mp3")
        ]
        if not files:
            raise RuntimeError("spotdl produced no output files")

        src  = max(files, key=os.path.getmtime)
        dest = os.path.join(TEMP_DIR, f"{BOT_FILE_PREFIX}{os.path.basename(src)}")
        shutil.move(src, dest)

        title = title or os.path.splitext(os.path.basename(dest))[0]
        return dest, {"title": title, "url": url}

    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)
