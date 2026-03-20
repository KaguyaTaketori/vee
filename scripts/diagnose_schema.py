# scripts/diagnose_schema.py
"""分析实际 DB 结构，告诉你 v5 迁移需要如何适配。"""
from __future__ import annotations

import asyncio
import sys
import os

_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)


async def main():
    import aiosqlite
    from config.settings import DB_PATH

    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row

        # 所有表
        async with db.execute(
            "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"
        ) as cur:
            tables = [r[0] for r in await cur.fetchall()]
        print(f"\n存在的表: {tables}\n")

        # 各核心表的列结构
        for table in ["users", "bills", "app_bills", "bill_items",
                      "app_bill_items", "app_users"]:
            if table not in tables:
                print(f"[{table}] 不存在")
                continue
            async with db.execute(f"PRAGMA table_info({table})") as cur:
                cols = [dict(r) for r in await cur.fetchall()]
            col_names = [c["name"] for c in cols]
            async with db.execute(f"SELECT COUNT(*) FROM {table}") as cur:
                count = (await cur.fetchone())[0]
            print(f"[{table}] {count}行  列: {col_names}")

        # 判断 bills 的 user 列名
        print("\n=== v5 迁移兼容性分析 ===")
        if "bills" in tables:
            async with db.execute("PRAGMA table_info(bills)") as cur:
                bill_cols = [r[1] for r in await cur.fetchall()]
            if "user_id" in bill_cols:
                print("✅ bills.user_id 存在 → 迁移 SQL 可以直接用")
            elif "app_user_id" in bill_cols:
                print("⚠️  bills.app_user_id 存在 → 需要修改迁移 SQL")
                print("   (由 001_app_users.py 创建的结构)")

        # 已执行的迁移
        if "schema_migrations" in tables:
            async with db.execute(
                "SELECT version, name FROM schema_migrations ORDER BY version"
            ) as cur:
                migrations = [dict(r) for r in await cur.fetchall()]
            print(f"\n已执行的迁移: {migrations}")
        else:
            print("\nschema_migrations 表不存在（迁移未执行）")


asyncio.run(main())
