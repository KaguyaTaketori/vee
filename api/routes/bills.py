"""
api/routes/bills.py
"""
from __future__ import annotations

import base64
import logging
from datetime import date
from typing import Annotated, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, status

from ..auth import require_auth, require_active_user
from ..schemas import (
    BillCreate, BillListResponse, BillOut, BillItemOut,
    BillPatch, MonthlySummary, CategorySummary, CurrencySummary,
    OcrRequest, OcrResponse,
)
from modules.billing.database.bills import (
    get_bill_items, get_monthly_summary, get_recent_bills_with_items,
    get_user_bill_count, get_user_bills, insert_bill, update_bill_field,
)
from modules.billing.services.bill_cache import BillEntry, BillItem
from modules.billing.services.bill_parser import BillParser

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/bills", tags=["bills"])


async def _get_tg_user_id(app_user_id: int) -> Optional[int]:
    """获取已绑定的 tg_user_id，未绑定返回 None。"""
    from repositories import AppUserRepository
    user = await AppUserRepository().get_by_id(app_user_id)
    return user.get("tg_user_id") if user else None


async def _list_bills_merged(
    app_user_id: int,
    tg_user_id: Optional[int],
    limit: int,
    offset: int,
    year: Optional[int] = None,
    month: Optional[int] = None,
    keyword: Optional[str] = None,
) -> tuple[list[dict], int]:
    """
    合并查询 app_bills 和 bills（Bot 侧）。
    已绑定时两边数据合并，未绑定只查 app_bills。
    统一返回 (rows, total)。
    """
    from database.db import get_db

    def _conditions(user_col: str, user_id: int):
        conds  = [f"{user_col} = ?"]
        params = [user_id]
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
        return " AND ".join(conds), params

    async with get_db() as db:
        if tg_user_id:
            # ── 已绑定：UNION 两张表 ──────────────────────────────────────
            app_where,  app_params  = _conditions("app_user_id", app_user_id)
            bot_where,  bot_params  = _conditions("user_id",     tg_user_id)

            union_sql = f"""
                SELECT id, amount, currency, category, description,
                       merchant, bill_date, receipt_url, created_at, updated_at,
                       'app' AS source
                FROM app_bills WHERE {app_where}

                UNION ALL

                SELECT id, amount, currency, category, description,
                       merchant, bill_date, receipt_url, created_at, updated_at,
                       'bot' AS source
                FROM bills WHERE {bot_where}
            """
            count_sql = f"SELECT COUNT(*) FROM ({union_sql})"
            all_params = app_params + bot_params

            async with db.execute(count_sql, all_params) as cur:
                total = (await cur.fetchone())[0]

            paged_sql = f"""
                SELECT * FROM ({union_sql})
                ORDER BY bill_date DESC, created_at DESC
                LIMIT ? OFFSET ?
            """
            async with db.execute(
                paged_sql, all_params + [limit, offset]
            ) as cur:
                rows = [dict(r) for r in await cur.fetchall()]

        else:
            # ── 未绑定：只查 app_bills ────────────────────────────────────
            app_where, app_params = _conditions("app_user_id", app_user_id)

            async with db.execute(
                f"SELECT COUNT(*) FROM app_bills WHERE {app_where}", app_params
            ) as cur:
                total = (await cur.fetchone())[0]

            async with db.execute(
                f"""
                SELECT id, amount, currency, category, description,
                       merchant, bill_date, receipt_url, created_at, updated_at,
                       'app' AS source
                FROM app_bills WHERE {app_where}
                ORDER BY bill_date DESC, created_at DESC
                LIMIT ? OFFSET ?
                """,
                app_params + [limit, offset],
            ) as cur:
                rows = [dict(r) for r in await cur.fetchall()]

    # ── 拼装 items ────────────────────────────────────────────────────────
    for row in rows:
        if row["source"] == "app":
            row["items"] = await get_bill_items(row["id"], app_user_id)
        else:
            row["items"] = await _get_bot_bill_items(row["id"], tg_user_id)

    return rows, total


