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


def _get_refresh_cmd() -> list[str] | None:
    if not _should_refresh_cookies():
        return None
    try:
        return shlex.split(COOKIE_REFRESH_CMD)
    except ValueError as e:
        logger.error("Invalid COOKIE_REFRESH_CMD syntax: %s", e)
        return None


def _handle_refresh_result(success: bool, returncode: int = 0, stderr: str = "") -> bool:
    global _cookie_last_refresh
    if success:
        _cookie_last_refresh = time.time()
        logger.info("Cookies refreshed successfully")
        return True
    logger.error("Cookie refresh failed (exit %s): %s", returncode, stderr[:200])
    return False


def refresh_cookies() -> bool:
    cmd = _get_refresh_cmd()
    if cmd is None:
        return False
    try:
        result = subprocess.run(
            cmd, shell=False, capture_output=True,
            text=True, check=True, timeout=120
        )
        return _handle_refresh_result(True)
    except subprocess.CalledProcessError as e:
        return _handle_refresh_result(False, e.returncode, e.stderr)
    except subprocess.TimeoutExpired:
        logger.error("Cookie refresh timed out after 120s")
        return False
    except FileNotFoundError:
        logger.error("Cookie refresh command not found: %s", cmd[0])
        return False
    except Exception as e:
        logger.error("Cookie refresh unexpected error: %s", e)
        return False


async def refresh_cookies_async() -> bool:
    cmd = _get_refresh_cmd()
    if cmd is None:
        return False
    try:
        process = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        try:
            _, stderr = await asyncio.wait_for(process.communicate(), timeout=120)
        except asyncio.TimeoutError:
            process.kill()
            await process.communicate()
            logger.error("Cookie refresh (async) timed out after 120s")
            return False

        if process.returncode != 0:
            return _handle_refresh_result(False, process.returncode, stderr.decode())
        return _handle_refresh_result(True)

    except FileNotFoundError:
        logger.error("Cookie refresh command not found: %s", cmd[0])
        return False
    except Exception as e:
        logger.error("Cookie refresh (async) unexpected error: %s", e)
        return False


async def resolve_short_url(url: str) -> str:
    """Resolve short URLs (b23.tv, youtu.be, etc.) to full URLs."""
    short_domains = {"b23.tv", "youtu.be"}
    try:
        domain = url.split("/")[2].lower() if "://" in url else url.split("/")[0].lower()
        if any(d in domain for d in short_domains):
            logger.info("Resolving short URL: %s", url)
            async with httpx.AsyncClient(follow_redirects=True, timeout=10) as client:
                response = await client.head(url)
            resolved = str(response.url)
            logger.info("Resolved to: %s", resolved)
            return resolved
    except Exception as e:
        logger.warning("Failed to resolve short URL %s: %s", url, e)
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
