# api/routes/transactions.py
"""
GET    /v1/transactions              流水列表
POST   /v1/transactions              创建流水
GET    /v1/transactions/{id}         单条流水详情
PATCH  /v1/transactions/{id}         修改流水
DELETE /v1/transactions/{id}         删除流水
POST   /v1/transactions/ocr          OCR 识别（不入库）
GET    /v1/transactions/summary      月度统计

GET    /v1/categories                分类列表
POST   /v1/categories                创建自定义分类
PATCH  /v1/categories/{id}           修改分类
DELETE /v1/categories/{id}           删除自定义分类
"""
from __future__ import annotations

import logging
import time
from typing import Annotated, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, status

from api.auth import require_active_user
from api.schemas_v2 import (
    TransactionOut, TransactionCreate, TransactionPatch,
    TransactionListResponse, TransactionItemOut,
    MonthlyStatOut, CategoryStatOut,
    CategoryOut, CategoryCreate, CategoryPatch,
    ReceiptOut,
)
from database.db import get_db
from utils.currency import int_to_amount, amount_to_int

logger = logging.getLogger(__name__)
router = APIRouter(tags=["transactions"])


# ─────────────────────────────────────────────────────────────────────────────
# 内部工具
# ─────────────────────────────────────────────────────────────────────────────

def _deserialize_txn(row: dict, items: list[dict], receipt_url: str = "") -> TransactionOut:
    currency = row.get("currency_code", "JPY")
    return TransactionOut(
        id=row["id"],
        type=row["type"],
        amount=int_to_amount(row["amount"], currency),
        currency_code=currency,
        base_amount=int_to_amount(row["base_amount"], currency),
        exchange_rate=row["exchange_rate"] / 1_000_000,
        account_id=row["account_id"],
        to_account_id=row.get("to_account_id"),
        transfer_peer_id=row.get("transfer_peer_id"),
        category_id=row["category_id"],
        user_id=row["user_id"],
        group_id=row["group_id"],
        is_private=bool(row["is_private"]),
        note=row.get("note"),
        transaction_date=float(row["transaction_date"]),
        receipt_url=receipt_url,
        items=[
            TransactionItemOut(
                id=i.get("id"),
                name=i["name"],
                name_raw=i.get("name_raw", ""),
                quantity=i.get("quantity", 1.0),
                unit_price=int_to_amount(i["unit_price"], currency)
                    if i.get("unit_price") is not None else None,
                amount=int_to_amount(i["amount"], currency),
                item_type=i.get("item_type", "item"),
                sort_order=i.get("sort_order", 0),
            )
            for i in items
        ],
        created_at=float(row["created_at"]),
        updated_at=float(row["updated_at"]),
        is_deleted=bool(row.get("is_deleted", False)),
    )


async def _get_txn_or_404(db, txn_id: int, user_id: int) -> dict:
    async with db.execute(
        """
        SELECT t.* FROM transactions t
        JOIN users u ON u.id = t.user_id
        WHERE t.id = ? AND t.is_deleted = 0
        """,
        (txn_id,),
    ) as cur:
        row = await cur.fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="流水记录不存在")
    return dict(row)


async def _get_items(db, txn_id: int) -> list[dict]:
    async with db.execute(
        "SELECT * FROM transaction_items WHERE transaction_id = ? ORDER BY sort_order",
        (txn_id,),
    ) as cur:
        return [dict(r) for r in await cur.fetchall()]


async def _get_first_receipt_url(db, txn_id: int) -> str:
    async with db.execute(
        "SELECT image_url FROM receipts WHERE transaction_id = ? AND is_deleted = 0 LIMIT 1",
        (txn_id,),
    ) as cur:
        row = await cur.fetchone()
    return row[0] if row else ""


