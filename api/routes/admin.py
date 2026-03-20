# api/routes/admin.py
"""
管理员专属 API
==============

所有路由均通过 require_admin 依赖保护，非管理员访问返回 403。

路由列表
--------
用户管理：
  GET    /v1/admin/users                     获取用户列表
  GET    /v1/admin/users/{user_id}           获取单个用户详情
  PATCH  /v1/admin/users/{user_id}/active    封禁 / 解封
  PATCH  /v1/admin/users/{user_id}/role      修改角色
  PATCH  /v1/admin/users/{user_id}/permissions  修改功能权限
  PATCH  /v1/admin/users/{user_id}/quota     修改 AI 配额

全局统计：
  GET    /v1/admin/stats                     全局数据统计

系统配置：
  GET    /v1/admin/configs                   获取全部配置
  GET    /v1/admin/configs/{key}             获取单条配置
  PUT    /v1/admin/configs/{key}             新增 / 更新单条配置
  DELETE /v1/admin/configs/{key}             删除配置
  POST   /v1/admin/configs/batch             批量更新配置

WS 状态：
  GET    /v1/admin/ws/stats                  WebSocket 连接统计
  POST   /v1/admin/ws/push/{user_id}         向指定用户推送测试消息
"""
from __future__ import annotations

import json
import logging
import time
from datetime import date
from typing import Annotated, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, status

from api.dependencies.permissions import require_admin, AdminUser
from api.schemas_admin import (
    AdminUserOut, AdminUserListResponse,
    SetActiveRequest, SetRoleRequest, SetPermissionsRequest, SetQuotaRequest,
    GlobalStatsOut,
    SystemConfigOut, SystemConfigListResponse,
    UpsertConfigRequest, BatchUpsertConfigRequest,
)
from shared.repositories.user_repo import UserRepository
from shared.repositories.system_config_repo import SystemConfigRepository

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/admin", tags=["admin"])

_user_repo   = UserRepository()
_config_repo = SystemConfigRepository()


# ============================================================
# 用户管理
# ============================================================

@router.get("/users", response_model=AdminUserListResponse)
async def list_users(
    admin_id: AdminUser,
    page:      int           = Query(1, ge=1),
    page_size: int           = Query(50, ge=1, le=200),
    keyword:   Optional[str] = Query(None, description="邮箱/用户名/昵称模糊搜索"),
    role:      Optional[str] = Query(None, description="user | admin"),
    is_active: Optional[int] = Query(None, description="1=正常 0=封禁"),
):
    """获取全部用户列表（支持搜索、角色/状态过滤、分页）。"""
    rows, total = await _user_repo.list_all_for_admin(
        page=page,
        page_size=page_size,
        keyword=keyword.strip() if keyword else None,
        role=role,
        is_active=is_active,
    )
    return AdminUserListResponse(
        users=[AdminUserOut.from_row(r) for r in rows],
        total=total,
        page=page,
        page_size=page_size,
        has_next=(page * page_size) < total,
    )


@router.get("/users/{user_id}", response_model=AdminUserOut)
async def get_user(
    user_id: int,
    admin_id: AdminUser,
):
    """获取单个用户详情（含 IP、权限、配额等全部字段）。"""
    user = await _user_repo.get_by_id(user_id)
    if not user:
        raise HTTPException(status_code=404, detail="用户不存在")
    return AdminUserOut.from_row(user)


@router.patch("/users/{user_id}/active", status_code=status.HTTP_204_NO_CONTENT)
async def set_user_active(
    user_id: int,
    body: SetActiveRequest,
    admin_id: AdminUser,
):
    """封禁（is_active=false）或解封（is_active=true）用户。"""
    # 不允许管理员封禁自己
    if user_id == admin_id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="不能封禁自己的账号",
        )
    ok = await _user_repo.set_active(user_id, 1 if body.is_active else 0)
    if not ok:
        raise HTTPException(status_code=404, detail="用户不存在")
    action = "解封" if body.is_active else "封禁"
    logger.info("管理员 %s %s 用户 %s", admin_id, action, user_id)

    # 若封禁，主动断开该用户的所有 WS 连接
    if not body.is_active:
        try:
            from shared.services.container import services
            if services.ws_manager and services.ws_manager.is_online(user_id):
                await services.ws_manager.push_to_user(
                    user_id, "force_logout",
                    {"reason": "账号已被管理员封禁"}
                )
        except Exception as e:
            logger.warning("推送封禁通知失败: %s", e)


@router.patch("/users/{user_id}/role", status_code=status.HTTP_204_NO_CONTENT)
async def set_user_role(
    user_id: int,
    body: SetRoleRequest,
    admin_id: AdminUser,
):
    """修改用户角色（user / admin）。"""
    if user_id == admin_id and body.role != "admin":
        raise HTTPException(
            status_code=400, detail="不能降级自己的管理员权限"
        )
    user = await _user_repo.get_by_id(user_id)
    if not user:
        raise HTTPException(status_code=404, detail="用户不存在")
    await _user_repo.set_role(user_id, body.role)
    logger.info(
        "管理员 %s 修改用户 %s 角色: %s → %s",
        admin_id, user_id, user.get("role"), body.role,
    )


