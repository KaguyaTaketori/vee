# api/schemas_v2.py
"""
新会计体系的 Pydantic Schema 定义
对应 v007 迁移后的新表结构：
  groups / accounts / categories / transactions / transaction_items
  receipts / statements / scheduled_bills
"""
from __future__ import annotations

from datetime import date
from typing import Optional
from pydantic import BaseModel, Field, field_validator
from enum import Enum


# ─────────────────────────────────────────────────────────────────────────────
# Enums
# ─────────────────────────────────────────────────────────────────────────────

class UserTier(str, Enum):
    FREE           = "free"
    PREMIUM        = "premium"
    FAMILY_MEMBER  = "family_member"


class AccountType(str, Enum):
    CASH        = "cash"
    BANK        = "bank"
    CREDIT_CARD = "credit_card"


class TransactionType(str, Enum):
    INCOME   = "income"
    EXPENSE  = "expense"
    TRANSFER = "transfer"


class CategoryType(str, Enum):
    INCOME  = "income"
    EXPENSE = "expense"
    BOTH    = "both"


class RecurringFrequency(str, Enum):
    DAILY   = "daily"
    WEEKLY  = "weekly"
    MONTHLY = "monthly"
    YEARLY  = "yearly"


# ─────────────────────────────────────────────────────────────────────────────
# Group
# ─────────────────────────────────────────────────────────────────────────────

class GroupOut(BaseModel):
    id:            int
    name:          str
    owner_id:      int
    invite_code:   str
    base_currency: str
    is_active:     bool
    created_at:    float
    updated_at:    float


class GroupCreate(BaseModel):
    name:          str = Field(default="我的账本", max_length=50)
    base_currency: str = Field(default="JPY", max_length=3)


class GroupJoin(BaseModel):
    invite_code: str


# ─────────────────────────────────────────────────────────────────────────────
# Account
# ─────────────────────────────────────────────────────────────────────────────

class AccountOut(BaseModel):
    id:               int
    name:             str
    type:             str
    currency_code:    str
    group_id:         int
    balance_cache:    int
    balance_updated_at: Optional[float]
    is_active:        bool
    created_at:       float
    updated_at:       float

    @property
    def balance_float(self) -> float:
        """余额转回 float（用于展示）"""
        from utils.currency import int_to_amount
        return int_to_amount(self.balance_cache, self.currency_code)


class AccountCreate(BaseModel):
    name:          str = Field(..., max_length=50)
    type:          AccountType = AccountType.CASH
    currency_code: str = Field(default="JPY", max_length=3)
    group_id:      int


class AccountPatch(BaseModel):
    name:      Optional[str]  = Field(None, max_length=50)
    is_active: Optional[bool] = None


# ─────────────────────────────────────────────────────────────────────────────
# Category
# ─────────────────────────────────────────────────────────────────────────────

class CategoryOut(BaseModel):
    id:         int
    name:       str
    icon:       Optional[str]
    color:      Optional[str]
    type:       str
    is_system:  bool
    group_id:   Optional[int]
    sort_order: int


class CategoryCreate(BaseModel):
    name:       str = Field(..., max_length=30)
    icon:       Optional[str]  = None
    color:      Optional[str]  = None
    type:       CategoryType   = CategoryType.EXPENSE
    group_id:   int
    sort_order: int = 0


class CategoryPatch(BaseModel):
    name:       Optional[str] = None
    icon:       Optional[str] = None
    color:      Optional[str] = None
    sort_order: Optional[int] = None


# ─────────────────────────────────────────────────────────────────────────────
# TransactionItem
# ─────────────────────────────────────────────────────────────────────────────

class TransactionItemOut(BaseModel):
    id:         Optional[int]
    name:       str
    name_raw:   str = ""
    quantity:   float = 1.0
    unit_price: Optional[float]   # 已转回 float
    amount:     float             # 已转回 float
    item_type:  str = "item"
    sort_order: int = 0


class TransactionItemIn(BaseModel):
    name:       str
    name_raw:   str   = ""
    quantity:   float = 1.0
    unit_price: Optional[float] = None
    amount:     float
    item_type:  str   = "item"
    sort_order: int   = 0


# ─────────────────────────────────────────────────────────────────────────────
# Transaction
# ─────────────────────────────────────────────────────────────────────────────

