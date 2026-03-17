# services/container.py
"""
Central registry for long-lived service singletons.

All instances are created once in ``main.py`` during ``post_init``,
before the bot starts accepting messages.

Upgrade notes (v2)
------------------
* ``services.queue``        → replaced by ``services.task_manager``
  (TaskManager wraps io_queue / cpu_queue / api_queue)
* ``services.bus``          → the module-level EventBus singleton
  (also importable directly as ``from services.event_bus import bus``)
* Backward-compat shim: ``services.queue`` is kept as a property that
  returns ``services.task_manager`` so that any code that hasn't been
  updated yet keeps working.
"""
from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from services.task_manager import TaskManager
    from services.event_bus import EventBus
    from services.ratelimit import RateLimiter


class AppContainer:
    """Holds the single shared instances of core services."""

    def __init__(self) -> None:
        self.task_manager: "TaskManager" = None  # type: ignore[assignment]
        self.bus: "EventBus" = None              # type: ignore[assignment]
        self.limiter: "RateLimiter" = None       # type: ignore[assignment]

    # ------------------------------------------------------------------
    # Backward-compatibility shim
    # ------------------------------------------------------------------
    @property
    def queue(self) -> "TaskManager":
        """
        Backward-compat alias.  Old call sites that use ``services.queue``
        will transparently receive the TaskManager.  The public API is a
        superset of the old DownloadQueue API, so nothing breaks.
        """
        return self.task_manager

    @queue.setter
    def queue(self, value: "TaskManager") -> None:
        self.task_manager = value


# Module-level instance – attributes populated by main.py before any
# handler is invoked.
services = AppContainer()
