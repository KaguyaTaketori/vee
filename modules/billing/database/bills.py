from __future__ import annotations

import logging
import time
from typing import Optional

from database.db import get_db
from modules.billing.services.bill_cache import BillEntry

logger = logging.getLogger(__name__)


async def init_bills_table() -> None:
    async with get_db() as db:
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
                FOREIGN KEY (user_id) REFERENCES users(user_id)
            )
        """)
        await db.execute("""
            CREATE INDEX IF NOT EXISTS idx_bills_user_id ON bills(user_id)
        """)
        await db.execute("""
            CREATE INDEX IF NOT EXISTS idx_bills_created_at ON bills(created_at)
        """)
        await db.commit()
    logger.info("Bills table initialized.")


async def insert_bill(entry: BillEntry) -> int:
    """
    将账单写入数据库。
    :returns: 新记录的 rowid。
    """
    async with get_db() as db:
        cursor = await db.execute(
            """
            INSERT INTO bills
                (user_id, amount, currency, category, description, merchant, bill_date, raw_text, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
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
                time.time(),
            ),
        )
        await db.commit()
        rowid = cursor.lastrowid
    logger.info("Bill inserted: rowid=%s user_id=%s amount=%s", rowid, entry.user_id, entry.amount)
    return rowid


async def get_user_bills(user_id: int, limit: int = 20, offset: int = 0) -> list[dict]:
    """查询用户的账单记录（分页）。"""
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
