import time
import logging
import aiosqlite
from core.db import DB_PATH

logger = logging.getLogger(__name__)


async def get_user_info(user_id: int) -> dict:
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            "SELECT user_id, username, first_name, last_name, lang, added_at, last_seen FROM users WHERE user_id = ?",
            (user_id,)
        ) as cursor:
            row = await cursor.fetchone()
            if row:
                return dict(row)
            return {}


async def get_user_lang(user_id: int) -> str:
    async with aiosqlite.connect(DB_PATH) as db:
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
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            """
            INSERT INTO users (user_id, lang, added_at, last_seen)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(user_id) DO UPDATE SET lang = excluded.lang
            """,
            (user_id, lang, now, now)
        )
        await db.commit()


async def update_user(user_id: int, username: str = None, first_name: str = None, last_name: str = None):
    now = time.time()
    
    async with aiosqlite.connect(DB_PATH) as db:
        existing_lang = None
        async with db.execute("SELECT lang FROM users WHERE user_id = ?", (user_id,)) as cursor:
            row = await cursor.fetchone()
            if row:
                existing_lang = row[0]
        
        set_clauses = []
        params = []
        
        if username is not None:
            set_clauses.append("username = ?")
            params.append(username)
        if first_name is not None:
            set_clauses.append("first_name = ?")
            params.append(first_name)
        if last_name is not None:
            set_clauses.append("last_name = ?")
            params.append(last_name)
        
        set_clauses.append("last_seen = ?")
        params.append(now)
        
        if existing_lang is not None:
            set_clauses.append("lang = ?")
            params.append(existing_lang)
        
        await db.execute(
            f"""
            INSERT INTO users (user_id, username, first_name, last_name, lang, added_at, last_seen)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(user_id) DO UPDATE SET
                {', '.join(set_clauses)}
            """,
            (user_id, username, first_name, last_name, existing_lang or "en", now, now) + tuple(params)
        )
        await db.commit()


async def get_all_users() -> list:
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
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

