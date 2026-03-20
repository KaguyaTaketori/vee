# modules/billing/utils.py
"""
Bot 侧账单模块通用工具。
核心问题：Bot 拿到的是 tg_user_id，数据库主键是 users.id（自增）。
"""
from __future__ import annotations
from typing import Optional
from shared.repositories.user_repo import UserRepository

_repo = UserRepository()


async def resolve_user_id(tg_user_id: int) -> int:
    """
    tg_user_id → users.id。
    若用户不存在则自动创建（首次发消息时触发）。
    """
    user = await _repo.get_by_tg_id(tg_user_id)
    if user:
        return user["id"]
    return await _repo.upsert_tg_user(tg_user_id)


async def get_user_row(tg_user_id: int) -> Optional[dict]:
    """返回完整的 users 行，不存在返回 None（不自动创建）。"""
    return await _repo.get_by_tg_id(tg_user_id)
