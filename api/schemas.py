"""
api/schemas.py
──────────────
Request / Response Pydantic 模型。

与数据库层（BillEntry / BillItem dataclass）分离：
  - API 层用这里的模型做序列化和校验
  - 内部转换函数负责两者互转
"""
from __future__ import annotations

from typing import Optional
from pydantic import BaseModel, Field


# ── Auth ──────────────────────────────────────────────────────────────────

class TokenRequest(BaseModel):
    user_id: int
    secret: str  # 对应 .env API_SECRET


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
    item_type: str = "item"   # "item" | "discount" | "tax" | "subtotal"
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
    receipt_image_url: Optional[str] = None  # App 端图片 URL（替换 Telegram file_id）
    items: list[BillItemOut] = []
    created_at: float
    updated_at: float


class BillCreate(BaseModel):
    """手动新建账单（不经过 OCR）"""
    amount: float = Field(..., gt=0)
    currency: str = "JPY"
    category: Optional[str] = None
    description: Optional[str] = None
    merchant: Optional[str] = None
    bill_date: Optional[str] = None   # "YYYY-MM-DD"
    items: list[BillItemOut] = []


class BillPatch(BaseModel):
    """PATCH /bills/{id} — 只传要改的字段"""
    amount: Optional[float] = Field(None, gt=0)
    currency: Optional[str] = None
    category: Optional[str] = None
    description: Optional[str] = None
    merchant: Optional[str] = None
    bill_date: Optional[str] = None


# ── OCR ───────────────────────────────────────────────────────────────────

class OcrRequest(BaseModel):
    """POST /bills/ocr — base64 图片"""
    image_base64: str
    mime_type: str = "image/jpeg"


class OcrResponse(BaseModel):
    """解析结果，让 App 用户确认后再调 POST /bills 存库"""
    amount: float
    currency: str
    category: Optional[str]
    description: Optional[str]
    merchant: Optional[str]
    bill_date: Optional[str]
    items: list[BillItemOut] = []
    confidence: str = "high"   # "high" | "low"
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
