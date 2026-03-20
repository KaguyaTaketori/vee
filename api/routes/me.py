# api/routes/me.py
"""
GET  /v1/me
PATCH /v1/me
POST /v1/me/change-password
POST /v1/me/forgot-password
POST /v1/me/reset-password
POST /v1/me/logout-all
"""
from __future__ import annotations

import logging
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status

from api.auth import require_active_user, require_auth
from api.mailer import send_reset_code
from api.schemas import (
    ChangePasswordRequest,
    ForgotPasswordRequest,
    ResetPasswordRequest,
    UpdateProfileRequest,
    UserProfile,
    TgBindRequestResponse
)
from api.security import hash_password, verify_password
from repositories import UserRepository

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/me", tags=["me"])

_REPO = UserRepository()


def _to_profile(user: dict) -> UserProfile:
    return UserProfile(
        id=user["id"],
        username=user.get("app_username") or "",   # ← 字段名变化
        email=user.get("email") or "",
        display_name=user.get("display_name"),
        avatar_url=user.get("avatar_url"),
        tg_user_id=user.get("tg_user_id"),
        is_active=bool(user["is_active"]),
        ai_quota_monthly=user["ai_quota_monthly"],
        ai_quota_used=user["ai_quota_used"],
        ai_quota_reset_at=float(user["ai_quota_reset_at"]),
        created_at=float(user["created_at"]),
    )


@router.get("", response_model=UserProfile)
async def get_me(app_user_id: Annotated[int, Depends(require_active_user)]):
    user = await _REPO.get_by_id(app_user_id)
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    return _to_profile(user)


@router.patch("", response_model=UserProfile)
async def update_me(
    body: UpdateProfileRequest,
    app_user_id: Annotated[int, Depends(require_active_user)],
):
    await _REPO.update_profile(
        app_user_id,
        display_name=body.display_name,
        avatar_url=body.avatar_url,
    )
    user = await _REPO.get_by_id(app_user_id)
    return _to_profile(user)


@router.post("/change-password", status_code=status.HTTP_204_NO_CONTENT)
async def change_password(
    body: ChangePasswordRequest,
    app_user_id: Annotated[int, Depends(require_active_user)],
):
    user = await _REPO.get_by_id(app_user_id)
    if not verify_password(body.old_password, user["password_hash"]):
        raise HTTPException(status_code=400, detail="原密码错误")
    if body.old_password == body.new_password:
        raise HTTPException(status_code=400, detail="新密码不能与原密码相同")
    await _REPO.update_password(app_user_id, hash_password(body.new_password))
    await _REPO.revoke_all_refresh_tokens(app_user_id)


@router.post("/forgot-password")
async def forgot_password(body: ForgotPasswordRequest):
    user = await _REPO.get_by_email(body.email)
    if user and user["is_active"]:
        code = await _REPO.create_verify_code(user["id"], purpose="reset")
        await send_reset_code(body.email, code)
    return {"message": "若邮箱已注册，重置验证码已发送。"}


@router.post("/reset-password", status_code=status.HTTP_204_NO_CONTENT)
async def reset_password(body: ResetPasswordRequest):
    user = await _REPO.get_by_email(body.email)
    if not user:
        raise HTTPException(status_code=400, detail="验证码无效或已过期")

    ok = await _REPO.consume_verify_code(user["id"], body.code, purpose="reset")
    if not ok:
        raise HTTPException(status_code=400, detail="验证码无效或已过期")

    await _REPO.update_password(user["id"], hash_password(body.new_password))
    await _REPO.revoke_all_refresh_tokens(user["id"])


@router.post("/logout-all", status_code=status.HTTP_204_NO_CONTENT)
async def logout_all(app_user_id: Annotated[int, Depends(require_auth)]):
    """退出全部设备（吊销该账号所有 refresh token）。"""
    await _REPO.revoke_all_refresh_tokens(app_user_id)


@router.post("/tg-bind/request", response_model=TgBindRequestResponse)
async def request_tg_bind(
    app_user_id: Annotated[int, Depends(require_active_user)],
):
    user = await _REPO.get_by_id(app_user_id)
    if user.get("tg_user_id"):
        raise HTTPException(
            status_code=400,
            detail=f"已绑定 Telegram 账号 #{user['tg_user_id']}，请先解绑再重新绑定",
        )
    code = await _REPO.create_bind_code(app_user_id)
    return TgBindRequestResponse(code=code, expires_in=600)


@router.delete("/tg-bind", status_code=status.HTTP_204_NO_CONTENT)
async def delete_tg_bind(
    app_user_id: Annotated[int, Depends(require_active_user)],
):
    await _REPO.unbind_tg(app_user_id)


@router.post("/internal/tg-bind", status_code=status.HTTP_204_NO_CONTENT)
async def internal_tg_bind(
    tg_user_id: int,
    code: str,
    internal_secret: str,
):
    """
    Bot 内部调用：核销绑定码完成绑定。
    用 INTERNAL_API_SECRET 鉴权，不走 JWT。
    """
    import os
    secret = os.getenv("INTERNAL_API_SECRET", "")
    if not secret or internal_secret != secret:
        raise HTTPException(status_code=403, detail="Forbidden")

    app_user_id = await _REPO.consume_bind_code(code, tg_user_id)
    if app_user_id is None:
        raise HTTPException(
            status_code=400,
            detail="绑定码无效、已过期，或该 Telegram 账号已绑定其他账号",
        )
