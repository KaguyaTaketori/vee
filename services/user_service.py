import os
import time
import asyncio
import aiosqlite

from database.users import update_user as _update_user, get_all_users as _get_all_users, get_user_info as _get_user_info
from database.db import DB_PATH
from config import (
    ALLOWED_USERS_FILE,
    ADMIN_IDS,
    CACHE_TTL,
    BOT_FILE_PREFIX,
    TEMP_DIR,
    TEMP_FILE_MAX_AGE_HOURS,
)

_allowed_users_cache = {"data": None, "time": 0}
_users_db_cache = {"data": None, "time": 0}


def track_user(user):
    if user:
        try:
            loop = asyncio.get_running_loop()
            asyncio.create_task(_update_user(user.id, username=user.username, first_name=user.first_name, last_name=user.last_name))
        except RuntimeError:
            asyncio.run(_update_user(user.id, username=user.username, first_name=user.first_name, last_name=user.last_name))


def get_allowed_users():
    global _allowed_users_cache
    now = time.time()
    if _allowed_users_cache["data"] and (now - _allowed_users_cache["time"]) < CACHE_TTL:
        return _allowed_users_cache["data"]
    
    users = set()
    if os.path.exists(ALLOWED_USERS_FILE):
        with open(ALLOWED_USERS_FILE, "r") as f:
            for line in f:
                line = line.strip()
                if line and line.isdigit():
                    users.add(int(line))
    
    result = users | ADMIN_IDS
    _allowed_users_cache = {"data": result, "time": now}
    return result


def save_allowed_users(users):
    with open(ALLOWED_USERS_FILE, "w") as f:
        for uid in sorted(users):
            f.write(f"{uid}\n")
    global _allowed_users_cache
    _allowed_users_cache = {"data": None, "time": 0}


async def get_all_users_info():
    global _users_db_cache
    import time
    now = time.time()
    if _users_db_cache["data"] and (now - _users_db_cache["time"]) < CACHE_TTL:
        return _users_db_cache["data"]
    
    data = await _get_all_users()
    _users_db_cache = {"data": data, "time": now}
    return data


async def get_user_display_name(user_id: int) -> str:
    try:
        async with aiosqlite.connect(DB_PATH) as db:
            async with db.execute(
                "SELECT username, first_name FROM users WHERE user_id = ?", (user_id,)
            ) as cursor:
                row = await cursor.fetchone()
                if row:
                    if row[0]:
                        return f"@{row[0]}"
                    if row[1]:
                        return row[1]
    except Exception:
        pass
    return str(user_id)


def cleanup_temp_files(max_age_hours: int = None):
    if max_age_hours is None:
        max_age_hours = TEMP_FILE_MAX_AGE_HOURS
    if not os.path.exists(TEMP_DIR):
        return
    cutoff = time.time() - (max_age_hours * 3600)
    try:
        for fname in os.listdir(TEMP_DIR):
            if fname.startswith(BOT_FILE_PREFIX):
                fpath = os.path.join(TEMP_DIR, fname)
                if os.path.getmtime(fpath) < cutoff:
                    try:
                        os.remove(fpath)
                    except OSError:
                        pass
    except OSError:
        pass
