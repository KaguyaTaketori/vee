import logging
import asyncio
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import CallbackContext
from telegram.constants import ParseMode
from telegram.helpers import escape_markdown
from config import ADMIN_IDS, RATE_TIER_LIMITS
from services.container import services
from services.user_service import (
    get_allowed_users, save_allowed_users, get_all_users_info,
    get_user_display_name, get_user_display_names_bulk
)
from services.ratelimit import save_rate_limit, get_user_tier, get_user_limit, set_user_tier
from database.db import get_db
from utils.i18n import t
from utils.utils import require_admin, require_message, format_history_list

logger = logging.getLogger(__name__)


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
                logger.warning(f"Failed to send broadcast to {uid}: {e}")
                return False

    results = await asyncio.gather(*[_send(uid) for uid in users])
    success = sum(results)
    await update.message.reply_text(
        f"Broadcast sent to {success}/{len(users)} users."
    )


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
        reply_markup=InlineKeyboardMarkup(keyboard)
    )


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
            limit_str = f"(自定义: {r[2]}/h)" if r[2] is not None else f"({RATE_TIER_LIMITS.get(r[1], '?')}/h)"
            note_str  = f" — {r[3]}" if r[3] else ""
            lines.append(f"  UID {r[0]}: [{r[1]}] {limit_str}{note_str}")

        await update.message.reply_text("\n".join(lines))
        return

    if len(context.args) == 1:
        try:
            target_id = int(context.args[0])
        except ValueError:
            await update.message.reply_text(t("settier_invalid_id", user_id))
            return

        tier      = await get_user_tier(target_id)
        limit     = await get_user_limit(target_id)
        remaining = await services.limiter.get_remaining(target_id)
        await update.message.reply_text(
            t("settier_info", user_id, target_id=target_id, tier=tier, limit=limit, remaining=remaining)
        )
        return

    try:
        target_id = int(context.args[0])
    except ValueError:
        await update.message.reply_text(t("settier_invalid_id", user_id))
        return

    tier_arg = context.args[1].lower()

    if tier_arg == "custom":
        if len(context.args) < 3:
            await update.message.reply_text(t("settier_usage", user_id))
            return
        try:
            custom_max = int(context.args[2])
            if custom_max < 0 or custom_max > 10000:
                raise ValueError
        except ValueError:
            await update.message.reply_text(t("settier_custom_invalid", user_id))
            return

        note = " ".join(context.args[3:]) if len(context.args) > 3 else ""
        await set_user_tier(target_id, "custom", note=note,
                            set_by=update.message.from_user.id, custom_max=custom_max)
        await update.message.reply_text(
            t("settier_custom_set", user_id, target_id=target_id, max=custom_max)
        )

    elif tier_arg in VALID_TIERS:
        note = " ".join(context.args[2:]) if len(context.args) > 2 else ""
        await set_user_tier(target_id, tier_arg, note=note,
                            set_by=update.message.from_user.id)
        tier_limit = RATE_TIER_LIMITS[tier_arg]
        await update.message.reply_text(
            t("settier_tier_set", user_id, target_id=target_id, tier=tier_arg, limit=tier_limit)
        )
    else:
        options = " | ".join(VALID_TIERS) + " | custom <数值>"
        await update.message.reply_text(
            t("settier_invalid_tier", user_id, tier=tier_arg, options=options)
        )

