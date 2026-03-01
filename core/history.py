import os
import json
import time
from datetime import datetime
from typing import Optional


HISTORY_FILE = "/home/ubuntu/vee/download_history.json"


def load_history() -> dict:
    if not os.path.exists(HISTORY_FILE):
        return {}
    try:
        with open(HISTORY_FILE, "r") as f:
            return json.load(f)
    except Exception:
        return {}


def save_history(history: dict):
    with open(HISTORY_FILE, "w") as f:
        json.dump(history, f, indent=2)


def add_history(user_id: int, url: str, download_type: str, file_size: Optional[int] = None, 
                title: Optional[str] = None, status: str = "success"):
    history = load_history()
    str_id = str(user_id)
    
    if str_id not in history:
        history[str_id] = []
    
    entry = {
        "timestamp": time.time(),
        "url": url,
        "type": download_type,
        "status": status,
    }
    if file_size:
        entry["file_size"] = file_size
    if title:
        entry["title"] = title
    
    history[str_id].append(entry)
    
    if len(history[str_id]) > 100:
        history[str_id] = history[str_id][-100:]
    
    save_history(history)


def get_user_history(user_id: int, limit: int = 10) -> list:
    history = load_history()
    str_id = str(user_id)
    entries = history.get(str_id, [])
    return sorted(entries, key=lambda x: x.get("timestamp", 0), reverse=True)[:limit]


def clear_user_history(user_id: int):
    history = load_history()
    str_id = str(user_id)
    if str_id in history:
        del history[str_id]
        save_history(history)


def get_all_users_count() -> int:
    history = load_history()
    return len(history)


def get_total_downloads() -> int:
    history = load_history()
    return sum(len(entries) for entries in history.values())
