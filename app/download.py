import asyncio
import time
from concurrent.futures import ThreadPoolExecutor

download_executor = ThreadPoolExecutor(max_workers=1)  # Reduced for low RAM


def _format_size(bytes_val):
    if bytes_val is None:
        return "?"
    for unit in ['B', 'KB', 'MB', 'GB']:
        if bytes_val < 1024:
            return f"{bytes_val:.1f}{unit}"
        bytes_val /= 1024
    return f"{bytes_val:.1f}TB"


class ProgressTracker:
    def __init__(self, processing_msg):
        self.msg = processing_msg
        self.last_text = None
        self.last_update = 0
        self.loop = asyncio.get_event_loop()
    
    def update(self, text):
        now = time.time()
        if text != self.last_text and now - self.last_update > 2:
            self.last_text = text
            self.last_update = now
            try:
                self.loop.call_soon_threadsafe(
                    asyncio.create_task,
                    self.msg.edit_text(text)
                )
            except Exception:
                pass


def _make_progress_hook(processing_msg):
    tracker = ProgressTracker(processing_msg)
    def progress_hook(d):
        if d.get('status') == 'downloading':
            total = d.get('total_bytes') or d.get('total_bytes_estimate', 0)
            downloaded = d.get('downloaded_bytes', 0)
            speed = d.get('speed', 0)
            
            if total > 0:
                percent = int(downloaded * 100 / total)
                bar_length = 10
                filled = int(bar_length * downloaded / total)
                bar = "█" * filled + "░" * (bar_length - filled)
                
                speed_str = _format_size(speed) + "/s" if speed else ""
                text = f"⬇️ Downloading...\n{bar} {percent}%\n{_format_size(downloaded)} / {_format_size(total)}"
                if speed_str:
                    text += f" • {speed_str}"
                
                tracker.update(text)
        elif d.get('status') == 'finished':
            tracker.update("✅ Download complete! Processing...")
    return progress_hook
