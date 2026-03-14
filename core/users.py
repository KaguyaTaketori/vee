import os
import time
from core.storage import JsonStore


BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
USERS_FILE = os.path.join(BASE_DIR, "users_db.json")
LOCK_FILE = os.path.join(BASE_DIR, "users_db.lock")

_users_store = JsonStore(USERS_FILE, LOCK_FILE, cache_ttl=5.0)


def get_user_info(user_id: int) -> dict:
    users = _users_store.load()
    return users.get(str(user_id), {}).copy()


def get_user_lang(user_id: int) -> str:
    users = _users_store.load()
    str_id = str(user_id)
    return users.get(str_id, {}).get("lang", "en")


def set_user_lang(user_id: int, lang: str):
    users = _users_store.load()
    str_id = str(user_id)
    
    if str_id not in users:
        users[str_id] = {"id": user_id, "added_at": time.time()}
    
    users[str_id]["lang"] = lang
    _users_store.mark_dirty(users)


def update_user(user_id: int, username: str = None, first_name: str = None, last_name: str = None):
    users = _users_store.load()
    str_id = str(user_id)
    
    if str_id not in users:
        users[str_id] = {"id": user_id, "added_at": time.time()}
    
    existing_lang = users[str_id].get("lang")
    
    if username:
        users[str_id]["username"] = username
    if first_name:
        users[str_id]["first_name"] = first_name
    if last_name:
        users[str_id]["last_name"] = last_name
    
    users[str_id]["last_seen"] = time.time()
    
    if existing_lang is not None:
        users[str_id]["lang"] = existing_lang
    
    _users_store.mark_dirty(users)


def get_all_users() -> list:
    users = _users_store.load()
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
    _users_store.force_persist()
