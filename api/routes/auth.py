# api/routes/auth.py
"""
POST /v1/auth/register
POST /v1/auth/verify-email
POST /v1/auth/resend-code
POST /v1/auth/login
POST /v1/auth/refresh
POST /v1/auth/logout
"""
from __future__ import annotations

import logging
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request, status

from api.auth import require_auth
from api.mailer import send_activation_code
from api.schemas import (
    LoginRequest, LoginResponse,
    LogoutRequest, RefreshRequest,
    RegisterRequest, ResendCodeRequest,
    TokenResponse, VerifyEmailRequest,
)
from api.security import (
    create_access_token,
    hash_password,
    verify_password,
)
from repositories import UserRepository

_REPO = UserRepository()

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/auth", tags=["auth"])


def _device_hint(request: Request) -> str:
    ua = request.headers.get("user-agent", "")
    return ua[:48]


# ── 注册 ──────────────────────────────────────────────────────────────────

@router.post("/register", status_code=status.HTTP_201_CREATED)
async def register(body: RegisterRequest):
    if await _REPO.get_by_email(body.email):
        raise HTTPException(status_code=409, detail="该邮箱已被注册")
    if await _REPO.get_by_app_username(body.username):   # ← 方法名变化
        raise HTTPException(status_code=409, detail="该用户名已被使用")

    pw_hash = hash_password(body.password)
    user_id = await _REPO.create_app_user(               # ← 方法名变化
        app_username=body.username,
        email=body.email,
        password_hash=pw_hash,
        display_name=body.display_name or body.username,
    )

    code = await _REPO.create_verify_code(user_id, purpose="activation")
    await send_activation_code(body.email, code)

    response: dict = {
        "message": "注册成功，验证码已发送到您的邮箱，请在 10 分钟内完成验证。",
        "email": body.email,
    }
    import os
    if os.getenv("APP_ENV", "production") == "development":
        response["debug_code"] = code
    return response

# ── 验证邮箱 ──────────────────────────────────────────────────────────────

@router.post("/verify-email")
async def verify_email(body: VerifyEmailRequest, request: Request):
    user = await _REPO.get_by_email(body.email)
    if not user:
        raise HTTPException(status_code=404, detail="邮箱不存在")
    if user["is_active"]:
        raise HTTPException(status_code=400, detail="账号已激活，请直接登录")

    ok = await _REPO.consume_verify_code(user["id"], body.code, purpose="activation")
    if not ok:
        raise HTTPException(status_code=400, detail="验证码错误或已过期")

    await _REPO.activate(user["id"])

    access_token  = create_access_token(user["id"])
    refresh_token = await _REPO.create_refresh_token(
        user["id"], device_hint=_device_hint(request)
    )
    return LoginResponse(access_token=access_token, refresh_token=refresh_token)

# ── 重发验证码 ────────────────────────────────────────────────────────────

@router.post("/resend-code")
async def resend_code(body: ResendCodeRequest):
    user = await _REPO.get_by_email(body.email)

    response: dict = {"message": "若邮箱已注册且未激活，验证码已重新发送。"}

    if user and not user["is_active"]:
        code = await _REPO.create_verify_code(user["id"], purpose="activation")
        await send_activation_code(body.email, code)

        import os
        if os.getenv("APP_ENV", "production") == "development":
            response["debug_code"] = code

    return response


# ── 登录 ──────────────────────────────────────────────────────────────────

@router.post("/login", response_model=LoginResponse)
async def login(body: LoginRequest, request: Request):
    user = await _REPO.get_by_identifier(body.identifier)
    if not user or not verify_password(body.password, user["password_hash"]):
        raise HTTPException(status_code=401, detail="用户名/邮箱或密码错误")
    if not user["is_active"]:
        raise HTTPException(status_code=403, detail="账号未激活，请先验证邮箱")

    access_token  = create_access_token(user["id"])
    refresh_token = await _REPO.create_refresh_token(
        user["id"], device_hint=_device_hint(request)
    )
    return LoginResponse(access_token=access_token, refresh_token=refresh_token)

# ── 刷新 Token ────────────────────────────────────────────────────────────

@router.post("/refresh", response_model=LoginResponse)
async def refresh_token(body: RefreshRequest, request: Request):
    result = await _REPO.verify_and_rotate_refresh_token(
        body.refresh_token, device_hint=_device_hint(request)
    )
    if result is None:
        raise HTTPException(status_code=401, detail="Refresh token 无效或已过期")
    user_id, new_refresh = result
    return LoginResponse(
        access_token=create_access_token(user_id),
        refresh_token=new_refresh,
    )



# ── 登出 ──────────────────────────────────────────────────────────────────

@router.post("/logout", status_code=status.HTTP_204_NO_CONTENT)
async def logout(body: LogoutRequest):
    await _REPO.revoke_refresh_token(body.refresh_token)
