# shared/services/bill_push.py
"""
账单实时推送工具
================

Bot 记账入库成功后，调用此模块向用户的 Flutter 客户端
推送新账单 JSON，无需用户手动刷新即可看到新记录。

使用方式（在 bill_callbacks.py 的 _cb_bill_confirm 中追加）：
---------------------------------------------------------------
    from shared.services.bill_push import push_new_bill

    bill_id = await _bill_repo.create(...)

    # 推送不阻塞主流程，失败仅记录日志
    await push_new_bill(user_id=user_id, bill_id=bill_id)

推送的消息格式（Flutter 端接收）：
-----------------------------------
{
  "type": "new_bill",
  "data": {
    "id": 42,
    "amount": 38.0,
    "currency": "JPY",
    "category": "餐饮",
    "merchant": "スターバックス",
    "bill_date": "2026-03-20",
    "receipt_url": "http://...",
    "items": [...],
    "created_at": 1710000000,
    "updated_at": 1710000000,
    "source": "bot"
  },
  "ts": 1710000000
}
"""
from __future__ import annotations

import logging
from typing import Optional

logger = logging.getLogger(__name__)


async def push_new_bill(user_id: int, bill_id: int) -> None:
    """
    入库成功后推送新账单至用户所有在线 WS 连接。
    失败静默处理，不影响 Bot 响应。

    Parameters
    ----------
    user_id : users.id（自增主键，非 tg_user_id）
    bill_id : bills.id
    """
    try:
        from shared.services.container import services
        if services.ws_manager is None or not services.ws_manager.is_online(user_id):
            return  # 用户不在线，跳过推送

        # 从 DB 读取完整账单（含 items，已反序列化为 float）
        from shared.repositories.bill_repo import BillRepository
        bill = await BillRepository().get_by_id(bill_id, user_id)
        if bill is None:
            return

        pushed = await services.ws_manager.push_to_user(
            user_id=user_id,
            event_type="new_bill",
            data=bill,
        )
        if pushed:
            logger.info(
                "账单实时推送成功: user_id=%s bill_id=%s connections=%d",
                user_id, bill_id, pushed,
            )
    except Exception as e:
        logger.warning(
            "账单实时推送失败（非致命）: user_id=%s bill_id=%s err=%s",
            user_id, bill_id, e,
        )


async def push_bill_deleted(user_id: int, bill_id: int) -> None:
    """账单删除后通知客户端从列表移除。"""
    try:
        from shared.services.container import services
        if services.ws_manager is None or not services.ws_manager.is_online(user_id):
            return
        await services.ws_manager.push_to_user(
            user_id=user_id,
            event_type="bill_deleted",
            data={"id": bill_id},
        )
    except Exception as e:
        logger.warning("账单删除推送失败: %s", e)


async def push_bill_updated(user_id: int, bill_id: int) -> None:
    """账单更新后推送最新内容。"""
    try:
        from shared.services.container import services
        if services.ws_manager is None or not services.ws_manager.is_online(user_id):
            return
        from shared.repositories.bill_repo import BillRepository
        bill = await BillRepository().get_by_id(bill_id, user_id)
        if bill is None:
            return
        await services.ws_manager.push_to_user(
            user_id=user_id,
            event_type="bill_updated",
            data=bill,
        )
    except Exception as e:
        logger.warning("账单更新推送失败: %s", e)


# ============================================================
# 以下是对 bill_callbacks.py 中 _cb_bill_confirm 的
# 修改说明（Diff 风格，直接粘贴替换对应位置）
# ============================================================
#
# 在 _cb_bill_confirm 中，insert_bill / _bill_repo.create 之后追加：
#
#   # ── WS 实时推送 ──────────────────────────────────────────────────────
#   from shared.services.bill_push import push_new_bill
#   await push_new_bill(user_id=user_id, bill_id=bill_id)
#
# ============================================================
# 对 api/routes/bills.py 中 delete_bill 路由的改动：
#
#   # 删除成功后追加：
#   from shared.services.bill_push import push_bill_deleted
#   await push_bill_deleted(app_user_id, bill_id)
#
# 对 patch_bill 路由追加：
#   from shared.services.bill_push import push_bill_updated
#   await push_bill_updated(app_user_id, bill_id)
# ============================================================
