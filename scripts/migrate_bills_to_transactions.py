#!/usr/bin/env python3
"""
scripts/migrate_bills_to_transactions.py
─────────────────────────────────────────
将现有 bills / bill_items 数据迁移到新的 transactions / transaction_items 体系。

迁移策略
--------
1. 每个有 bills 记录的用户，自动创建一个默认 Group（"我的账本"）
2. 每个 Group 创建一个默认现金账户（JPY）
3. bills         → transactions  (type='expense')
4. bill_items    → transaction_items
5. bills.receipt_url → receipts（非空时）
6. 保留 legacy_bill_id 用于追溯

幂等性
------
脚本可以安全重复执行：
  - 通过 legacy_bill_id 检测是否已迁移，已迁移的跳过
  - Group / Account 通过 user_id 检测是否已创建

使用方式
--------
    # 预演（不写入数据库）
    python scripts/migrate_bills_to_transactions.py --dry-run

    # 正式执行
    python scripts/migrate_bills_to_transactions.py

    # 执行后验证
    python scripts/migrate_bills_to_transactions.py --verify
"""
from __future__ import annotations

import asyncio
import logging
import os
import secrets
import sys
import time
from typing import Optional

# 确保项目根目录在 path 中
_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
)
logger = logging.getLogger(__name__)

DRY_RUN = "--dry-run" in sys.argv
VERIFY  = "--verify"  in sys.argv


# ─────────────────────────────────────────────────────────────────────────────
# 辅助函数
# ─────────────────────────────────────────────────────────────────────────────

def _amount_to_int(amount: float, currency: str) -> int:
    """与 utils/currency.py 保持一致。"""
    if currency.upper() in ("JPY", "KRW", "VND"):
        return round(amount)
    return round(amount * 100)


async def _get_or_create_group(db, user_id: int, now: int) -> int:
    """
    返回该用户的默认 Group id。
    若不存在则创建，已存在则直接返回。
    """
    async with db.execute(
        "SELECT id FROM groups WHERE owner_id = ? LIMIT 1", (user_id,)
    ) as cur:
        row = await cur.fetchone()

    if row:
        return row[0]

    invite_code = secrets.token_urlsafe(8)
    cursor = await db.execute(
        """
        INSERT INTO groups (name, owner_id, invite_code, base_currency, is_active, created_at, updated_at)
        VALUES ('我的账本', ?, ?, 'JPY', 1, ?, ?)
        """,
        (user_id, invite_code, now, now),
    )
    group_id = cursor.lastrowid
    logger.info("  创建 Group id=%d (user_id=%d)", group_id, user_id)
    return group_id


async def _get_or_create_account(db, group_id: int, now: int) -> int:
    """
    返回该 Group 的默认现金账户 id。
    若不存在则创建。
    """
    async with db.execute(
        "SELECT id FROM accounts WHERE group_id = ? AND type = 'cash' LIMIT 1",
        (group_id,),
    ) as cur:
        row = await cur.fetchone()

    if row:
        return row[0]

    cursor = await db.execute(
        """
        INSERT INTO accounts (name, type, currency_code, group_id,
                              balance_cache, is_active, created_at, updated_at)
        VALUES ('现金', 'cash', 'JPY', ?, 0, 1, ?, ?)
        """,
        (group_id, now, now),
    )
    account_id = cursor.lastrowid
    logger.info("  创建 Account id=%d (group_id=%d)", account_id, group_id)
    return account_id


