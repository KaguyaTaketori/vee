# api/routes/bills.py
from __future__ import annotations

import base64
import logging
from datetime import date
from typing import Annotated, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, status

from api.auth import require_auth, require_active_user
from api.schemas import (
    BillCreate, BillListResponse, BillOut, BillItemOut,
    BillPatch, MonthlySummary, CategorySummary, CurrencySummary,
    OcrRequest, OcrResponse,
)
from shared.repositories.bill_repo import BillRepository
from modules.billing.services.bill_parser import BillParser
from utils.currency import int_to_amount

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/bills", tags=["bills"])

_bill_repo = BillRepository()


def _get_parser() -> BillParser:
    import shared.integrations.llm.manager as llm_mod
    if llm_mod.llm_manager is None:
        raise HTTPException(status_code=503, detail="LLM service not available")
    return BillParser(llm_mod.llm_manager)


def _row_to_bill_out(row: dict) -> BillOut:
    """row 已经过 BillRepository._deserialize，amount 是 float"""
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


# ── OCR ───────────────────────────────────────────────────────────────────

@router.post("/ocr", response_model=OcrResponse, summary="拍照解析账单（不存库）")
async def ocr_bill(
    body: OcrRequest,
    app_user_id: Annotated[int, Depends(require_active_user)],
):
    from repositories import UserRepository
    allowed, _ = await UserRepository().check_and_deduct_ai_quota(app_user_id)
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
    ext = _MIME_TO_EXT.get(body.mime_type, ".jpg")
    receipt_url = ""
    try:
        receipt_url = await services.receipt_storage.save_permanent(image_bytes, ext)
    except Exception as e:
        logger.warning("ocr_bill: image save failed user=%s: %s", app_user_id, e)

    parser = _get_parser()
    try:
        from modules.billing.services.bill_cache import BillEntry
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
        raise HTTPException(status_code=422, detail=f"Parse failed: {e}")

    return OcrResponse(
        amount=entry.amount,
        currency=entry.currency,
        category=entry.category,
        description=entry.description,
        merchant=entry.merchant,
        bill_date=entry.bill_date,
        receipt_url=receipt_url,
        items=[
            BillItemOut(
                name=item.name, name_raw=item.name_raw,
                quantity=item.quantity, unit_price=item.unit_price,
                amount=item.amount, item_type=item.item_type,
                sort_order=item.sort_order,
            )
            for item in entry.items
        ],
        confidence=entry.extra.get("confidence", "high") if entry.extra else "high",
        raw_text=entry.raw_text,
    )


# ── LIST ──────────────────────────────────────────────────────────────────

@router.get("", response_model=BillListResponse, summary="账单列表")
async def list_bills(
    app_user_id: Annotated[int, Depends(require_active_user)],
    page:      int           = Query(1, ge=1),
    page_size: int           = Query(20, ge=1, le=100),
    year:      Optional[int] = Query(None),
    month:     Optional[int] = Query(None, ge=1, le=12),
    keyword:   Optional[str] = Query(None),
    source:    Optional[str] = Query(None, description="bot/app/web，不传则全部"),
):
    kw = keyword.strip() if keyword else None

    # 有关键词时先尝试 Meilisearch
    rows, total = None, 0
    if kw:
        from shared.services.search_service import search_bills
        result = await search_bills(
            app_user_id, kw,
            year=year, month=month,
            page=page, page_size=page_size,
        )
        if result is not None:
            # Meilisearch 返回的 amount 已是 float（原始数据写入时就是 float）
            # 但我们存的是 INTEGER，所以还需要反序列化
            for row in result["bills"]:
                currency = row.get("currency", "JPY")
                if isinstance(row.get("amount"), int):
                    row["amount"] = int_to_amount(row["amount"], currency)
                row.setdefault("items", [])
                row.setdefault("updated_at", row.get("created_at", 0))
            return BillListResponse(
                bills=[_row_to_bill_out(r) for r in result["bills"]],
                total=result["total"],
                page=page,
                page_size=page_size,
                has_next=result["has_next"],
            )

    # 降级到 SQLite
    rows, total = await _bill_repo.list_by_user(
        app_user_id,
        page=page, page_size=page_size,
        year=year, month=month,
        keyword=kw, source=source,
    )

    return BillListResponse(
        bills=[_row_to_bill_out(r) for r in rows],
        total=total,
        page=page,
        page_size=page_size,
        has_next=(page * page_size) < total,
    )


# ── SUMMARY ───────────────────────────────────────────────────────────────

