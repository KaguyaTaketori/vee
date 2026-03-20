# scripts/verify_migration_v5.py
"""
迁移后数据完整性验证。
对比迁移前记录的基准数据与迁移后的实际数据。
"""
from __future__ import annotations

import asyncio
import sys
import os
import logging

_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)

# 把迁移前记下来的数字填进来
BEFORE = {
    "users":          3,    # 填实际数字
    "app_users":       1,
    "bills":         3,
    "app_bills":      0,
    "bill_items":    22,
    "app_bill_items": 6,
    "history": 38,
    "rate_limit": 2,
    "tasks": 63,
}


async def main():
    from database.db import get_db

    async with get_db() as db:

        # 1. users 表核对
        async with db.execute("SELECT COUNT(*) FROM users") as cur:
            total_users = (await cur.fetchone())[0]
        async with db.execute(
            "SELECT COUNT(*) FROM users WHERE tg_user_id IS NOT NULL"
        ) as cur:
            tg_users = (await cur.fetchone())[0]
        async with db.execute(
            "SELECT COUNT(*) FROM users WHERE email IS NOT NULL"
        ) as cur:
            app_users = (await cur.fetchone())[0]
        async with db.execute(
            "SELECT COUNT(*) FROM users WHERE tg_user_id IS NOT NULL AND email IS NOT NULL"
        ) as cur:
            bound_users = (await cur.fetchone())[0]

        logger.info("=== users 表 ===")
        logger.info("  总行数: %d", total_users)
        logger.info("  含 tg_user_id (原 Bot 用户): %d", tg_users)
        logger.info("  含 email (原 App 用户): %d", app_users)
        logger.info("  同时有两者 (已绑定): %d", bound_users)
        logger.info(
            "  预期: 原 TG(%d) + 未绑定 App(%d) = %d",
            BEFORE["users"],
            BEFORE["app_users"] - bound_users,
            BEFORE["users"] + BEFORE["app_users"] - bound_users,
        )

        # 2. bills 表核对
        async with db.execute("SELECT COUNT(*) FROM bills") as cur:
            total_bills = (await cur.fetchone())[0]
        async with db.execute(
            "SELECT COUNT(*) FROM bills WHERE source = 'bot'"
        ) as cur:
            bot_bills = (await cur.fetchone())[0]
        async with db.execute(
            "SELECT COUNT(*) FROM bills WHERE source = 'app'"
        ) as cur:
            app_bills = (await cur.fetchone())[0]

        logger.info("=== bills 表 ===")
        logger.info("  总行数: %d（预期 %d）", total_bills,
                    BEFORE["bills"] + BEFORE["app_bills"])
        logger.info("  source=bot: %d（预期 %d）", bot_bills, BEFORE["bills"])
        logger.info("  source=app: %d（预期 %d）", app_bills, BEFORE["app_bills"])

        if total_bills != BEFORE["bills"] + BEFORE["app_bills"]:
            logger.error("❌ bills 总数不匹配！")
        else:
            logger.info("✅ bills 总数核对通过")

        # 3. bill_items 核对
        async with db.execute("SELECT COUNT(*) FROM bill_items") as cur:
            total_items = (await cur.fetchone())[0]

        logger.info("=== bill_items 表 ===")
        logger.info("  总行数: %d（预期 %d）",
                    total_items,
                    BEFORE["bill_items"] + BEFORE["app_bill_items"])

        if total_items != BEFORE["bill_items"] + BEFORE["app_bill_items"]:
            logger.error("❌ bill_items 总数不匹配！")
        else:
            logger.info("✅ bill_items 总数核对通过")

        # 4. 金额合理性检查（确保 int 转换没有丢失精度）
        async with db.execute(
            "SELECT COUNT(*) FROM bills WHERE amount <= 0"
        ) as cur:
            zero_amount = (await cur.fetchone())[0]
        if zero_amount > 0:
            logger.error("❌ 发现 %d 条 amount <= 0 的记录！", zero_amount)
        else:
            logger.info("✅ 金额合理性检查通过（无 <= 0 记录）")

        # 5. 外键完整性检查
        async with db.execute(
            """
            SELECT COUNT(*) FROM bills b
            LEFT JOIN users u ON b.user_id = u.id
            WHERE u.id IS NULL
            """
        ) as cur:
            orphan_bills = (await cur.fetchone())[0]
        if orphan_bills > 0:
            logger.error("❌ 发现 %d 条孤立账单（user_id 在 users 中不存在）！", orphan_bills)
        else:
            logger.info("✅ bills 外键完整性检查通过")

        async with db.execute(
            """
            SELECT COUNT(*) FROM bill_items bi
            LEFT JOIN bills b ON bi.bill_id = b.id
            WHERE b.id IS NULL
            """
        ) as cur:
            orphan_items = (await cur.fetchone())[0]
        if orphan_items > 0:
            logger.error("❌ 发现 %d 条孤立明细（bill_id 在 bills 中不存在）！", orphan_items)
        else:
            logger.info("✅ bill_items 外键完整性检查通过")

        # 6. history / rate_limit 外键检查（这两张表 user_id 存的是 tg_user_id）
        async with db.execute(
            """
            SELECT COUNT(*) FROM history h
            LEFT JOIN users u ON h.user_id = u.tg_user_id
            WHERE u.id IS NULL
            """
        ) as cur:
            orphan_history = (await cur.fetchone())[0]
        if orphan_history > 0:
            logger.warning(
                "⚠️ history 表有 %d 条记录的 user_id 在 users.tg_user_id 中找不到"
                "（可能是已删除用户，可忽略）",
                orphan_history,
            )
        else:
            logger.info("✅ history 外键检查通过")

        # 7. 旧表是否保留
        for old_table in ["users_v4", "bills_v4", "bill_items_v4"]:
            async with db.execute(
                f"SELECT name FROM sqlite_master WHERE type='table' AND name='{old_table}'"
            ) as cur:
                exists = await cur.fetchone()
            if exists:
                logger.info("📦 旧表 %s 已保留备份", old_table)
            else:
                logger.warning("⚠️ 旧表 %s 不存在（迁移脚本可能未正确保留）", old_table)


asyncio.run(main())
