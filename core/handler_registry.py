"""
core/handler_registry.py
─────────────────────────
Global command handler registry.

The @command_handler decorator fires at import time (module-level side
effect).  In tests this can cause commands registered by one test module
to leak into unrelated tests.

Fix: added HandlerRegistry.reset() so test suites can call it in tearDown.

Usage in tests
--------------
from core.handler_registry import registry

class MyTest(unittest.TestCase):
    def tearDown(self):
        registry.reset()
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Callable, Any, Protocol, runtime_checkable

logger = logging.getLogger(__name__)


@runtime_checkable
class CommandRegistrar(Protocol):
    def register_command(
        self,
        command: str,
        handler: Callable,
        *,
        admin_only: bool = False,
        **kwargs: Any,
    ) -> None: ...


@dataclass
class CommandEntry:
    command: str
    handler: Callable
    admin_only: bool = False
    extra_kwargs: dict = field(default_factory=dict)


class HandlerRegistry:

    def __init__(self) -> None:
        self._entries: list[CommandEntry] = []

    def add(self, entry: CommandEntry) -> None:
        self._entries.append(entry)
        logger.debug("HandlerRegistry: queued /%s (admin=%s)", entry.command, entry.admin_only)

    def apply(self, registrar: CommandRegistrar) -> None:
        for entry in self._entries:
            registrar.register_command(
                entry.command,
                entry.handler,
                admin_only=entry.admin_only,
                **entry.extra_kwargs,
            )
            logger.debug("Applied /%s (admin=%s)", entry.command, entry.admin_only)

    def reset(self) -> None:
        """
        Clear all registered command entries.

        Intended for use in test tearDown to prevent cross-test
        contamination from module-level @command_handler side effects.
        """
        self._entries.clear()
        logger.debug("HandlerRegistry: reset (all entries cleared)")

    @property
    def all_commands(self) -> list[CommandEntry]:
        return list(self._entries)


registry = HandlerRegistry()


def command_handler(command: str, *, admin_only: bool = False, **kwargs: Any):
    def decorator(func: Callable) -> Callable:
        registry.add(CommandEntry(
            command=command,
            handler=func,
            admin_only=admin_only,
            extra_kwargs=kwargs,
        ))
        return func
    return decorator
