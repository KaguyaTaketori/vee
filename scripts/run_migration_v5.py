# scripts/run_migration_v5.py
from __future__ import annotations

import asyncio
import sys
import os
import shutil
import logging

_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
)
logger = logging.getLogger(__name__)

DRY_RUN = "--dry-run" in sys.argv


async def run_migration(db_path: str) -> dict:
    """
    在指定的数据库文件上执行 v5 迁移。
    返回迁移前后的数据量统计。
    """
    import aiosqlite
    from database.migrations import _v005_unified_schema

    async with aiosqlite.connect(db_path) as db:
        db.row_factory = aiosqlite.Row

        # 检查是否有上次失败留下的临时表
        for tmp_table in ["users_new", "bills_new", "bills_clean", "bill_items_new"]:
            async with db.execute(
                "SELECT name FROM sqlite_master "
                "WHERE type='table' AND name=?",
                (tmp_table,),
            ) as cur:
                if await cur.fetchone():
                    logger.error("发现残留临时表 %s，上次迁移可能中途失败", tmp_table)
                    logger.error(
                        "请先手动清理：\n"
                        "  sqlite3 %s 'DROP TABLE IF EXISTS %s;'",
                        db_path, tmp_table,
                    )
                    raise RuntimeError(f"残留临时表 {tmp_table}，请先清理")

        # 检查 v5 是否已执行
        async with db.execute(
            "SELECT name FROM sqlite_master "
            "WHERE type='table' AND name='schema_migrations'"
        ) as cur:
            if await cur.fetchone():
                async with db.execute(
                    "SELECT version FROM schema_migrations WHERE version = 5"
                ) as cur2:
                    if await cur2.fetchone():
                        raise RuntimeError("v5 迁移已执行过")

        # 迁移前快照
        counts_before = {}
        for table in ["users", "app_users", "bills", "app_bills",
                      "bill_items", "app_bill_items"]:
            try:
                async with db.execute(f"SELECT COUNT(*) FROM {table}") as cur:
                    counts_before[table] = (await cur.fetchone())[0]
            except Exception:
                counts_before[table] = 0
                logger.warning("表 %s 不存在，计为 0", table)

        logger.info("迁移前数据量: %s", counts_before)

        try:
            async with db.execute("""
                SELECT COUNT(*) FROM app_bill_items abi
                WHERE EXISTS (
                    SELECT 1 FROM app_bills ab WHERE ab.id = abi.bill_id
                )
            """) as cur:
                valid_app_items = (await cur.fetchone())[0]
        except Exception:
            valid_app_items = 0
        logger.info(
            "有效 app_bill_items: %d 条（共 %d 条，%d 条孤立）",
            valid_app_items,
            counts_before["app_bill_items"],
            counts_before["app_bill_items"] - valid_app_items,
        )

        # 执行迁移
        await _v005_unified_schema(db)

        # 迁移后快照
        counts_after = {}
        for table in ["users", "bills", "bill_items"]:
            async with db.execute(f"SELECT COUNT(*) FROM {table}") as cur:
                counts_after[table] = (await cur.fetchone())[0]

        logger.info("迁移后数据量: %s", counts_after)

    return {
        "before":          counts_before,
        "after":           counts_after,
        "valid_app_items": valid_app_items,   # ← 新增
    }

def verify_counts(counts: dict, valid_app_items: int = 0) -> bool:
    before = counts["before"]
    after  = counts["after"]
    ok = True

    # bills 核对
    expected_bills = before["bills"] + before["app_bills"]
    if after["bills"] != expected_bills:
        logger.error(
            "❌ bills 数量不匹配！预期 %d，实际 %d",
            expected_bills, after["bills"],
        )
        ok = False
    else:
        logger.info("✅ bills 核对通过: %d", after["bills"])

    # bill_items 核对（只统计有效的 app_bill_items）
    expected_items = before["bill_items"] + valid_app_items
    logger.info(
        "bill_items 预期: %d（bot明细）+ %d（有效app明细）= %d，"
        "跳过了 %d 条孤立的 app_bill_items",
        before["bill_items"],
        valid_app_items,
        expected_items,
        before["app_bill_items"] - valid_app_items,
    )
    if after["bill_items"] != expected_items:
        logger.error(
            "❌ bill_items 数量不匹配！预期 %d，实际 %d",
            expected_items, after["bill_items"],
        )
        ok = False
    else:
        logger.info("✅ bill_items 核对通过: %d", after["bill_items"])

    # users 核对
    expected_users_max = before["users"] + before["app_users"]
    expected_users_min = before["users"]
    if not (expected_users_min <= after["users"] <= expected_users_max):
        logger.error(
            "❌ users 数量异常！预期在 [%d, %d] 之间，实际 %d",
            expected_users_min, expected_users_max, after["users"],
        )
        ok = False
    else:
        logger.info(
            "✅ users 核对通过: %d（合并了 %d 个已绑定 App 用户）",
            after["users"],
            expected_users_max - after["users"],
        )

    return ok

async def main():
    from config.settings import DB_PATH

    if not os.path.exists(DB_PATH):
        logger.error("数据库文件不存在: %s", DB_PATH)
        return

    if DRY_RUN:
        # ── Dry Run：在临时副本上操作，不碰原始文件 ──────────────────────
        tmp_db = DB_PATH + ".dry_run_tmp"
        try:
            logger.info("=== DRY RUN 开始 ===")
            logger.info("复制数据库到临时文件: %s", tmp_db)
            shutil.copy2(DB_PATH, tmp_db)

            counts = await run_migration(tmp_db)
            all_ok = verify_counts(counts, valid_app_items=counts["valid_app_items"])
            
            if all_ok:
                logger.info("=== DRY RUN 完成，所有核对通过，原始数据库未修改 ===")
                logger.info("可以运行正式迁移：python scripts/run_migration_v5.py")
            else:
                logger.error("=== DRY RUN 完成，但存在数据异常，请检查后再正式执行 ===")

        except RuntimeError as e:
            logger.error("DRY RUN 中止: %s", e)
        except Exception as e:
            logger.error("DRY RUN 失败: %s", e, exc_info=True)
        finally:
            # 无论成功失败都删除临时文件
            if os.path.exists(tmp_db):
                os.remove(tmp_db)
                logger.info("临时文件已清理: %s", tmp_db)

    else:
        # ── 正式执行 ──────────────────────────────────────────────────────
        logger.info("=== v5 正式迁移开始 ===")
        logger.info("数据库路径: %s", DB_PATH)

        # 自动备份（以防万一）
        backup_path = DB_PATH + ".pre_v5.bak"
        if not os.path.exists(backup_path):
            shutil.copy2(DB_PATH, backup_path)
            logger.info("已自动备份到: %s", backup_path)
        else:
            logger.info("备份已存在，跳过: %s", backup_path)

        try:
            counts = await run_migration(DB_PATH)
            all_ok = verify_counts(counts, valid_app_items=counts["valid_app_items"])

            if all_ok:
                logger.info("=== v5 迁移成功完成 ===")
                logger.info("下一步：python scripts/verify_migration_v5.py")
            else:
                logger.error(
                    "=== 迁移完成但存在数据异常！===\n"
                    "如需回滚：cp %s %s",
                    backup_path, DB_PATH,
                )

        except RuntimeError as e:
            logger.error("迁移中止: %s", e)
        except Exception as e:
            logger.error("迁移失败: %s", e, exc_info=True)
            logger.error("如需回滚：cp %s %s", backup_path, DB_PATH)


asyncio.run(main())
