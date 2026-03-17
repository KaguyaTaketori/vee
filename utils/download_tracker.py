import asyncio
import time
import logging
from utils.utils import format_bytes as _format_bytes
from concurrent.futures import ThreadPoolExecutor

logger = logging.getLogger(__name__)

download_executor = ThreadPoolExecutor(max_workers=10)


class ProgressTracker:
    def __init__(self, processing_msg, loop: asyncio.AbstractEventLoop):
        self.msg = processing_msg
        self.last_text = None
        self.last_update = 0.0
        self.loop = loop
        self.max_total: float = 0.0
    
    def update(self, text):
        now = time.time()
        if text != self.last_text and now - self.last_update > 2:
            self.last_text = text
            self.last_update = now
            try:
                asyncio.run_coroutine_threadsafe(
                    self.msg.edit_text(text), 
                    self.loop
                )
            except Exception:
                pass

def _make_progress_hook(processing_msg, loop: asyncio.AbstractEventLoop):
    tracker = ProgressTracker(processing_msg, loop)

    def progress_hook(d):
        try:
            status = d.get("status")

            if status == "downloading" and "downloaded_bytes" in d:
                try:
                    total_bytes_val = d.get("total_bytes")
                    total_est_val = d.get("total_bytes_estimate")
                    if isinstance(total_bytes_val, (int, float)) and total_bytes_val:
                        total: float = float(total_bytes_val)
                    elif isinstance(total_est_val, (int, float)) and total_est_val:
                        total = float(total_est_val)
                    else:
                        total = 0.0
                except (TypeError, ValueError):
                    total = 0.0
                try:
                    downloaded_val = d.get("downloaded_bytes")
                    downloaded: float = float(downloaded_val) if isinstance(downloaded_val, (int, float)) else 0.0
                except (TypeError, ValueError):
                    downloaded = 0.0

                if not isinstance(total, (int, float)) or not isinstance(downloaded, (int, float)):
                    return
                try:
                    raw_speed = d.get("speed")
                    speed = float(raw_speed) if isinstance(raw_speed, (int, float)) else 0.0
                except (TypeError, ValueError):
                    speed = 0.0

                if total > tracker.max_total:
                    tracker.max_total = float(total)
                display_total = tracker.max_total

                if display_total > 0:
                    downloaded_float = float(downloaded)
                    display_total_float = float(display_total)
                    percent  = min(100, int(downloaded_float * 100 / display_total_float))
                    filled   = max(0, min(10, int(10 * downloaded_float / display_total_float)))
                    bar      = "█" * filled + "░" * (10 - filled)
                    speed_s  = f" • {_format_bytes(speed)}/s" if speed else ""
                    text = (
                        f"⬇️ Downloading...\n"
                        f"{bar} {percent}%\n"
                        f"{_format_bytes(downloaded)} / {_format_bytes(display_total)}"
                        f"{speed_s}"
                    )
                    tracker.update(text)

            elif status == "finished" and "downloaded_bytes" in d:
                tracker.update("✅ Download complete! Processing...")

            elif status == "downloading" and "percent" in d:
                percent = int(d["percent"])
                filled  = max(0, min(10, percent // 10))
                bar     = "█" * filled + "░" * (10 - filled)
                title   = d.get("title", "")
                title_s = f"\n🎵 {title[:30]}" if title else ""
                text = (
                    f"⬇️ Downloading Spotify...{title_s}\n"
                    f"{bar} {percent}%"
                )
                tracker.update(text)

            elif status == "finished" and "title" in d and "downloaded_bytes" not in d:
                tracker.update(f"✅ Downloaded: {d.get('title', '')[:30]}")
        except Exception as e:
            logger.error("progress_hook error: %s", e, exc_info=True)

    return progress_hook
