"""
api/routes/bills.py
───────────────────
账单相关路由。全部需要 JWT 鉴权。

端点总览：
  POST   /bills/ocr          上传图片 → AI 解析，返回预览（不存库）
  GET    /bills              分页列表，带明细
  GET    /bills/summary      月度汇总（分类 + 货币）
  GET    /bills/{id}         单条账单详情
  POST   /bills              手动新建账单（确认 OCR 结果后调此接口）
  PATCH  /bills/{id}         修改单条账单字段
  DELETE /bills/{id}         删除账单
"""
from __future__ import annotations

import base64
import logging
import os
import time
from datetime import date
from typing import Annotated, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, status

from ..auth import require_auth
from ..schemas import (
    BillCreate,
    BillListResponse,
    BillOut,
    BillItemOut,
    BillPatch,
    MonthlySummary,
    CategorySummary,
    CurrencySummary,
    OcrRequest,
    OcrResponse,
)
from modules.billing.database.bills import (
    get_bill_items,
    get_monthly_summary,
    get_recent_bills_with_items,
    get_user_bill_count,
    get_user_bills,
    insert_bill,
    update_bill_field,
)
from modules.billing.services.bill_cache import BillEntry, BillItem
from modules.billing.services.bill_parser import BillParser

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/bills", tags=["bills"])

# ── 工具函数 ──────────────────────────────────────────────────────────────

def _get_parser() -> BillParser:
    import shared.integrations.llm.manager as llm_mod
    if llm_mod.llm_manager is None:
        raise HTTPException(status_code=503, detail="LLM service not available")
    return BillParser(llm_mod.llm_manager)


def _row_to_bill_out(row: dict) -> BillOut:
    """把数据库行（含 items 列表）转成 BillOut。"""
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
        receipt_image_url=None,   # TODO: 对象存储 URL（替换 Telegram file_id）
        items=items,
        created_at=row["created_at"],
        updated_at=row.get("updated_at", row["created_at"]),
    )


# ── POST /bills/ocr ───────────────────────────────────────────────────────

@router.post("/ocr", response_model=OcrResponse, summary="拍照解析账单（不存库）")
async def ocr_bill(
    body: OcrRequest,
    user_id: Annotated[int, Depends(require_auth)],
):
    """
    接收 base64 图片，调 BillParser 解析，返回结构化预览。
    用户在 App 确认后调 POST /bills 存库。
    """
    try:
        image_bytes = base64.b64decode(body.image_base64)
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid base64 image data")

    parser = _get_parser()
    try:
        entry: BillEntry = await parser.parse_image(
            user_id=user_id,
            image_bytes=image_bytes,
            mime_type=body.mime_type,
        )
    except Exception as e:
        logger.error("OCR parse failed for user %s: %s", user_id, e)
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
        items=items_out,
        confidence=confidence,
        raw_text=entry.raw_text,
    )


# ── GET /bills ────────────────────────────────────────────────────────────

@router.get("", response_model=BillListResponse, summary="账单列表（分页+明细）")
async def list_bills(
    user_id: Annotated[int, Depends(require_auth)],
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    year: Optional[int] = Query(None),
    month: Optional[int] = Query(None, ge=1, le=12),
):
    offset = (page - 1) * page_size
    rows = await get_user_bills(user_id, limit=page_size, offset=offset)
    total = await get_user_bill_count(user_id)

    # 附加 items
    for row in rows:
        row["items"] = await get_bill_items(row["id"], user_id)

    # 简单月份过滤（数据量不大时 OK；大量数据时应在 SQL 层加 WHERE bill_date LIKE）
    if year or month:
        prefix = ""
        if year:
            prefix += f"{year:04d}"
        if month:
            prefix += f"-{month:02d}"
        rows = [r for r in rows if (r.get("bill_date") or "").startswith(prefix)]

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
    user_id: Annotated[int, Depends(require_auth)],
    year: Optional[int] = Query(None),
    month: Optional[int] = Query(None, ge=1, le=12),
):
    today = date.today()
    y = year or today.year
    m = month or today.month

    data = await get_monthly_summary(user_id, y, m)

    return MonthlySummary(
        year=y,
        month=m,
        total=data["total"],
        count=data["count"],
        by_category=[
            CategorySummary(**item) for item in data["by_category"]
        ],
        by_currency=[
            CurrencySummary(**item) for item in data["by_currency"]
        ],
    )


