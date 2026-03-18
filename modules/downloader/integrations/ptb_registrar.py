# modules/downloader/integrations/ptb_registrar.py
"""
PTB implementation of ``core.registrar.HandlerRegistrar``.

All ``telegram.*`` imports are confined to this file.  No BotModule ever
needs to touch PTB handler types directly.

Filter name mapping
-------------------
See ``core.registrar`` module docstring for the full list of supported
``filter_name`` strings.
"""
from __future__ import annotations

import logging
from typing import Callable, Any

from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    CallbackQueryHandler,
    filters,
)

from core.filters import AdminFilter, CookieFilter
from core.handler_registry import registry as _cmd_registry, CommandRegistrar

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Filter name → PTB filter object
# ---------------------------------------------------------------------------

_FILTER_MAP: dict[str, Any] = {
    "TEXT":         filters.TEXT & ~filters.COMMAND,
    "TEXT_REPLY":   filters.TEXT & filters.REPLY & ~filters.COMMAND,
    "PHOTO":        filters.PHOTO,
    "DOCUMENT_ALL": filters.Document.ALL,
    "COOKIE":       filters.Document.ALL & CookieFilter(),
}


def _resolve_filter(name: str) -> Any:
    key = name.upper()
    if key not in _FILTER_MAP:
        raise ValueError(
            f"Unknown filter name {name!r}. "
            f"Supported: {sorted(_FILTER_MAP)}"
        )
    return _FILTER_MAP[key]


# ---------------------------------------------------------------------------
# CommandRegistrar adapter (bridges HandlerRegistry → PtbHandlerRegistrar)
# ---------------------------------------------------------------------------

class _BridgeCommandRegistrar:
    """
    Thin adapter that satisfies ``core.handler_registry.CommandRegistrar``
    and forwards each entry to a ``PtbHandlerRegistrar`` instance.

    This lets ``HandlerRegistry.apply()`` work without knowing about PTB.
    """

    def __init__(self, ptb_registrar: "PtbHandlerRegistrar") -> None:
        self._reg = ptb_registrar

    def register_command(
        self,
        command: str,
        handler: Callable,
        *,
        admin_only: bool = False,
        **kwargs: Any,
    ) -> None:
        group = kwargs.pop("group", 0)
        self._reg.register_command(
            command, handler, admin_only=admin_only, group=group
        )


# ---------------------------------------------------------------------------
# PtbHandlerRegistrar
# ---------------------------------------------------------------------------

class PtbHandlerRegistrar:
    """
    Concrete ``HandlerRegistrar`` for python-telegram-bot (PTB).

    Constructs PTB handler objects and registers them with the
    ``Application`` instance.  Nothing outside this class (or
    ``infra/telegram/runner.py``) needs to import ``telegram.*``.

    Parameters
    ----------
    app:
        The PTB ``Application`` object built by the runner.
    admin_ids:
        Frozen set of admin user IDs, used by ``register_command`` when
        ``admin_only=True``.
    """

    def __init__(self, app: Application, *, admin_ids: frozenset[int]) -> None:
        self._app = app
        self._admin_ids = admin_ids

    # ── HandlerRegistrar interface ─────────────────────────────────────────

    def register_command(
        self,
        command: str,
        handler: Callable,
        *,
        admin_only: bool = False,
        group: int = 0,
    ) -> None:
        if admin_only:
            if not self._admin_ids:
                logger.debug(
                    "Skipping admin command /%s (no ADMIN_IDS configured)", command
                )
                return
            self._app.add_handler(
                CommandHandler(command, handler, filters=AdminFilter()),
                group=group,
            )
            logger.debug("Registered admin command: /%s (group=%d)", command, group)
        else:
            self._app.add_handler(
                CommandHandler(command, handler),
                group=group,
            )
            logger.debug("Registered user command: /%s (group=%d)", command, group)

    def register_message(
        self,
        handler: Callable,
        filter_name: str,
        *,
        group: int = 0,
    ) -> None:
        ptb_filter = _resolve_filter(filter_name)
        self._app.add_handler(MessageHandler(ptb_filter, handler), group=group)
        logger.debug(
            "Registered message handler %s (filter=%s, group=%d)",
            handler.__name__, filter_name, group,
        )

    def register_callback_query(self, handler: Callable) -> None:
        self._app.add_handler(CallbackQueryHandler(handler))
        logger.debug("Registered callback query handler: %s", handler.__name__)

    def register_callback_query_bus(self) -> None:
        from core.callback_bus import handle_callback
        self.register_callback_query(handle_callback)
        logger.debug("Mounted core.callback_bus as unified CallbackQueryHandler")

    def apply_command_registry(self) -> None:
        bridge = _BridgeCommandRegistrar(self)
        _cmd_registry.apply(bridge)
        logger.debug("Flushed HandlerRegistry into PtbHandlerRegistrar")