async def _get_category_id(db, category_name: Optional[str]) -> int:
    """
    将 bill.category（中文名）映射到 categories 表的 id。
    找不到时回退到"其他"。
    """
    name = category_name or "其他"
    async with db.execute(
        "SELECT id FROM categories WHERE name = ? AND is_system = 1 LIMIT 1",
        (name,),
    ) as cur:
        row = await cur.fetchone()

    if row:
        return row[0]

    # 找不到匹配分类，回退到"其他"
    async with db.execute(
        "SELECT id FROM categories WHERE name = '其他' AND is_system = 1 LIMIT 1"
    ) as cur:
        row = await cur.fetchone()

    if row:
        return row[0]

    # 兜底：取 categories 里 id 最小的那条
    async with db.execute("SELECT id FROM categories ORDER BY id LIMIT 1") as cur:
        row = await cur.fetchone()

    if row:
        return row[0]

    raise RuntimeError("categories 表为空，请先执行 v007 迁移")


# ─────────────────────────────────────────────────────────────────────────────
# 核心迁移逻辑
# ─────────────────────────────────────────────────────────────────────────────

async def migrate(dry_run: bool = False) -> dict:
    """
    执行迁移，返回统计信息。

    Parameters
    ----------
    dry_run : 若为 True，在事务末尾 ROLLBACK，不实际写入。
    """
    import aiosqlite
    from config.settings import DB_PATH

    stats = {
        "users":          0,
        "groups_created": 0,
        "accounts_created": 0,
        "bills_migrated": 0,
        "bills_skipped":  0,
        "items_migrated": 0,
        "receipts_migrated": 0,
        "errors":         [],
    }

    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row

        # ── 前置检查 ─────────────────────────────────────────────────────

        # 确认 v007 已执行（transactions 表存在）
        async with db.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='transactions'"
        ) as cur:
            if not await cur.fetchone():
                raise RuntimeError(
                    "transactions 表不存在，请先执行 v007 迁移再运行本脚本"
                )

        # 确认 categories 有数据
        async with db.execute("SELECT COUNT(*) FROM categories WHERE is_system=1") as cur:
            cat_count = (await cur.fetchone())[0]
        if cat_count == 0:
            raise RuntimeError(
                "categories 表没有系统预设分类，请先执行 v007 迁移"
            )

        # ── 取所有有 bills 的用户 ─────────────────────────────────────────

        async with db.execute(
            "SELECT DISTINCT user_id FROM bills ORDER BY user_id"
        ) as cur:
            user_ids = [r[0] for r in await cur.fetchall()]

        logger.info("共找到 %d 个用户需要迁移", len(user_ids))

        await db.execute("BEGIN")
        try:
            now = int(time.time())

            for user_id in user_ids:
                logger.info("── 处理 user_id=%d ──", user_id)
                stats["users"] += 1

                # 1. 确保 Group 存在
                group_id = await _get_or_create_group(db, user_id, now)
                if not dry_run:
                    # 更新 users.group_id
                    await db.execute(
                        "UPDATE users SET group_id = ? WHERE id = ? AND group_id IS NULL",
                        (group_id, user_id),
                    )

                # 2. 确保默认账户存在
                account_id = await _get_or_create_account(db, group_id, now)

                # 3. 取该用户所有未迁移的 bills
                async with db.execute(
                    """
                    SELECT b.*
                    FROM bills b
                    WHERE b.user_id = ?
                      AND NOT EXISTS (
                          SELECT 1 FROM transactions t
                          WHERE t.legacy_bill_id = b.id
                      )
                    ORDER BY b.id
                    """,
                    (user_id,),
                ) as cur:
                    bills = [dict(r) for r in await cur.fetchall()]

                logger.info("  待迁移 bills: %d 条", len(bills))

                for bill in bills:
                    bill_id  = bill["id"]
                    currency = bill.get("currency") or "JPY"

                    # amount 已经是 INTEGER（v005 迁移后），直接使用
                    amount = bill.get("amount") or 0
                    if not isinstance(amount, int):
                        amount = _amount_to_int(float(amount), currency)

                    try:
                        category_id = await _get_category_id(
                            db, bill.get("category")
                        )
                    except RuntimeError as e:
                        stats["errors"].append(
                            f"bill_id={bill_id}: {e}"
                        )
                        stats["bills_skipped"] += 1
                        continue

                    txn_date = int(bill.get("created_at") or now)

                    # 4. 插入 transactions
                    cursor = await db.execute(
                        """
                        INSERT INTO transactions (
                            type, amount, currency_code,
                            base_amount, exchange_rate,
                            account_id, category_id,
                            user_id, group_id,
                            is_private, note,
                            transaction_date,
                            created_at, updated_at,
                            is_deleted, legacy_bill_id
                        ) VALUES (
                            'expense', ?, ?,
                            ?, 1000000,
                            ?, ?,
                            ?, ?,
                            0, ?,
                            ?,
                            ?, ?,
                            0, ?
                        )
                        """,
                        (
                            amount, currency,
                            amount,              # base_amount = amount（无汇率历史）
                            account_id, category_id,
                            user_id, group_id,
                            bill.get("description"),
                            txn_date,
                            int(bill.get("created_at") or now),
                            int(bill.get("updated_at") or now),
                            bill_id,
                        ),
                    )
                    txn_id = cursor.lastrowid
                    stats["bills_migrated"] += 1

                    # 5. 迁移 bill_items → transaction_items
                    async with db.execute(
                        "SELECT * FROM bill_items WHERE bill_id = ? ORDER BY sort_order",
                        (bill_id,),
                    ) as cur:
                        items = [dict(r) for r in await cur.fetchall()]

                    for item in items:
                        item_amount = item.get("amount") or 0
                        if not isinstance(item_amount, int):
                            item_amount = _amount_to_int(
                                float(item_amount), currency
                            )

                        unit_price = item.get("unit_price")
                        if unit_price is not None and not isinstance(unit_price, int):
                            unit_price = _amount_to_int(
                                float(unit_price), currency
                            )

                        await db.execute(
                            """
                            INSERT INTO transaction_items (
                                transaction_id, name, name_raw,
                                quantity, unit_price, amount,
                                item_type, sort_order
                            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                            """,
                            (
                                txn_id,
                                item.get("name") or "",
                                item.get("name_raw") or "",
                                item.get("quantity") or 1.0,
                                unit_price,
                                item_amount,
                                item.get("item_type") or "item",
                                item.get("sort_order") or 0,
                            ),
                        )
                        stats["items_migrated"] += 1

                    # 6. 迁移 receipt_url → receipts
                    receipt_url = bill.get("receipt_url") or ""
                    if receipt_url.strip():
                        await db.execute(
                            """
                            INSERT INTO receipts (
                                transaction_id, image_url,
                                created_at, updated_at, is_deleted
                            ) VALUES (?, ?, ?, ?, 0)
                            """,
                            (
                                txn_id,
                                receipt_url.strip(),
                                int(bill.get("created_at") or now),
                                int(bill.get("updated_at") or now),
                            ),
                        )
                        stats["receipts_migrated"] += 1

                    logger.debug(
                        "  迁移 bill_id=%d → txn_id=%d items=%d receipt=%s",
                        bill_id, txn_id, len(items),
                        "yes" if receipt_url else "no",
                    )

            if dry_run:
                await db.execute("ROLLBACK")
                logger.info("DRY RUN：已回滚，数据库未变更")
            else:
                await db.commit()
                logger.info("事务提交成功")

        except Exception:
            await db.execute("ROLLBACK")
            raise

    return stats


