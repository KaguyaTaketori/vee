from __future__ import annotations

import logging
import re
from datetime import date
from typing import Optional

from telegram import Update
from telegram.ext import CallbackContext

from database.db import get_db
from modules.billing.database.bills import (
    get_monthly_summary,
    get_recent_bills_with_items,
)
from shared.services.platform_context import PlatformContext, TelegramContext
from utils.utils import require_message

logger = logging.getLogger(__name__)

_CATEGORY_EMOJI: dict[str, str] = {
    "餐饮":  "🍜", "交通":  "🚇", "购物":  "🛍️",
    "娱乐":  "🎮", "医疗":  "💊", "住房":  "🏠",
    "水电煤": "💡", "其他":  "📦",
}
_MAX_ITEMS_IN_RECENT = 5


# ── 合并查询工具函数 ───────────────────────────────────────────────────────

async def _get_bound_app_user_id(tg_user_id: int) -> Optional[int]:
    """通过 tg_user_id 查找绑定的 app_user_id，未绑定返回 None。"""
    async with get_db() as db:
        async with db.execute(
            "SELECT id FROM app_users WHERE tg_user_id = ? AND is_active = 1",
            (tg_user_id,),
        ) as cur:
            row = await cur.fetchone()
            return row[0] if row else None


async def _get_merged_monthly_summary(
    tg_user_id: int,
    app_user_id: Optional[int],
    year: int,
    month: int,
) -> dict:
    """
    合并 bills（Bot）和 app_bills（App）的月度汇总。
    未绑定只查 bills。
    """
    month_str = f"{year:04d}-{month:02d}%"

    async with get_db() as db:
        if app_user_id:
            union = """
                SELECT amount, currency, category FROM bills
                WHERE user_id = ? AND bill_date LIKE ?
                UNION ALL
                SELECT amount, currency, category FROM app_bills
                WHERE app_user_id = ? AND bill_date LIKE ?
            """
            params = [tg_user_id, month_str, app_user_id, month_str]
        else:
            union  = "SELECT amount, currency, category FROM bills WHERE user_id = ? AND bill_date LIKE ?"
            params = [tg_user_id, month_str]

        async with db.execute(
            f"""
            SELECT COALESCE(category,'其他'), SUM(amount), COUNT(*)
            FROM ({union}) GROUP BY category ORDER BY SUM(amount) DESC
            """,
            params,
        ) as cur:
            by_category = [
                {"category": r[0], "total": r[1], "count": r[2]}
                for r in await cur.fetchall()
            ]

        async with db.execute(
            f"SELECT currency, SUM(amount) FROM ({union}) GROUP BY currency",
            params,
        ) as cur:
            by_currency = [
                {"currency": r[0], "total": r[1]}
                for r in await cur.fetchall()
            ]

        async with db.execute(
            f"SELECT SUM(amount), COUNT(*) FROM ({union})", params
        ) as cur:
            row   = await cur.fetchone()
            total = row[0] or 0.0
            count = row[1] or 0

    return {
        "total": total,
        "count": count,
        "by_category": by_category,
        "by_currency": by_currency,
    }


async def _get_merged_recent_bills(
    tg_user_id: int,
    app_user_id: Optional[int],
    limit: int = 5,
) -> list[dict]:
    """
    合并两张表的最近账单，按日期降序取 limit 条。
    """
    async with get_db() as db:
        if app_user_id:
            union = """
                SELECT id, amount, currency, category, description,
                       merchant, bill_date, receipt_url, created_at,
                       'bot' AS source
                FROM bills WHERE user_id = ?

                UNION ALL

                SELECT id, amount, currency, category, description,
                       merchant, bill_date, receipt_url, created_at,
                       'app' AS source
                FROM app_bills WHERE app_user_id = ?
            """
            params = [tg_user_id, app_user_id]
        else:
            union = """
                SELECT id, amount, currency, category, description,
                       merchant, bill_date, receipt_url, created_at,
                       'bot' AS source
                FROM bills WHERE user_id = ?
            """
            params = [tg_user_id]

        async with db.execute(
            f"""
            SELECT * FROM ({union})
            ORDER BY bill_date DESC, created_at DESC
            LIMIT ?
            """,
            params + [limit],
        ) as cur:
            rows = [dict(r) for r in await cur.fetchall()]

    # 拼装 items
    for row in rows:
        if row["source"] == "bot":
            async with get_db() as db:
                async with db.execute(
                    """
                    SELECT * FROM bill_items
                    WHERE bill_id = ? ORDER BY sort_order ASC
                    """,
                    (row["id"],),
                ) as cur:
                    row["items"] = [dict(r) for r in await cur.fetchall()]
        else:
            async with get_db() as db:
                async with db.execute(
                    """
                    SELECT * FROM app_bill_items
                    WHERE bill_id = ? ORDER BY sort_order ASC
                    """,
                    (row["id"],),
                ) as cur:
                    row["items"] = [dict(r) for r in await cur.fetchall()]

    return rows


