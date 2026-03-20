from __future__ import annotations
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from shared.services.task_manager import TaskManager
    from shared.services.event_bus import EventBus
    from shared.services.ratelimit import RateLimiter
    from shared.services.notifier import AdminNotifier
    from shared.services.receipt_storage import ReceiptStorage
    from shared.services.ws_manager import ConnectionManager


class AppContainer:
    def __init__(self) -> None:
        self.task_manager: "TaskManager" = None   # type: ignore[assignment]
        self.bus: "EventBus" = None               # type: ignore[assignment]
        self.limiter: "RateLimiter" = None        # type: ignore[assignment]
        self.notifier: "AdminNotifier" = None     # type: ignore[assignment]
        self.receipt_storage: "ReceiptStorage" = None  # type: ignore[assignment]
        self.ws_manager: "ConnectionManager" = None

    @property
    def queue(self) -> "TaskManager":
        return self.task_manager

    @queue.setter
    def queue(self, value: "TaskManager") -> None:
        self.task_manager = value


services = AppContainer()
