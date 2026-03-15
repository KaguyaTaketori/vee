import os
import shlex
import logging
import asyncio
import subprocess
import time
import httpx
from urllib.parse import urlparse

from config import COOKIE_FILE, COOKIE_REFRESH_CMD, COOKIE_REFRESH_INTERVAL_HOURS, COOKIES_DIR

logger = logging.getLogger(__name__)

_cookie_last_refresh = 0

_DOMAIN_COOKIE_MAP = {
    "youtube.com":  "www.youtube.com_cookies.txt",
    "youtu.be":     "www.youtube.com_cookies.txt",
    "bilibili.com": "www.bilibili.com_cookies.txt",
    "b23.tv":       "www.bilibili.com_cookies.txt",
}


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


def get_cookie_file(url: str) -> str:
    """Get the appropriate cookie file for a given URL."""
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


def mask_url(url: str, max_path_len: int = 20) -> str:
    """Mask URL for logging purposes."""
    try:
        p = urlparse(url)
        path = p.path[:max_path_len] + ("..." if len(p.path) > max_path_len else "")
        return f"{p.scheme}://{p.netloc}{path}"
    except Exception:
        return "[invalid url]"
