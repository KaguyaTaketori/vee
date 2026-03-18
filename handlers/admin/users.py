# handlers/admin/users.py
"""
handlers/admin/users.py

Decoupling
──────────
Two-layer pattern throughout:

  _xxx_impl(ctx: PlatformContext, ...)  — pure business logic, no PTB
  xxx_command(update, context)          — thin PTB adapter

ParseMode.MARKDOWN_V2 content (escape_markdown) is handled via the new
``ctx.send_markdown_v2()`` method on PlatformContext, keeping the
telegram.constants import out of _impl functions.

The ``context.bot.send_message`` call for broadcast stays in the PTB
adapter layer — it is a platform-specific operation with no PlatformContext
equivalent at this scope.
"""
from __future__ import annotations

import asyncio
import logging

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import CallbackContext
from telegram.constants import ParseMode
from telegram.helpers import escape_markdown

from config import ADMIN_IDS, RATE_TIER_LIMITS
from core.handler_registry import command_handler
from shared.services.container import services
from shared.services.platform_context import PlatformContext, TelegramContext, btn, KeyboardLayout
from shared.services.user_service import (
    get_allowed_users, save_allowed_users, get_all_users_info,
    get_user_display_names_bulk,
)
from shared.services.ratelimit import save_rate_limit, get_user_tier, get_user_limit, set_user_tier
from database.db import get_db
from utils.i18n import t
from utils.utils import require_admin, require_message

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# /allow
# ---------------------------------------------------------------------------

async def _allow_impl(ctx: PlatformContext) -> None:
    if not ctx.args:
        await ctx.send(t("usage_allow", ctx.user_id))
        return
    try:
        target_id = int(ctx.args[0])
    except ValueError:
        await ctx.send(t("invalid_user_id", ctx.user_id))
        return
    users = get_allowed_users()
    users.add(target_id)
    save_allowed_users(users)
    await ctx.send(t("user_allowed", ctx.user_id, target_id=target_id))


@command_handler("allow", admin_only=True)
@require_admin
@require_message
async def allow_command(update: Update, context: CallbackContext) -> None:
    await _allow_impl(TelegramContext.from_message(update, context))


# ---------------------------------------------------------------------------
# /block
# ---------------------------------------------------------------------------

async def _block_impl(ctx: PlatformContext) -> None:
    if not ctx.args:
        await ctx.send(t("usage_block", ctx.user_id))
        return
    try:
        target_id = int(ctx.args[0])
    except ValueError:
        await ctx.send(t("invalid_user_id", ctx.user_id))
        return
    users = get_allowed_users()
    users.discard(target_id)
    save_allowed_users(users)
    await ctx.send(t("user_blocked", ctx.user_id, target_id=target_id))


@command_handler("block", admin_only=True)
@require_admin
@require_message
async def block_command(update: Update, context: CallbackContext) -> None:
    await _block_impl(TelegramContext.from_message(update, context))


# ---------------------------------------------------------------------------
# /users
# ---------------------------------------------------------------------------

async def _users_impl(ctx: PlatformContext) -> None:
    allowed = get_allowed_users()
    if not allowed:
        await ctx.send(t("no_users", ctx.user_id))
        return

    users_info = await get_all_users_info()
    last_seen_map = {u.get("user_id"): u.get("last_seen", 0) for u in (users_info or [])}
    name_map = await get_user_display_names_bulk(list(allowed))

    lines = [escape_markdown(t("admin.allowed_users_title", ctx.user_id), version=2)]
    for uid in sorted(allowed, key=lambda x: last_seen_map.get(x, 0), reverse=True):
        name = name_map.get(uid, str(uid))
        uid_str = escape_markdown(f"`{uid}`", version=2)
        if name == str(uid):
            lines.append(f"• {uid_str} _\\(Never used bot\\)_")
        else:
            safe_name = escape_markdown(name, version=2)
            lines.append(f"• {safe_name} \\({uid_str}\\)")

    await ctx.send_markdown_v2("\n".join(lines))


@command_handler("users", admin_only=True)
@require_admin
@require_message
async def users_command(update: Update, context: CallbackContext) -> None:
    await _users_impl(TelegramContext.from_message(update, context))


# ---------------------------------------------------------------------------
# /broadcast
# ---------------------------------------------------------------------------

async def _broadcast_impl(
    ctx: PlatformContext,
    message: str,
    *,
    send_fn,          # async (uid: int) -> bool  — platform-specific sender
) -> None:
    """Business logic: fan-out message, report results.

    ``send_fn`` is injected by the PTB adapter so the _impl stays
    platform-free while the actual bot.send_message call stays in PTB land.
    """
    users = get_allowed_users() - (ADMIN_IDS or set())
    semaphore = asyncio.Semaphore(10)

    async def _send(uid: int) -> bool:
        async with semaphore:
            return await send_fn(uid)

    results = await asyncio.gather(*[_send(uid) for uid in users])
    success = sum(results)
    await ctx.send(t("broadcast_sent", ctx.user_id, success=success, failed=len(users) - success))


