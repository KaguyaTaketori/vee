# modules/billing/handlers/bill_callbacks.py
"""
modules/billing/handlers/bill_callbacks.py

变更说明（相对原版）：
1. _cb_bill_edit：支持多字段编辑，callback_data 格式由 bill_edit:{cache_id}
   改为 bill_edit:{field}:{cache_id}
2. _parse_cache_id：废弃，改为在各 handler 内部按需解析
3. handle_bill_edit_reply：支持多字段写回，按字段类型做校验
"""
from __future__ import annotations

import logging
import re

from telegram.ext import CallbackContext as PTBCallbackContext

from core.callback_bus import register, CallbackContext
from modules.billing.database.bills import insert_bill
from modules.billing.services.bill_cache import bill_cache, BillEntry
from shared.services.platform_context import TelegramContext

logger = logging.getLogger(__name__)

# state_key 写入 user_data 的前缀，格式：bill_edit:{field}:{cache_id}
_STATE_PREFIX = "bill_edit:"

# 各字段的中文标签和输入提示
_FIELD_CONFIG: dict[str, tuple[str, str]] = {
    "amount":      ("金额",    "请输入新金额（纯数字，如：128.5）"),
    "category":    ("类别",    "请输入类别（餐饮/交通/购物/娱乐/医疗/住房/水电煤/其他）"),
    "merchant":    ("商家",    "请输入商家名称"),
    "description": ("描述",    "请输入描述（15字以内）"),
    "bill_date":   ("日期",    "请输入日期（格式：2024-03-18）"),
}


# ---------------------------------------------------------------------------
# bill_confirm
# ---------------------------------------------------------------------------

@register(lambda d: d.startswith("bill_confirm:"))
async def _cb_bill_confirm(ctx: CallbackContext) -> None:
    cache_id = ctx.data.split(":", 1)[1]

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
        f"💰 {entry.amount:.2f} {entry.currency}  |  {entry.category or '未分类'}\n"
        f"📝 {entry.description or '—'}"
    )
    logger.info("Bill confirmed and saved: cache_id=%s user_id=%s", cache_id, ctx.user_id)


# ---------------------------------------------------------------------------
# bill_edit  —  callback_data 格式：bill_edit:{field}:{cache_id}
# ---------------------------------------------------------------------------

@register(lambda d: d.startswith("bill_edit:"))
async def _cb_bill_edit(ctx: CallbackContext) -> None:
    # 格式：bill_edit:{field}:{cache_id}
    parts = ctx.data.split(":", 2)
    if len(parts) != 3:
        await ctx.answer_alert("❌ 数据格式错误。")
        return

    _, field, cache_id = parts

    if field not in _FIELD_CONFIG:
        await ctx.answer_alert(f"❌ 不支持编辑字段：{field}")
        return

    entry = await bill_cache.get(cache_id)
    if entry is None:
        await ctx.answer_alert("⏰ 账单已过期，请重新发送。")
        return
    if entry.user_id != ctx.user_id:
        await ctx.answer_alert("❌ 这不是你的账单。")
        return

    field_label, prompt_hint = _FIELD_CONFIG[field]
    current_val = getattr(entry, field, "")

    await ctx.answer()
    await ctx.request_text_input(
        prompt=f"当前{field_label}：`{current_val}`\n{prompt_hint}：",
        state_key=f"{_STATE_PREFIX}{field}:{cache_id}",
        placeholder=f"请输入新{field_label}",
    )


# ---------------------------------------------------------------------------
# bill_cancel
# ---------------------------------------------------------------------------

@register(lambda d: d.startswith("bill_cancel:"))
async def _cb_bill_cancel(ctx: CallbackContext) -> None:
    cache_id = ctx.data.split(":", 1)[1]

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
    """处理用户回复 ForceReply 的输入，支持多字段写回。"""
    if not update.message:
        return

    state = context.user_data.get("text_input_state", "")
    if not state.startswith(_STATE_PREFIX):
        return

    # state 格式：bill_edit:{field}:{cache_id}
    remainder = state[len(_STATE_PREFIX):]          # "{field}:{cache_id}"
    field, cache_id = remainder.split(":", 1)
    context.user_data.pop("text_input_state", None)

    ctx = TelegramContext.from_message(update, context)
    text = update.message.text.strip()

    # ── 字段校验 ──────────────────────────────────────────────────────────
    new_val: object

    if field == "amount":
        try:
            new_val = float(text.replace(",", "."))
            if new_val <= 0:
                raise ValueError("金额必须大于 0")
        except ValueError as e:
            await ctx.send(f"❌ 金额格式不正确：{e}，请输入正数（如：128.5）。")
            return

    elif field == "bill_date":
        if not re.match(r'^\d{4}-\d{2}-\d{2}$', text):
            await ctx.send("❌ 日期格式不正确，请使用 YYYY-MM-DD 格式（如：2024-03-18）。")
            return
        new_val = text

    elif field == "description":
        new_val = text[:50]

    else:
        # category / merchant — 直接使用，截断即可
        new_val = text[:50]

    # ── 写回缓存 ──────────────────────────────────────────────────────────
    entry = await bill_cache.get(cache_id)
    if entry is None:
        await ctx.send("⏰ 账单已过期，请重新发送。")
        return
    if entry.user_id != ctx.user_id:
        await ctx.send("❌ 这不是你的账单。")
        return

    updated = BillEntry(
        user_id=entry.user_id,
        amount=new_val if field == "amount" else entry.amount,
        currency=entry.currency,
        category=new_val if field == "category" else entry.category,
        description=new_val if field == "description" else entry.description,
        merchant=new_val if field == "merchant" else entry.merchant,
        bill_date=new_val if field == "bill_date" else entry.bill_date,
        raw_text=entry.raw_text,
    )
    await bill_cache.set_with_id(cache_id, updated)

    from modules.billing.handlers.bill_handler import _confirmation_keyboard, _build_confirmation_text
    await ctx.send_keyboard(_build_confirmation_text(updated), _confirmation_keyboard(cache_id))