async def _require_group_member(db, group_id: int, user_id: int) -> None:
    async with db.execute(
        "SELECT id FROM users WHERE id = ? AND group_id = ?",
        (user_id, group_id),
    ) as cur:
        if not await cur.fetchone():
            raise HTTPException(status_code=403, detail="您不属于该账本")


# ─────────────────────────────────────────────────────────────────────────────
# Transaction 路由
# ─────────────────────────────────────────────────────────────────────────────

@router.get("/transactions", response_model=TransactionListResponse)
async def list_transactions(
    user_id:   Annotated[int, Depends(require_active_user)],
    group_id:  int,
    page:      int           = Query(1, ge=1),
    page_size: int           = Query(20, ge=1, le=100),
    year:      Optional[int] = Query(None),
    month:     Optional[int] = Query(None, ge=1, le=12),
    type:      Optional[str] = Query(None, description="income/expense/transfer"),
    account_id: Optional[int] = Query(None),
    keyword:   Optional[str] = Query(None),
    updated_after: Optional[float] = Query(None, description="增量同步用，unix timestamp"),
):
    """
    流水列表，支持：
    - 月度筛选
    - 类型筛选
    - 账户筛选
    - 关键词搜索（备注/分类名）
    - updated_after（增量同步）
    """
    async with get_db() as db:
        await _require_group_member(db, group_id, user_id)

        conds  = ["t.group_id = ?", "t.is_deleted = 0"]
        params: list = [group_id]

        if year and month:
            start = int(time.mktime(time.strptime(f"{year:04d}-{month:02d}-01", "%Y-%m-%d")))
            if month == 12:
                end = int(time.mktime(time.strptime(f"{year+1:04d}-01-01", "%Y-%m-%d")))
            else:
                end = int(time.mktime(time.strptime(f"{year:04d}-{month+1:02d}-01", "%Y-%m-%d")))
            conds.append("t.transaction_date >= ? AND t.transaction_date < ?")
            params.extend([start, end])

        if type:
            conds.append("t.type = ?"); params.append(type)
        if account_id:
            conds.append("t.account_id = ?"); params.append(account_id)
        if keyword:
            kw = f"%{keyword}%"
            conds.append("(t.note LIKE ? OR c.name LIKE ?)")
            params.extend([kw, kw])
        if updated_after:
            conds.append("t.updated_at > ?"); params.append(int(updated_after))

        # is_private：非群主只能看自己的私密流水
        async with db.execute(
            "SELECT owner_id FROM groups WHERE id = ?", (group_id,)
        ) as cur:
            grp = await cur.fetchone()
        is_owner = grp and grp[0] == user_id
        if not is_owner:
            conds.append("(t.is_private = 0 OR t.user_id = ?)")
            params.append(user_id)

        where = " AND ".join(conds)
        join  = "LEFT JOIN categories c ON c.id = t.category_id"

        async with db.execute(
            f"SELECT COUNT(*) FROM transactions t {join} WHERE {where}", params
        ) as cur:
            total = (await cur.fetchone())[0]

        offset = (page - 1) * page_size
        async with db.execute(
            f"""
            SELECT t.* FROM transactions t {join}
            WHERE {where}
            ORDER BY t.transaction_date DESC, t.id DESC
            LIMIT ? OFFSET ?
            """,
            params + [page_size, offset],
        ) as cur:
            rows = [dict(r) for r in await cur.fetchall()]

        result = []
        for row in rows:
            items       = await _get_items(db, row["id"])
            receipt_url = await _get_first_receipt_url(db, row["id"])
            result.append(_deserialize_txn(row, items, receipt_url))

    return TransactionListResponse(
        transactions=result,
        total=total,
        page=page,
        page_size=page_size,
        has_next=(page * page_size) < total,
    )


