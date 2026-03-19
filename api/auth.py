"""
api/auth.py
───────────
JWT 认证层。

Token 结构：{ "sub": "<user_id>", "exp": <unix_ts> }

使用方式：
    在需要鉴权的路由注入 current_user_id:
        async def route(user_id: int = Depends(require_auth)):
            ...

Token 获取：
    POST /auth/token  { "user_id": 123, "secret": "<API_SECRET>" }
    → { "access_token": "...", "token_type": "bearer" }

生产环境注意：
    - API_SECRET 通过 .env 注入，不要硬编码
    - 可替换为 Telegram Login Widget 方案，无需共享 secret
"""
from __future__ import annotations

import os
import time
from typing import Annotated

import jwt
from dotenv import load_dotenv

load_dotenv()
from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

# ── 配置 ──────────────────────────────────────────────────────────────────

_JWT_SECRET: str = os.getenv("API_JWT_SECRET", "change-me-in-production")
_JWT_ALGORITHM: str = "HS256"
_TOKEN_TTL_SECONDS: int = int(os.getenv("API_TOKEN_TTL", str(30 * 24 * 3600)))  # 30天
_API_SECRET: str = os.getenv("API_SECRET", "")  # 换 token 时校验身份

_bearer = HTTPBearer(auto_error=False)


# ── Token 签发 ─────────────────────────────────────────────────────────────

def create_access_token(user_id: int) -> str:
    payload = {
        "sub": str(user_id),
        "exp": int(time.time()) + _TOKEN_TTL_SECONDS,
        "iat": int(time.time()),
    }
    return jwt.encode(payload, _JWT_SECRET, algorithm=_JWT_ALGORITHM)


# ── FastAPI 依赖 ───────────────────────────────────────────────────────────

async def require_auth(
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(_bearer)],
) -> int:
    """
    从 Authorization: Bearer <token> 中解出 user_id。
    校验失败统一返回 401。
    """
    _unauthorized = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Invalid or expired token",
        headers={"WWW-Authenticate": "Bearer"},
    )

    if credentials is None:
        raise _unauthorized

    try:
        payload = jwt.decode(
            credentials.credentials,
            _JWT_SECRET,
            algorithms=[_JWT_ALGORITHM],
        )
        user_id = int(payload["sub"])
    except (jwt.ExpiredSignatureError, jwt.DecodeError, KeyError, ValueError):
        raise _unauthorized

    return user_id
