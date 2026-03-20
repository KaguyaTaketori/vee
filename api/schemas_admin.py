# api/schemas_admin.py
"""
管理员 API 的 Pydantic Schema 定义
"""
from __future__ import annotations

import json
from typing import Any, Optional
from pydantic import BaseModel, Field, field_validator


# ── 用户管理 ──────────────────────────────────────────────────────────────

class AdminUserOut(BaseModel):
    id: int
    app_username: Optional[str]
    email: Optional[str]
    display_name: Optional[str]
    tg_user_id: Optional[int]
    tg_username: Optional[str]
    is_active: bool
    role: str
    permissions: list[str]
    ai_quota_monthly: int
    ai_quota_used: int
    ai_quota_reset_at: float
    registration_ip: str
    last_login_ip: str
    created_at: float
    updated_at: float
    last_seen: Optional[float]

    @classmethod
    def from_row(cls, row: dict) -> "AdminUserOut":
        # 解析 permissions JSON
        raw_perms = row.get("permissions") or "[]"
        try:
            perms = json.loads(raw_perms)
            if not isinstance(perms, list):
                perms = []
        except (ValueError, TypeError):
            perms = []

        return cls(
            id=row["id"],
            app_username=row.get("app_username"),
            email=row.get("email"),
            display_name=row.get("display_name"),
            tg_user_id=row.get("tg_user_id"),
            tg_username=row.get("tg_username"),
            is_active=bool(row.get("is_active", 1)),
            role=row.get("role") or "user",
            permissions=perms,
            ai_quota_monthly=row.get("ai_quota_monthly") or 100,
            ai_quota_used=row.get("ai_quota_used") or 0,
            ai_quota_reset_at=float(row.get("ai_quota_reset_at") or 0),
            registration_ip=row.get("registration_ip") or "",
            last_login_ip=row.get("last_login_ip") or "",
            created_at=float(row.get("created_at") or 0),
            updated_at=float(row.get("updated_at") or 0),
            last_seen=float(row["last_seen"]) if row.get("last_seen") else None,
        )


class AdminUserListResponse(BaseModel):
    users: list[AdminUserOut]
    total: int
    page: int
    page_size: int
    has_next: bool


class SetActiveRequest(BaseModel):
    is_active: bool


class SetRoleRequest(BaseModel):
    role: str = Field(..., pattern="^(user|admin)$")


class SetPermissionsRequest(BaseModel):
    permissions: list[str]

    @field_validator("permissions")
    @classmethod
    def validate_permissions(cls, v: list[str]) -> list[str]:
        allowed = {
            "bot_text", "bot_receipt", "bot_voice", "bot_download",
            "app_ocr", "app_export", "app_upload",
        }
        unknown = set(v) - allowed
        if unknown:
            raise ValueError(f"未知的权限标识: {unknown}。允许值: {allowed}")
        return list(set(v))  # 去重


class SetQuotaRequest(BaseModel):
    ai_quota_monthly: int = Field(..., ge=-1)  # -1 = 无限


# ── 全局统计 ──────────────────────────────────────────────────────────────

class GlobalStatsOut(BaseModel):
    total_users: int
    active_users: int
    admin_count: int
    total_bills: int
    bills_this_month: int
    ai_quota_used_this_month: int
    online_ws_users: int
    total_ws_connections: int


# ── 系统配置 ──────────────────────────────────────────────────────────────

class SystemConfigOut(BaseModel):
    config_key: str
    config_value: str
    description: str
    updated_by: Optional[int]
    updated_at: float


class SystemConfigListResponse(BaseModel):
    configs: list[SystemConfigOut]


class UpsertConfigRequest(BaseModel):
    config_value: str
    description: Optional[str] = None


class BatchUpsertConfigRequest(BaseModel):
    configs: dict[str, str] = Field(
        ..., description="key → value 字典，批量更新"
    )