@router.get("/transactions/summary", response_model=MonthlyStatOut)
async def monthly_summary(
    user_id:  Annotated[int, Depends(require_active_user)],
    group_id: int,
    year:     Optional[int] = Query(None),
    month:    Optional[int] = Query(None, ge=1, le=12),
):
    import datetime
    now   = datetime.date.today()
    year  = year  or now.year
    month = month or now.month

    start = int(time.mktime(time.strptime(f"{year:04d}-{month:02d}-01", "%Y-%m-%d")))
    end   = int(time.mktime(time.strptime(
        f"{year+1 if month==12 else year:04d}-{1 if month==12 else month+1:02d}-01",
        "%Y-%m-%d",
    )))

    async with get_db() as db:
        await _require_group_member(db, group_id, user_id)

        base = (group_id, 0, start, end)

        async with db.execute(
            """
            SELECT COALESCE(SUM(base_amount),0), COUNT(*)
            FROM transactions
            WHERE group_id=? AND is_deleted=? AND transaction_date>=? AND transaction_date<?
              AND type='expense'
            """, base
        ) as cur:
            row = await cur.fetchone()
            expense_int, count_exp = row[0], row[1]

        async with db.execute(
            """
            SELECT COALESCE(SUM(base_amount),0), COUNT(*)
            FROM transactions
            WHERE group_id=? AND is_deleted=? AND transaction_date>=? AND transaction_date<?
              AND type='income'
            """, base
        ) as cur:
            row = await cur.fetchone()
            income_int, count_inc = row[0], row[1]

        # 按分类汇总（只统计支出）
        async with db.execute(
            """
            SELECT t.category_id, c.name, c.icon, c.color,
                   SUM(t.base_amount) AS total, COUNT(*) AS cnt
            FROM transactions t
            LEFT JOIN categories c ON c.id = t.category_id
            WHERE t.group_id=? AND t.is_deleted=? AND t.transaction_date>=?
              AND t.transaction_date<? AND t.type='expense'
            GROUP BY t.category_id
            ORDER BY total DESC
            """, base
        ) as cur:
            cat_rows = [dict(r) for r in await cur.fetchall()]

        # 按货币汇总
        async with db.execute(
            """
            SELECT currency_code, SUM(amount) AS total
            FROM transactions
            WHERE group_id=? AND is_deleted=? AND transaction_date>=? AND transaction_date<?
            GROUP BY currency_code ORDER BY total DESC
            """, base
        ) as cur:
            currency_rows = [dict(r) for r in await cur.fetchall()]

    # int → float（用 base_currency 转换，暂时用 JPY）
    base_currency = "JPY"
    total_expense = int_to_amount(expense_int, base_currency)
    total_income  = int_to_amount(income_int,  base_currency)

    by_category = [
        CategoryStatOut(
            category_id=r["category_id"],
            name=r["name"] or "未知",
            icon=r["icon"],
            color=r["color"],
            total=int_to_amount(r["total"], base_currency),
            count=r["cnt"],
            percent=round(r["total"] / expense_int * 100, 1)
                if expense_int else 0.0,
        )
        for r in cat_rows
    ]

    by_currency = [
        {
            "currency": r["currency_code"],
            "total": int_to_amount(r["total"], r["currency_code"]),
        }
        for r in currency_rows
    ]

    return MonthlyStatOut(
        year=year,
        month=month,
        total_expense=total_expense,
        total_income=total_income,
        net=total_income - total_expense,
        count=count_exp + count_inc,
        by_category=by_category,
        by_currency=by_currency,
    )


@router.get("/transactions/{txn_id}", response_model=TransactionOut)
async def get_transaction(
    txn_id:  int,
    user_id: Annotated[int, Depends(require_active_user)],
):
    async with get_db() as db:
        row         = await _get_txn_or_404(db, txn_id, user_id)
        await _require_group_member(db, row["group_id"], user_id)
        items       = await _get_items(db, txn_id)
        receipt_url = await _get_first_receipt_url(db, txn_id)
    return _deserialize_txn(row, items, receipt_url)


@router.post("/transactions", response_model=TransactionOut,
             status_code=status.HTTP_201_CREATED)
