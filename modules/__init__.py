# modules/__init__.py
"""
BotModule Protocol — the contract every feature module must satisfy.

``setup()`` now accepts a ``HandlerRegistrar`` (platform-agnostic) instead
of a PTB ``Application`` object.  This means:

  • Module files have zero ``telegram.*`` imports at the module level.
  • Unit tests can call ``module.setup(FakeRegistrar())`` without
    constructing a PTB ``Application``.
  • Swapping to a different bot platform only requires a new
    ``HandlerRegistrar`` implementation — modules are untouched.
"""
from __future__ import annotations

from typing import Protocol

from core.registrar import HandlerRegistrar


class BotModule(Protocol):
    name: str

    def setup(self, registrar: HandlerRegistrar) -> None:
        """Register all handlers via the platform-agnostic registrar."""
        ...

    def get_user_commands(self) -> list[str]:
        """Command names (without /) this module exposes to regular users."""
        return []

    def get_admin_commands(self) -> list[str]:
        """Command names (without /) this module exposes to admins only."""
        return []

    async def init_db(self) -> None:
        """Create / migrate this module's DB tables (called at startup)."""
        ...
