# api/security.py
"""
密码哈希（passlib/bcrypt）+ JWT 签发/校验。
Access Token：15分钟，payload = {"sub": str(app_user_id), "type": "access"}
Refresh Token：由 AppUserRepository 生成明文、存 hash，此处不签发。
"""
from __future__ import annotations

import os
import time
from typing import Optional

import jwt
from passlib.context import CryptContext

# ── 密码 ──────────────────────────────────────────────────────────────────

_pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")


def hash_password(plain: str) -> str:
    return _pwd_context.hash(plain)


def verify_password(plain: str, hashed: str) -> bool:
    return _pwd_context.verify(plain, hashed)


# ── JWT ───────────────────────────────────────────────────────────────────

_JWT_SECRET: str    = os.getenv("API_JWT_SECRET", "change-me-in-production")
_JWT_ALGORITHM      = "HS256"
_ACCESS_TOKEN_TTL   = 15 * 60      # 15 分钟（秒）


def create_access_token(app_user_id: int) -> str:
    now = int(time.time())
    payload = {
        "sub":  str(app_user_id),
        "type": "access",
        "iat":  now,
        "exp":  now + _ACCESS_TOKEN_TTL,
    }
    return jwt.encode(payload, _JWT_SECRET, algorithm=_JWT_ALGORITHM)


def decode_access_token(token: str) -> Optional[int]:
    """
    校验 Access Token，成功返回 app_user_id，失败返回 None。
    不抛异常——调用方统一处理 None。
    """
    try:
        payload = jwt.decode(token, _JWT_SECRET, algorithms=[_JWT_ALGORITHM])
        if payload.get("type") != "access":
            return None
        return int(payload["sub"])
    except (jwt.ExpiredSignatureError, jwt.DecodeError, KeyError, ValueError):
        return None