@command_handler("broadcast", admin_only=True)
@require_admin
@require_message
async def broadcast_command(update: Update, context: CallbackContext) -> None:
    ctx = TelegramContext.from_message(update, context)
    if not ctx.args:
        await ctx.send(t("usage_broadcast", ctx.user_id))
        return
    message = " ".join(ctx.args)

    async def _ptb_send(uid: int) -> bool:
        try:
            await context.bot.send_message(chat_id=uid, text=message)
            return True
        except Exception as e:
            logger.warning("Failed to send broadcast to %s: %s", uid, e)
            return False

    await _broadcast_impl(ctx, message, send_fn=_ptb_send)


# ---------------------------------------------------------------------------
# /userhistory
# ---------------------------------------------------------------------------

async def _userhistory_impl(ctx: PlatformContext) -> None:
    users = get_allowed_users() - (ADMIN_IDS or set())
    if not users:
        await ctx.send(t("no_users_to_show", ctx.user_id))
        return
    name_map = await get_user_display_names_bulk(list(users))
    buttons: KeyboardLayout = [
        [btn(name_map.get(uid, str(uid)), f"uh_{uid}")]
        for uid in sorted(users)
    ]
    await ctx.send_keyboard(t("select_user_history", ctx.user_id), buttons)


@command_handler("userhistory", admin_only=True)
@require_admin
@require_message
async def userhistory_command(update: Update, context: CallbackContext) -> None:
    await _userhistory_impl(TelegramContext.from_message(update, context))


# ---------------------------------------------------------------------------
# /settier
# ---------------------------------------------------------------------------

async def _settier_impl(ctx: PlatformContext) -> None:
    VALID_TIERS = list(RATE_TIER_LIMITS.keys())

    if not ctx.args:
        async with get_db() as db:
            async with db.execute(
                "SELECT user_id, tier, max_per_hour, note, set_at "
                "FROM user_rate_tiers ORDER BY set_at DESC LIMIT 20"
            ) as cur:
                rows = await cur.fetchall()

        if not rows:
            await ctx.send(t("settier_no_custom", ctx.user_id))
            return

        lines = [t("settier_list_title", ctx.user_id)]
        for r in rows:
            limit_str = (
                f"(自定义: {r[2]}/h)" if r[2] is not None
                else f"({RATE_TIER_LIMITS.get(r[1], '?')}/h)"
            )
            lines.append(f"• {r[0]} → {r[1]} {limit_str}")
        await ctx.send("\n".join(lines))
        return

    try:
        target_id = int(ctx.args[0])
    except ValueError:
        await ctx.send(t("invalid_user_id", ctx.user_id))
        return

    tier = ctx.args[1] if len(ctx.args) > 1 else None
    if not tier or tier not in VALID_TIERS:
        await ctx.send(t("settier_usage", ctx.user_id, tiers=", ".join(VALID_TIERS)))
        return

    custom_limit: int | None = None
    if len(ctx.args) > 2:
        try:
            custom_limit = int(ctx.args[2])
        except ValueError:
            pass

    await set_user_tier(target_id, tier, custom_limit)
    limit = custom_limit if custom_limit is not None else RATE_TIER_LIMITS.get(tier, "?")
    await ctx.send(t("settier_updated", ctx.user_id, target_id=target_id, tier=tier, limit=limit))


@command_handler("settier", admin_only=True)
@require_admin
@require_message
async def settier_command(update: Update, context: CallbackContext) -> None:
    await _settier_impl(TelegramContext.from_message(update, context))


# ---------------------------------------------------------------------------
# /setrate
# ---------------------------------------------------------------------------

async def _setrate_impl(ctx: PlatformContext) -> None:
    if not ctx.args:
        status = services.limiter.get_status()
        await ctx.send(
            t("rateinfo_current", ctx.user_id,
              max=status["max_downloads_per_hour"],
              enabled=status["enabled"])
        )
        return

    try:
        max_val = int(ctx.args[0])
    except ValueError:
        await ctx.send(t("setrate_invalid", ctx.user_id))
        return

    enabled = True
    if len(ctx.args) > 1:
        enabled = ctx.args[1].lower() != "off"

    await save_rate_limit(max_val, enabled)
    services.limiter.reload()
    await ctx.send(t("setrate_updated", ctx.user_id, max=max_val, enabled=enabled))


@command_handler("setrate", admin_only=True)
@require_admin
@require_message
async def setrate_command(update: Update, context: CallbackContext) -> None:
    await _setrate_impl(TelegramContext.from_message(update, context))


# ---------------------------------------------------------------------------
# /rateinfo
# ---------------------------------------------------------------------------

async def _rateinfo_impl(ctx: PlatformContext) -> None:
    status = services.limiter.get_status()
    tier = await get_user_tier(ctx.user_id)
    limit = await get_user_limit(ctx.user_id)
    await ctx.send(
        t("rateinfo", ctx.user_id,
          max=status["max_downloads_per_hour"],
          enabled=status["enabled"],
          tier=tier,
          limit=limit)
    )


@command_handler("rateinfo", admin_only=True)
@require_admin
@require_message
async def rateinfo_command(update: Update, context: CallbackContext) -> None:
    await _rateinfo_impl(TelegramContext.from_message(update, context))