async def create_transaction(
    body:    TransactionCreate,
    user_id: Annotated[int, Depends(require_active_user)],
):
    now      = int(time.time())
    currency = body.currency_code

    # exchange_rate float → int (× 1_000_000)
    exchange_rate_int = round(body.exchange_rate * 1_000_000)
    amount_int        = amount_to_int(body.amount, currency)
    # base_amount = amount × exchange_rate（折算本位币）
    base_amount_int   = round(amount_int * body.exchange_rate)

    async with get_db() as db:
        await _require_group_member(db, body.group_id, user_id)

        # 转账必须有目标账户
        if body.type.value == "transfer" and not body.to_account_id:
            raise HTTPException(status_code=400, detail="转账类型必须指定目标账户")

        cursor = await db.execute(
            """
            INSERT INTO transactions (
                type, amount, currency_code, base_amount, exchange_rate,
                account_id, to_account_id, category_id,
                user_id, group_id,
                is_private, note, transaction_date,
                created_at, updated_at, is_deleted
            ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,0)
            """,
            (
                body.type.value, amount_int, currency,
                base_amount_int, exchange_rate_int,
                body.account_id, body.to_account_id, body.category_id,
                user_id, body.group_id,
                int(body.is_private), body.note,
                int(body.transaction_date),
                now, now,
            ),
        )
        txn_id = cursor.lastrowid

        # 明细
        if body.items:
            await db.executemany(
                """
                INSERT INTO transaction_items
                    (transaction_id, name, name_raw, quantity,
                     unit_price, amount, item_type, sort_order)
                VALUES (?,?,?,?,?,?,?,?)
                """,
                [
                    (
                        txn_id,
                        item.name, item.name_raw, item.quantity,
                        amount_to_int(item.unit_price, currency)
                            if item.unit_price is not None else None,
                        amount_to_int(item.amount, currency),
                        item.item_type, item.sort_order,
                    )
                    for item in body.items
                ],
            )

        # 凭证 URL
        if body.receipt_url:
            await db.execute(
                """
                INSERT INTO receipts (transaction_id, image_url, created_at, updated_at, is_deleted)
                VALUES (?, ?, ?, ?, 0)
                """,
                (txn_id, body.receipt_url, now, now),
            )

        # 转账：插入对方那条
        if body.type.value == "transfer" and body.to_account_id:
            cursor2 = await db.execute(
                """
                INSERT INTO transactions (
                    type, amount, currency_code, base_amount, exchange_rate,
                    account_id, category_id,
                    user_id, group_id,
                    is_private, note, transaction_date,
                    created_at, updated_at, is_deleted,
                    transfer_peer_id
                ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,0,?)
                """,
                (
                    "transfer", amount_int, currency,
                    base_amount_int, exchange_rate_int,
                    body.to_account_id, body.category_id,
                    user_id, body.group_id,
                    int(body.is_private), body.note,
                    int(body.transaction_date),
                    now, now,
                    txn_id,
                ),
            )
            peer_id = cursor2.lastrowid
            # 回填 peer_id
            await db.execute(
                "UPDATE transactions SET transfer_peer_id = ? WHERE id = ?",
                (peer_id, txn_id),
            )

        await db.commit()

        row         = dict(await (await db.execute(
            "SELECT * FROM transactions WHERE id = ?", (txn_id,)
        )).fetchone())
        items       = await _get_items(db, txn_id)
        receipt_url = await _get_first_receipt_url(db, txn_id)

    # Meilisearch 索引
    from shared.services.search_service import index_bill
    await index_bill({
        "id":          txn_id,
        "user_id":     user_id,
        "amount":      body.amount,
        "currency":    currency,
        "category_id": body.category_id,
        "note":        body.note,
        "transaction_date": int(body.transaction_date),
        "created_at":  now,
    })

    # WS 推送
    from shared.services.container import services
    if services.ws_manager and services.ws_manager.is_online(user_id):
        txn_out = _deserialize_txn(row, items, receipt_url)
        await services.ws_manager.push_to_user(
            user_id, "new_transaction", txn_out.model_dump()
        )

    return _deserialize_txn(row, items, receipt_url)


