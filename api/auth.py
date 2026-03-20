from __future__ import annotations

from typing import Annotated, Optional

from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from api.security import decode_access_token

_bearer = HTTPBearer(auto_error=False)

_UNAUTHORIZED = HTTPException(
    status_code=status.HTTP_401_UNAUTHORIZED,
    detail="Invalid or expired token",
    headers={"WWW-Authenticate": "Bearer"},
)


async def require_auth(
    credentials: Annotated[Optional[HTTPAuthorizationCredentials], Depends(_bearer)],
) -> int:
    """
    解析 Bearer Access Token → app_user_id。
    所有受保护路由注入此依赖即可。
    """
    if credentials is None:
        raise _UNAUTHORIZED
    user_id = decode_access_token(credentials.credentials)
    if user_id is None:
        raise _UNAUTHORIZED
    return user_id


async def require_active_user(
    app_user_id: Annotated[int, Depends(require_auth)],
) -> int:
    """
    在 require_auth 基础上额外校验账号是否激活。
    账单、OCR 等业务接口使用此依赖。
    """
    from repositories import AppUserRepository
    user = await AppUserRepository().get_by_id(app_user_id)
    if not user or not user["is_active"]:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Account not activated. Please verify your email.",
        )
    return app_user_id
