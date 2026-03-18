# shared/repositories/__init__.py
"""
Re-exports for backward compatibility
"""

from .history_repo import HistoryRepository
from .user_repo import UserRepository

__all__ = [
    "HistoryRepository",
    "UserRepository",
]
