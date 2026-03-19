"""
api/schemas.py
"""
from __future__ import annotations

from typing import Optional
from pydantic import BaseModel, Field


# ── Auth ──────────────────────────────────────────────────────────────────

class TokenRequest(BaseModel):
    user_id: int
    secret: str


class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"


# ── Bill Item ─────────────────────────────────────────────────────────────

class BillItemOut(BaseModel):
    id: Optional[int] = None
    name: str
    name_raw: str = ""
    quantity: float = 1.0
    unit_price: Optional[float] = None
    amount: float
    item_type: str = "item"
    sort_order: int = 0


# ── Bill ──────────────────────────────────────────────────────────────────

class BillOut(BaseModel):
    id: int
    amount: float
    currency: str
    category: Optional[str]
    description: Optional[str]
    merchant: Optional[str]
    bill_date: Optional[str]
    receipt_url: str = ""          # 正式凭证 URL，空字符串表示无凭证
    items: list[BillItemOut] = []
    created_at: float
    updated_at: float


class BillCreate(BaseModel):
    amount: float = Field(..., gt=0)
    currency: str = "JPY"
    category: Optional[str] = None
    description: Optional[str] = None
    merchant: Optional[str] = None
    bill_date: Optional[str] = None
    receipt_url: str = ""          # App 上传图片后得到的 URL
    items: list[BillItemOut] = []


class BillPatch(BaseModel):
    amount: Optional[float] = Field(None, gt=0)
    currency: Optional[str] = None
    category: Optional[str] = None
    description: Optional[str] = None
    merchant: Optional[str] = None
    bill_date: Optional[str] = None
    receipt_url: Optional[str] = None


# ── Upload ────────────────────────────────────────────────────────────────

class UploadReceiptResponse(BaseModel):
    """POST /v1/uploads/receipt 的响应体"""
    receipt_url: str               # 可供 App 直接访问的公开 URL


# ── OCR ───────────────────────────────────────────────────────────────────

class OcrRequest(BaseModel):
    image_base64: str
    mime_type: str = "image/jpeg"


class OcrResponse(BaseModel):
    amount: float
    currency: str
    category: Optional[str]
    description: Optional[str]
    merchant: Optional[str]
    bill_date: Optional[str]
    receipt_url: str = ""          # OCR 同时上传图片后返回的 URL
    items: list[BillItemOut] = []
    confidence: str = "high"
    raw_text: str = ""


# ── Summary ───────────────────────────────────────────────────────────────

class CategorySummary(BaseModel):
    category: str
    total: float
    count: int


class CurrencySummary(BaseModel):
    currency: str
    total: float


class MonthlySummary(BaseModel):
    year: int
    month: int
    total: float
    count: int
    by_category: list[CategorySummary]
    by_currency: list[CurrencySummary]


# ── Pagination ────────────────────────────────────────────────────────────

class BillListResponse(BaseModel):
    bills: list[BillOut]
    total: int
    page: int
    page_size: int
    has_next: bool