@router.get("/summary", response_model=MonthlySummary, summary="月度消费汇总")
async def monthly_summary(
    app_user_id: Annotated[int, Depends(require_active_user)],
    year:  Optional[int] = Query(None),
    month: Optional[int] = Query(None, ge=1, le=12),
):
    today = date.today()
    y = year  or today.year
    m = month or today.month

    summary = await _bill_repo.monthly_summary(app_user_id, y, m)

    # int → float 转换用于展示
    main_currency = (
        summary["by_currency"][0]["currency"]
        if summary["by_currency"] else "JPY"
    )
    total_float = int_to_amount(summary["total"], main_currency)

    by_category = [
        CategorySummary(
            category=c["category"],
            total=int_to_amount(c["total"], main_currency),
            count=c["count"],
        )
        for c in summary["by_category"]
    ]
    by_currency = [
        CurrencySummary(
            currency=c["currency"],
            total=int_to_amount(c["total"], c["currency"]),
        )
        for c in summary["by_currency"]
    ]

    return MonthlySummary(
        year=y, month=m,
        total=total_float,
        count=summary["count"],
        by_category=by_category,
        by_currency=by_currency,
    )


# ── GET ONE ───────────────────────────────────────────────────────────────

@router.get("/{bill_id}", response_model=BillOut, summary="单条账单详情")
async def get_bill(
    bill_id: int,
    app_user_id: Annotated[int, Depends(require_active_user)],
):
    row = await _bill_repo.get_by_id(bill_id, app_user_id)
    if row is None:
        raise HTTPException(status_code=404, detail="Bill not found")
    return _row_to_bill_out(row)


# ── CREATE ────────────────────────────────────────────────────────────────

@router.post("", response_model=BillOut, status_code=status.HTTP_201_CREATED,
             summary="新建账单")
async def create_bill(
    body: BillCreate,
    app_user_id: Annotated[int, Depends(require_active_user)],
):
    today = date.today().isoformat()
    items = [
        {
            "name":       item.name,
            "name_raw":   item.name_raw,
            "quantity":   item.quantity,
            "unit_price": item.unit_price,
            "amount":     item.amount,
            "item_type":  item.item_type,
            "sort_order": item.sort_order,
        }
        for item in body.items
    ]

    bill_id = await _bill_repo.create(
        user_id=app_user_id,
        amount=body.amount,
        currency=body.currency or "JPY",
        category=body.category or "其他",
        description=body.description or "",
        merchant=body.merchant or "未知商家",
        bill_date=body.bill_date or today,
        source="app",
        receipt_url=body.receipt_url or "",
        items=items,
    )

    # Meilisearch 索引
    from shared.services.search_service import index_bill
    import time
    await index_bill({
        "id":          bill_id,
        "user_id":     app_user_id,
        "amount":      body.amount,
        "currency":    body.currency or "JPY",
        "category":    body.category,
        "description": body.description,
        "merchant":    body.merchant,
        "bill_date":   body.bill_date or today,
        "receipt_url": body.receipt_url or "",
        "created_at":  int(time.time()),
    })

    row = await _bill_repo.get_by_id(bill_id, app_user_id)
    return _row_to_bill_out(row)


# ── PATCH ─────────────────────────────────────────────────────────────────

@router.patch("/{bill_id}", response_model=BillOut, summary="修改账单字段")
async def patch_bill(
    bill_id: int,
    body: BillPatch,
    app_user_id: Annotated[int, Depends(require_active_user)],
):
    updates = body.model_dump(exclude_none=True)
    if not updates:
        raise HTTPException(status_code=400, detail="No fields to update")

    ok = await _bill_repo.update_fields(bill_id, app_user_id, updates)
    if not ok:
        raise HTTPException(status_code=404, detail="Bill not found")

    # 更新 Meilisearch
    searchable = {
        k: v for k, v in updates.items()
        if k in {"merchant", "description", "category", "bill_date", "receipt_url"}
    }
    if searchable:
        from shared.services.search_service import update_bill_in_index
        await update_bill_in_index(bill_id, searchable)

    row = await _bill_repo.get_by_id(bill_id, app_user_id)
    return _row_to_bill_out(row)


# ── DELETE ────────────────────────────────────────────────────────────────

@router.delete("/{bill_id}", status_code=status.HTTP_204_NO_CONTENT,
               summary="删除账单")
async def delete_bill(
    bill_id: int,
    app_user_id: Annotated[int, Depends(require_active_user)],
):
    from shared.services.container import services

    receipt_url = await _bill_repo.delete(bill_id, app_user_id)
    if receipt_url is None:
        raise HTTPException(status_code=404, detail="Bill not found")

    if receipt_url:
        try:
            await services.receipt_storage.delete(receipt_url)
        except Exception as e:
            logger.warning("delete_bill: image delete failed %s: %s", receipt_url, e)

    from shared.services.search_service import delete_bill_from_index
    await delete_bill_from_index(bill_id)
