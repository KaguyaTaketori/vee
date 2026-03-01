import time
from dataclasses import dataclass
from typing import Optional


@dataclass
class RateLimit:
    max_downloads: int
    window_seconds: int
    downloads: list


class RateLimiter:
    def __init__(self, max_downloads_per_hour: int = 10):
        self.max_downloads_per_hour = max_downloads_per_hour
        self.user_downloads: dict[int, list] = {}

    def check_limit(self, user_id: int) -> tuple[bool, Optional[str]]:
        now = time.time()
        window = 3600
        
        if user_id not in self.user_downloads:
            self.user_downloads[user_id] = []
        
        recent = [t for t in self.user_downloads[user_id] if now - t < window]
        self.user_downloads[user_id] = recent
        
        if len(recent) >= self.max_downloads_per_hour:
            wait_time = int(window - (now - recent[0]) + 60)
            return False, f"Rate limit exceeded. Try again in {wait_time // 60} minutes."
        
        self.user_downloads[user_id].append(now)
        return True, None

    def get_remaining(self, user_id: int) -> int:
        now = time.time()
        window = 3600
        recent = self.user_downloads.get(user_id, [])
        recent = [t for t in recent if now - t < window]
        return max(0, self.max_downloads_per_hour - len(recent))

    def reset(self, user_id: int):
        if user_id in self.user_downloads:
            del self.user_downloads[user_id]


rate_limiter = RateLimiter(max_downloads_per_hour=10)
