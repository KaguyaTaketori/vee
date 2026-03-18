"""
modules/downloader/integrations/downloaders/aria2_client.py
────────────────────────────────────────────────────────────
aria2c multi-connection downloader.

Fix: RuntimeError was raised with a format-string template as the message
instead of the interpolated string:

    BEFORE: raise RuntimeError("aria2c failed: %s", stderr.decode())
            → RuntimeError: ('aria2c failed: %s', 'actual error')

    AFTER:  raise RuntimeError(f"aria2c failed: {stderr.decode()}")
            → RuntimeError: aria2c failed: actual error
"""
from __future__ import annotations

import asyncio
import logging
import os
import shutil

from config import TEMP_DIR, BOT_FILE_PREFIX, ARIA2_CONNECTIONS
from .ytdlp_client import YtDlpHelper
from utils.utils import get_running_loop as _get_running_loop

logger = logging.getLogger(__name__)

_aria2_available: bool | None = None


def is_aria2_available() -> bool:
    global _aria2_available
    if _aria2_available is None:
        _aria2_available = shutil.which("aria2c") is not None
    return _aria2_available


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
        # ← FIXED: was raise RuntimeError("msg: %s", value) — wrong!
        raise RuntimeError(f"aria2c failed: {stderr.decode()}")


async def download_video_aria2(url, format_id, progress_hook=None):
    url = await YtDlpHelper.prepare_url(url)

    if not is_aria2_available():
        raise RuntimeError("aria2c is not installed")

    loop = _get_running_loop()

    def _get_info():
        import yt_dlp
        helper = YtDlpHelper(url)
        ydl_opts = helper.get_opts()

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

            direct_url = target_format.get("url") if target_format else None
            title = info.get("title", "video")
            ext = target_format.get("ext", "mp4") if target_format else "mp4"
            safe_title = "".join(
                c for c in title if c.isalnum() or c in " -_"
            ).strip()[:50]
            filename = os.path.join(TEMP_DIR, f"{BOT_FILE_PREFIX}{safe_title}.{ext}")
            return direct_url, filename, info

    direct_url, filename, info = await loop.run_in_executor(None, _get_info)

    if not direct_url:
        raise RuntimeError("Could not extract direct URL from video")

    cmd = _build_aria2_cmd(
        direct_url, os.path.dirname(filename), os.path.basename(filename)
    )
    await _run_aria2(cmd)
    return filename, info


async def download_with_aria2(url, filename, progress_hook=None, connections=16):
    if not is_aria2_available():
        raise RuntimeError("aria2c is not installed")
    cmd = _build_aria2_cmd(url, os.path.dirname(filename), os.path.basename(filename))
    await _run_aria2(cmd)
    return filename
