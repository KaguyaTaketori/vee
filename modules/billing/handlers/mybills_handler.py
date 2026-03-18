# modules/billing/handlers/mybills_handler.py
"""
/mybills — 查看本月消费汇总 + 最近 5 条流水。

用法：
  /mybills          — 本月汇总
  /mybills 2025-03  — 指定月份汇总
"""
from __future__ import annotations

import logging
import re
from datetime import date

from telegram import Update
from telegram.ext import CallbackContext

from modules.billing.database.bills import get_monthly_summary, get_recent_bills
from shared.services.platform_context import PlatformContext, TelegramContext
from utils.utils import require_message

logger = logging.getLogger(__name__)

# 类别 emoji 映射
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


def _parse_month_arg(arg: str) -> tuple[int, int] | None:
    """解析 'YYYY-MM' 参数，失败返回 None。"""
    m = re.fullmatch(r"(\d{4})-(\d{2})", arg.strip())
    if not m:
        return None
    year, month = int(m.group(1)), int(m.group(2))
    if not (1 <= month <= 12):
        return None
    return year, month


def _format_summary(year: int, month: int, summary: dict) -> str:
    """将汇总数据格式化为 Markdown 消息。"""
    count = summary["count"]
    total = summary["total"]
    by_category = summary["by_category"]
    by_currency = summary["by_currency"]

    if count == 0:
        return f"📭 *{year} 年 {month} 月*\n\n该月暂无记账记录。"

    lines: list[str] = [f"📊 *{year} 年 {month} 月消费汇总*\n"]

    # 多币种时列出各货币合计，单一 CNY 则只显示总额
    if len(by_currency) == 1:
        currency = by_currency[0]["currency"]
        lines.append(f"💴 总支出：`{total:,.2f} {currency}`　共 {count} 笔\n")
    else:
        lines.append(f"💴 总支出（{count} 笔）：")
        for c in by_currency:
            lines.append(f"　`{c['total']:,.2f} {c['currency']}`")
        lines.append("")

    # 分类明细
    lines.append("━━━━━━━━ 分类明细 ━━━━━━━━")
    for item in by_category:
        cat = item["category"]
        emoji = _CATEGORY_EMOJI.get(cat, "📦")
        # 占比（相对同币种总额，跨币种时仅供参考）
        pct = (item["total"] / total * 100) if total else 0
        lines.append(
            f"{emoji} {cat}　`{item['total']:,.2f}`　{item['count']} 笔　{pct:.0f}%"
        )

    return "\n".join(lines)


def _format_recent(bills: list[dict]) -> str:
    """格式化最近流水预览。"""
    if not bills:
        return ""
    lines = ["\n━━━━━━━━ 最近记录 ━━━━━━━━"]
    for b in bills:
        cat = b.get("category") or "其他"
        emoji = _CATEGORY_EMOJI.get(cat, "📦")
        merchant = b.get("merchant") or ""
        merchant = "" if merchant == "unknown" else f" @ {merchant}"
        desc = b.get("description") or ""
        label = desc or cat
        lines.append(
            f"{emoji} `{b['amount']:,.2f} {b['currency']}`　{label}{merchant}　_{b['bill_date']}_"
        )
    return "\n".join(lines)


async def _mybills_impl(ctx: PlatformContext) -> None:
    today = date.today()

    # 解析月份参数
    if ctx.args:
        parsed = _parse_month_arg(ctx.args[0])
        if parsed is None:
            await ctx.send_markdown(
                "❌ 日期格式不正确，请使用 `YYYY-MM`，例如：`/mybills 2025-03`"
            )
            return
        year, month = parsed
    else:
        year, month = today.year, today.month

    summary = await get_monthly_summary(ctx.user_id, year, month)
    recent  = await get_recent_bills(ctx.user_id, limit=5)

    # 仅在查询本月时才展示最近流水
    show_recent = (year == today.year and month == today.month)

    text = _format_summary(year, month, summary)
    if show_recent and summary["count"] > 0:
        text += _format_recent(recent)

    await ctx.send_markdown(text)


@require_message
async def handle_mybills_command(update: Update, context: CallbackContext) -> None:
    ctx = TelegramContext.from_message(update, context)
    await _mybills_impl(ctx)