# ─────────────────────────────────────────────────────────────────────────────
# 验证
# ─────────────────────────────────────────────────────────────────────────────

async def verify() -> None:
    import aiosqlite
    from config.settings import DB_PATH

    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row

        # 1. bills 总数
        async with db.execute("SELECT COUNT(*) FROM bills") as cur:
            total_bills = (await cur.fetchone())[0]

        # 2. 已迁移的 transactions 数
        async with db.execute(
            "SELECT COUNT(*) FROM transactions WHERE legacy_bill_id IS NOT NULL"
        ) as cur:
            migrated_txns = (await cur.fetchone())[0]

        # 3. 未迁移的 bills
        async with db.execute(
            """
            SELECT COUNT(*) FROM bills b
            WHERE NOT EXISTS (
                SELECT 1 FROM transactions t WHERE t.legacy_bill_id = b.id
            )
            """
        ) as cur:
            not_migrated = (await cur.fetchone())[0]

        # 4. bill_items vs transaction_items 数量比对
        async with db.execute("SELECT COUNT(*) FROM bill_items") as cur:
            total_items = (await cur.fetchone())[0]
        async with db.execute("SELECT COUNT(*) FROM transaction_items") as cur:
            migrated_items = (await cur.fetchone())[0]

        # 5. receipts 迁移数
        async with db.execute(
            "SELECT COUNT(*) FROM bills WHERE receipt_url != '' AND receipt_url IS NOT NULL"
        ) as cur:
            bills_with_receipt = (await cur.fetchone())[0]
        async with db.execute("SELECT COUNT(*) FROM receipts") as cur:
            migrated_receipts = (await cur.fetchone())[0]

        # 6. 孤立检查（transaction 找不到对应 group）
        async with db.execute(
            """
            SELECT COUNT(*) FROM transactions t
            LEFT JOIN groups g ON t.group_id = g.id
            WHERE g.id IS NULL
            """
        ) as cur:
            orphan_txns = (await cur.fetchone())[0]

        print("\n══════════════ 迁移验证报告 ══════════════")
        print(f"bills 总数:              {total_bills}")
        print(f"已迁移 transactions:     {migrated_txns}",
              "✅" if migrated_txns == total_bills else "❌")
        print(f"未迁移 bills:            {not_migrated}",
              "✅" if not_migrated == 0 else "❌ 需要关注")
        print(f"bill_items 总数:         {total_items}")
        print(f"已迁移 transaction_items:{migrated_items}",
              "✅" if migrated_items == total_items else "❌")
        print(f"有凭证的 bills:          {bills_with_receipt}")
        print(f"已迁移 receipts:         {migrated_receipts}",
              "✅" if migrated_receipts == bills_with_receipt else "❌")
        print(f"孤立 transactions:       {orphan_txns}",
              "✅" if orphan_txns == 0 else "❌")
        print("══════════════════════════════════════════\n")


