# modules/billing/handlers/mybills_handler.py
from __future__ import annotations

import logging
import re
from datetime import date
from typing import Optional

from telegram import Update
from telegram.ext import CallbackContext

from modules.billing.utils import resolve_user_id
from shared.repositories.bill_repo import BillRepository
from shared.services.platform_context import PlatformContext, TelegramContext
from utils.currency import int_to_amount
from utils.utils import require_message

logger = logging.getLogger(__name__)

_bill_repo = BillRepository()

_CATEGORY_EMOJI: dict[str, str] = {
    "餐饮": "🍜", "交通": "🚇", "购物": "🛍️",
    "娱乐": "🎮", "医疗": "💊", "住房": "🏠",
    "水电煤": "💡", "其他": "📦",
}
_MAX_ITEMS_IN_RECENT = 5


def _parse_month_arg(arg: str) -> tuple[int, int] | None:
    m = re.fullmatch(r"(\d{4})-(\d{2})", arg.strip())
    if not m:
        return None
    year, month = int(m.group(1)), int(m.group(2))
    if not (1 <= month <= 12):
        return None
    return year, month


def _format_item_line(item: dict, currency: str) -> str:
    name      = item.get("name", "未知商品")
    amount    = int_to_amount(item.get("amount", 0), currency)
    qty       = item.get("quantity", 1)
    item_type = item.get("item_type", "item")
    if item_type == "discount":
        return f"    ➖ _{name}_　`{amount:+.0f}`"
    elif item_type == "tax":
        return f"    🧾 _{name}_　`{amount:.0f}`"
    else:
        qty_str = f" ×{qty:.0f}" if qty and qty != 1 else ""
        return f"    • {name}{qty_str}　`{amount:.0f}`"


def _format_summary(year: int, month: int, summary: dict) -> str:
    count       = summary["count"]
    total_int   = summary["total"]
    by_category = summary["by_category"]
    by_currency = summary["by_currency"]

    if count == 0:
        return f"📭 *{year} 年 {month} 月*\n\n该月暂无记账记录。"

    lines: list[str] = [f"📊 *{year} 年 {month} 月消费汇总*\n"]

    if len(by_currency) == 1:
        currency = by_currency[0]["currency"]
        total    = int_to_amount(total_int, currency)
        lines.append(f"💴 总支出：`{total:,.2f} {currency}`　共 {count} 笔\n")
    else:
        lines.append(f"💴 总支出（{count} 笔）：")
        for c in by_currency:
            amt = int_to_amount(c["total"], c["currency"])
            lines.append(f"　`{amt:,.2f} {c['currency']}`")
        lines.append("")

    lines.append("━━━━━━━━ 分类明细 ━━━━━━━━")
    for item in by_category:
        cat   = item["category"]
        emoji = _CATEGORY_EMOJI.get(cat, "📦")
        # 汇总金额用主货币展示（取第一个货币）
        main_currency = by_currency[0]["currency"] if by_currency else "JPY"
        cat_amt = int_to_amount(item["total"], main_currency)
        total_f = int_to_amount(total_int, main_currency)
        pct   = (cat_amt / total_f * 100) if total_f else 0
        lines.append(
            f"{emoji} {cat}　`{cat_amt:,.2f}`　{item['count']} 笔　{pct:.0f}%"
        )
    return "\n".join(lines)


def _format_recent(bills: list[dict]) -> str:
    if not bills:
        return ""

    lines = ["\n━━━━━━━━ 最近记录 ━━━━━━━━"]
    for b in bills:
        cat      = b.get("category") or "其他"
        emoji    = _CATEGORY_EMOJI.get(cat, "📦")
        merchant = b.get("merchant") or ""
        if merchant in ("unknown", "未知商家", ""):
            merchant = ""
        merchant_str = f" @ {merchant}" if merchant else ""

        currency  = b.get("currency", "JPY")
        amount    = int_to_amount(b.get("amount", 0), currency)
        bill_date = b.get("bill_date", "")
        source    = b.get("source", "bot")
        source_tag = " `[App]`" if source == "app" else ""

        lines.append(
            f"\n{emoji} `{amount:,.0f} {currency}`{merchant_str}　"
            f"{bill_date}{source_tag}"
        )

        items: list[dict] = b.get("items", [])
        if items:
            for item in items[:_MAX_ITEMS_IN_RECENT]:
                lines.append(_format_item_line(item, currency))
            if len(items) > _MAX_ITEMS_IN_RECENT:
                lines.append(f"    _…另有 {len(items) - _MAX_ITEMS_IN_RECENT} 项_")
        else:
            desc = b.get("description") or ""
            if desc:
                lines.append(f"    {desc}")

    return "\n".join(lines)


async def _mybills_impl(
    ctx: PlatformContext,
    tg_user_id: int,
    month_arg: Optional[str],
) -> None:
    today = date.today()

    if month_arg:
        parsed = _parse_month_arg(month_arg)
        if not parsed:
            await ctx.send_markdown("❌ 月份格式错误，请使用 `YYYY-MM`，如 `2026-03`")
            return
        year, month = parsed
    else:
        year, month = today.year, today.month

    # 合并后直接用 users.id 查，无需区分来源
    user_id = await resolve_user_id(tg_user_id)

    summary = await _bill_repo.monthly_summary(user_id, year, month)
    bills, _ = await _bill_repo.list_by_user(
        user_id, year=year, month=month, page_size=5
    )

    # 拼装 items（list_by_user 已附带）
    text = _format_summary(year, month, summary)
    recent_text = _format_recent(bills)
    if recent_text:
        text += recent_text

    await ctx.send_markdown(text)


@require_message
async def handle_mybills_command(update: Update, context: CallbackContext) -> None:
    user      = update.message.from_user
    ctx       = TelegramContext.from_message(update, context)
    month_arg = context.args[0] if context.args else None
    await _mybills_impl(ctx, tg_user_id=user.id, month_arg=month_arg)
