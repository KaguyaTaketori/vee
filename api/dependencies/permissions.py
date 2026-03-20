# api/dependencies/permissions.py
"""
FastAPI 权限拦截依赖
====================

提供三个可组合的鉴权依赖，按严格程度递增：

  1. require_active_user   ← 已有，账号激活即可（不改动）
  2. require_permission    ← 新增，检查 permissions 字段中的功能标识
  3. require_admin         ← 新增，检查 role == 'admin'

使用示例
--------
# 只需登录激活
@router.get("/me")
async def get_me(user_id: int = Depends(require_active_user)):
    ...

# 需要特定功能权限
@router.post("/bills/ocr")
async def ocr_bill(
    user_id: int = Depends(require_permission("app_ocr")),
):
    ...

# 需要管理员身份
@router.get("/admin/users")
async def list_users(
    user_id: int = Depends(require_admin),
):
    ...

权限标识符约定
--------------
Bot 侧：
  bot_text          文字记账（/bill 命令 + 账单文本触发）
  bot_receipt       图片收据识别（发图片触发）
  bot_voice         语音记账（预留）
  bot_download      下载功能（downloader 模块）

App 侧：
  app_ocr           App 拍照 OCR 识别
  app_export        数据导出（预留）
  app_upload        上传凭证图片
"""
from __future__ import annotations

import json
import logging
from functools import lru_cache
from typing import Annotated, Callable

from fastapi import Depends, HTTPException, status

from api.auth import require_active_user
from shared.repositories.user_repo import UserRepository
from api.dependencies.client_ip import get_client_ip, ClientIP

logger = logging.getLogger(__name__)

_repo = UserRepository()


# ---------------------------------------------------------------------------
# 内部辅助：获取用户行（带缓存避免同一请求多次查 DB）
# ---------------------------------------------------------------------------

async def _get_user_row(
    user_id: int = Depends(require_active_user),
) -> dict:
    """从 DB 拿用户完整行，找不到则 403。"""
    user = await _repo.get_by_id(user_id)
    if not user:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN,
                            detail="用户不存在")
    return user


# ---------------------------------------------------------------------------
# 1. require_permission(perm) — 工厂函数，返回依赖
# ---------------------------------------------------------------------------

def require_permission(perm: str) -> Callable:
    """
    返回一个 FastAPI 依赖，检查当前用户是否拥有 `perm` 权限。

    admin 角色自动跳过权限检查（超级权限）。

    用法：
        Depends(require_permission("app_ocr"))
    """
    async def _dependency(
        user: dict = Depends(_get_user_row),
        client_ip: ClientIP = Depends(get_client_ip),
    ) -> int:
        user_id = user["id"]

        # 管理员免检
        if user.get("role") == "admin":
            await _repo.update_last_login_ip(user_id, client_ip.address)
            return user_id

        # 解析 permissions
        raw = user.get("permissions") or "[]"
        try:
            perms: list[str] = json.loads(raw)
        except (ValueError, TypeError):
            perms = []

        if perm not in perms:
            logger.warning(
                "权限拒绝: user_id=%s perm=%s ip=%s",
                user_id, perm, client_ip.address,
            )
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"您暂无使用此功能的权限（需要：{perm}）",
            )

        # 更新最后登录 IP（有效请求才更新）
        await _repo.update_last_login_ip(user_id, client_ip.address)
        return user_id

    # 让 FastAPI 能识别不同权限的依赖为不同对象
    _dependency.__name__ = f"require_permission_{perm}"
    return _dependency


# ---------------------------------------------------------------------------
# 2. require_admin — 直接可用的依赖
# ---------------------------------------------------------------------------

async def require_admin(
    user: dict = Depends(_get_user_row),
    client_ip: ClientIP = Depends(get_client_ip),
) -> int:
    """
    确保当前用户 role == 'admin'，否则 403。
    同时更新 last_login_ip。
    """
    user_id = user["id"]

    if user.get("role") != "admin":
        logger.warning(
            "非法管理员访问: user_id=%s ip=%s",
            user_id, client_ip.address,
        )
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="此接口仅限管理员访问",
        )

    await _repo.update_last_login_ip(user_id, client_ip.address)
    return user_id


# ---------------------------------------------------------------------------
# 3. 登录时自动记录 IP（在 auth 路由中调用，非依赖）
# ---------------------------------------------------------------------------

async def record_login_ip(user_id: int, ip: str) -> None:
    """
    登录成功后调用：
    - 首次时写入 registration_ip
    - 每次写入 last_login_ip
    """
    await _repo.set_registration_ip(user_id, ip)
    await _repo.update_last_login_ip(user_id, ip)


# 类型别名方便路由标注
AdminUser = Annotated[int, Depends(require_admin)]
