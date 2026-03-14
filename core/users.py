import os
import json
import time
import fcntl
import threading


BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
USERS_FILE = os.path.join(BASE_DIR, "users_db.json")
LOCK_FILE = os.path.join(BASE_DIR, "users_db.lock")

_cache = {"data": {}, "dirty": False, "time": 0}
_cache_lock = threading.Lock()
_persist_interval = 30
_last_persist = 0


def _load_users_unsafe() -> dict:
    if not os.path.exists(USERS_FILE):
        return {}
    try:
        with open(USERS_FILE, "r") as f:
            return json.load(f)
    except Exception:
        return {}


def _load_users() -> dict:
    global _cache, _last_persist
    with _cache_lock:
        now = time.time()
        if _cache["data"] is None or now - _cache["time"] > 5:
            _cache["data"] = _load_users_unsafe()
            _cache["time"] = now
        return _cache["data"].copy()


def _save_users_unsafe(users: dict):
    with open(LOCK_FILE, "w") as lock:
        fcntl.flock(lock.fileno(), fcntl.LOCK_EX)
        try:
            with open(USERS_FILE, "w") as f:
                json.dump(users, f, indent=2)
        finally:
            fcntl.flock(lock.fileno(), fcntl.LOCK_UN)


def _persist_users():
    global _cache, _last_persist
    with _cache_lock:
        if _cache["dirty"]:
            _save_users_unsafe(_cache["data"])
            _cache["dirty"] = False
            _last_persist = time.time()


def _schedule_persist():
    global _cache
    with _cache_lock:
        _cache["dirty"] = True


def get_user_info(user_id: int) -> dict:
    users = _load_users()
    return users.get(str(user_id), {}).copy()


def update_user(user_id: int, username: str = None, first_name: str = None, last_name: str = None):
    global _cache
    users = _load_users()
    str_id = str(user_id)
    
    if str_id not in users:
        users[str_id] = {"id": user_id, "added_at": time.time()}
    
    if username:
        users[str_id]["username"] = username
    if first_name:
        users[str_id]["first_name"] = first_name
    if last_name:
        users[str_id]["last_name"] = last_name
    
    users[str_id]["last_seen"] = time.time()
    
    with _cache_lock:
        _cache["data"] = users
        _cache["dirty"] = True
        _cache["time"] = time.time()


def get_all_users() -> list:
    users = _load_users()
    return [
        {
            "id": int(uid),
            "username": data.get("username"),
            "first_name": data.get("first_name"),
            "last_name": data.get("last_name"),
            "last_seen": data.get("last_seen", 0),
            "added_at": data.get("added_at", 0)
        }
        for uid, data in users.items()
    ]


def format_user_display(user: dict) -> str:
    parts = []
    if user.get("username"):
        parts.append(f"@{user['username']}")
    if user.get("first_name"):
        parts.append(user["first_name"])
    if user.get("last_name"):
        parts.append(user["last_name"])
    
    name = " ".join(parts) if parts else f"User {user['id']}"
    return f"{name} (`{user['id']}`)"


def force_persist():
    _persist_users()
