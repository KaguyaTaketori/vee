import os
import time
from dataclasses import dataclass
from typing import Optional


RATE_LIMIT_FILE = "/home/ubuntu/vee/rate_limit.json"


def load_rate_limit() -> dict:
    if os.path.exists(RATE_LIMIT_FILE):
        import json
        with open(RATE_LIMIT_FILE, "r") as f:
            return json.load(f)
    return {"max_downloads_per_hour": 10, "enabled": True}


def save_rate_limit(max_downloads: int, enabled: bool):
    import json
    with open(RATE_LIMIT_FILE, "w") as f:
        json.dump({"max_downloads_per_hour": max_downloads, "enabled": enabled}, f)


class RateLimiter:
    def __init__(self):
        config = load_rate_limit()
        self.max_downloads_per_hour = config.get("max_downloads_per_hour", 10)
        self.enabled = config.get("enabled", True)
        self.user_downloads: dict[int, list] = {}

    def reload(self):
        config = load_rate_limit()
        self.max_downloads_per_hour = config.get("max_downloads_per_hour", 10)
        self.enabled = config.get("enabled", True)

    def check_limit(self, user_id: int) -> tuple[bool, Optional[str]]:
        if not self.enabled:
            return True, None
            
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
        if not self.enabled:
            return 999
        now = time.time()
        window = 3600
        recent = self.user_downloads.get(user_id, [])
        recent = [t for t in recent if now - t < window]
        return max(0, self.max_downloads_per_hour - len(recent))

    def reset(self, user_id: int):
        if user_id in self.user_downloads:
            del self.user_downloads[user_id]

    def get_status(self) -> dict:
        return {
            "max_downloads_per_hour": self.max_downloads_per_hour,
            "enabled": self.enabled,
            "active_users": len(self.user_downloads)
        }


rate_limiter = RateLimiter()
