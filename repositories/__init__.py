# repositories/__init__.py
"""
Repository layer – the single source of truth for all SQL statements.

Import convention inside services / handlers:
    from repositories import HistoryRepository, UserRepository, ...
"""

from shared.repositories.history_repo import HistoryRepository
from shared.repositories.user_repo import UserRepository
from .task_repo import TaskRepository
from .rate_limit_repo import RateLimitRepository
from .analytics_repo import AnalyticsRepository

__all__ = [
    "HistoryRepository",
    "UserRepository",
    "TaskRepository",
    "RateLimitRepository",
    "AnalyticsRepository",
]
