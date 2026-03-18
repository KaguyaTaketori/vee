# database/users.py
"""
Backward-compatible façade for user-related DB operations.

All SQL has been moved to repositories.UserRepository.
Existing call sites continue to work unchanged.

If you are writing NEW code, import UserRepository directly:
    from repositories import UserRepository
"""

from typing import Optional

_repo = None


def _get_repo():
    global _repo
    if _repo is None:
        from repositories import UserRepository
        _repo = UserRepository()
    return _repo


async def get_user_info(user_id: int) -> dict:
    return await _get_repo().get(user_id)


async def fetch_user_lang_from_db(user_id: int) -> str:
    return await _get_repo().get_lang(user_id)


async def set_user_lang(user_id: int, lang: str) -> None:
    await _get_repo().set_lang(user_id, lang)


async def upsert_user(
    user_id: int,
    username: Optional[str] = None,
    first_name: Optional[str] = None,
    last_name: Optional[str] = None,
    lang: str = "en",
) -> None:
    await _get_repo().upsert(
        user_id,
        username=username,
        first_name=first_name,
        last_name=last_name,
        lang=lang,
    )


async def touch_user(user_id: int) -> None:
    await _get_repo().touch(user_id)


async def get_all_users() -> list[dict]:
    return await _get_repo().get_all()
