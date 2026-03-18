"""
modules/billing/handlers/bill_callbacks.py

Decoupling
──────────
All @register handlers receive ``CallbackContext`` (core.callback_bus).
No handler touches ``telegram.*`` directly.

``_cb_bill_edit`` previously used:
    ctx.raw_context.bot.send_message(..., reply_markup=ForceReply(...))
    ctx.raw_context.user_data["bill_edit_cache_id"] = cache_id

It now uses:
    await ctx.request_text_input(prompt, state_key=..., placeholder=...)

``request_text_input`` is defined on ``CallbackContext`` ABC and implemented
by ``TelegramCallbackContext`` as bot.send_message + ForceReply + user_data
write — no ForceReply import or raw_context access in this file.
"""
from __future__ import annotations

import logging

from telegram.ext import CallbackContext as PTBCallbackContext

from core.callback_bus import register, CallbackContext
from modules.billing.database.bills import insert_bill
from modules.billing.services.bill_cache import bill_cache, BillEntry
from shared.services.platform_context import TelegramContext

logger = logging.getLogger(__name__)

# State key prefix written by request_text_input, read by handle_bill_edit_reply.
# Encoding cache_id inside the key avoids a second user_data entry.
_STATE_PREFIX = "bill_edit:"


def _parse_cache_id(data: str) -> str:
    return data.split(":", 1)[1]


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
    await ctx.request_text_input(
        prompt=(
            f"当前金额：`{entry.amount:.2f} {entry.currency}`\n"
            f"请输入新的金额（纯数字，如 `128.5`）："
        ),
        state_key=f"{_STATE_PREFIX}{cache_id}",
        placeholder="请输入新金额",
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
# ForceReply reply handler — registered as MessageHandler in BillingModule
# ---------------------------------------------------------------------------

async def handle_bill_edit_reply(update, context: PTBCallbackContext) -> None:
    """Handle the user's reply to the ForceReply prompt from _cb_bill_edit."""
    if not update.message:
        return

    state = context.user_data.get("text_input_state", "")
    if not state.startswith(_STATE_PREFIX):
        return

    cache_id = state[len(_STATE_PREFIX):]
    context.user_data.pop("text_input_state", None)

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
