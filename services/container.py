"""
Central registry for long-lived service singletons.

All instances are created once in ``main.py`` during ``post_init``,
before the bot starts accepting messages.

Every module that previously imported a module-level singleton
(``download_queue``, ``rate_limiter``) now imports ``services`` from
this module and accesses ``services.queue`` / ``services.limiter``
instead.
"""
from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from services.queue import DownloadQueue
    from services.ratelimit import RateLimiter


class AppContainer:
    """Holds the single shared instances of core services."""

    def __init__(self) -> None:
        self.queue: "DownloadQueue" = None   # type: ignore[assignment]
        self.limiter: "RateLimiter" = None   # type: ignore[assignment]


# Module-level instance – attributes populated by main.py before any
# handler is invoked.
services = AppContainer()