@router.patch("/transactions/{txn_id}", response_model=TransactionOut)
async def patch_transaction(
    txn_id:  int,
    body:    TransactionPatch,
    user_id: Annotated[int, Depends(require_active_user)],
):
    now = int(time.time())
    async with get_db() as db:
        row = await _get_txn_or_404(db, txn_id, user_id)
        await _require_group_member(db, row["group_id"], user_id)

        updates = body.model_dump(exclude_none=True)
        if not updates:
            items       = await _get_items(db, txn_id)
            receipt_url = await _get_first_receipt_url(db, txn_id)
            return _deserialize_txn(row, items, receipt_url)

        currency = updates.get("currency_code", row["currency_code"])

        fields, params = [], []
        for k, v in updates.items():
            if k == "amount":
                fields.append("amount = ?")
                params.append(amount_to_int(v, currency))
                # 重算 base_amount
                er = (updates.get("exchange_rate") or
                      row["exchange_rate"] / 1_000_000)
                fields.append("base_amount = ?")
                params.append(round(amount_to_int(v, currency) * er))
            elif k == "exchange_rate":
                fields.append("exchange_rate = ?")
                params.append(round(v * 1_000_000))
            elif k == "receipt_url":
                # receipt_url 单独处理（写入 receipts 表）
                await db.execute(
                    "UPDATE receipts SET is_deleted = 1 WHERE transaction_id = ?",
                    (txn_id,),
                )
                if v:
                    await db.execute(
                        "INSERT INTO receipts (transaction_id, image_url, created_at, updated_at, is_deleted)"
                        " VALUES (?,?,?,?,0)",
                        (txn_id, v, now, now),
                    )
            else:
                fields.append(f"{k} = ?")
                params.append(v)

        fields.append("updated_at = ?"); params.append(now)
        params.append(txn_id)
        await db.execute(
            f"UPDATE transactions SET {', '.join(fields)} WHERE id = ?", params
        )
        await db.commit()

        row         = dict(await (await db.execute(
            "SELECT * FROM transactions WHERE id = ?", (txn_id,)
        )).fetchone())
        items       = await _get_items(db, txn_id)
        receipt_url = await _get_first_receipt_url(db, txn_id)

    # WS 推送
    from shared.services.container import services
    if services.ws_manager and services.ws_manager.is_online(user_id):
        txn_out = _deserialize_txn(row, items, receipt_url)
        await services.ws_manager.push_to_user(
            user_id, "transaction_updated", txn_out.model_dump()
        )

    return _deserialize_txn(row, items, receipt_url)


@router.delete("/transactions/{txn_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_transaction(
    txn_id:  int,
    user_id: Annotated[int, Depends(require_active_user)],
):
    now = int(time.time())
    async with get_db() as db:
        row = await _get_txn_or_404(db, txn_id, user_id)
        await _require_group_member(db, row["group_id"], user_id)

        await db.execute(
            "UPDATE transactions SET is_deleted = 1, updated_at = ? WHERE id = ?",
            (now, txn_id),
        )
        # 同步软删除对方转账记录
        if row.get("transfer_peer_id"):
            await db.execute(
                "UPDATE transactions SET is_deleted = 1, updated_at = ? WHERE id = ?",
                (now, row["transfer_peer_id"]),
            )
        await db.commit()

    # 清理 Meilisearch
    from shared.services.search_service import delete_bill_from_index
    await delete_bill_from_index(txn_id)

    # WS 推送
    from shared.services.container import services
    if services.ws_manager and services.ws_manager.is_online(user_id):
        await services.ws_manager.push_to_user(
            user_id, "transaction_deleted", {"id": txn_id}
        )


