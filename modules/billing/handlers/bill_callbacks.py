# modules/billing/handlers/bill_callbacks.py
"""
修复说明：
1. [Bug1] _cb_bill_cat 和 handle_bill_edit_reply 创建新 BillEntry 时补回
   receipt_file_id 和 items，防止凭证和明细丢失。
2. [Bug2] 新增 bill_items: / bill_item_edit: / bill_item_del: 回调，
   让用户可以查看并逐条编辑/删除明细。
3. 其余逻辑（ForceReply、字段校验、bill_back 等）不变。
"""
from __future__ import annotations

import logging
import re
from dataclasses import replace

from telegram.ext import CallbackContext as PTBCallbackContext

from core.callback_bus import register, CallbackContext
from modules.billing.database.bills import insert_bill
from modules.billing.services.bill_cache import bill_cache, BillEntry, BillItem
from modules.billing.services.bill_parser import VALID_CATEGORIES
from shared.services.platform_context import TelegramContext, btn
from utils.decorators import auto_delete 

logger = logging.getLogger(__name__)

# state_key 写入 user_data 的前缀
_STATE_PREFIX = "bill_edit:"

# 各字段的中文标签和 ForceReply 输入提示（category 不在此列）
_FIELD_CONFIG: dict[str, tuple[str, str]] = {
    "amount":      ("金额",    "请输入新金额（纯数字，如：128.5）"),
    "merchant":    ("商家",    "请输入商家名称"),
    "description": ("描述",    "请输入描述（15字以内）"),
    "bill_date":   ("日期",    "请输入日期（格式：2024-03-18）"),
}

# 明细字段配置
_ITEM_FIELD_CONFIG: dict[str, tuple[str, str]] = {
    "name":   ("名称", "请输入新商品名称"),
    "amount": ("金额", "请输入新金额（数字，折扣为负数如 -50）"),
}

# category 类别选择键盘
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

_CATEGORY_ORDER = ["餐饮", "交通", "购物", "娱乐", "医疗", "住房", "水电煤", "其他"]


def _category_keyboard(cache_id: str):
    rows = []
    for i in range(0, len(_CATEGORY_ORDER), 2):
        row = []
        for cat in _CATEGORY_ORDER[i:i + 2]:
            emoji = _CATEGORY_EMOJI.get(cat, "")
            row.append(btn(f"{emoji} {cat}", f"bill_cat:{cat}:{cache_id}"))
        rows.append(row)
    rows.append([btn("« 返回", f"bill_back:{cache_id}")])
    return rows


