from __future__ import annotations

import os
import re
import logging

from config import COOKIE_FILE, COOKIES_DIR

logger = logging.getLogger(__name__)

# 匹配 "<domain>_cookies.txt"
_SITE_PATTERN = re.compile(r'^([^_]+)_cookies\.txt$')


class CookieSaveResult:
    __slots__ = ("domain", "path")

    def __init__(self, domain: str | None, path: str) -> None:
        self.domain = domain   # None 表示写入了全局 cookies.txt
        self.path = path


def resolve_cookie_path(filename: str) -> str | None:
    m = _SITE_PATTERN.match(filename)
    if m:
        domain = m.group(1)
        return os.path.join(COOKIES_DIR, f"{domain}_cookies.txt")
    if COOKIE_FILE:
        return COOKIE_FILE
    return None


async def save_cookie_bytes(filename: str, data: bytes) -> CookieSaveResult:
    path = resolve_cookie_path(filename)
    if path is None:
        raise ValueError(f"Cannot determine cookie path for filename: {filename!r}")

    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)

    with open(path, "wb") as f:
        f.write(data)
    logger.info("Cookie file saved: %s (%d bytes)", path, len(data))

    m = _SITE_PATTERN.match(filename)
    return CookieSaveResult(domain=m.group(1) if m else None, path=path)
