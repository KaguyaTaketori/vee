"""
utils/auth.py
─────────────
Authentication helpers — guards and decorators.

Architecture
────────────
There are **two layers** here, intentionally kept separate:

1. **Pure guard functions** (no Telegram import):
   ``guard_require_message``, ``guard_require_admin``

   These operate on ``RequestContext`` from ``shared.services.middleware``
   and have zero platform coupling.  They can be used directly in the
   middleware pipeline or in unit tests without a Telegram object in sight.

2. **PTB decorator wrappers** (Telegram-aware, lives in the adapter layer):
   ``require_message``, ``require_admin``

   These wrap PTB handler functions ``(Update, CallbackContext) -> None``
   and build a ``RequestContext`` from the incoming ``Update`` so the pure
   guards above can be reused.  All Telegram-specific imports are confined
   to these two functions.

Migration path
──────────────
- Existing handlers that use ``@require_admin @require_message`` continue
  to work unchanged — the decorators are **backward-compatible**.
- New handlers that accept a ``RequestContext`` directly should call the
  guard functions instead of the decorators.
- When the platform is eventually replaced, only the two PTB wrappers at
  the bottom of this file need to change; the guard logic stays.
"""

from __future__ import annotations

import logging
from functools import wraps
from typing import Callable, Awaitable

from config import ADMIN_IDS
from utils.i18n import t

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Pure, platform-agnostic helpers
# ---------------------------------------------------------------------------

def is_user_allowed(user_id: int) -> bool:
    """Return True if *user_id* is in the allow-list (or if no list is set)."""
    from shared.services.user_service import get_allowed_users
    allowed = get_allowed_users()
    return not allowed or user_id in allowed


def check_admin(user_id: int) -> bool:
    """Return True if *user_id* is a configured admin."""
    return bool(ADMIN_IDS) and user_id in ADMIN_IDS


# ---------------------------------------------------------------------------
# Platform-agnostic guards (RequestContext-based)
# ---------------------------------------------------------------------------

async def guard_require_admin(ctx: "RequestContext") -> bool:  # type: ignore[name-defined]
    """Return False (and reply with an error) if the user is not an admin.

    Designed to be used inside a handler that already has a RequestContext:

    ::

        ctx = RequestContext(user=update.message.from_user,
                             reply=update.message.reply_text)
        if not await guard_require_admin(ctx):
            return

    Returns True when the user is allowed to proceed.
    """
    from shared.services.middleware import RequestContext  # local import avoids cycles

    if not check_admin(ctx.user_id):
        logger.warning(
            "Unauthorized admin access attempt by user_id=%s", ctx.user_id
        )
        await ctx.reply(t("not_authorized", ctx.user_id))
        return False
    return True


# ---------------------------------------------------------------------------
# PTB decorator wrappers  (Telegram-specific — keep all telegram.* here)
# ---------------------------------------------------------------------------

def require_message(func: Callable) -> Callable:
    """Skip invocation if the Update carries no message.

    This is the PTB adapter for the common pattern::

        if not update.message:
            return

    All Telegram coupling lives in this wrapper.
    """
    @wraps(func)
    async def wrapper(update, context):  # type: ignore[no-untyped-def]
        from telegram import Update as TgUpdate  # lazy import — adapter layer only
        if isinstance(update, TgUpdate) and not update.message:
            return
        return await func(update, context)
    return wrapper


def require_admin(func: Callable) -> Callable:
    """Reject the call if the requesting user is not an admin.

    Builds a ``RequestContext`` from the PTB ``Update`` and delegates to
    ``guard_require_admin`` so the auth logic itself is platform-free.
    """
    @wraps(func)
    async def wrapper(update, context):  # type: ignore[no-untyped-def]
        from telegram import Update as TgUpdate  # lazy import — adapter layer only
        from shared.services.middleware import RequestContext

        if isinstance(update, TgUpdate):
            if not update.message:
                return
            ctx = RequestContext(
                user=update.message.from_user,
                reply=update.message.reply_text,
                meta={"command_text": update.message.text},
            )
            if not await guard_require_admin(ctx):
                return

        return await func(update, context)
    return wrapper
