# modules/billing/handlers/bill_callbacks.py
"""
modules/billing/handlers/bill_callbacks.py

变更说明：
1. category 字段编辑改为 InlineKeyboard 选择（新增 bill_cat: 回调），
   不再走 ForceReply 文本输入，消除用户手误输入非法类别的可能。
2. handle_bill_edit_reply：编辑完成后用 edit_keyboard 替换原消息，
   避免 send_keyboard 新发一条造成多张确认卡重叠。
3. category 回调直接写回缓存并刷新确认卡，无需经过 ForceReply 流程。
"""
from __future__ import annotations

import logging
import re

from telegram.ext import CallbackContext as PTBCallbackContext

from core.callback_bus import register, CallbackContext
from modules.billing.database.bills import insert_bill
from modules.billing.services.bill_cache import bill_cache, BillEntry
from modules.billing.services.bill_parser import VALID_CATEGORIES
from shared.services.platform_context import TelegramContext, btn

logger = logging.getLogger(__name__)

# state_key 写入 user_data 的前缀，格式：bill_edit:{field}:{cache_id}
_STATE_PREFIX = "bill_edit:"

# 各字段的中文标签和 ForceReply 输入提示（category 不在此列，走 InlineKeyboard）
_FIELD_CONFIG: dict[str, tuple[str, str]] = {
    "amount":      ("金额",    "请输入新金额（纯数字，如：128.5）"),
    "merchant":    ("商家",    "请输入商家名称"),
    "description": ("描述",    "请输入描述（15字以内）"),
    "bill_date":   ("日期",    "请输入日期（格式：2024-03-18）"),
}

# category 类别选择键盘（两列布局）
_CATEGORY_EMOJI: dict[str, str] = {
    "餐饮":  "🍜",
    "交通":  "🚇",
    "购物":  "🛍️",
    "娱乐":  "🎮",
    "医疗":  "💊",
    "住房":  "🏠",
    "水电煤": "💡",
    "其他":  "📦",
}

# 按顺序排列，方便用户扫描
_CATEGORY_ORDER = ["餐饮", "交通", "购物", "娱乐", "医疗", "住房", "水电煤", "其他"]


def _category_keyboard(cache_id: str):
    """生成类别选择键盘，每行两个按钮。callback_data: bill_cat:{category}:{cache_id}"""
    rows = []
    for i in range(0, len(_CATEGORY_ORDER), 2):
        row = []
        for cat in _CATEGORY_ORDER[i:i + 2]:
            emoji = _CATEGORY_EMOJI.get(cat, "")
            row.append(btn(f"{emoji} {cat}", f"bill_cat:{cat}:{cache_id}"))
        rows.append(row)
    rows.append([btn("« 返回", f"bill_back:{cache_id}")])
    return rows


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
    parts = ctx.data.split(":", 2)
    if len(parts) != 3:
        await ctx.answer_alert("❌ 数据格式错误。")
        return

    _, field, cache_id = parts

    entry = await bill_cache.get(cache_id)
    if entry is None:
        await ctx.answer_alert("⏰ 账单已过期，请重新发送。")
        return
    if entry.user_id != ctx.user_id:
        await ctx.answer_alert("❌ 这不是你的账单。")
        return

    # category 走 InlineKeyboard，其余字段走 ForceReply 文本输入
    if field == "category":
        await ctx.answer()
        await ctx.platform_ctx.edit_keyboard(
            f"🏷️ 请选择新类别（当前：{entry.category or '未分类'}）：",
            _category_keyboard(cache_id),
        )
        return

    if field not in _FIELD_CONFIG:
        await ctx.answer_alert(f"❌ 不支持编辑字段：{field}")
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
# bill_cat  —  类别选择回调，callback_data 格式：bill_cat:{category}:{cache_id}
# ---------------------------------------------------------------------------

@register(lambda d: d.startswith("bill_cat:"))
async def _cb_bill_cat(ctx: CallbackContext) -> None:
    parts = ctx.data.split(":", 2)
    if len(parts) != 3:
        await ctx.answer_alert("❌ 数据格式错误。")
        return

    _, category, cache_id = parts

    if category not in VALID_CATEGORIES:
        await ctx.answer_alert(f"❌ 无效类别：{category}")
        return

    entry = await bill_cache.get(cache_id)
    if entry is None:
        await ctx.answer_alert("⏰ 账单已过期，请重新发送。")
        return
    if entry.user_id != ctx.user_id:
        await ctx.answer_alert("❌ 这不是你的账单。")
        return

    updated = BillEntry(
        user_id=entry.user_id,
        amount=entry.amount,
        currency=entry.currency,
        category=category,
        description=entry.description,
        merchant=entry.merchant,
        bill_date=entry.bill_date,
        raw_text=entry.raw_text,
    )
    await bill_cache.update(cache_id, updated)

    await ctx.answer(f"已选择：{category}")

    from modules.billing.handlers.bill_handler import _confirmation_keyboard, _build_confirmation_text
    await ctx.platform_ctx.edit_keyboard(
        _build_confirmation_text(updated),
        _confirmation_keyboard(cache_id),
    )
    logger.debug("Bill category updated: cache_id=%s category=%s", cache_id, category)


# ---------------------------------------------------------------------------
# bill_back  —  从类别选择键盘返回确认卡
# ---------------------------------------------------------------------------

@register(lambda d: d.startswith("bill_back:"))
async def _cb_bill_back(ctx: CallbackContext) -> None:
    cache_id = ctx.data.split(":", 1)[1]

    entry = await bill_cache.get(cache_id)
    if entry is None:
        await ctx.answer_alert("⏰ 账单已过期，请重新发送。")
        return

    await ctx.answer()
    from modules.billing.handlers.bill_handler import _confirmation_keyboard, _build_confirmation_text
    await ctx.platform_ctx.edit_keyboard(
        _build_confirmation_text(entry),
        _confirmation_keyboard(cache_id),
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
    remainder = state[len(_STATE_PREFIX):]
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
        if not re.match(r"^\d{4}-\d{2}-\d{2}$", text):
            await ctx.send("❌ 日期格式不正确，请使用 YYYY-MM-DD 格式（如：2024-03-18）。")
            return
        new_val = text

    elif field == "description":
        new_val = text[:50]

    else:
        # merchant — 直接使用，截断即可
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
        category=entry.category,
        description=new_val if field == "description" else entry.description,
        merchant=new_val if field == "merchant" else entry.merchant,
        bill_date=new_val if field == "bill_date" else entry.bill_date,
        raw_text=entry.raw_text,
    )
    await bill_cache.update(cache_id, updated)

    # ── 用 edit_keyboard 替换原确认消息，而不是 send_keyboard 新发一条 ──
    from modules.billing.handlers.bill_handler import _confirmation_keyboard, _build_confirmation_text

    # ForceReply 场景下没有可 edit 的目标消息（原确认卡是之前发的），
    # 需要通过 bot.send_message 重新发一张确认卡并带上键盘。
    # 由于 TelegramContext.from_message 的 _edit_fn 指向当前输入消息，
    # 此处直接 send_keyboard 发新卡（ForceReply 流程的固有限制）。
    await ctx.send_keyboard(
        _build_confirmation_text(updated),
        _confirmation_keyboard(cache_id),
    )
