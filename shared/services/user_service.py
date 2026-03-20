import os
import time
import asyncio

from utils.cache import TTLCache
from database.users import (
    upsert_user as _update_user,
    get_all_users as _get_all_users,
    get_user_info as _get_user_info,
    fetch_user_lang_from_db,
    set_user_lang as _db_set_user_lang,
)
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


async def track_user_async(user) -> int:
    """返回 users.id（自增主键），供后续操作使用"""
    if not user:
        return 0
    from shared.repositories.user_repo import UserRepository
    return await UserRepository().upsert_tg_user(
        user.id,
        username=user.username,
        first_name=user.first_name,
        last_name=user.last_name,
    )


def track_user(user):
    """兼容原有同步调用入口"""
    if user:
        try:
            loop = asyncio.get_running_loop()
            asyncio.create_task(track_user_async(user))
        except RuntimeError:
            asyncio.run(track_user_async(user))


def get_allowed_users() -> set:
    cached = _allowed_users_cache.get()
    if cached is not TTLCache._MISSING:
        return cached

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


# ---------------------------------------------------------------------------
# Language preference helpers
# These are the single authoritative point for reading/writing user language,
# keeping database.users and utils.i18n properly decoupled from each other.
# ---------------------------------------------------------------------------

async def warm_user_lang(user_id: int) -> str:
    """Fetch the user's language from the DB and seed the i18n in-memory cache.

    Call this once per session (e.g. on the first message from a user) so
    that ``t()`` resolves the correct language without hitting the DB on
    every translation lookup.
    """
    from utils.i18n import get_user_lang, set_user_lang as _cache_set

    # Skip DB round-trip if already cached for this session
    from cachetools import LRUCache  # just for isinstance check
    from utils.i18n import _lang_cache, DEFAULT_LANG
    if user_id in _lang_cache:
        return _lang_cache[user_id]

    lang = await fetch_user_lang_from_db(user_id)
    _cache_set(user_id, lang)
    return lang


async def set_user_language(user_id: int, lang: str) -> None:
    """Persist a new language preference to the DB and update the i18n cache.

    This is the single call-site for any language change – handlers should
    call this instead of touching i18n or database.users directly.
    """
    from utils.i18n import set_user_lang as _cache_set
    _cache_set(user_id, lang)
    await _db_set_user_lang(user_id, lang)


def format_user_display(user: dict) -> str:
    parts = []
    if user.get("username"):
        parts.append(f"@{user['username']}")
    if user.get("first_name"):
        parts.append(user["first_name"])
    if user.get("last_name"):
        parts.append(user["last_name"])

    name = " ".join(parts) if parts else f"User {user['user_id']}"
    return f"{name} (`{user['user_id']}`)"