# ─────────────────────────────────────────────────────────────────────────────
# Category 路由
# ─────────────────────────────────────────────────────────────────────────────

@router.get("/categories", response_model=list[CategoryOut])
async def list_categories(
    user_id:  Annotated[int, Depends(require_active_user)],
    group_id: Optional[int] = Query(None),
):
    """返回系统预设分类 + 该 group 的自定义分类。"""
    async with get_db() as db:
        if group_id:
            async with db.execute(
                """
                SELECT * FROM categories
                WHERE is_system = 1
                   OR group_id = ?
                ORDER BY sort_order, id
                """,
                (group_id,),
            ) as cur:
                rows = [dict(r) for r in await cur.fetchall()]
        else:
            async with db.execute(
                "SELECT * FROM categories WHERE is_system = 1 ORDER BY sort_order",
            ) as cur:
                rows = [dict(r) for r in await cur.fetchall()]

    return [CategoryOut(**r) for r in rows]


@router.post("/categories", response_model=CategoryOut,
             status_code=status.HTTP_201_CREATED)
async def create_category(
    body:    CategoryCreate,
    user_id: Annotated[int, Depends(require_active_user)],
):
    async with get_db() as db:
        cursor = await db.execute(
            """
            INSERT INTO categories
                (name, icon, color, type, is_system, group_id, sort_order)
            VALUES (?, ?, ?, ?, 0, ?, ?)
            """,
            (body.name, body.icon, body.color, body.type.value,
             body.group_id, body.sort_order),
        )
        cat_id = cursor.lastrowid
        await db.commit()
        async with db.execute(
            "SELECT * FROM categories WHERE id = ?", (cat_id,)
        ) as cur:
            row = dict(await cur.fetchone())
    return CategoryOut(**row)


@router.patch("/categories/{cat_id}", response_model=CategoryOut)
async def patch_category(
    cat_id:  int,
    body:    CategoryPatch,
    user_id: Annotated[int, Depends(require_active_user)],
):
    async with get_db() as db:
        async with db.execute(
            "SELECT * FROM categories WHERE id = ?", (cat_id,)
        ) as cur:
            row = await cur.fetchone()
        if not row:
            raise HTTPException(status_code=404, detail="分类不存在")
        if dict(row)["is_system"]:
            raise HTTPException(status_code=400, detail="系统预设分类不可修改")

        updates = body.model_dump(exclude_none=True)
        if not updates:
            return CategoryOut(**dict(row))

        fields = [f"{k} = ?" for k in updates]
        params = list(updates.values()) + [cat_id]
        await db.execute(
            f"UPDATE categories SET {', '.join(fields)} WHERE id = ?", params
        )
        await db.commit()
        async with db.execute(
            "SELECT * FROM categories WHERE id = ?", (cat_id,)
        ) as cur:
            updated = dict(await cur.fetchone())
    return CategoryOut(**updated)


@router.delete("/categories/{cat_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_category(
    cat_id:  int,
    user_id: Annotated[int, Depends(require_active_user)],
):
    async with get_db() as db:
        async with db.execute(
            "SELECT is_system FROM categories WHERE id = ?", (cat_id,)
        ) as cur:
            row = await cur.fetchone()
        if not row:
            raise HTTPException(status_code=404, detail="分类不存在")
        if row[0]:
            raise HTTPException(status_code=400, detail="系统预设分类不可删除")

        # 检查是否有流水使用该分类
        async with db.execute(
            "SELECT COUNT(*) FROM transactions WHERE category_id = ? AND is_deleted = 0",
            (cat_id,),
        ) as cur:
            count = (await cur.fetchone())[0]
        if count:
            raise HTTPException(
                status_code=400,
                detail=f"该分类有 {count} 条流水，无法删除，请先修改流水分类",
            )

        await db.execute("DELETE FROM categories WHERE id = ?", (cat_id,))
        await db.commit()
