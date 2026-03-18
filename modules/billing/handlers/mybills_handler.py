"""
modules/billing/handlers/mybills_handler.py

变更说明（items 支持版本）：
1. _format_recent：每条流水支持展示 items 明细（折叠形式）
2. 新增 _format_item_line：单行 item 格式化（商品/折扣/税）
3. get_recent_bills_with_items 替代 get_recent_bills，带明细数据
4. /mybills 结尾流水预览默认展示有明细的账单的商品列表
"""
from __future__ import annotations

import logging
import re
from datetime import date

from telegram import Update
from telegram.ext import CallbackContext

from modules.billing.database.bills import (
    get_monthly_summary,
    get_recent_bills_with_items,
)
from shared.services.platform_context import PlatformContext, TelegramContext
from utils.utils import require_message

logger = logging.getLogger(__name__)

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

_MAX_ITEMS_IN_RECENT = 5  # 流水预览中每笔账单最多展示的明细行数


def _parse_month_arg(arg: str) -> tuple[int, int] | None:
    m = re.fullmatch(r"(\d{4})-(\d{2})", arg.strip())
    if not m:
        return None
    year, month = int(m.group(1)), int(m.group(2))
    if not (1 <= month <= 12):
        return None
    return year, month


def _format_item_line(item: dict) -> str:
    """单行 item 格式化。"""
    name = item.get("name", "未知商品")
    amount = item.get("amount", 0)
    qty = item.get("quantity", 1)
    item_type = item.get("item_type", "item")

    if item_type == "discount":
        return f"    ➖ _{name}_　`{amount:+.0f}`"
    elif item_type == "tax":
        return f"    🧾 _{name}_　`{amount:.0f}`"
    else:
        qty_str = f" ×{qty:.0f}" if qty and qty != 1 else ""
        return f"    • {name}{qty_str}　`{amount:.0f}`"


def _format_summary(year: int, month: int, summary: dict) -> str:
    count = summary["count"]
    total = summary["total"]
    by_category = summary["by_category"]
    by_currency = summary["by_currency"]

    if count == 0:
        return f"📭 *{year} 年 {month} 月*\n\n该月暂无记账记录。"

    lines: list[str] = [f"📊 *{year} 年 {month} 月消费汇总*\n"]

    if len(by_currency) == 1:
        currency = by_currency[0]["currency"]
        lines.append(f"💴 总支出：`{total:,.2f} {currency}`　共 {count} 笔\n")
    else:
        lines.append(f"💴 总支出（{count} 笔）：")
        for c in by_currency:
            lines.append(f"　`{c['total']:,.2f} {c['currency']}`")
        lines.append("")

    lines.append("━━━━━━━━ 分类明细 ━━━━━━━━")
    for item in by_category:
        cat = item["category"]
        emoji = _CATEGORY_EMOJI.get(cat, "📦")
        pct = (item["total"] / total * 100) if total else 0
        lines.append(
            f"{emoji} {cat}　`{item['total']:,.2f}`　{item['count']} 笔　{pct:.0f}%"
        )

    return "\n".join(lines)


def _format_recent(bills: list[dict]) -> str:
    """格式化最近流水预览，带商品明细。"""
    if not bills:
        return ""

    lines = ["\n━━━━━━━━ 最近记录 ━━━━━━━━"]
    for b in bills:
        cat = b.get("category") or "其他"
        emoji = _CATEGORY_EMOJI.get(cat, "📦")
        merchant = b.get("merchant") or ""
        if merchant in ("unknown", "未知商家", ""):
            merchant = ""
        merchant_str = f" @ {merchant}" if merchant else ""
        amount = b.get("amount", 0)
        currency = b.get("currency", "")
        bill_date = b.get("bill_date", "")

        lines.append(
            f"\n{emoji} `{amount:,.0f} {currency}`{merchant_str}　{bill_date}"
        )

        # 展示商品明细（最多 _MAX_ITEMS_IN_RECENT 行）
        items: list[dict] = b.get("items", [])
        if items:
            display = items[:_MAX_ITEMS_IN_RECENT]
            for item in display:
                lines.append(_format_item_line(item))
            if len(items) > _MAX_ITEMS_IN_RECENT:
                lines.append(f"    _…另有 {len(items) - _MAX_ITEMS_IN_RECENT} 项_")
        else:
            desc = b.get("description") or ""
            if desc:
                lines.append(f"    {desc}")

    return "\n".join(lines)


async def _mybills_impl(ctx: PlatformContext, month_arg: str | None) -> None:
    today = date.today()

    if month_arg:
        parsed = _parse_month_arg(month_arg)
        if not parsed:
            await ctx.send_markdown("❌ 月份格式错误，请使用 `YYYY-MM`，如 `2026-03`")
            return
        year, month = parsed
    else:
        year, month = today.year, today.month

    summary = await get_monthly_summary(ctx.user_id, year, month)
    recent = await get_recent_bills_with_items(ctx.user_id, limit=5)

    text = _format_summary(year, month, summary)
    recent_text = _format_recent(recent)
    if recent_text:
        text += recent_text

    await ctx.send_markdown(text)


@require_message
async def handle_mybills_command(update: Update, context: CallbackContext) -> None:
    user = update.message.from_user
    ctx = TelegramContext.from_message(update, context)
    month_arg = context.args[0] if context.args else None
    await _mybills_impl(ctx, month_arg)
