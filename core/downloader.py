import os
import logging
import asyncio
import subprocess
import time
import yt_dlp
import httpx
from config import MAX_FILE_SIZE, COOKIE_FILE, get_temp_template, TEMP_DIR, BOT_FILE_PREFIX, USE_ARIA2, ARIA2_CONNECTIONS, COOKIE_REFRESH_CMD, COOKIE_REFRESH_INTERVAL_HOURS

logger = logging.getLogger(__name__)

_cookie_last_refresh = 0


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


def _get_base_opts():
    opts = {
        "quiet": True,
        "no_warnings": True,
        "extractor_retries": 3,
        "fragment_retries": 3,
        "js_runtimes": {"node": {}},
    }
    if COOKIE_FILE and os.path.exists(COOKIE_FILE):
        opts["cookiefile"] = COOKIE_FILE
    
    return opts


def _add_extractor_headers(url, opts):
    """Add extractor-specific headers for sites like Bilibili."""
    if "bilibili.com" in url:
        opts["http_headers"] = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
            "Referer": "https://www.bilibili.com/",
            "Origin": "https://www.bilibili.com",
        }
    return opts


def _use_intl_api(url):
    """Convert Bilibili URL to use international API."""
    if "bilibili.com" in url and "b23.tv" not in url:
        return url
    return url


async def get_formats(url):
    refresh_cookies()
    url = resolve_short_url(url)
    
    loop = asyncio.get_event_loop()
    def _get():
        ydl_opts = _get_base_opts()
        ydl_opts = _add_extractor_headers(url, ydl_opts)
        ydl_opts['logger'] = logging.getLogger('yt_dlp')
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(url, download=False)
            formats = info.get("formats") or []
            logger.error(f"URL: {url}, Format count: {len(formats)}, First 10: {[(f.get('format_id'), f.get('height'), f.get('ext')) for f in formats[:10]]}")
            return formats, info
    return await loop.run_in_executor(None, _get)


async def download_video(url, format_id, progress_hook=None):
    refresh_cookies()
    url = resolve_short_url(url)
    
    if USE_ARIA2 and is_aria2_available():
        try:
            return await download_video_aria2(url, format_id, progress_hook)
        except Exception as e:
            logger.warning(f"aria2 failed, falling back to yt-dlp: {e}")
    
    loop = asyncio.get_event_loop()
    def _download():
        ydl_opts = _get_base_opts()
        ydl_opts.update({
            "format": "bestvideo+bestaudio/best" if format_id == "best" else f"{format_id}+bestaudio/best",
            "outtmpl": get_temp_template(),
            "merge_output_format": "mp4",
            "progress_hooks": [progress_hook] if progress_hook else None,
        })
        
        ydl_opts = _add_extractor_headers(url, ydl_opts)
        
        if "twitter.com" in url or "x.com" in url:
            ydl_opts["extractor_args"] = {
                "twitter": {"lang": "en"}
            }

        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(url, download=True)
            filename = ydl.prepare_filename(info)
        return filename, info
    return await loop.run_in_executor(None, _download)


async def download_audio(url, progress_hook=None):
    refresh_cookies()
    url = resolve_short_url(url)
    
    loop = asyncio.get_event_loop()
    def _download():
        ydl_opts = _get_base_opts()
        ydl_opts = _add_extractor_headers(url, ydl_opts)
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
    url = resolve_short_url(url)
    loop = asyncio.get_event_loop()
    def _get():
        ydl_opts = _get_base_opts()
        ydl_opts = _add_extractor_headers(url, ydl_opts)
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(url, download=False)
            thumbnail_url = info.get("thumbnail")
        return thumbnail_url, info
    return await loop.run_in_executor(None, _get)


async def get_images(url):
    url = resolve_short_url(url)
    loop = asyncio.get_event_loop()
    def _get():
        ydl_opts = _get_base_opts()
        ydl_opts = _add_extractor_headers(url, ydl_opts)
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
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
    refresh_cookies()
    url = resolve_short_url(url)
    
    loop = asyncio.get_event_loop()
    def _get():
        ydl_opts = _get_base_opts()
        ydl_opts = _add_extractor_headers(url, ydl_opts)
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
    refresh_cookies()
    url = resolve_short_url(url)
    
    if not is_aria2_available():
        raise RuntimeError("aria2c is not installed")

    loop = asyncio.get_event_loop()
    
    def _get_info():
        ydl_opts = _get_base_opts()
        ydl_opts = _add_extractor_headers(url, ydl_opts)
        if format_id == "best":
            ydl_opts["format"] = "bestvideo+bestaudio/best"
        else:
            ydl_opts["format"] = f"{format_id}+bestaudio/best"
        
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(url, download=False)
            
            formats = info.get("formats") or []
            target_format = None
            
            if format_id == "best":
                for f in reversed(formats):
                    if f.get("url") and f.get("ext") in ("mp4", "m4a", "webm"):
                        target_format = f
                        break
            else:
                for f in formats:
                    if str(f.get("format_id")) == str(format_id):
                        target_format = f
                        break
            
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
    loop = asyncio.get_event_loop()
    
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
