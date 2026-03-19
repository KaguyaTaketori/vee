# database/history.py
"""
Backward-compatible façade for history-related DB operations.

All SQL has been moved to repositories.HistoryRepository.
Existing call sites (handlers, strategies, etc.) continue to work unchanged
because this module re-exports the same async function names they import.

If you are writing NEW code, import HistoryRepository directly:
    from repositories import HistoryRepository
"""

from typing import Optional
from repositories import HistoryRepository

_repo = HistoryRepository()

# Public constants kept for any callers that referenced them
MAX_ENTRIES_PER_USER = 100
MAX_TOTAL_ENTRIES = 5000


async def add_history(
    user_id: int,
    url: str,
    download_type: str,
    file_size: Optional[int] = None,
    title: Optional[str] = None,
    status: str = "success",
    file_path: Optional[str] = None,
    file_id: Optional[str] = None,
) -> None:
    await _repo.add(
        user_id, url, download_type,
        file_size=file_size, title=title, status=status,
        file_path=file_path, file_id=file_id,
    )


async def get_user_history(user_id: int, limit: int = 10) -> list:
    return await _repo.get_by_user(user_id, limit=limit)


async def get_user_history_page(
    user_id: int, page: int = 0, page_size: int = 5
) -> tuple[list, int]:
    return await _repo.get_by_user_paged(user_id, page=page, page_size=page_size)


async def clear_user_history(user_id: int) -> None:
    await _repo.clear_user_history(user_id)


async def get_failed_downloads(
    user_id: Optional[int] = None, limit: int = 20
) -> list:
    return await _repo.get_failed(user_id=user_id, limit=limit)


async def get_all_users_count() -> int:
    from repositories import UserRepository
    return await UserRepository().count_all()


async def get_total_downloads() -> int:
    return await _repo.count_all()


async def get_recent_cached_urls(
    limit: int = 5, offset: int = 0
) -> tuple[list, int]:
    return await _repo.get_recent_cached_urls(limit=limit, offset=offset)



async def get_file_id_by_url(
    url: str,
    download_type: Optional[str] = None,
) -> Optional[str]:
    return await _repo.get_file_id_by_url(url, download_type=download_type)


async def clear_file_id_by_url(
    url: str, download_type: Optional[str] = None
) -> None:
    await _repo.clear_file_id_by_url(url, download_type=download_type)


async def get_file_id_and_title_by_url(
    url: str,
    download_type: Optional[str] = None,
) -> Optional[tuple[str, str | None]]:
    return await _repo.get_file_id_and_title_by_url(url, download_type=download_type)
