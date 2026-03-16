import asyncio
import time
from utils.utils import format_bytes as _format_size
from concurrent.futures import ThreadPoolExecutor

download_executor = ThreadPoolExecutor(max_workers=10)


class ProgressTracker:
    def __init__(self, processing_msg, loop: asyncio.AbstractEventLoop):
        self.msg = processing_msg
        self.last_text = None
        self.last_update = 0
        self.loop = loop
        self.max_total = 0
    
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
        status = d.get("status")

        if status == "downloading" and "downloaded_bytes" in d:
            total = d.get("total_bytes") or d.get("total_bytes_estimate", 0)
            downloaded = d.get("downloaded_bytes", 0)
            speed = d.get("speed", 0)

            if total > tracker.max_total:
                tracker.max_total = total
            display_total = tracker.max_total

            if display_total > 0:
                percent  = min(100, int(downloaded * 100 / display_total))
                filled   = max(0, min(10, int(10 * downloaded / display_total)))
                bar      = "█" * filled + "░" * (10 - filled)
                speed_s  = f" • {_format_size(speed)}/s" if speed else ""
                text = (
                    f"⬇️ Downloading...\n"
                    f"{bar} {percent}%\n"
                    f"{_format_size(downloaded)} / {_format_size(display_total)}"
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

    return progress_hook
