from __future__ import annotations

import logging
from typing import Callable, Any

from telegram.ext import Application, CommandHandler

from core.filters import AdminFilter

logger = logging.getLogger(__name__)


class PtbCommandRegistrar:
    def __init__(self, app: Application, *, admin_ids: frozenset[int]) -> None:
        self._app = app
        self._admin_ids = admin_ids

    def register_command(
        self,
        command: str,
        handler: Callable,
        *,
        admin_only: bool = False,
        **kwargs: Any,
    ) -> None:
        if admin_only:
            if not self._admin_ids:
                logger.debug("Skipping admin command /%s (no ADMIN_IDS configured)", command)
                return
            self._app.add_handler(
                CommandHandler(command, handler, filters=AdminFilter(), **kwargs)
            )
            logger.debug("Registered admin command: /%s", command)
        else:
            self._app.add_handler(
                CommandHandler(command, handler, **kwargs)
            )
            logger.debug("Registered user command: /%s", command)
