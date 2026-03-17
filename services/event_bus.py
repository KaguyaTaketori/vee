# services/event_bus.py
"""
Lightweight in-process event bus.

Usage
-----
Register listeners at startup (main.py post_init):

    bus.on("task_completed", task_repo.save)
    bus.on("task_completed", some_other_async_handler)

Emit events from inside any queue channel's _finalize_task:

    bus.emit("task_completed", task)

Design decisions
----------------
- Listeners are called in registration order.
- Both sync and async callables are accepted; async ones are scheduled
  as fire-and-forget Tasks so _finalize_task never needs to await.
- Exceptions in individual listeners are logged but never propagate
  to the emitter, keeping queue workers alive.
"""
from __future__ import annotations

import asyncio
import logging
from collections import defaultdict
from typing import Any, Callable

logger = logging.getLogger(__name__)


class EventBus:
    def __init__(self) -> None:
        self._handlers: dict[str, list[Callable]] = defaultdict(list)

    # ------------------------------------------------------------------
    # Registration
    # ------------------------------------------------------------------

    def on(self, event: str, handler: Callable) -> None:
        """Register *handler* to be called whenever *event* is emitted."""
        self._handlers[event].append(handler)
        logger.debug("EventBus: registered '%s' -> %s", event, handler)

    def off(self, event: str, handler: Callable) -> None:
        """Remove a previously registered handler (no-op if not found)."""
        try:
            self._handlers[event].remove(handler)
        except ValueError:
            pass

    # ------------------------------------------------------------------
    # Emission
    # ------------------------------------------------------------------

    def emit(self, event: str, *args: Any, **kwargs: Any) -> None:
        """
        Fire *event* synchronously from the caller's context.

        Async handlers are scheduled on the running event loop as
        independent Tasks — the caller is never blocked.
        """
        for handler in list(self._handlers[event]):
            try:
                result = handler(*args, **kwargs)
                if asyncio.iscoroutine(result):
                    try:
                        loop = asyncio.get_running_loop()
                        task = loop.create_task(result)
                        task.add_done_callback(_log_task_error)
                    except RuntimeError:
                        # No running loop — unusual in our async app, but
                        # degrade gracefully rather than crash.
                        logger.warning(
                            "EventBus: no running loop; cannot schedule async "
                            "handler for event '%s'", event
                        )
            except Exception as exc:  # noqa: BLE001
                logger.error(
                    "EventBus: handler %s raised for event '%s': %s",
                    handler, event, exc, exc_info=True,
                )


def _log_task_error(fut: asyncio.Future) -> None:
    if fut.cancelled():
        return
    exc = fut.exception()
    if exc:
        logger.error("EventBus async handler failed: %s", exc, exc_info=exc)


# ---------------------------------------------------------------------------
# Module-level singleton – imported everywhere as:
#   from services.event_bus import bus
# ---------------------------------------------------------------------------
bus = EventBus()
