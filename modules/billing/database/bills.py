"""
modules/billing/database/bills.py

变更说明：
1. init_bills_table：新增 receipt_url 列的幂等迁移
2. insert_bill：写入 receipt_url
3. 其余接口不变
"""
from __future__ import annotations

import logging
import time

from database.db import get_db
from modules.billing.services.bill_cache import BillEntry, BillItem

logger = logging.getLogger(__name__)


async def init_bills_table() -> None:
    async with get_db() as db:
        # ── 主表 ──────────────────────────────────────────────────────────
        await db.execute("""
            CREATE TABLE IF NOT EXISTS bills (
                id               INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id          INTEGER NOT NULL,
                amount           REAL    NOT NULL,
                currency         TEXT    NOT NULL DEFAULT 'CNY',
                category         TEXT,
                description      TEXT,
                merchant         TEXT,
                bill_date        TEXT,
                raw_text         TEXT,
                receipt_file_id  TEXT    NOT NULL DEFAULT '',
                receipt_url      TEXT    NOT NULL DEFAULT '',
                created_at       REAL    NOT NULL,
                updated_at       REAL    NOT NULL,
                FOREIGN KEY (user_id) REFERENCES users(user_id)
            )
        """)

        # ── 商品明细表 ────────────────────────────────────────────────────
        await db.execute("""
            CREATE TABLE IF NOT EXISTS bill_items (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                bill_id     INTEGER NOT NULL,
                user_id     INTEGER NOT NULL,
                name        TEXT    NOT NULL,
                name_raw    TEXT    DEFAULT '',
                quantity    REAL    NOT NULL DEFAULT 1,
                unit_price  REAL,
                amount      REAL    NOT NULL,
                item_type   TEXT    NOT NULL DEFAULT 'item',
                sort_order  INTEGER NOT NULL DEFAULT 0,
                FOREIGN KEY (bill_id) REFERENCES bills(id) ON DELETE CASCADE
            )
        """)

        # ── 索引 ──────────────────────────────────────────────────────────
        await db.execute(
            "CREATE INDEX IF NOT EXISTS idx_bills_user_id    ON bills(user_id)"
        )
        await db.execute(
            "CREATE INDEX IF NOT EXISTS idx_bills_created_at ON bills(created_at)"
        )
        await db.execute(
            "CREATE INDEX IF NOT EXISTS idx_bills_bill_date  ON bills(bill_date)"
        )
        await db.execute(
            "CREATE INDEX IF NOT EXISTS idx_bill_items_bill_id ON bill_items(bill_id)"
        )
        await db.execute(
            "CREATE INDEX IF NOT EXISTS idx_bill_items_user_id ON bill_items(user_id)"
        )

        # ── 旧部署 schema 升级（幂等）────────────────────────────────────
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

        if "receipt_file_id" not in columns:
            await db.execute(
                "ALTER TABLE bills ADD COLUMN receipt_file_id TEXT NOT NULL DEFAULT ''"
            )
            logger.info("bills: migrated — added receipt_file_id column")

        if "receipt_url" not in columns:
            await db.execute(
                "ALTER TABLE bills ADD COLUMN receipt_url TEXT NOT NULL DEFAULT ''"
            )
            logger.info("bills: migrated — added receipt_url column")

        await db.commit()

    logger.info("Bills table (+ bill_items) initialized.")


async def insert_bill(entry: BillEntry) -> int:
    now = time.time()
    async with get_db() as db:
        cursor = await db.execute(
            """
            INSERT INTO bills
                (user_id, amount, currency, category, description,
                 merchant, bill_date, raw_text, receipt_file_id, receipt_url,
                 created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
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
                entry.receipt_file_id,
                entry.receipt_url,
                now,
                now,
            ),
        )
        bill_id = cursor.lastrowid

        if entry.items:
            await db.executemany(
                """
                INSERT INTO bill_items
                    (bill_id, user_id, name, name_raw, quantity,
                     unit_price, amount, item_type, sort_order)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                [
                    (
                        bill_id,
                        entry.user_id,
                        item.name,
                        item.name_raw,
                        item.quantity,
                        item.unit_price,
                        item.amount,
                        item.item_type,
                        item.sort_order,
                    )
                    for item in entry.items
                ],
            )

        await db.commit()

    logger.info(
        "Bill inserted: bill_id=%s user_id=%s amount=%s items=%d receipt_url=%s",
        bill_id, entry.user_id, entry.amount, len(entry.items), entry.receipt_url,
    )
    return bill_id


async def get_bill_items(bill_id: int, user_id: int) -> list[dict]:
    async with get_db() as db:
        cursor = await db.execute(
            """
            SELECT bi.*
            FROM bill_items bi
            JOIN bills b ON bi.bill_id = b.id
            WHERE bi.bill_id = ?
              AND b.user_id  = ?
            ORDER BY bi.sort_order ASC
            """,
            (bill_id, user_id),
        )
        rows = await cursor.fetchall()
    return [dict(r) for r in rows]


async def update_bill_field(bill_id: int, user_id: int, field: str, value: object) -> bool:
    _ALLOWED_FIELDS = {
        "amount", "currency", "category", "description",
        "merchant", "bill_date", "receipt_url",
    }
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
    async with get_db() as db:
        cursor = await db.execute(
            """
            SELECT id, amount, currency, category, description,
                   merchant, bill_date, receipt_url
            FROM bills
            WHERE user_id = ?
            ORDER BY bill_date DESC, created_at DESC
            LIMIT ?
            """,
            (user_id, limit),
        )
        rows = await cursor.fetchall()
    return [dict(r) for r in rows]


async def get_recent_bills_with_items(user_id: int, limit: int = 5) -> list[dict]:
    bills = await get_recent_bills(user_id, limit)
    for bill in bills:
        bill["items"] = await get_bill_items(bill["id"], user_id)
    return bills
