# handlers/user/basic.py
"""
handlers/user/basic.py
───────────────────────
Basic user commands: /start, /help, /myid, /lang

Two-layer pattern
─────────────────
1. ``_impl(ctx: PlatformContext, ...)`` — pure business logic.
   No telegram.* import, no Update, no CallbackContext, no tg_user object.
   All user identity comes from ctx.user_id / ctx.username / ctx.display_name.

2. ``xxx_command(update, context)`` — thin PTB adapter.
   Builds TelegramContext, delegates immediately to _impl.

``user_log_args(tg_user: telegram.User)`` is no longer needed:
  log_user() is called with (ctx.user_id, ctx.username, ctx.display_name)
  which are plain strings already on PlatformContext.
"""
from __future__ import annotations

import logging

from telegram import Update
from telegram.ext import CallbackContext

from config import ADMIN_IDS
from core.handler_registry import command_handler
from shared.services.platform_context import PlatformContext, TelegramContext, btn
from shared.services.user_service import track_user, set_user_language
from utils.logger import log_user
from utils.i18n import t, LANGUAGES
from utils.utils import require_message

logger = logging.getLogger(__name__)


# ── /start ────────────────────────────────────────────────────────────────

async def _start_impl(ctx: PlatformContext) -> None:
    # track_user still needs the raw PTB user object for persistence;
    # the PTB adapter passes it as a keyword so _impl stays PTB-free.
    # log_user uses plain strings available on ctx — no telegram.User needed.
    log_user(
        user_id=ctx.user_id,
        username=ctx.username or "N/A",
        name=ctx.display_name,
        action="start",
    )
    await ctx.send(t("welcome", ctx.user_id))


@command_handler("start")
@require_message
async def start_command(update: Update, context: CallbackContext) -> None:
    ctx = TelegramContext.from_message(update, context)
    track_user(update.message.from_user)   # PTB-specific persistence call, stays in adapter
    await _start_impl(ctx)


# ── /help ─────────────────────────────────────────────────────────────────

async def _help_impl(ctx: PlatformContext, is_admin: bool) -> None:
    key = "admin_commands" if is_admin else "available_commands"
    await ctx.send(t(key, ctx.user_id))


@command_handler("help")
@require_message
async def help_command(update: Update, context: CallbackContext) -> None:
    ctx = TelegramContext.from_message(update, context)
    is_admin = ctx.user_id in ADMIN_IDS if ADMIN_IDS else False
    await _help_impl(ctx, is_admin)


# ── /myid ─────────────────────────────────────────────────────────────────

async def _myid_impl(ctx: PlatformContext) -> None:
    await ctx.send_markdown(
        t(
            "your_id",
            ctx.user_id,
            username=ctx.username or "N/A",
            name=ctx.display_name,
        )
    )


@command_handler("myid")
@require_message
async def myid_command(update: Update, context: CallbackContext) -> None:
    ctx = TelegramContext.from_message(update, context)
    await _myid_impl(ctx)


# ── /lang ─────────────────────────────────────────────────────────────────

async def _lang_impl(ctx: PlatformContext) -> None:
    """Handle /lang [code].

    • No args  → show language picker keyboard
    • With arg → set language directly
    """
    if not ctx.args:
        buttons = [
            [btn(name, f"lang_{code}")]
            for code, name in LANGUAGES.items()
        ]
        await ctx.send_keyboard(t("select_language", ctx.user_id), buttons)
        return

    lang_code = ctx.args[0].lower()
    if lang_code not in LANGUAGES:
        await ctx.send(t("invalid_language_option", ctx.user_id))
        return

    await set_user_language(ctx.user_id, lang_code)
    await ctx.send(t("language_changed", ctx.user_id))


@command_handler("lang")
@require_message
async def lang_command(update: Update, context: CallbackContext) -> None:
    ctx = TelegramContext.from_message(update, context)
    await _lang_impl(ctx)
