import logging
import asyncio
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import CallbackContext
from telegram.constants import ParseMode
from telegram.helpers import escape_markdown
from config import ADMIN_IDS, RATE_TIER_LIMITS
from core.handler_registry import command_handler
from services.container import services
from services.user_service import (
    get_allowed_users, save_allowed_users, get_all_users_info,
    get_user_display_name, get_user_display_names_bulk,
)
from services.ratelimit import save_rate_limit, get_user_tier, get_user_limit, set_user_tier
from database.db import get_db
from utils.i18n import t
from utils.utils import require_admin, require_message, format_history_list

logger = logging.getLogger(__name__)


@command_handler("allow", admin_only=True)
@require_admin
@require_message
async def allow_command(update: Update, context: CallbackContext):
    user_id = update.message.from_user.id
    if not context.args:
        await update.message.reply_text(t("usage_allow", user_id))
        return

    try:
        target_id = int(context.args[0])
    except ValueError:
        await update.message.reply_text(t("invalid_user_id", user_id))
        return

    users = get_allowed_users()
    users.add(target_id)
    save_allowed_users(users)

    await update.message.reply_text(t("user_allowed", user_id, target_id=target_id))


@command_handler("block", admin_only=True)
@require_admin
@require_message
async def block_command(update: Update, context: CallbackContext):
    user_id = update.message.from_user.id
    if not context.args:
        await update.message.reply_text(t("usage_block", user_id))
        return

    try:
        target_id = int(context.args[0])
    except ValueError:
        await update.message.reply_text(t("invalid_user_id", user_id))
        return

    users = get_allowed_users()
    users.discard(target_id)
    save_allowed_users(users)

    await update.message.reply_text(t("user_blocked", user_id, target_id=target_id))


@command_handler("users", admin_only=True)
@require_admin
@require_message
async def users_command(update: Update, context: CallbackContext):
    user_id = update.message.from_user.id
    allowed = get_allowed_users()
    users_info = await get_all_users_info()

    if not allowed:
        await update.message.reply_text(t("no_users", user_id))
        return

    last_seen_map = {u.get("user_id"): u.get("last_seen", 0) for u in (users_info or [])}
    name_map = await get_user_display_names_bulk(list(allowed))

    lines = [escape_markdown(t("admin.allowed_users_title", user_id), version=2)]
    for uid in sorted(allowed, key=lambda x: last_seen_map.get(x, 0), reverse=True):
        name = name_map.get(uid, str(uid))
        uid_str = escape_markdown(f"`{uid}`", version=2)

        if name == str(uid):
            lines.append(f"• {uid_str} _\\(Never used bot\\)_")
        else:
            safe_name = escape_markdown(name, version=2)
            lines.append(f"• {safe_name} \\({uid_str}\\)")

    await update.message.reply_text(
        "\n".join(lines),
        parse_mode=ParseMode.MARKDOWN_V2,
    )


@command_handler("broadcast", admin_only=True)
@require_admin
@require_message
async def broadcast_command(update: Update, context: CallbackContext):
    user_id = update.message.from_user.id
    if not context.args:
        await update.message.reply_text(t("usage_broadcast", user_id))
        return

    message = " ".join(context.args)
    users = get_allowed_users() - ADMIN_IDS

    semaphore = asyncio.Semaphore(10)

    async def _send(uid):
        async with semaphore:
            try:
                await context.bot.send_message(chat_id=uid, text=message)
                return True
            except Exception as e:
                logger.warning("Failed to send broadcast to %s: %s", uid, e)
                return False

    results = await asyncio.gather(*[_send(uid) for uid in users])
    success = sum(results)
    await update.message.reply_text(
        t("broadcast_sent", user_id, success=success, failed=len(users) - success)
    )


@command_handler("userhistory", admin_only=True)
@require_admin
@require_message
async def userhistory_command(update: Update, context: CallbackContext):
    users = get_allowed_users() - ADMIN_IDS
    user_id = update.message.from_user.id
    if not users:
        await update.message.reply_text(t("no_users_to_show", user_id))
        return

    name_map = await get_user_display_names_bulk(list(users))

    keyboard = [
        [InlineKeyboardButton(name_map.get(uid, str(uid)), callback_data=f"uh_{uid}")]
        for uid in sorted(users)
    ]
    await update.message.reply_text(
        t("select_user_history", user_id),
        reply_markup=InlineKeyboardMarkup(keyboard),
    )


@command_handler("settier", admin_only=True)
@require_admin
@require_message
async def settier_command(update: Update, context: CallbackContext):
    user_id = update.message.from_user.id
    VALID_TIERS = list(RATE_TIER_LIMITS.keys())

    if not context.args:
        async with get_db() as db:
            async with db.execute(
                "SELECT user_id, tier, max_per_hour, note, set_at FROM user_rate_tiers ORDER BY set_at DESC LIMIT 20"
            ) as cur:
                rows = await cur.fetchall()

        if not rows:
            await update.message.reply_text(t("settier_no_custom", user_id))
            return

        lines = [t("settier_list_title", user_id)]
        for r in rows:
            limit_str = (
                f"(自定义: {r[2]}/h)" if r[2] is not None
                else f"({RATE_TIER_LIMITS.get(r[1], '?')}/h)"
            )
            lines.append(f"• {r[0]} → {r[1]} {limit_str}")
        await update.message.reply_text("\n".join(lines))
        return

    try:
        target_id = int(context.args[0])
    except ValueError:
        await update.message.reply_text(t("invalid_user_id", user_id))
        return

    tier = context.args[1] if len(context.args) > 1 else None
    if not tier or tier not in VALID_TIERS:
        await update.message.reply_text(
            t("settier_usage", user_id, tiers=", ".join(VALID_TIERS))
        )
        return

    custom_limit = None
    if len(context.args) > 2:
        try:
            custom_limit = int(context.args[2])
        except ValueError:
            pass

    await set_user_tier(target_id, tier, custom_limit)
    limit = custom_limit if custom_limit is not None else RATE_TIER_LIMITS.get(tier, "?")
    await update.message.reply_text(
        t("settier_updated", user_id, target_id=target_id, tier=tier, limit=limit)
    )