# ── GET /bills/{id} ───────────────────────────────────────────────────────

@router.get("/{bill_id}", response_model=BillOut, summary="单条账单详情")
async def get_bill(
    bill_id: int,
    user_id: Annotated[int, Depends(require_auth)],
):
    rows = await get_user_bills(user_id, limit=1, offset=0)
    # get_user_bills 不支持按 id 查，直接用 get_bill_items 验权后组装
    # 生产环境可在 bills.py 加 get_bill_by_id(bill_id, user_id)
    items = await get_bill_items(bill_id, user_id)
    # 若 items 为空不代表账单不存在，需单独查主表
    from database.db import get_db
    async with get_db() as db:
        cursor = await db.execute(
            "SELECT * FROM bills WHERE id = ? AND user_id = ?",
            (bill_id, user_id),
        )
        row = await cursor.fetchone()

    if row is None:
        raise HTTPException(status_code=404, detail="Bill not found")

    row_dict = dict(row)
    row_dict["items"] = items
    return _row_to_bill_out(row_dict)


# ── POST /bills ───────────────────────────────────────────────────────────

@router.post("", response_model=BillOut, status_code=status.HTTP_201_CREATED,
             summary="新建账单（手动 or OCR 确认后）")
async def create_bill(
    body: BillCreate,
    user_id: Annotated[int, Depends(require_auth)],
):
    today = date.today().isoformat()
    items = [
        BillItem(
            name=item.name,
            name_raw=item.name_raw,
            quantity=item.quantity,
            unit_price=item.unit_price,
            amount=item.amount,
            item_type=item.item_type,
            sort_order=item.sort_order,
        )
        for item in body.items
    ]

    entry = BillEntry(
        user_id=user_id,
        amount=body.amount,
        currency=body.currency or "JPY",
        category=body.category or "其他",
        description=body.description or "",
        merchant=body.merchant or "未知商家",
        bill_date=body.bill_date or today,
        items=items,
        receipt_file_id="",  # App 上传图片走对象存储，URL 存 description 或新增字段
    )

    bill_id = await insert_bill(entry)

    # 读回完整数据返回
    from database.db import get_db
    async with get_db() as db:
        cursor = await db.execute("SELECT * FROM bills WHERE id = ?", (bill_id,))
        row = dict(await cursor.fetchone())
    row["items"] = await get_bill_items(bill_id, user_id)
    return _row_to_bill_out(row)


# ── PATCH /bills/{id} ────────────────────────────────────────────────────

@router.patch("/{bill_id}", response_model=BillOut, summary="修改账单字段")
async def patch_bill(
    bill_id: int,
    body: BillPatch,
    user_id: Annotated[int, Depends(require_auth)],
):
    updates = body.model_dump(exclude_none=True)
    if not updates:
        raise HTTPException(status_code=400, detail="No fields to update")

    for field, value in updates.items():
        ok = await update_bill_field(bill_id, user_id, field, value)
        if not ok:
            raise HTTPException(status_code=404, detail="Bill not found")

    # 读回最新数据
    from database.db import get_db
    async with get_db() as db:
        cursor = await db.execute(
            "SELECT * FROM bills WHERE id = ? AND user_id = ?", (bill_id, user_id)
        )
        row = await cursor.fetchone()
    if row is None:
        raise HTTPException(status_code=404, detail="Bill not found")
    row_dict = dict(row)
    row_dict["items"] = await get_bill_items(bill_id, user_id)
    return _row_to_bill_out(row_dict)


# ── DELETE /bills/{id} ────────────────────────────────────────────────────

@router.delete("/{bill_id}", status_code=status.HTTP_204_NO_CONTENT, summary="删除账单")
async def delete_bill(
    bill_id: int,
    user_id: Annotated[int, Depends(require_auth)],
):
    from database.db import get_db
    async with get_db() as db:
        cursor = await db.execute(
            "DELETE FROM bills WHERE id = ? AND user_id = ?", (bill_id, user_id)
        )
        await db.commit()
        if cursor.rowcount == 0:
            raise HTTPException(status_code=404, detail="Bill not found")
