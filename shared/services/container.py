from __future__ import annotations
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from services.task_manager import TaskManager
    from services.event_bus import EventBus
    from services.ratelimit import RateLimiter
    from services.notifier import AdminNotifier


class AppContainer:
    def __init__(self) -> None:
        self.task_manager: "TaskManager" = None   # type: ignore[assignment]
        self.bus: "EventBus" = None               # type: ignore[assignment]
        self.limiter: "RateLimiter" = None        # type: ignore[assignment]
        self.notifier: "AdminNotifier" = None     # type: ignore[assignment]  

    @property
    def queue(self) -> "TaskManager":
        return self.task_manager

    @queue.setter
    def queue(self, value: "TaskManager") -> None:
        self.task_manager = value


services = AppContainer()
