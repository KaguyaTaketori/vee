# core/registrar.py
"""
Platform-agnostic handler registration interface.

Design
------
``BotModule.setup()`` previously accepted a PTB ``Application`` object
directly, which meant every module had ``from telegram.ext import Application``
at the top and could not be instantiated or tested without a live PTB app.

This module defines ``HandlerRegistrar`` — a ``Protocol`` that expresses
exactly the registration capabilities any BotModule needs:

    • register_command(command, handler, *, admin_only, group)
    • register_message(handler, filter, *, group)
    • register_callback_query(handler)
    • register_callback_query_bus()  — shorthand: mounts core.callback_bus

The concrete PTB implementation lives in
``modules/downloader/integrations/ptb_registrar.PtbHandlerRegistrar``.
Tests inject a ``RecordingRegistrar`` (or any other fake) without
constructing a PTB ``Application`` at all.

Usage
-----
In a module::

    from core.registrar import HandlerRegistrar

    class BillingModule:
        def setup(self, registrar: HandlerRegistrar) -> None:
            registrar.register_command("bill", handle_bill_command)
            registrar.register_message(handle_bill_photo, filter_name="PHOTO")

In ``infra/telegram/runner.py``::

    from modules.downloader.integrations.ptb_registrar import PtbHandlerRegistrar

    registrar = PtbHandlerRegistrar(app, admin_ids=frozenset(ADMIN_IDS))
    for module in modules:
        module.setup(registrar)

Filter names
------------
``register_message`` accepts a ``filter_name`` string rather than a PTB
``filters.*`` object so that module code stays PTB-free.  The PTB
implementation maps names to concrete filter objects.

Supported filter names (case-insensitive):

    "TEXT"          — filters.TEXT & ~filters.COMMAND
    "TEXT_REPLY"    — filters.TEXT & filters.REPLY & ~filters.COMMAND
    "PHOTO"         — filters.PHOTO
    "DOCUMENT_ALL"  — filters.Document.ALL
    "COOKIE"        — filters.Document.ALL & AdminFilter & document is not None
                      (uses core.filters.CookieFilter)

Composing filters beyond this set should be done inside the PTB registrar;
module code never needs the raw filter objects.
"""
from __future__ import annotations

from typing import Callable, Any, Protocol, runtime_checkable


@runtime_checkable
class HandlerRegistrar(Protocol):
    """
    Platform-agnostic interface for registering bot handlers.

    Every method maps to one category of bot interaction.  Implementations
    translate these calls into platform-specific wiring (e.g. PTB
    ``app.add_handler``).  Test doubles record calls without any I/O.

    All parameters are Python primitives or plain callables — no
    ``telegram.*`` types appear anywhere in this interface.
    """

    def register_command(
        self,
        command: str,
        handler: Callable,
        *,
        admin_only: bool = False,
        group: int = 0,
    ) -> None:
        """Register a ``/command`` handler.

        Parameters
        ----------
        command:
            Command name without the leading slash (e.g. ``"start"``).
        handler:
            Async callable ``(update, context) -> None``.
        admin_only:
            When *True*, the handler is wrapped with an admin filter.
            The registrar is responsible for what "admin" means on the
            target platform.
        group:
            Handler group (PTB concept; 0 = default).  Higher numbers
            run after lower numbers.
        """
        ...

    def register_message(
        self,
        handler: Callable,
        filter_name: str,
        *,
        group: int = 0,
    ) -> None:
        """Register a message handler for a named filter.

        Parameters
        ----------
        handler:
            Async callable ``(update, context) -> None``.
        filter_name:
            One of the supported filter name strings (see module docstring).
        group:
            Handler group.
        """
        ...

    def register_callback_query(self, handler: Callable) -> None:
        """Register a generic ``CallbackQueryHandler``.

        Typically called once with ``core.callback_bus.handle_callback``
        as the argument; all per-module callbacks are then routed through
        the bus rather than registered individually.
        """
        ...

    def register_callback_query_bus(self) -> None:
        """Shorthand: mount ``core.callback_bus.handle_callback``.

        Equivalent to::

            from core.callback_bus import handle_callback
            registrar.register_callback_query(handle_callback)

        Provided as a convenience so module code does not need to import
        ``handle_callback`` directly.
        """
        ...

    def apply_command_registry(self) -> None:
        """Flush ``core.handler_registry.registry`` into this registrar.

        Calls ``registry.apply(command_registrar)`` where
        ``command_registrar`` is an adapter that forwards each entry to
        ``self.register_command``.  This lets modules that use the
        ``@command_handler`` decorator remain unaware of the registrar.
        """
        ...