class TransactionOut(BaseModel):
    id:               int
    type:             str
    amount:           float          # 已转回 float
    currency_code:    str
    base_amount:      float          # 已转回 float
    exchange_rate:    float          # exchange_rate_int / 1_000_000
    account_id:       int
    to_account_id:    Optional[int]
    transfer_peer_id: Optional[int]
    category_id:      int
    user_id:          int
    group_id:         int
    is_private:       bool
    note:             Optional[str]
    transaction_date: float          # unix timestamp
    receipt_url:      str = ""       # 第一张凭证 URL，便于列表展示
    items:            list[TransactionItemOut] = []
    created_at:       float
    updated_at:       float
    is_deleted:       bool


class TransactionCreate(BaseModel):
    type:             TransactionType = TransactionType.EXPENSE
    amount:           float = Field(..., gt=0)
    currency_code:    str   = Field(default="JPY", max_length=3)
    exchange_rate:    float = Field(default=1.0, gt=0)  # 对本位币的汇率
    account_id:       int
    to_account_id:    Optional[int] = None
    category_id:      int
    group_id:         int
    is_private:       bool  = False
    note:             Optional[str] = Field(None, max_length=200)
    transaction_date: float         # unix timestamp
    receipt_url:      str   = ""
    items:            list[TransactionItemIn] = []

    @field_validator("type", mode="before")
    @classmethod
    def validate_transfer(cls, v, info):
        return v

    def requires_to_account(self) -> bool:
        return self.type == TransactionType.TRANSFER


class TransactionPatch(BaseModel):
    type:             Optional[TransactionType] = None
    amount:           Optional[float]           = Field(None, gt=0)
    currency_code:    Optional[str]             = None
    exchange_rate:    Optional[float]           = None
    account_id:       Optional[int]             = None
    to_account_id:    Optional[int]             = None
    category_id:      Optional[int]             = None
    is_private:       Optional[bool]            = None
    note:             Optional[str]             = None
    transaction_date: Optional[float]           = None
    receipt_url:      Optional[str]             = None


class TransactionListResponse(BaseModel):
    transactions: list[TransactionOut]
    total:        int
    page:         int
    page_size:    int
    has_next:     bool


# ─────────────────────────────────────────────────────────────────────────────
# Summary
# ─────────────────────────────────────────────────────────────────────────────

class CategoryStatOut(BaseModel):
    category_id: int
    name:        str
    icon:        Optional[str]
    color:       Optional[str]
    total:       float
    count:       int
    percent:     float = 0.0


class MonthlyStatOut(BaseModel):
    year:          int
    month:         int
    total_expense: float
    total_income:  float
    net:           float
    count:         int
    by_category:   list[CategoryStatOut] = []
    by_currency:   list[dict]            = []


# ─────────────────────────────────────────────────────────────────────────────
# Receipt
# ─────────────────────────────────────────────────────────────────────────────

class ReceiptOut(BaseModel):
    id:             int
    transaction_id: int
    image_url:      str
    extracted_text: Optional[str]
    created_at:     float


# ─────────────────────────────────────────────────────────────────────────────
# Statement
# ─────────────────────────────────────────────────────────────────────────────

class StatementOut(BaseModel):
    id:                  int
    account_id:          int
    period_start:        str
    period_end:          str
    total_amount:        float
    is_amount_confirmed: bool
    closing_date:        str
    due_date:            str
    is_settled:          bool
    created_at:          float
    updated_at:          float


class StatementCreate(BaseModel):
    account_id:   int
    period_start: str
    period_end:   str
    closing_date: str
    due_date:     str


# ─────────────────────────────────────────────────────────────────────────────
# ScheduledBill
# ─────────────────────────────────────────────────────────────────────────────

class ScheduledBillOut(BaseModel):
    id:              int
    title:           str
    amount:          float
    currency_code:   str
    account_id:      int
    category_id:     int
    user_id:         int
    group_id:        int
    frequency:       str
    next_due_date:   str
    auto_record:     bool
    is_active:       bool
    last_executed_at: Optional[float]
    created_at:      float
    updated_at:      float


class ScheduledBillCreate(BaseModel):
    title:         str   = "未命名订阅"
    amount:        float = Field(..., gt=0)
    currency_code: str   = Field(default="JPY", max_length=3)
    account_id:    int
    category_id:   int
    group_id:      int
    frequency:     RecurringFrequency = RecurringFrequency.MONTHLY
    next_due_date: str   # YYYY-MM-DD
    auto_record:   bool  = True


class ScheduledBillPatch(BaseModel):
    title:         Optional[str]   = None
    amount:        Optional[float] = None
    next_due_date: Optional[str]   = None
    auto_record:   Optional[bool]  = None
    is_active:     Optional[bool]  = None
