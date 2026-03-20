"""
api/schemas.py
"""
from __future__ import annotations

import re as _re
from typing import Optional
from pydantic import BaseModel, Field, field_validator

# ── 校验规则 ──────────────────────────────────────────────────────────────

_USERNAME_RE = _re.compile(r"^[a-zA-Z0-9_]{3,30}$")


# ── Auth ──────────────────────────────────────────────────────────────────

class TokenRequest(BaseModel):
    user_id: int
    secret: str


class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"

# ── Auth schemas ──────────────────────────────────────────────────────────

class RegisterRequest(BaseModel):
    username:     str
    email:        str
    password:     str
    display_name: Optional[str] = None

    @field_validator("username")
    @classmethod
    def validate_username(cls, v: str) -> str:
        v = v.strip()
        if not _USERNAME_RE.match(v):
            raise ValueError("用户名只能包含字母、数字、下划线，长度 3-30 位")
        return v

    @field_validator("email")
    @classmethod
    def validate_email(cls, v: str) -> str:
        v = v.strip().lower()
        if "@" not in v or "." not in v.split("@")[-1]:
            raise ValueError("邮箱格式不正确")
        return v

    @field_validator("password")
    @classmethod
    def validate_password(cls, v: str) -> str:
        if len(v) < 8:
            raise ValueError("密码至少 8 位")
        if not any(c.isdigit() for c in v):
            raise ValueError("密码须包含至少一个数字")
        if not any(c.isalpha() for c in v):
            raise ValueError("密码须包含至少一个字母")
        return v


class VerifyEmailRequest(BaseModel):
    email: str
    code:  str


class LoginRequest(BaseModel):
    identifier: str   # username 或 email
    password:   str


class LoginResponse(BaseModel):
    access_token:  str
    refresh_token: str
    token_type:    str = "bearer"
    expires_in:    int = 900        # access token 秒数


class RefreshRequest(BaseModel):
    refresh_token: str


class LogoutRequest(BaseModel):
    refresh_token: str


class ResendCodeRequest(BaseModel):
    email: str
# ── Me schemas ────────────────────────────────────────────────────────────

class UserProfile(BaseModel):
    id:               int
    username:         str
    email:            str
    display_name:     Optional[str]
    avatar_url:       Optional[str]
    tg_user_id:       Optional[int]
    is_active:        bool
    role:             str
    permissions:      list[str]
    ai_quota_monthly: int
    ai_quota_used:    int
    ai_quota_reset_at: float
    created_at:       float


class UpdateProfileRequest(BaseModel):
    display_name: Optional[str] = None
    avatar_url:   Optional[str] = None

    @field_validator("display_name")
    @classmethod
    def validate_display_name(cls, v: Optional[str]) -> Optional[str]:
        if v is not None and len(v.strip()) > 30:
            raise ValueError("昵称最多 30 个字符")
        return v.strip() if v else v


class ChangePasswordRequest(BaseModel):
    old_password: str
    new_password: str

    @field_validator("new_password")
    @classmethod
    def validate_new_password(cls, v: str) -> str:
        if len(v) < 8:
            raise ValueError("密码至少 8 位")
        if not any(c.isdigit() for c in v):
            raise ValueError("密码须包含至少一个数字")
        if not any(c.isalpha() for c in v):
            raise ValueError("密码须包含至少一个字母")
        return v


class ForgotPasswordRequest(BaseModel):
    email: str


class ResetPasswordRequest(BaseModel):
    email:        str
    code:         str
    new_password: str

    @field_validator("new_password")
    @classmethod
    def validate_new_password(cls, v: str) -> str:
        if len(v) < 8:
            raise ValueError("密码至少 8 位")
        return v

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


class TgBindRequestResponse(BaseModel):
    code:       str
    expires_in: int = 600   # 秒
    message:    str = "请在 10 分钟内前往 Bot 发送 /bind <验证码> 完成绑定"
