"""
database/users.py
─────────────────
Backward-compatible façade for user-related DB operations.

All SQL lives in repositories.UserRepository.
Existing call sites continue to work unchanged.

Change: _repo is now initialised at module level (not lazily via a
getter function), eliminating the thread-safety concern.
"""
from typing import Optional
from repositories import UserRepository

# Initialised once at import time — safe for concurrent coroutines
# because UserRepository is stateless (it opens a new DB connection
# per call via the async context-manager pattern).
_repo = UserRepository()


async def get_user_info(user_id: int) -> dict:
    return await _repo.get(user_id)


async def fetch_user_lang_from_db(user_id: int) -> str:
    return await _repo.get_lang(user_id)


async def set_user_lang(user_id: int, lang: str) -> None:
    await _repo.set_lang(user_id, lang)


async def upsert_user(
    user_id: int,
    username: Optional[str] = None,
    first_name: Optional[str] = None,
    last_name: Optional[str] = None,
    lang: str = "en",
) -> None:
    await _repo.upsert(
        user_id,
        username=username,
        first_name=first_name,
        last_name=last_name,
        lang=lang,
    )


async def touch_user(user_id: int) -> None:
    await _repo.touch(user_id)


async def get_all_users() -> list[dict]:
    return await _repo.get_all()
