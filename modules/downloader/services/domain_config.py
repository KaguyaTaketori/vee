from __future__ import annotations

from urllib.parse import urlparse


SUPPORTED_DOMAINS = (
    "youtube.com",
    "youtu.be",
    "youtube-nocookie.com",
    "spotify.com",
    "bilibili.com",
    "bilibili.tv",
    "b23.tv",
    "tiktok.com",
    "instagram.com",
    "twitter.com",
    "x.com",
    "facebook.com",
    "reddit.com",
    "vk.com",
    "soundcloud.com",
    "vimeo.com",
    "twitch.tv",
    "dailymotion.com",
)


def is_spotify_url(url: str) -> bool:
    try:
        parsed = urlparse(url)
        return "spotify.com" in parsed.netloc.lower()
    except Exception:
        return False
