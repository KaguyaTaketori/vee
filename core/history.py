import os
import json
import time
import fcntl
import threading
from datetime import datetime
from typing import Optional


BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
HISTORY_FILE = os.path.join(BASE_DIR, "download_history.json")
LOCK_FILE = os.path.join(BASE_DIR, "history.lock")
MAX_ENTRIES_PER_USER = 100
MAX_TOTAL_ENTRIES = 5000

_cache = {"data": {}, "dirty": False, "time": 0}
_cache_lock = threading.Lock()
_persist_interval = 30
_last_persist = 0


def _load_history_unsafe() -> dict:
    if not os.path.exists(HISTORY_FILE):
        return {}
    try:
        with open(HISTORY_FILE, "r") as f:
            return json.load(f)
    except Exception:
        return {}


def _load_history() -> dict:
    global _cache, _last_persist
    with _cache_lock:
        now = time.time()
        if _cache["data"] is None or now - _cache["time"] > 5:
            _cache["data"] = _load_history_unsafe()
            _cache["time"] = now
        return _cache["data"].copy()


def _save_history_unsafe(history: dict):
    with open(LOCK_FILE, "w") as lock:
        fcntl.flock(lock.fileno(), fcntl.LOCK_EX)
        try:
            with open(HISTORY_FILE, "w") as f:
                json.dump(history, f, indent=2)
        finally:
            fcntl.flock(lock.fileno(), fcntl.LOCK_UN)


def _persist_history():
    global _cache, _last_persist
    with _cache_lock:
        if _cache["dirty"]:
            _save_history_unsafe(_cache["data"])
            _cache["dirty"] = False
            _last_persist = time.time()


def _schedule_persist():
    global _cache
    with _cache_lock:
        _cache["dirty"] = True


def add_history(user_id: int, url: str, download_type: str, file_size: Optional[int] = None, 
                title: Optional[str] = None, status: str = "success", file_path: Optional[str] = None,
                file_id: Optional[str] = None):
    global _cache
    history = _load_history()
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
    if file_path:
        entry["file_path"] = file_path
    if file_id:
        entry["file_id"] = file_id
    
    history[str_id].append(entry)
    
    if len(history[str_id]) > MAX_ENTRIES_PER_USER:
        history[str_id] = history[str_id][-MAX_ENTRIES_PER_USER:]
    
    total_entries = sum(len(entries) for entries in history.values())
    if total_entries > MAX_TOTAL_ENTRIES:
        oldest_users = sorted(history.items(), key=lambda x: x[1][0]["timestamp"] if x[1] else 0)
        for uid, entries in oldest_users[:len(history) // 4]:
            history[uid] = history[uid][-MAX_ENTRIES_PER_USER // 2:]
    
    with _cache_lock:
        _cache["data"] = history
        _cache["dirty"] = True
        _cache["time"] = time.time()


def get_user_history(user_id: int, limit: int = 10) -> list:
    history = _load_history()
    str_id = str(user_id)
    entries = history.get(str_id, [])
    return sorted(entries, key=lambda x: x.get("timestamp", 0), reverse=True)[:limit]


def clear_user_history(user_id: int):
    global _cache
    history = _load_history()
    str_id = str(user_id)
    if str_id in history:
        del history[str_id]
        with _cache_lock:
            _cache["data"] = history
            _cache["dirty"] = True
            _cache["time"] = time.time()


def get_all_users_count() -> int:
    history = _load_history()
    return len(history)


def get_total_downloads() -> int:
    history = _load_history()
    return sum(len(entries) for entries in history.values())


def get_failed_downloads(user_id: Optional[int] = None, limit: int = 20) -> list:
    history = _load_history()
    failed = []
    
    if user_id:
        str_id = str(user_id)
        entries = history.get(str_id, [])
        for entry in entries:
            if entry.get("status") == "failed":
                failed.append(entry)
    else:
        for uid, entries in history.items():
            for entry in entries:
                if entry.get("status") == "failed":
                    entry["user_id"] = int(uid)
                    failed.append(entry)
    
    return sorted(failed, key=lambda x: x.get("timestamp", 0), reverse=True)[:limit]


def force_persist():
    _persist_history()


def check_recent_download(url: str, max_age_hours: int = 24) -> Optional[dict]:
    """Check if URL was recently downloaded and file still exists."""
    history = _load_history()
    now = time.time()
    max_age_seconds = max_age_hours * 3600
    
    for user_entries in history.values():
        for entry in reversed(user_entries):
            if entry.get("url") == url:
                entry_time = entry.get("timestamp", 0)
                if now - entry_time < max_age_seconds:
                    if entry.get("status") == "success" and (entry.get("file_path") or entry.get("file_id")):
                        if entry.get("file_path") and os.path.exists(entry["file_path"]):
                            return entry
                        if entry.get("file_id"):
                            return entry
    return None


def get_file_id_by_url(url: str, max_age_hours: int = 168) -> Optional[str]:
    """Get file_id by URL (up to 7 days). Returns file_id if available."""
    history = _load_history()
    now = time.time()
    max_age_seconds = max_age_hours * 3600
    
    for user_entries in history.values():
        for entry in reversed(user_entries):
            if entry.get("url") == url:
                entry_time = entry.get("timestamp", 0)
                if now - entry_time < max_age_seconds:
                    if entry.get("status") == "success" and entry.get("file_id"):
                        return entry["file_id"]
    return None


def clear_file_id_by_url(url: str):
    """Remove file_id from history entries for a URL, forcing re-download."""
    global _cache
    history = _load_history()
    modified = False
    
    for user_entries in history.values():
        for entry in user_entries:
            if entry.get("url") == url:
                if "file_id" in entry:
                    del entry["file_id"]
                    modified = True
    
    if modified:
        with _cache_lock:
            _cache["data"] = history
            _cache["dirty"] = True
            _cache["time"] = time.time()
        _persist_history()
