from __future__ import annotations

import logging
import time

from database.db import get_db
from modules.billing.services.bill_cache import BillEntry

logger = logging.getLogger(__name__)


async def init_bills_table() -> None:
    async with get_db() as db:
        # ── 主表 ──────────────────────────────────────────────────────────
        await db.execute("""
            CREATE TABLE IF NOT EXISTS bills (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id     INTEGER NOT NULL,
                amount      REAL    NOT NULL,
                currency    TEXT    NOT NULL DEFAULT 'CNY',
                category    TEXT,
                description TEXT,
                merchant    TEXT,
                bill_date   TEXT,
                raw_text    TEXT,
                created_at  REAL    NOT NULL,
                updated_at  REAL    NOT NULL,
                FOREIGN KEY (user_id) REFERENCES users(user_id)
            )
        """)

        # ── 索引 ──────────────────────────────────────────────────────────
        await db.execute(
            "CREATE INDEX IF NOT EXISTS idx_bills_user_id    ON bills(user_id)"
        )
        await db.execute(
            "CREATE INDEX IF NOT EXISTS idx_bills_created_at ON bills(created_at)"
        )
        # bill_date 索引：支持按日期范围查询（如"本月账单"）
        await db.execute(
            "CREATE INDEX IF NOT EXISTS idx_bills_bill_date  ON bills(bill_date)"
        )

        # ── 旧部署 schema 升级：补充 updated_at 列（幂等）────────────────
        cursor = await db.execute("PRAGMA table_info(bills)")
        columns = {row[1] for row in await cursor.fetchall()}
        if "updated_at" not in columns:
            await db.execute(
                "ALTER TABLE bills ADD COLUMN updated_at REAL NOT NULL DEFAULT 0"
            )
            await db.execute(
                "UPDATE bills SET updated_at = created_at WHERE updated_at = 0"
            )
            logger.info("bills: migrated — added updated_at column")

        await db.commit()

    logger.info("Bills table initialized.")


async def insert_bill(entry: BillEntry) -> int:
    """
    将账单写入数据库。
    :returns: 新记录的 rowid。
    """
    now = time.time()
    async with get_db() as db:
        cursor = await db.execute(
            """
            INSERT INTO bills
                (user_id, amount, currency, category, description,
                 merchant, bill_date, raw_text, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                entry.user_id,
                entry.amount,
                entry.currency,
                entry.category,
                entry.description,
                entry.merchant,
                entry.bill_date,
                entry.raw_text,
                now,
                now,
            ),
        )
        await db.commit()
        rowid = cursor.lastrowid
    logger.info(
        "Bill inserted: rowid=%s user_id=%s amount=%s", rowid, entry.user_id, entry.amount
    )
    return rowid


async def update_bill_field(bill_id: int, user_id: int, field: str, value: object) -> bool:
    """
    更新账单的单个字段，同时刷新 updated_at。

    :param bill_id:  账单主键。
    :param user_id:  调用方用户 ID（防止越权）。
    :param field:    字段名，仅允许白名单内的字段。
    :param value:    新值。
    :returns:        是否实际更新了行（False 表示 id/user_id 不匹配）。
    :raises ValueError: field 不在白名单时。
    """
    _ALLOWED_FIELDS = {"amount", "currency", "category", "description", "merchant", "bill_date"}
    if field not in _ALLOWED_FIELDS:
        raise ValueError(f"不允许更新字段：{field}")

    async with get_db() as db:
        cursor = await db.execute(
            f"UPDATE bills SET {field} = ?, updated_at = ? WHERE id = ? AND user_id = ?",
            (value, time.time(), bill_id, user_id),
        )
        await db.commit()
        updated = cursor.rowcount > 0

    if updated:
        logger.info("Bill updated: id=%s user_id=%s field=%s", bill_id, user_id, field)
    else:
        logger.warning(
            "Bill update failed (not found or wrong user): id=%s user_id=%s field=%s",
            bill_id, user_id, field,
        )
    return updated


async def get_user_bills(user_id: int, limit: int = 20, offset: int = 0) -> list[dict]:
    """查询用户的账单记录（分页，按创建时间倒序）。"""
    async with get_db() as db:
        cursor = await db.execute(
            """
            SELECT * FROM bills
            WHERE user_id = ?
            ORDER BY created_at DESC
            LIMIT ? OFFSET ?
            """,
            (user_id, limit, offset),
        )
        rows = await cursor.fetchall()
    return [dict(r) for r in rows]


async def get_user_bill_count(user_id: int) -> int:
    async with get_db() as db:
        cursor = await db.execute(
            "SELECT COUNT(*) FROM bills WHERE user_id = ?", (user_id,)
        )
        row = await cursor.fetchone()
    return row[0] if row else 0


async def get_monthly_summary(user_id: int, year: int, month: int) -> dict:
    """
    返回指定月份的消费汇总。

    :returns: {
        "total": float,
        "count": int,
        "by_category": [{"category": str, "total": float, "count": int}, ...],
        "by_currency": [{"currency": str, "total": float}, ...],
    }
    """
    month_str = f"{year:04d}-{month:02d}"

    async with get_db() as db:
        cursor = await db.execute(
            """
            SELECT
                COALESCE(category, '其他') AS category,
                SUM(amount)               AS total,
                COUNT(*)                  AS cnt
            FROM bills
            WHERE user_id = ?
              AND bill_date LIKE ?
            GROUP BY category
            ORDER BY total DESC
            """,
            (user_id, f"{month_str}%"),
        )
        by_category = [
            {"category": row[0], "total": row[1], "count": row[2]}
            for row in await cursor.fetchall()
        ]

        cursor = await db.execute(
            """
            SELECT currency, SUM(amount) AS total
            FROM bills
            WHERE user_id = ?
              AND bill_date LIKE ?
            GROUP BY currency
            ORDER BY total DESC
            """,
            (user_id, f"{month_str}%"),
        )
        by_currency = [
            {"currency": row[0], "total": row[1]}
            for row in await cursor.fetchall()
        ]

        cursor = await db.execute(
            """
            SELECT SUM(amount), COUNT(*)
            FROM bills
            WHERE user_id = ?
              AND bill_date LIKE ?
            """,
            (user_id, f"{month_str}%"),
        )
        row = await cursor.fetchone()
        total = row[0] or 0.0
        count = row[1] or 0

    return {
        "total": total,
        "count": count,
        "by_category": by_category,
        "by_currency": by_currency,
    }


async def get_recent_bills(user_id: int, limit: int = 5) -> list[dict]:
    """返回最近 N 条账单，用于 /mybills 末尾的流水预览。"""
    async with get_db() as db:
        cursor = await db.execute(
            """
            SELECT id, amount, currency, category, description, merchant, bill_date
            FROM bills
            WHERE user_id = ?
            ORDER BY bill_date DESC, created_at DESC
            LIMIT ?
            """,
            (user_id, limit),
        )
        rows = await cursor.fetchall()
    return [dict(r) for r in rows]
