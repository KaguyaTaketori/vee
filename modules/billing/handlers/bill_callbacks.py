"""
modules/billing/handlers/bill_callbacks.py

Decoupling
──────────
All @register handlers now receive ``CallbackContext`` (core.callback_bus)
instead of raw PTB objects.  No handler imports telegram.* directly.

• ctx.data            — callback_data string ("bill_confirm:<cache_id>", …)
• ctx.user_id         — sender's user ID
• ctx.platform_ctx    — PlatformContext (edit / send / send_keyboard)
• ctx.answer()        — silent ACK
• ctx.answer_alert()  — alert popup
• ctx.raw_context     — PTB CallbackContext (only for user_data in bill_edit)

The ForceReply edit handler (handle_bill_edit_reply) is a MessageHandler,
not a callback — it is unchanged and still uses PTB update/context directly.
"""
from __future__ import annotations

import logging

from telegram import ForceReply
from telegram.ext import CallbackContext as PTBCallbackContext

from core.callback_bus import register, CallbackContext
from modules.billing.database.bills import insert_bill
from modules.billing.services.bill_cache import bill_cache, BillEntry
from shared.services.platform_context import TelegramContext
from utils.i18n import t

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _parse_cache_id(data: str) -> str:
    """Extract cache_id from 'bill_confirm:<cache_id>' etc."""
    return data.split(":", 1)[1]


def _confirmation_text(entry: BillEntry) -> str:
    return (
        f"📋 *账单确认*\n\n"
        f"💰 金额：`{entry.amount:.2f} {entry.currency}`\n"
        f"🏷️ 类别：{entry.category}\n"
        f"🏪 商家：{entry.merchant}\n"
        f"📝 描述：{entry.description}\n"
        f"📅 日期：{entry.bill_date}\n\n"
        f"请确认以上信息是否正确："
    )


# ---------------------------------------------------------------------------
# bill_confirm
# ---------------------------------------------------------------------------

@register(lambda d: d.startswith("bill_confirm:"))
async def _cb_bill_confirm(ctx: CallbackContext) -> None:
    cache_id = _parse_cache_id(ctx.data)

    entry = await bill_cache.get(cache_id)
    if entry is None:
        await ctx.answer_alert("⏰ 账单已过期，请重新发送。")
        return
    if entry.user_id != ctx.user_id:
        await ctx.answer_alert("❌ 这不是你的账单。")
        return

    await insert_bill(entry)
    await bill_cache.delete(cache_id)
    await ctx.answer()
    await ctx.platform_ctx.edit(
        f"✅ 记账成功！\n\n"
        f"💰 {entry.amount:.2f} {entry.currency}  |  {entry.category}\n"
        f"📝 {entry.description}"
    )
    logger.info("Bill confirmed and saved: cache_id=%s user_id=%s", cache_id, ctx.user_id)


# ---------------------------------------------------------------------------
# bill_edit
# ---------------------------------------------------------------------------

@register(lambda d: d.startswith("bill_edit:"))
async def _cb_bill_edit(ctx: CallbackContext) -> None:
    cache_id = _parse_cache_id(ctx.data)

    entry = await bill_cache.get(cache_id)
    if entry is None:
        await ctx.answer_alert("⏰ 账单已过期，请重新发送。")
        return
    if entry.user_id != ctx.user_id:
        await ctx.answer_alert("❌ 这不是你的账单。")
        return

    await ctx.answer()

    # Store edit state in PTB user_data (PTB-specific, accessed via raw_context)
    ctx.raw_context.user_data["bill_edit_cache_id"] = cache_id
    ctx.raw_context.user_data["bill_edit_message_id"] = (
        ctx.raw_context.effective_message.message_id
        if hasattr(ctx.raw_context, "effective_message")
        else None
    )

    # ForceReply is a PTB-specific UI construct; send it via raw_context.bot
    # so that PlatformContext doesn't need to expose this niche capability.
    await ctx.raw_context.bot.send_message(
        chat_id=ctx.user_id,
        text=(
            f"当前金额：`{entry.amount:.2f} {entry.currency}`\n"
            f"请输入新的金额（纯数字，如 `128.5`）："
        ),
        parse_mode="Markdown",
        reply_markup=ForceReply(
            selective=True,
            input_field_placeholder="请输入新金额",
        ),
    )


# ---------------------------------------------------------------------------
# bill_cancel
# ---------------------------------------------------------------------------

@register(lambda d: d.startswith("bill_cancel:"))
async def _cb_bill_cancel(ctx: CallbackContext) -> None:
    cache_id = _parse_cache_id(ctx.data)

    entry = await bill_cache.get(cache_id)
    if entry and entry.user_id != ctx.user_id:
        await ctx.answer_alert("❌ 这不是你的账单。")
        return

    await bill_cache.delete(cache_id)
    await ctx.answer()
    await ctx.platform_ctx.edit("❌ 已取消记账。")


# ---------------------------------------------------------------------------
# ForceReply edit handler
# — registered as MessageHandler in BillingModule, NOT via callback_bus —
# ---------------------------------------------------------------------------

async def handle_bill_edit_reply(update, context: PTBCallbackContext) -> None:
    """
    Handles the user's reply to the ForceReply prompt sent by _cb_bill_edit.

    This is a MessageHandler entry point (PTB update/context), not a callback,
    so it retains direct PTB wiring.  Business logic is kept minimal here.
    """
    if not update.message:
        return

    cache_id = context.user_data.get("bill_edit_cache_id")
    if not cache_id:
        return

    context.user_data.pop("bill_edit_cache_id", None)
    context.user_data.pop("bill_edit_message_id", None)

    ctx = TelegramContext.from_message(update, context)

    text = update.message.text.strip()
    try:
        new_amount = float(text.replace(",", "."))
    except ValueError:
        await ctx.send("❌ 金额格式不正确，请输入数字。")
        return

    entry = await bill_cache.get(cache_id)
    if entry is None:
        await ctx.send("⏰ 账单已过期，请重新发送。")
        return
    if entry.user_id != ctx.user_id:
        await ctx.send("❌ 这不是你的账单。")
        return

    updated = BillEntry(
        user_id=entry.user_id,
        amount=new_amount,
        currency=entry.currency,
        category=entry.category,
        description=entry.description,
        merchant=entry.merchant,
        bill_date=entry.bill_date,
    )
    await bill_cache.set_with_id(cache_id, updated)

    from modules.billing.handlers.bill_handler import _confirmation_keyboard, _build_confirmation_text
    await ctx.send_keyboard(_build_confirmation_text(updated), _confirmation_keyboard(cache_id))