@router.patch("/users/{user_id}/permissions", response_model=AdminUserOut)
async def set_user_permissions(
    user_id: int,
    body: SetPermissionsRequest,
    admin_id: AdminUser,
):
    """
    覆盖写入用户的功能权限列表。

    可用权限标识：
    - bot_text      Bot 文字记账
    - bot_receipt   Bot 图片识别
    - bot_voice     Bot 语音记账（预留）
    - bot_download  Bot 下载功能
    - app_ocr       App 拍照识别
    - app_export    数据导出（预留）
    - app_upload    App 上传凭证
    """
    user = await _user_repo.get_by_id(user_id)
    if not user:
        raise HTTPException(status_code=404, detail="用户不存在")

    await _user_repo.set_permissions(user_id, body.permissions)
    logger.info(
        "管理员 %s 更新用户 %s 权限: %s",
        admin_id, user_id, body.permissions,
    )

    # 实时推送权限变更（让 App 端即时更新 UI 状态）
    try:
        from shared.services.container import services
        if services.ws_manager and services.ws_manager.is_online(user_id):
            await services.ws_manager.push_to_user(
                user_id, "permissions_updated",
                {"permissions": body.permissions},
            )
    except Exception as e:
        logger.warning("权限变更推送失败: %s", e)

    updated = await _user_repo.get_by_id(user_id)
    return AdminUserOut.from_row(updated)


@router.patch("/users/{user_id}/quota", status_code=status.HTTP_204_NO_CONTENT)
async def set_user_quota(
    user_id: int,
    body: SetQuotaRequest,
    admin_id: AdminUser,
):
    """修改用户 AI 月配额（-1 表示无限制）。"""
    user = await _user_repo.get_by_id(user_id)
    if not user:
        raise HTTPException(status_code=404, detail="用户不存在")
    from database.db import get_db
    async with get_db() as db:
        await db.execute(
            "UPDATE users SET ai_quota_monthly = ?, updated_at = ? WHERE id = ?",
            (body.ai_quota_monthly, int(time.time()), user_id),
        )
        await db.commit()
    logger.info(
        "管理员 %s 修改用户 %s AI 配额: %s",
        admin_id, user_id, body.ai_quota_monthly,
    )


# ============================================================
# 全局统计
# ============================================================

@router.get("/stats", response_model=GlobalStatsOut)
async def global_stats(admin_id: AdminUser):
    """
    返回系统全局数据统计：
    - 用户总数 / 活跃数 / 管理员数
    - 账单总数 / 本月账单数
    - 本月 AI 配额消耗总量
    - 当前 WS 在线用户数 / 连接数
    """
    from database.db import get_db

    async with get_db() as db:
        # 用户统计
        async with db.execute("SELECT COUNT(*) FROM users") as cur:
            total_users = (await cur.fetchone())[0]

        async with db.execute(
            "SELECT COUNT(*) FROM users WHERE is_active = 1"
        ) as cur:
            active_users = (await cur.fetchone())[0]

        async with db.execute(
            "SELECT COUNT(*) FROM users WHERE role = 'admin'"
        ) as cur:
            admin_count = (await cur.fetchone())[0]

        # 账单统计
        async with db.execute("SELECT COUNT(*) FROM bills") as cur:
            total_bills = (await cur.fetchone())[0]

        today = date.today()
        month_prefix = f"{today.year:04d}-{today.month:02d}%"
        async with db.execute(
            "SELECT COUNT(*) FROM bills WHERE bill_date LIKE ?",
            (month_prefix,),
        ) as cur:
            bills_this_month = (await cur.fetchone())[0]

        # 本月 AI 配额消耗（所有用户 ai_quota_used 之和，简化统计）
        async with db.execute(
            "SELECT COALESCE(SUM(ai_quota_used), 0) FROM users"
        ) as cur:
            ai_quota_used_this_month = (await cur.fetchone())[0]

    # WS 连接统计
    ws_stats = {"online_users": 0, "total_connections": 0}
    try:
        from shared.services.container import services
        if services.ws_manager:
            ws_stats = services.ws_manager.stats()
    except Exception:
        pass

    return GlobalStatsOut(
        total_users=total_users,
        active_users=active_users,
        admin_count=admin_count,
        total_bills=total_bills,
        bills_this_month=bills_this_month,
        ai_quota_used_this_month=ai_quota_used_this_month,
        online_ws_users=ws_stats.get("online_users", 0),
        total_ws_connections=ws_stats.get("total_connections", 0),
    )


# ============================================================
# 系统配置 CRUD
# ============================================================

