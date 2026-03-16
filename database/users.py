import time
import logging
import aiosqlite
from database.db import get_db

logger = logging.getLogger(__name__)


async def get_user_info(user_id: int) -> dict:
    async with get_db() as db:
        async with db.execute(
            "SELECT user_id, username, first_name, last_name, lang, added_at, last_seen FROM users WHERE user_id = ?",
            (user_id,)
        ) as cursor:
            row = await cursor.fetchone()
            if row:
                return dict(row)
            return {}


async def fetch_user_lang_from_db(user_id: int) -> str:
    async with get_db() as db:
        async with db.execute(
            "SELECT lang FROM users WHERE user_id = ?",
            (user_id,)
        ) as cursor:
            row = await cursor.fetchone()
            if row and row[0]:
                return row[0]
            return "en"


async def set_user_lang(user_id: int, lang: str):
    now = time.time()
    async with get_db() as db:
        await db.execute(
            """
            INSERT INTO users (user_id, lang, added_at, last_seen)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(user_id) DO UPDATE SET lang = excluded.lang
            """,
            (user_id, lang, now, now)
        )
        await db.commit()


async def update_user(user_id: int, username: str = None,
                      first_name: str = None, last_name: str = None):
    """
    Upsert user info. Only provided (non-None) fields are updated.
    The user's language preference is always preserved via COALESCE.
    """
    now = time.time()
    async with get_db() as db:
        await db.execute(
            """
            INSERT INTO users (user_id, username, first_name, last_name, lang, added_at, last_seen)
            VALUES (?, ?, ?, ?, 'en', ?, ?)
            ON CONFLICT(user_id) DO UPDATE SET
                username   = COALESCE(excluded.username,   users.username),
                first_name = COALESCE(excluded.first_name, users.first_name),
                last_name  = COALESCE(excluded.last_name,  users.last_name),
                last_seen  = excluded.last_seen
                -- lang 列故意不在这里更新，由 set_user_lang 单独管理
            """,
            (user_id, username, first_name, last_name, now, now),
        )
        await db.commit()


async def get_all_users() -> list:
    async with get_db() as db:
        async with db.execute(
            "SELECT user_id, username, first_name, last_name, last_seen, added_at FROM users"
        ) as cursor:
            rows = await cursor.fetchall()
            return [dict(row) for row in rows]


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

