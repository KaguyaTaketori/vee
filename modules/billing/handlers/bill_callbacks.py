# modules/billing/handlers/bill_callbacks.py
"""
修复说明：
1. _cb_bill_confirm：确认时调用 receipt_storage.confirm()，
   将临时图片移入正式目录，写入 entry.receipt_url 后入库。
2. 其余回调逻辑不变。
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
from utils.auto_delete import auto_delete
from modules.billing.utils import resolve_user_id
from shared.repositories.bill_repo import BillRepository

_bill_repo = BillRepository()

logger = logging.getLogger(__name__)

_STATE_PREFIX = "bill_edit:"

_FIELD_CONFIG: dict[str, tuple[str, str]] = {
    "amount":      ("金额",    "请输入新金额（纯数字，如：128.5）"),
    "merchant":    ("商家",    "请输入商家名称"),
    "description": ("描述",    "请输入描述（15字以内）"),
    "bill_date":   ("日期",    "请输入日期（格式：2024-03-18）"),
}

_ITEM_FIELD_CONFIG: dict[str, tuple[str, str]] = {
    "name":   ("名称", "请输入新商品名称"),
    "amount": ("金额", "请输入新金额（数字，折扣为负数如 -50）"),
}

_CATEGORY_EMOJI: dict[str, str] = {
    "餐饮":  "🍜", "交通":  "🚇", "购物":  "🛍️",
    "娱乐":  "🎮", "医疗":  "💊", "住房":  "🏠",
    "水电煤": "💡", "其他":  "📦",
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

    # entry.user_id 此时存的是 tg_user_id（BillParser 传入的是 ctx.user_id）
    # 需要解析成 users.id
    tg_user_id = entry.user_id
    if entry.user_id != ctx.user_id:
        await ctx.answer_alert("❌ 这不是你的账单。")
        return

    user_id = await resolve_user_id(tg_user_id)

    # 确认临时图片
    receipt_url = entry.receipt_url
    if entry.receipt_tmp_path:
        from shared.services.container import services
        try:
            receipt_url = await services.receipt_storage.confirm(
                entry.receipt_tmp_path
            )
        except Exception as e:
            logger.warning(
                "_cb_bill_confirm: image confirm failed cache_id=%s: %s",
                cache_id, e,
            )

    # 统一写入 bills 表，source='bot'
    bill_id = await _bill_repo.create(
        user_id=user_id,
        amount=entry.amount,
        currency=entry.currency,
        category=entry.category,
        description=entry.description,
        merchant=entry.merchant,
        bill_date=entry.bill_date,
        raw_text=entry.raw_text,
        source="bot",
        receipt_file_id=entry.receipt_file_id,
        receipt_url=receipt_url,
        items=[
            {
                "name":       item.name,
                "name_raw":   item.name_raw,
                "quantity":   item.quantity,
                "unit_price": item.unit_price,
                "amount":     item.amount,
                "item_type":  item.item_type,
                "sort_order": item.sort_order,
            }
            for item in entry.items
        ],
    )

    # Meilisearch 索引
    from shared.services.search_service import index_bill
    import time
    await index_bill({
        "id":          bill_id,
        "user_id":     user_id,
        "amount":      entry.amount,
        "currency":    entry.currency,
        "category":    entry.category,
        "description": entry.description,
        "merchant":    entry.merchant,
        "bill_date":   entry.bill_date,
        "receipt_url": receipt_url,
        "created_at":  int(time.time()),
    })

    await bill_cache.delete(cache_id)
    await ctx.answer()
    await ctx.platform_ctx.edit(
        f"✅ 记账成功！\n\n"
        f"💰 {entry.amount:.2f} {entry.currency}  |  {entry.category or '未分类'}\n"
        f"📝 {entry.description or '—'}"
        + (f"\n📎 凭证已保存" if receipt_url else "")
    )
    logger.info(
        "Bill confirmed: bill_id=%s cache_id=%s user_id=%s",
        bill_id, cache_id, user_id,
    )

# ---------------------------------------------------------------------------
# bill_edit
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
# bill_item_edit
# ---------------------------------------------------------------------------

@register(lambda d: d.startswith("bill_item_edit:"))
async def _cb_bill_item_edit(ctx: CallbackContext) -> None:
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
# bill_item_del
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
# bill_cat
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

    updated = replace(entry, category=category)
    await bill_cache.update(cache_id, updated)
    await ctx.answer(f"已选择：{category}")

    from modules.billing.handlers.bill_handler import _confirmation_keyboard, _build_confirmation_text
    await ctx.platform_ctx.edit_keyboard(
        _build_confirmation_text(updated),
        _confirmation_keyboard(cache_id),
    )


# ---------------------------------------------------------------------------
# bill_back
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

    # delete() 内部会自动清理临时文件
    await bill_cache.delete(cache_id)
    await ctx.answer()
    await ctx.platform_ctx.edit("❌ 已取消记账。")


# ---------------------------------------------------------------------------
# ForceReply reply handler
# ---------------------------------------------------------------------------

@auto_delete(delay=3.0)
async def handle_bill_edit_reply(update, context: PTBCallbackContext) -> None:
    if not update.message:
        return

    state = context.user_data.get("text_input_state", "")
    if not state.startswith(_STATE_PREFIX):
        return

    remainder = state[len(_STATE_PREFIX):]
    context.user_data.pop("text_input_state", None)

    ctx = TelegramContext.from_message(update, context)
    text = update.message.text.strip()

    if remainder.startswith("item:"):
        parts = remainder[len("item:"):].split(":", 2)
        if len(parts) != 3:
            await ctx.send("❌ 状态数据异常，请重试。")
            return
        field, idx_str, cache_id = parts
        await _handle_item_reply(ctx, cache_id, field, int(idx_str), text)
    else:
        field, cache_id = remainder.split(":", 1)
        await _handle_main_field_reply(ctx, cache_id, field, text)


async def _handle_main_field_reply(
    ctx: TelegramContext,
    cache_id: str,
    field: str,
    text: str,
) -> None:
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
            name=text[:50], name_raw=old_item.name_raw,
            quantity=old_item.quantity, unit_price=old_item.unit_price,
            amount=old_item.amount, item_type=old_item.item_type,
            sort_order=old_item.sort_order,
        )
    elif field == "amount":
        try:
            new_amount = float(text.replace(",", "."))
        except ValueError:
            await ctx.send("❌ 金额格式不正确，请输入数字（折扣为负数，如 -50）。")
            return
        new_item = BillItem(
            name=old_item.name, name_raw=old_item.name_raw,
            quantity=old_item.quantity, unit_price=old_item.unit_price,
            amount=new_amount, item_type=old_item.item_type,
            sort_order=old_item.sort_order,
        )
    else:
        await ctx.send(f"❌ 不支持编辑字段：{field}")
        return

    new_items = list(entry.items)
    new_items[idx] = new_item
    updated = replace(entry, items=new_items)
    await bill_cache.update(cache_id, updated)

    await ctx.send_keyboard(
        "📋 *商品明细*\n点击行内按钮编辑名称/金额，或删除该条目：",
        _items_keyboard(updated, cache_id),
    )