# ─────────────────────────────────────────────────────────────────────────────
# 入口
# ─────────────────────────────────────────────────────────────────────────────

async def main() -> None:
    from config.settings import DB_PATH

    if not os.path.exists(DB_PATH):
        logger.error("数据库文件不存在: %s", DB_PATH)
        sys.exit(1)

    if VERIFY:
        await verify()
        return

    if DRY_RUN:
        logger.info("═══════════════ DRY RUN 开始 ═══════════════")
    else:
        logger.info("══════════════ 正式迁移开始 ══════════════")
        logger.info("数据库路径: %s", DB_PATH)

    stats = await migrate(dry_run=DRY_RUN)

    print("\n══════════════════ 迁移统计 ══════════════════")
    print(f"处理用户数:         {stats['users']}")
    print(f"迁移 bills:         {stats['bills_migrated']}")
    print(f"跳过 bills:         {stats['bills_skipped']}")
    print(f"迁移 items:         {stats['items_migrated']}")
    print(f"迁移 receipts:      {stats['receipts_migrated']}")
    if stats["errors"]:
        print(f"\n⚠️  错误 ({len(stats['errors'])} 条):")
        for err in stats["errors"]:
            print(f"   {err}")
    print("══════════════════════════════════════════════\n")

    if DRY_RUN:
        logger.info("DRY RUN 完成，数据库未变更")
        logger.info("确认无误后执行: python scripts/migrate_bills_to_transactions.py")
    else:
        logger.info("迁移完成，执行验证: python scripts/migrate_bills_to_transactions.py --verify")


if __name__ == "__main__":
    asyncio.run(main())
