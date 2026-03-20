# shared/repositories/bill_repo.py
"""
统一 BillRepository：合并原 bills + app_bills 的全部操作。
金额以 INTEGER 存储，通过 currency 字段判断精度。
"""
from __future__ import annotations

import logging
import time
from typing import Optional

from shared.repositories.base import BaseRepository
from utils.currency import amount_to_int, int_to_amount

logger = logging.getLogger(__name__)

_MAX_ENTRIES_PER_USER = 500
_DEFAULT_PAGE_SIZE = 20


class BillRepository(BaseRepository):

    # ── 写入 ─────────────────────────────────────────────────────────────

    async def create(
        self,
        *,
        user_id: int,
        amount: float,         # Python float，内部转 int
        currency: str,
        category: Optional[str] = None,
        description: Optional[str] = None,
        merchant: Optional[str] = None,
        bill_date: Optional[str] = None,
        raw_text: str = "",
        source: str = "bot",   # 'bot' | 'app' | 'web'
        receipt_file_id: str = "",
        receipt_url: str = "",
        items: list[dict] = (),
    ) -> int:
        now = int(time.time())
        amount_int = amount_to_int(amount, currency)

        async with self._db() as db:
            cursor = await db.execute(
                """
                INSERT INTO bills (
                    user_id, amount, currency, category, description,
                    merchant, bill_date, raw_text, source,
                    receipt_file_id, receipt_url, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    user_id, amount_int, currency, category, description,
                    merchant, bill_date, raw_text, source,
                    receipt_file_id, receipt_url, now, now,
                ),
            )
            bill_id = cursor.lastrowid

            if items:
                await db.executemany(
                    """
                    INSERT INTO bill_items
                        (bill_id, user_id, name, name_raw, quantity,
                         unit_price, amount, item_type, sort_order)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    [
                        (
                            bill_id, user_id,
                            item["name"], item.get("name_raw", ""),
                            item.get("quantity", 1),
                            amount_to_int(item["unit_price"], currency)
                                if item.get("unit_price") is not None else None,
                            amount_to_int(item["amount"], currency),
                            item.get("item_type", "item"),
                            item.get("sort_order", i),
                        )
                        for i, item in enumerate(items)
                    ],
                )
            await db.commit()

        return bill_id

    async def update_fields(
        self, bill_id: int, user_id: int, fields: dict
    ) -> bool:
        _ALLOWED = {
            "amount", "currency", "category", "description",
            "merchant", "bill_date", "receipt_url", "source",
        }
        updates = {k: v for k, v in fields.items() if k in _ALLOWED}
        if not updates:
            return False

        # amount 要转 int，但需要先拿到 currency
        if "amount" in updates:
            currency = updates.get("currency")
            if currency is None:
                async with self._db() as db:
                    async with db.execute(
                        "SELECT currency FROM bills WHERE id = ? AND user_id = ?",
                        (bill_id, user_id),
                    ) as cur:
                        row = await cur.fetchone()
                        currency = row[0] if row else "JPY"
            updates["amount"] = amount_to_int(updates["amount"], currency)

        set_clause = ", ".join(f"{k} = ?" for k in updates)
        values = list(updates.values()) + [int(time.time()), bill_id, user_id]
        async with self._db() as db:
            cursor = await db.execute(
                f"UPDATE bills SET {set_clause}, updated_at = ? "
                f"WHERE id = ? AND user_id = ?",
                values,
            )
            await db.commit()
            return cursor.rowcount > 0

    async def delete(self, bill_id: int, user_id: int) -> Optional[str]:
        """删除账单，返回 receipt_url（供调用方清理文件）"""
        async with self._db() as db:
            async with db.execute(
                "SELECT receipt_url FROM bills WHERE id = ? AND user_id = ?",
                (bill_id, user_id),
            ) as cur:
                row = await cur.fetchone()
            if row is None:
                return None
            receipt_url = row[0] or ""
            await db.execute(
                "DELETE FROM bills WHERE id = ? AND user_id = ?",
                (bill_id, user_id),
            )
            await db.commit()
        return receipt_url

    # ── 查询 ─────────────────────────────────────────────────────────────

    async def get_by_id(
        self, bill_id: int, user_id: int
    ) -> Optional[dict]:
        async with self._db() as db:
            async with db.execute(
                "SELECT * FROM bills WHERE id = ? AND user_id = ?",
                (bill_id, user_id),
            ) as cur:
                row = await cur.fetchone()
                if not row:
                    return None
                result = dict(row)

            async with db.execute(
                "SELECT * FROM bill_items WHERE bill_id = ? ORDER BY sort_order",
                (bill_id,),
            ) as cur:
                result["items"] = [dict(r) for r in await cur.fetchall()]

        return self._deserialize(result)

    async def list_by_user(
        self,
        user_id: int,
        *,
        page: int = 1,
        page_size: int = _DEFAULT_PAGE_SIZE,
        year: Optional[int] = None,
        month: Optional[int] = None,
        keyword: Optional[str] = None,
        source: Optional[str] = None,  # None=全部
    ) -> tuple[list[dict], int]:
        conds = ["user_id = ?"]
        params: list = [user_id]

        if year and month:
            conds.append("bill_date LIKE ?")
            params.append(f"{year:04d}-{month:02d}%")
        elif year:
            conds.append("bill_date LIKE ?")
            params.append(f"{year:04d}%")

        if keyword:
            kw = f"%{keyword}%"
            conds.append(
                "(merchant LIKE ? OR description LIKE ? OR category LIKE ?)"
            )
            params.extend([kw, kw, kw])

        if source:
            conds.append("source = ?")
            params.append(source)

        where = " AND ".join(conds)
        offset = (page - 1) * page_size

        async with self._db() as db:
            async with db.execute(
                f"SELECT COUNT(*) FROM bills WHERE {where}", params
            ) as cur:
                total = (await cur.fetchone())[0]

            async with db.execute(
                f"""
                SELECT * FROM bills WHERE {where}
                ORDER BY bill_date DESC, created_at DESC
                LIMIT ? OFFSET ?
                """,
                params + [page_size, offset],
            ) as cur:
                rows = [dict(r) for r in await cur.fetchall()]

        # 拼装 items
        for row in rows:
            async with self._db() as db:
                async with db.execute(
                    "SELECT * FROM bill_items WHERE bill_id = ? "
                    "ORDER BY sort_order",
                    (row["id"],),
                ) as cur:
                    row["items"] = [dict(r) for r in await cur.fetchall()]
            self._deserialize(row)

        return rows, total

    async def monthly_summary(
        self, user_id: int, year: int, month: int
    ) -> dict:
        month_str = f"{year:04d}-{month:02d}%"
        async with self._db() as db:
            async with db.execute(
                """
                SELECT COALESCE(category,'其他'), SUM(amount), COUNT(*)
                FROM bills WHERE user_id = ? AND bill_date LIKE ?
                GROUP BY category ORDER BY SUM(amount) DESC
                """,
                (user_id, month_str),
            ) as cur:
                by_category = [
                    {"category": r[0], "total": r[1], "count": r[2]}
                    for r in await cur.fetchall()
                ]
            async with db.execute(
                """
                SELECT currency, SUM(amount)
                FROM bills WHERE user_id = ? AND bill_date LIKE ?
                GROUP BY currency ORDER BY SUM(amount) DESC
                """,
                (user_id, month_str),
            ) as cur:
                by_currency_raw = [
                    {"currency": r[0], "total_int": r[1]}
                    for r in await cur.fetchall()
                ]
            async with db.execute(
                "SELECT SUM(amount), COUNT(*) FROM bills "
                "WHERE user_id = ? AND bill_date LIKE ?",
                (user_id, month_str),
            ) as cur:
                row = await cur.fetchone()
                total_int = row[0] or 0
                count = row[1] or 0

        # int → float（按各货币换算，summary 按主货币展示）
        # 这里简化：返回 int，由上层按需转换
        by_category_out = [
            {
                "category": c["category"],
                "total": c["total"],   # 仍为 INTEGER，上层转换
                "count": c["count"],
            }
            for c in by_category
        ]
        by_currency_out = [
            {
                "currency": c["currency"],
                "total": c["total_int"],
            }
            for c in by_currency_raw
        ]

        return {
            "total": total_int,
            "count": count,
            "by_category": by_category_out,
            "by_currency": by_currency_out,
        }

    async def get_items(self, bill_id: int) -> list[dict]:
        async with self._db() as db:
            async with db.execute(
                "SELECT * FROM bill_items WHERE bill_id = ? ORDER BY sort_order",
                (bill_id,),
            ) as cur:
                return [dict(r) for r in await cur.fetchall()]

    # ── 反序列化（int → float）───────────────────────────────────────────

    @staticmethod
    def _deserialize(row: dict) -> dict:
        """将 bills 行的 amount 从 INTEGER 转回 float"""
        currency = row.get("currency", "JPY")
        if "amount" in row and row["amount"] is not None:
            row["amount"] = int_to_amount(row["amount"], currency)
        for item in row.get("items", []):
            if item.get("amount") is not None:
                item["amount"] = int_to_amount(item["amount"], currency)
            if item.get("unit_price") is not None:
                item["unit_price"] = int_to_amount(item["unit_price"], currency)
        return row
