import os
import time
import asyncio

from utils.cache import TTLCache
from database.users import update_user as _update_user, get_all_users as _get_all_users, get_user_info as _get_user_info
from database.db import get_db
from config import (
    ALLOWED_USERS_FILE,
    ADMIN_IDS,
    CACHE_TTL,
    BOT_FILE_PREFIX,
    TEMP_DIR,
    TEMP_FILE_MAX_AGE_HOURS,
)

_allowed_users_cache: TTLCache = TTLCache(ttl=CACHE_TTL)
_users_db_cache: TTLCache      = TTLCache(ttl=CACHE_TTL)

def track_user(user):
    if user:
        try:
            loop = asyncio.get_running_loop()
            asyncio.create_task(_update_user(user.id, username=user.username, first_name=user.first_name, last_name=user.last_name))
        except RuntimeError:
            asyncio.run(_update_user(user.id, username=user.username, first_name=user.first_name, last_name=user.last_name))


def get_allowed_users() -> set:
    cached = _allowed_users_cache.get()
    if cached is not TTLCache._MISSING:
        return cached                       # ✅ 空 set() 也能正确命中

    users = set()
    if os.path.exists(ALLOWED_USERS_FILE):
        with open(ALLOWED_USERS_FILE, "r") as f:
            for line in f:
                line = line.strip()
                if line and line.isdigit():
                    users.add(int(line))

    result = users | ADMIN_IDS
    _allowed_users_cache.set(result)
    return result


def save_allowed_users(users: set):
    with open(ALLOWED_USERS_FILE, "w") as f:
        for uid in sorted(users):
            f.write(f"{uid}\n")
    _allowed_users_cache.invalidate()


async def get_all_users_info() -> list:
    cached = _users_db_cache.get()
    if cached is not TTLCache._MISSING:
        return cached

    data = await _get_all_users()
    _users_db_cache.set(data)
    return data


async def get_user_display_name(user_id: int) -> str:
    try:
        async with get_db() as db:
            async with db.execute(
                "SELECT username, first_name FROM users WHERE user_id = ?", (user_id,)
            ) as cursor:
                row = await cursor.fetchone()
                if row:
                    return f"@{row['username']}" if row["username"] else (row["first_name"] or str(user_id))
    except Exception:
        pass
    return str(user_id)


async def get_user_display_names_bulk(user_ids: list[int]) -> dict[int, str]:
    if not user_ids:
        return {}

    async with get_db() as db:
        placeholders = ",".join("?" * len(user_ids))
        async with db.execute(
            f"SELECT user_id, username, first_name, last_name "
            f"FROM users WHERE user_id IN ({placeholders})",
            user_ids,
        ) as cur:
            rows = await cur.fetchall()

    result = {}
    for row in rows:
        r = dict(row)
        result[r["user_id"]] = format_user_display(r)
    return result


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
