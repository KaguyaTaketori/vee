# shared/services/bot_permissions.py
"""
Bot 侧功能权限校验装饰器
========================

与 FastAPI 端的 require_permission 对称，
为 Telegram Bot handler 提供声明式权限拦截。

使用方式
--------
# 方式一：装饰器（最简）
@require_bot_permission("bot_receipt")
async def handle_bill_photo(update, context):
    ...

# 方式二：在函数内手动调用（适合需要自定义错误消息的场景）
async def handle_bill_photo(update, context):
    if not await check_bot_permission(update, "bot_receipt"):
        return
    ...

权限豁免
--------
- ADMIN_IDS 中的用户自动跳过权限检查（与 FastAPI 端一致）
- is_active = 0（封禁用户）在 AuthMiddleware 层面已被拦截，此处无需重复检查

与 AuthMiddleware 的关系
------------------------
中间件管道：AuthMiddleware（白名单） → RateLimitMiddleware（限流）
本模块在管道 **通过后** 做功能级细粒度拦截，二者互不干扰。
"""
from __future__ import annotations

import json
import logging
from functools import wraps
from typing import Callable

from config import ADMIN_IDS
from utils.i18n import t

logger = logging.getLogger(__name__)

# 权限标识 → 用户友好说明（用于 Bot 侧错误提示）
_PERM_LABELS: dict[str, str] = {
    "bot_text":     "文字记账",
    "bot_receipt":  "图片收据识别",
    "bot_voice":    "语音记账",
    "bot_download": "文件下载",
    "app_ocr":      "拍照识别",
    "app_export":   "数据导出",
    "app_upload":   "图片上传",
}


async def _get_user_permissions(tg_user_id: int) -> list[str]:
    """
    从 DB 查询用户权限列表。
    内部函数，通过 tg_user_id 查找。
    """
    from shared.repositories.user_repo import UserRepository
    repo = UserRepository()
    user = await repo.get_by_tg_id(tg_user_id)
    if not user:
        return []
    raw = user.get("permissions") or "[]"
    try:
        result = json.loads(raw)
        return result if isinstance(result, list) else []
    except (ValueError, TypeError):
        return []


async def _is_admin(tg_user_id: int) -> bool:
    """检查是否为管理员（ADMIN_IDS 配置 或 DB role == 'admin'）。"""
    # 优先走 ADMIN_IDS 内存检查（无 DB 开销）
    if ADMIN_IDS and tg_user_id in ADMIN_IDS:
        return True
    from shared.repositories.user_repo import UserRepository
    user = await UserRepository().get_by_tg_id(tg_user_id)
    return bool(user and user.get("role") == "admin")


async def check_bot_permission(update, perm: str) -> bool:
    """
    检查发消息用户是否拥有 perm 权限。

    返回 True 表示允许，False 表示已发送拒绝消息（handler 直接 return 即可）。

    Parameters
    ----------
    update : telegram.Update
    perm   : 权限标识字符串
    """
    msg = update.message or (
        update.callback_query.message if update.callback_query else None
    )
    if msg is None:
        return False

    user = (
        update.message.from_user
        if update.message
        else update.callback_query.from_user
    )
    tg_user_id = user.id

    # 管理员免检
    if await _is_admin(tg_user_id):
        return True

    perms = await _get_user_permissions(tg_user_id)
    if perm in perms:
        return True

    # 权限不足：发送友好提示
    label = _PERM_LABELS.get(perm, perm)
    try:
        if update.message:
            await update.message.reply_text(
                f"❌ 您暂无使用「{label}」功能的权限。\n"
                "如需开通，请联系管理员。"
            )
        elif update.callback_query:
            await update.callback_query.answer(
                f"您暂无「{label}」权限", show_alert=True
            )
    except Exception as e:
        logger.warning("发送权限拒绝消息失败: %s", e)

    logger.info(
        "Bot 权限拒绝: tg_user_id=%s perm=%s", tg_user_id, perm
    )
    return False


def require_bot_permission(perm: str) -> Callable:
    """
    装饰器工厂。在 handler 入口处检查权限，无权限则静默返回。

    用法：
        @require_bot_permission("bot_receipt")
        @require_message
        async def handle_bill_photo(update, context):
            ...

    注意：应放在 @require_message 等装饰器的 **外层**（即先执行）。
    """
    def decorator(func: Callable) -> Callable:
        @wraps(func)
        async def wrapper(update, context):
            if not await check_bot_permission(update, perm):
                return
            return await func(update, context)
        return wrapper
    return decorator