async def _get_bot_bill_items(bill_id: int, tg_user_id: int) -> list[dict]:
    """查询 Bot 侧账单的明细（bill_items 表）。"""
    from database.db import get_db
    async with get_db() as db:
        async with db.execute(
            """
            SELECT bi.*
            FROM bill_items bi
            JOIN bills b ON bi.bill_id = b.id
            WHERE bi.bill_id = ? AND b.user_id = ?
            ORDER BY bi.sort_order ASC
            """,
            (bill_id, tg_user_id),
        ) as cur:
            return [dict(r) for r in await cur.fetchall()]


def _get_parser() -> BillParser:
    import shared.integrations.llm.manager as llm_mod
    if llm_mod.llm_manager is None:
        raise HTTPException(status_code=503, detail="LLM service not available")
    return BillParser(llm_mod.llm_manager)


# ── FIX 1: _row_to_bill_out was truncated, missing closing parenthesis ────

def _row_to_bill_out(row: dict) -> BillOut:
    items = [
        BillItemOut(
            id=item.get("id"),
            name=item["name"],
            name_raw=item.get("name_raw", ""),
            quantity=item.get("quantity", 1.0),
            unit_price=item.get("unit_price"),
            amount=item["amount"],
            item_type=item.get("item_type", "item"),
            sort_order=item.get("sort_order", 0),
        )
        for item in row.get("items", [])
    ]
    return BillOut(
        id=row["id"],
        amount=row["amount"],
        currency=row["currency"],
        category=row.get("category"),
        description=row.get("description"),
        merchant=row.get("merchant"),
        bill_date=row.get("bill_date"),
        receipt_url=row.get("receipt_url", ""),
        items=items,
        created_at=row["created_at"],
        updated_at=row.get("updated_at", row["created_at"]),
    )


# ── POST /bills/ocr ───────────────────────────────────────────────────────
# FIX 2: was Depends(require_auth) + wrong var name `app_user_id` not defined.
#         Now uses require_active_user and consistent app_user_id param name.

@router.post("/ocr", response_model=OcrResponse, summary="拍照解析账单（不存库）")
async def ocr_bill(
    body: OcrRequest,
    app_user_id: Annotated[int, Depends(require_active_user)],
):
    from repositories import AppUserRepository
    allowed, remaining = await AppUserRepository().check_and_deduct_ai_quota(app_user_id)
    if not allowed:
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="AI 使用次数已达本月上限，请联系管理员提升配额。",
            headers={"X-Quota-Remaining": "0"},
        )

    try:
        image_bytes = base64.b64decode(body.image_base64)
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid base64 image data")

    from shared.services.container import services
    from api.routes.uploads import _MIME_TO_EXT
    import os
    ext = _MIME_TO_EXT.get(body.mime_type, ".jpg")
    receipt_url = ""
    try:
        receipt_url = await services.receipt_storage.save_permanent(image_bytes, ext)
    except Exception as e:
        logger.warning("ocr_bill: failed to save image for user %s: %s", app_user_id, e)

    parser = _get_parser()
    try:
        entry: BillEntry = await parser.parse_image(
            user_id=app_user_id,
            image_base64=body.image_base64,
            mime_type=body.mime_type,
        )
    except Exception as e:
        if receipt_url:
            try:
                await services.receipt_storage.delete(receipt_url)
            except Exception:
                pass
        logger.error("OCR parse failed for user %s: %s", app_user_id, e)
        raise HTTPException(status_code=422, detail=f"Parse failed: {e}")

    items_out = [
        BillItemOut(
            name=item.name,
            name_raw=item.name_raw,
            quantity=item.quantity,
            unit_price=item.unit_price,
            amount=item.amount,
            item_type=item.item_type,
            sort_order=item.sort_order,
        )
        for item in entry.items
    ]
    confidence = entry.extra.get("confidence", "high") if entry.extra else "high"

    return OcrResponse(
        amount=entry.amount,
        currency=entry.currency,
        category=entry.category,
        description=entry.description,
        merchant=entry.merchant,
        bill_date=entry.bill_date,
        receipt_url=receipt_url,
        items=items_out,
        confidence=confidence,
        raw_text=entry.raw_text,
    )


# ── GET /bills ────────────────────────────────────────────────────────────