# ── 格式化函数（不变）────────────────────────────────────────────────────────

def _parse_month_arg(arg: str) -> tuple[int, int] | None:
    m = re.fullmatch(r"(\d{4})-(\d{2})", arg.strip())
    if not m:
        return None
    year, month = int(m.group(1)), int(m.group(2))
    if not (1 <= month <= 12):
        return None
    return year, month


def _format_item_line(item: dict) -> str:
    name      = item.get("name", "未知商品")
    amount    = item.get("amount", 0)
    qty       = item.get("quantity", 1)
    item_type = item.get("item_type", "item")
    if item_type == "discount":
        return f"    ➖ _{name}_　`{amount:+.0f}`"
    elif item_type == "tax":
        return f"    🧾 _{name}_　`{amount:.0f}`"
    else:
        qty_str = f" ×{qty:.0f}" if qty and qty != 1 else ""
        return f"    • {name}{qty_str}　`{amount:.0f}`"


def _format_summary(
    year: int,
    month: int,
    summary: dict,
    is_merged: bool = False,
) -> str:
    count       = summary["count"]
    total       = summary["total"]
    by_category = summary["by_category"]
    by_currency = summary["by_currency"]

    if count == 0:
        return f"📭 *{year} 年 {month} 月*\n\n该月暂无记账记录。"

    # 绑定后加一个小标识
    source_hint = "（Bot + App 合并）" if is_merged else ""
    lines: list[str] = [f"📊 *{year} 年 {month} 月消费汇总*{source_hint}\n"]

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
        cat   = item["category"]
        emoji = _CATEGORY_EMOJI.get(cat, "📦")
        pct   = (item["total"] / total * 100) if total else 0
        lines.append(
            f"{emoji} {cat}　`{item['total']:,.2f}`　{item['count']} 笔　{pct:.0f}%"
        )
    return "\n".join(lines)


def _format_recent(bills: list[dict], is_merged: bool = False) -> str:
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
        amount   = b.get("amount", 0)
        currency = b.get("currency", "")
        bill_date = b.get("bill_date", "")

        # 合并模式下显示来源标识
        source_tag = ""
        if is_merged:
            source_tag = " `[App]`" if b.get("source") == "app" else " `[Bot]`"

        lines.append(
            f"\n{emoji} `{amount:,.0f} {currency}`{merchant_str}　{bill_date}{source_tag}"
        )

        items: list[dict] = b.get("items", [])
        if items:
            for item in items[:_MAX_ITEMS_IN_RECENT]:
                lines.append(_format_item_line(item))
            if len(items) > _MAX_ITEMS_IN_RECENT:
                lines.append(f"    _…另有 {len(items) - _MAX_ITEMS_IN_RECENT} 项_")
        else:
            desc = b.get("description") or ""
            if desc:
                lines.append(f"    {desc}")

    return "\n".join(lines)


# ── 核心实现 ──────────────────────────────────────────────────────────────

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

    # 查是否绑定了 App 账号
    app_user_id = await _get_bound_app_user_id(tg_user_id)
    is_merged   = app_user_id is not None

    summary = await _get_merged_monthly_summary(tg_user_id, app_user_id, year, month)
    recent  = await _get_merged_recent_bills(tg_user_id, app_user_id, limit=5)

    text = _format_summary(year, month, summary, is_merged=is_merged)
    recent_text = _format_recent(recent, is_merged=is_merged)
    if recent_text:
        text += recent_text

    # 未绑定时在底部加提示
    if not is_merged:
        text += (
            "\n\n─────────────────────\n"
            "💡 在 App 绑定 Telegram 后，Bot 和 App 的账单将合并显示。\n"
            "发送 /bind <验证码> 完成绑定。"
        )

    await ctx.send_markdown(text)


# ── PTB 入口 ──────────────────────────────────────────────────────────────

@require_message
async def handle_mybills_command(update: Update, context: CallbackContext) -> None:
    user      = update.message.from_user
    ctx       = TelegramContext.from_message(update, context)
    month_arg = context.args[0] if context.args else None
    await _mybills_impl(ctx, tg_user_id=user.id, month_arg=month_arg)
