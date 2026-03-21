from __future__ import annotations

import logging
from typing import Annotated, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Response, status
from fastapi.responses import RedirectResponse

from api.auth import require_active_user
from api.schemas import BillCreate, BillPatch, BillListResponse, BillOut

logger = logging.getLogger(__name__)

# 标记为 deprecated，Swagger 文档里会显示删除线
router = APIRouter(
    prefix="/bills",
    tags=["bills (deprecated — use /v1/transactions)"],
    deprecated=True,
)


@router.get("", response_model=BillListResponse)
async def list_bills_compat(
    user_id:   Annotated[int, Depends(require_active_user)],
    page:      int           = Query(1, ge=1),
    page_size: int           = Query(20, ge=1, le=100),
    year:      Optional[int] = Query(None),
    month:     Optional[int] = Query(None, ge=1, le=12),
    keyword:   Optional[str] = Query(None),
):
    """
    ⚠️  Deprecated：请迁移到 GET /v1/transactions
    兼容旧版客户端，从 transactions 表读取数据后转换成旧格式返回。
    """
    from database.db import get_db
    from utils.currency import int_to_amount
    from api.schemas import BillOut, BillItemOut

    async with get_db() as db:
        # 从 users 获取 group_id
        async with db.execute(
            "SELECT group_id FROM users WHERE id = ?", (user_id,)
        ) as cur:
            row = await cur.fetchone()
        if not row or not row[0]:
            return BillListResponse(bills=[], total=0, page=1, page_size=page_size, has_next=False)
        group_id = row[0]

        conds  = ["t.group_id = ?", "t.is_deleted = 0", "t.type = 'expense'"]
        params: list = [group_id]

        if year and month:
            import time
            start = int(time.mktime(time.strptime(f"{year:04d}-{month:02d}-01", "%Y-%m-%d")))
            end_m = 1 if month == 12 else month + 1
            end_y = year + 1 if month == 12 else year
            end = int(time.mktime(time.strptime(f"{end_y:04d}-{end_m:02d}-01", "%Y-%m-%d")))
            conds.append("t.transaction_date >= ? AND t.transaction_date < ?")
            params.extend([start, end])

        if keyword:
            kw = f"%{keyword}%"
            conds.append("(t.note LIKE ? OR c.name LIKE ?)")
            params.extend([kw, kw])

        where = " AND ".join(conds)
        join  = "LEFT JOIN categories c ON c.id = t.category_id"

        async with db.execute(
            f"SELECT COUNT(*) FROM transactions t {join} WHERE {where}", params
        ) as cur:
            total = (await cur.fetchone())[0]

        offset = (page - 1) * page_size
        async with db.execute(
            f"""
            SELECT t.*, c.name AS category_name FROM transactions t {join}
            WHERE {where}
            ORDER BY t.transaction_date DESC
            LIMIT ? OFFSET ?
            """,
            params + [page_size, offset],
        ) as cur:
            rows = [dict(r) for r in await cur.fetchall()]

        bills = []
        for row in rows:
            currency = row.get("currency_code", "JPY")

            # 获取第一张凭证
            async with db.execute(
                "SELECT image_url FROM receipts WHERE transaction_id = ? AND is_deleted = 0 LIMIT 1",
                (row["id"],),
            ) as cur2:
                rec = await cur2.fetchone()
            receipt_url = rec[0] if rec else ""

            # 获取明细
            async with db.execute(
                "SELECT * FROM transaction_items WHERE transaction_id = ? ORDER BY sort_order",
                (row["id"],),
            ) as cur2:
                items_raw = [dict(r) for r in await cur2.fetchall()]

            bills.append(BillOut(
                id=row["id"],
                amount=int_to_amount(row["amount"], currency),
                currency=currency,
                category=row.get("category_name"),
                description=row.get("note"),
                merchant=None,          # 新表没有 merchant 字段
                bill_date=None,         # 用 transaction_date 转换
                receipt_url=receipt_url,
                items=[
                    BillItemOut(
                        id=i.get("id"),
                        name=i["name"],
                        name_raw=i.get("name_raw", ""),
                        quantity=i.get("quantity", 1.0),
                        unit_price=int_to_amount(i["unit_price"], currency)
                            if i.get("unit_price") else None,
                        amount=int_to_amount(i["amount"], currency),
                        item_type=i.get("item_type", "item"),
                        sort_order=i.get("sort_order", 0),
                    )
                    for i in items_raw
                ],
                created_at=float(row["created_at"]),
                updated_at=float(row["updated_at"]),
            ))

    return BillListResponse(
        bills=bills,
        total=total,
        page=page,
        page_size=page_size,
        has_next=(page * page_size) < total,
    )


@router.get("/summary")
async def summary_compat(
    user_id: Annotated[int, Depends(require_active_user)],
    year: Optional[int] = Query(None),
    month: Optional[int] = Query(None),
):
    """⚠️  Deprecated：请迁移到 GET /v1/transactions/summary"""
    return RedirectResponse(
        url=f"/v1/transactions/summary?year={year or ''}&month={month or ''}",
        status_code=302,
    )


@router.get("/{bill_id}")
async def get_bill_compat(bill_id: int):
    """⚠️  Deprecated：请迁移到 GET /v1/transactions/{id}"""
    return RedirectResponse(
        url=f"/v1/transactions/{bill_id}",
        status_code=301,
    )


@router.delete("/{bill_id}")
async def delete_bill_compat(bill_id: int):
    """⚠️  Deprecated：请迁移到 DELETE /v1/transactions/{id}"""
    return RedirectResponse(
        url=f"/v1/transactions/{bill_id}",
        status_code=307,  # 307 保留原 HTTP method
    )
