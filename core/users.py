import os
import json
import time


USERS_FILE = "/home/ubuntu/vee/users_db.json"


def load_users() -> dict:
    if not os.path.exists(USERS_FILE):
        return {}
    try:
        with open(USERS_FILE, "r") as f:
            return json.load(f)
    except Exception:
        return {}


def save_users(users: dict):
    with open(USERS_FILE, "w") as f:
        json.dump(users, f, indent=2)


def get_user_info(user_id: int) -> dict:
    users = load_users()
    return users.get(str(user_id), {})


def update_user(user_id: int, username: str = None, first_name: str = None, last_name: str = None):
    users = load_users()
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
    
    save_users(users)


def get_all_users() -> list:
    users = load_users()
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