@router.get("/configs", response_model=SystemConfigListResponse)
async def list_configs(admin_id: AdminUser):
    """获取全部系统配置项（含描述、最后更新时间）。"""
    rows = await _config_repo.get_all_with_meta()
    return SystemConfigListResponse(
        configs=[
            SystemConfigOut(
                config_key=r["config_key"],
                config_value=r["config_value"],
                description=r.get("description") or "",
                updated_by=r.get("updated_by"),
                updated_at=float(r.get("updated_at") or 0),
            )
            for r in rows
        ]
    )


@router.get("/configs/{key}", response_model=SystemConfigOut)
async def get_config(key: str, admin_id: AdminUser):
    """获取单条系统配置。"""
    rows = await _config_repo.get_all_with_meta()
    for r in rows:
        if r["config_key"] == key:
            return SystemConfigOut(
                config_key=r["config_key"],
                config_value=r["config_value"],
                description=r.get("description") or "",
                updated_by=r.get("updated_by"),
                updated_at=float(r.get("updated_at") or 0),
            )
    raise HTTPException(status_code=404, detail=f"配置项 '{key}' 不存在")


@router.put("/configs/{key}", response_model=SystemConfigOut)
async def upsert_config(
    key: str,
    body: UpsertConfigRequest,
    admin_id: AdminUser,
):
    """新增或更新单条系统配置。"""
    await _config_repo.set(
        key,
        body.config_value,
        description=body.description,
        updated_by=admin_id,
    )
    logger.info("管理员 %s 更新配置 [%s]", admin_id, key)

    # 特殊配置变更时实时刷新相关模块
    _handle_config_side_effect(key, body.config_value)

    rows = await _config_repo.get_all_with_meta()
    for r in rows:
        if r["config_key"] == key:
            return SystemConfigOut(
                config_key=r["config_key"],
                config_value=r["config_value"],
                description=r.get("description") or "",
                updated_by=r.get("updated_by"),
                updated_at=float(r.get("updated_at") or 0),
            )


@router.delete("/configs/{key}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_config(key: str, admin_id: AdminUser):
    """删除配置项（内置默认项建议不删除，会在下次重启时重新创建）。"""
    deleted = await _config_repo.delete(key)
    if not deleted:
        raise HTTPException(status_code=404, detail=f"配置项 '{key}' 不存在")
    logger.info("管理员 %s 删除配置 [%s]", admin_id, key)


@router.post("/configs/batch", response_model=SystemConfigListResponse)
async def batch_upsert_configs(
    body: BatchUpsertConfigRequest,
    admin_id: AdminUser,
):
    """
    批量更新配置项。

    请求体示例：
    {
      "configs": {
        "registration_open": "false",
        "max_bills_per_user": "1000"
      }
    }
    """
    for key, value in body.configs.items():
        await _config_repo.set(key, value, updated_by=admin_id)
        _handle_config_side_effect(key, value)

    logger.info(
        "管理员 %s 批量更新 %d 条配置: %s",
        admin_id, len(body.configs), list(body.configs.keys()),
    )
    rows = await _config_repo.get_all_with_meta()
    return SystemConfigListResponse(
        configs=[
            SystemConfigOut(
                config_key=r["config_key"],
                config_value=r["config_value"],
                description=r.get("description") or "",
                updated_by=r.get("updated_by"),
                updated_at=float(r.get("updated_at") or 0),
            )
            for r in rows
        ]
    )


def _handle_config_side_effect(key: str, value: str) -> None:
    """
    特定配置变更时的副作用处理（非阻塞，失败仅记录日志）。
    """
    try:
        if key == "ai_default_model":
            # 触发 LLM manager 切换 provider（如果 value 是已配置的 provider 名）
            import shared.integrations.llm.manager as llm_mod
            if llm_mod.llm_manager and value in llm_mod.llm_manager._states:
                llm_mod.llm_manager.switch_provider(value)
                logger.info("LLM provider 已切换为: %s", value)
    except Exception as e:
        logger.warning("配置副作用处理失败 key=%s: %s", key, e)


# ============================================================
# WebSocket 管理
# ============================================================

@router.get("/ws/stats")
async def ws_stats(admin_id: AdminUser):
    """查看当前 WebSocket 连接统计。"""
    from shared.services.container import services
    if not services.ws_manager:
        return {"online_users": 0, "total_connections": 0}
    return services.ws_manager.stats()


@router.post("/ws/push/{user_id}", status_code=status.HTTP_200_OK)
async def push_to_user(
    user_id: int,
    admin_id: AdminUser,
    message: str = Query(..., description="推送的文本内容"),
):
    """向指定用户推送系统通知（测试 / 公告用）。"""
    from shared.services.container import services
    if not services.ws_manager:
        raise HTTPException(status_code=503, detail="WS 服务未初始化")

    pushed = await services.ws_manager.push_to_user(
        user_id,
        event_type="system_notice",
        data={"message": message, "from_admin": admin_id},
    )
    return {
        "pushed_connections": pushed,
        "is_online": services.ws_manager.is_online(user_id),
    }
