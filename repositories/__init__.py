# repositories/__init__.py
from shared.repositories.history_repo import HistoryRepository
from shared.repositories.user_repo import UserRepository          # 合并后的
from shared.repositories.bill_repo import BillRepository          # 新增
from .task_repo import TaskRepository
from .rate_limit_repo import RateLimitRepository
from .analytics_repo import AnalyticsRepository

# 向后兼容别名
AppUserRepository = UserRepository

__all__ = [
    "HistoryRepository",
    "UserRepository",
    "AppUserRepository",
    "BillRepository",
    "TaskRepository",
    "RateLimitRepository",
    "AnalyticsRepository",
]