def _items_keyboard(entry: BillEntry, cache_id: str):
    """明细列表键盘：每条明细一行，可点击编辑名称或金额，可删除。"""
    rows = []
    for idx, item in enumerate(entry.items):
        label = f"{item.name[:12]}  ¥{item.amount:.0f}"
        rows.append([
            btn(f"✏️ {label}", f"bill_item_edit:name:{idx}:{cache_id}"),
            btn(f"💴 金额",    f"bill_item_edit:amount:{idx}:{cache_id}"),
            btn(f"🗑️",         f"bill_item_del:{idx}:{cache_id}"),
        ])
    rows.append([btn("« 返回确认", f"bill_back:{cache_id}")])
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
# bill_edit  —  callback_data：bill_edit:{field}:{cache_id}
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

    if field == "category":
        await ctx.answer()
        await ctx.platform_ctx.edit_keyboard(
            f"🏷️ 请选择新类别（当前：{entry.category or '未分类'}）：",
            _category_keyboard(cache_id),
        )
        return

    if field == "items":
        await ctx.answer()
        if not entry.items:
            await ctx.platform_ctx.edit_keyboard(
                "📋 当前账单没有商品明细。",
                [[btn("« 返回", f"bill_back:{cache_id}")]],
            )
            return
        await ctx.platform_ctx.edit_keyboard(
            "📋 *商品明细*\n点击行内按钮编辑名称/金额，或删除该条目：",
            _items_keyboard(entry, cache_id),
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
# bill_item_edit  —  callback_data：bill_item_edit:{field}:{idx}:{cache_id}
# [Bug2] 新增：编辑单条明细的名称或金额
# ---------------------------------------------------------------------------

@register(lambda d: d.startswith("bill_item_edit:"))
async def _cb_bill_item_edit(ctx: CallbackContext) -> None:
    # 格式：bill_item_edit:{field}:{idx}:{cache_id}
    parts = ctx.data.split(":", 3)
    if len(parts) != 4:
        await ctx.answer_alert("❌ 数据格式错误。")
        return

    _, field, idx_str, cache_id = parts
    try:
        idx = int(idx_str)
    except ValueError:
        await ctx.answer_alert("❌ 明细索引无效。")
        return

    if field not in _ITEM_FIELD_CONFIG:
        await ctx.answer_alert(f"❌ 不支持编辑字段：{field}")
        return

    entry = await bill_cache.get(cache_id)
    if entry is None:
        await ctx.answer_alert("⏰ 账单已过期，请重新发送。")
        return
    if entry.user_id != ctx.user_id:
        await ctx.answer_alert("❌ 这不是你的账单。")
        return
    if idx >= len(entry.items):
        await ctx.answer_alert("❌ 明细条目不存在。")
        return

    item = entry.items[idx]
    field_label, prompt_hint = _ITEM_FIELD_CONFIG[field]
    current_val = getattr(item, field)

    await ctx.answer()
    await ctx.request_text_input(
        prompt=f"第 {idx + 1} 条：{item.name}\n当前{field_label}：`{current_val}`\n{prompt_hint}：",
        state_key=f"{_STATE_PREFIX}item:{field}:{idx}:{cache_id}",
        placeholder=f"请输入新{field_label}",
    )


# ---------------------------------------------------------------------------
# bill_item_del  —  callback_data：bill_item_del:{idx}:{cache_id}
# [Bug2] 新增：删除单条明细
# ---------------------------------------------------------------------------

@register(lambda d: d.startswith("bill_item_del:"))
async def _cb_bill_item_del(ctx: CallbackContext) -> None:
    parts = ctx.data.split(":", 2)
    if len(parts) != 3:
        await ctx.answer_alert("❌ 数据格式错误。")
        return

    _, idx_str, cache_id = parts
    try:
        idx = int(idx_str)
    except ValueError:
        await ctx.answer_alert("❌ 明细索引无效。")
        return

    entry = await bill_cache.get(cache_id)
    if entry is None:
        await ctx.answer_alert("⏰ 账单已过期，请重新发送。")
        return
    if entry.user_id != ctx.user_id:
        await ctx.answer_alert("❌ 这不是你的账单。")
        return
    if idx >= len(entry.items):
        await ctx.answer_alert("❌ 明细条目不存在。")
        return

    new_items = [item for i, item in enumerate(entry.items) if i != idx]
    updated = replace(entry, items=new_items)
    await bill_cache.update(cache_id, updated)
    await ctx.answer("已删除")

    # 刷新明细列表
    if updated.items:
        await ctx.platform_ctx.edit_keyboard(
            "📋 *商品明细*\n点击行内按钮编辑名称/金额，或删除该条目：",
            _items_keyboard(updated, cache_id),
        )
    else:
        from modules.billing.handlers.bill_handler import _confirmation_keyboard, _build_confirmation_text
        await ctx.platform_ctx.edit_keyboard(
            _build_confirmation_text(updated),
            _confirmation_keyboard(cache_id),
        )


# ---------------------------------------------------------------------------
# bill_cat  —  callback_data：bill_cat:{category}:{cache_id}
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

    # [Bug1] 修复：dataclasses.replace 自动保留 receipt_file_id 和 items
    updated = replace(entry, category=category)
    await bill_cache.update(cache_id, updated)

    await ctx.answer(f"已选择：{category}")

    from modules.billing.handlers.bill_handler import _confirmation_keyboard, _build_confirmation_text
    await ctx.platform_ctx.edit_keyboard(
        _build_confirmation_text(updated),
        _confirmation_keyboard(cache_id),
    )
    logger.debug("Bill category updated: cache_id=%s category=%s", cache_id, category)


# ---------------------------------------------------------------------------
# bill_back  —  从各子菜单返回确认卡
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
@auto_delete(delay=3.0)
async def handle_bill_edit_reply(update, context: PTBCallbackContext) -> None:
    """处理 ForceReply 回复，支持主字段和明细字段。"""
    if not update.message:
        return

    state = context.user_data.get("text_input_state", "")
    if not state.startswith(_STATE_PREFIX):
        return

    remainder = state[len(_STATE_PREFIX):]
    context.user_data.pop("text_input_state", None)

    ctx = TelegramContext.from_message(update, context)
    text = update.message.text.strip()

    # ── 判断是普通字段还是明细字段 ────────────────────────────────────────
    if remainder.startswith("item:"):
        # 格式：item:{field}:{idx}:{cache_id}
        parts = remainder[len("item:"):].split(":", 2)
        if len(parts) != 3:
            await ctx.send("❌ 状态数据异常，请重试。")
            return
        field, idx_str, cache_id = parts
        await _handle_item_reply(ctx, cache_id, field, int(idx_str), text)
    else:
        # 格式：{field}:{cache_id}
        field, cache_id = remainder.split(":", 1)
        await _handle_main_field_reply(ctx, cache_id, field, text)


async def _handle_main_field_reply(
    ctx: TelegramContext,
    cache_id: str,
    field: str,
    text: str,
) -> None:
    """处理主字段（amount / merchant / description / bill_date）的 ForceReply 回复。"""
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
        new_val = text[:50]

    entry = await bill_cache.get(cache_id)
    if entry is None:
        await ctx.send("⏰ 账单已过期，请重新发送。")
        return
    if entry.user_id != ctx.user_id:
        await ctx.send("❌ 这不是你的账单。")
        return

    # [Bug1] 修复：dataclasses.replace 自动保留 receipt_file_id 和 items
    updated = replace(entry, **{field: new_val})
    await bill_cache.update(cache_id, updated)

    from modules.billing.handlers.bill_handler import _confirmation_keyboard, _build_confirmation_text
    await ctx.send_keyboard(
        _build_confirmation_text(updated),
        _confirmation_keyboard(cache_id),
    )


async def _handle_item_reply(
    ctx: TelegramContext,
    cache_id: str,
    field: str,
    idx: int,
    text: str,
) -> None:
    """处理明细字段（name / amount）的 ForceReply 回复。"""
    entry = await bill_cache.get(cache_id)
    if entry is None:
        await ctx.send("⏰ 账单已过期，请重新发送。")
        return
    if entry.user_id != ctx.user_id:
        await ctx.send("❌ 这不是你的账单。")
        return
    if idx >= len(entry.items):
        await ctx.send("❌ 明细条目不存在。")
        return

    old_item = entry.items[idx]

    if field == "name":
        new_item = BillItem(
            name=text[:50],
            name_raw=old_item.name_raw,
            quantity=old_item.quantity,
            unit_price=old_item.unit_price,
            amount=old_item.amount,
            item_type=old_item.item_type,
            sort_order=old_item.sort_order,
        )
    elif field == "amount":
        try:
            new_amount = float(text.replace(",", "."))
        except ValueError:
            await ctx.send("❌ 金额格式不正确，请输入数字（折扣为负数，如 -50）。")
            return
        new_item = BillItem(
            name=old_item.name,
            name_raw=old_item.name_raw,
            quantity=old_item.quantity,
            unit_price=old_item.unit_price,
            amount=new_amount,
            item_type=old_item.item_type,
            sort_order=old_item.sort_order,
        )
    else:
        await ctx.send(f"❌ 不支持编辑字段：{field}")
        return

    new_items = list(entry.items)
    new_items[idx] = new_item
    updated = replace(entry, items=new_items)
    await bill_cache.update(cache_id, updated)

    # 回到明细列表
    await ctx.send_keyboard(
        "📋 *商品明细*\n点击行内按钮编辑名称/金额，或删除该条目：",
        _items_keyboard(updated, cache_id),
    )