@router.get("", response_model=BillListResponse, summary="账单列表（分页+明细）")
async def list_bills(
    app_user_id: Annotated[int, Depends(require_active_user)],
    page:      int           = Query(1, ge=1),
    page_size: int           = Query(20, ge=1, le=100),
    year:      Optional[int] = Query(None),
    month:     Optional[int] = Query(None, ge=1, le=12),
    keyword:   Optional[str] = Query(None),
):
    tg_user_id = await _get_tg_user_id(app_user_id)
    offset     = (page - 1) * page_size
    kw         = keyword.strip() if keyword else None

    rows, total = await _list_bills_merged(
        app_user_id=app_user_id,
        tg_user_id=tg_user_id,
        limit=page_size,
        offset=offset,
        year=year,
        month=month,
        keyword=kw,
    )

    bills = [_row_to_bill_out(r) for r in rows]
    return BillListResponse(
        bills=bills,
        total=total,
        page=page,
        page_size=page_size,
        has_next=(offset + len(bills)) < total,
    )


# ── GET /bills/summary ────────────────────────────────────────────────────

@router.get("/summary", response_model=MonthlySummary, summary="月度消费汇总")
async def monthly_summary(
    app_user_id: Annotated[int, Depends(require_active_user)],
    year:  Optional[int] = Query(None),
    month: Optional[int] = Query(None, ge=1, le=12),
):
    from database.db import get_db
    today      = date.today()
    y          = year  or today.year
    m          = month or today.month
    month_str  = f"{y:04d}-{m:02d}%"
    tg_user_id = await _get_tg_user_id(app_user_id)

    async with get_db() as db:
        if tg_user_id:
            union = f"""
                SELECT amount, currency, category FROM app_bills
                WHERE app_user_id = ? AND bill_date LIKE ?
                UNION ALL
                SELECT amount, currency, category FROM bills
                WHERE user_id = ? AND bill_date LIKE ?
            """
            params = [app_user_id, month_str, tg_user_id, month_str]
        else:
            union  = "SELECT amount, currency, category FROM app_bills WHERE app_user_id = ? AND bill_date LIKE ?"
            params = [app_user_id, month_str]

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

    return MonthlySummary(
        year=y, month=m,
        total=total, count=count,
        by_category=[CategorySummary(**i) for i in by_category],
        by_currency=[CurrencySummary(**i) for i in by_currency],
    )


# ── GET /bills/{id} ───────────────────────────────────────────────────────

@router.get("/{bill_id}", response_model=BillOut, summary="单条账单详情")
async def get_bill(
    bill_id: int,
    app_user_id: Annotated[int, Depends(require_active_user)],
):
    from database.db import get_db
    tg_user_id = await _get_tg_user_id(app_user_id)

    async with get_db() as db:
        # Try app_bills first
        async with db.execute(
            "SELECT * FROM app_bills WHERE id = ? AND app_user_id = ?",
            (bill_id, app_user_id),
        ) as cur:
            row = await cur.fetchone()

        # If not found in app_bills and user has a linked bot account, try bills
        if row is None and tg_user_id:
            async with db.execute(
                "SELECT * FROM bills WHERE id = ? AND user_id = ?",
                (bill_id, tg_user_id),
            ) as cur:
                row = await cur.fetchone()
                if row:
                    row_dict = dict(row)
                    row_dict["items"] = await _get_bot_bill_items(bill_id, tg_user_id)
                    return _row_to_bill_out(row_dict)

    if row is None:
        raise HTTPException(status_code=404, detail="Bill not found")

    row_dict = dict(row)
    row_dict["items"] = await get_bill_items(bill_id, app_user_id)
    return _row_to_bill_out(row_dict)


# ── POST /bills ───────────────────────────────────────────────────────────

@router.post("", response_model=BillOut, status_code=status.HTTP_201_CREATED,
             summary="新建账单")
async def create_bill(
    body: BillCreate,
    app_user_id: Annotated[int, Depends(require_active_user)],
):
    from database.db import get_db
    today = date.today().isoformat()
    items = [
        BillItem(
            name=item.name, name_raw=item.name_raw,
            quantity=item.quantity, unit_price=item.unit_price,
            amount=item.amount, item_type=item.item_type,
            sort_order=item.sort_order,
        )
        for item in body.items
    ]
    entry = BillEntry(
        user_id=app_user_id,
        amount=body.amount,
        currency=body.currency or "JPY",
        category=body.category or "其他",
        description=body.description or "",
        merchant=body.merchant or "未知商家",
        bill_date=body.bill_date or today,
        receipt_url=body.receipt_url or "",
        items=items,
    )

    # Insert into app_bills (not legacy bills table)
    from database.db import get_db
    import time
    now = time.time()
    async with get_db() as db:
        cursor = await db.execute(
            """
            INSERT INTO app_bills
                (app_user_id, amount, currency, category, description,
                 merchant, bill_date, raw_text, receipt_file_id, receipt_url,
                 created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                app_user_id, entry.amount, entry.currency, entry.category,
                entry.description, entry.merchant, entry.bill_date,
                entry.raw_text, entry.receipt_file_id, entry.receipt_url,
                now, now,
            ),
        )
        bill_id = cursor.lastrowid

        if entry.items:
            await db.executemany(
                """
                INSERT INTO app_bill_items
                    (bill_id, app_user_id, name, name_raw, quantity,
                     unit_price, amount, item_type, sort_order)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                [
                    (bill_id, app_user_id, item.name, item.name_raw,
                     item.quantity, item.unit_price, item.amount,
                     item.item_type, item.sort_order)
                    for item in entry.items
                ],
            )
        await db.commit()

        async with db.execute(
            "SELECT * FROM app_bills WHERE id = ?", (bill_id,)
        ) as cur:
            row = dict(await cur.fetchone())

    row["items"] = await get_bill_items(bill_id, app_user_id)
    return _row_to_bill_out(row)


# ── PATCH /bills/{id} ─────────────────────────────────────────────────────

@router.patch("/{bill_id}", response_model=BillOut, summary="修改账单字段")
async def patch_bill(
    bill_id: int,
    body: BillPatch,
    app_user_id: Annotated[int, Depends(require_active_user)],
):
    updates = body.model_dump(exclude_none=True)
    if not updates:
        raise HTTPException(status_code=400, detail="No fields to update")

    for field, value in updates.items():
        ok = await update_bill_field(bill_id, app_user_id, field, value)
        if not ok:
            raise HTTPException(status_code=404, detail="Bill not found")

    from database.db import get_db
    async with get_db() as db:
        async with db.execute(
            "SELECT * FROM app_bills WHERE id = ? AND app_user_id = ?",
            (bill_id, app_user_id)
        ) as cur:
            row = await cur.fetchone()
    if row is None:
        raise HTTPException(status_code=404, detail="Bill not found")
    row_dict = dict(row)
    row_dict["items"] = await get_bill_items(bill_id, app_user_id)

    searchable = {
        k: v for k, v in updates.items()
        if k in {"merchant", "description", "category", "bill_date", "receipt_url"}
    }
    if searchable:
        from shared.services.search_service import update_bill_in_index
        await update_bill_in_index(bill_id, searchable)

    return _row_to_bill_out(row_dict)


# ── DELETE /bills/{id} ────────────────────────────────────────────────────

@router.delete("/{bill_id}", status_code=status.HTTP_204_NO_CONTENT,
               summary="删除账单")
async def delete_bill(
    bill_id: int,
    app_user_id: Annotated[int, Depends(require_active_user)],
):
    from database.db import get_db
    from shared.services.container import services

    async with get_db() as db:
        async with db.execute(
            "SELECT receipt_url FROM app_bills WHERE id = ? AND app_user_id = ?",
            (bill_id, app_user_id),
        ) as cur:
            row = await cur.fetchone()
        if row is None:
            raise HTTPException(status_code=404, detail="Bill not found")
        receipt_url = row[0] or ""

        await db.execute(
            "DELETE FROM app_bills WHERE id = ? AND app_user_id = ?",
            (bill_id, app_user_id)
        )
        await db.commit()

    if receipt_url:
        try:
            await services.receipt_storage.delete(receipt_url)
        except Exception as e:
            logger.warning("delete_bill: failed to delete image %s: %s", receipt_url, e)

    from shared.services.search_service import delete_bill_from_index
    await delete_bill_from_index(bill_id)
