# scripts/cleanup_old_tables.py
import asyncio

async def main():
    from database.db import get_db
    async with get_db() as db:
        # 最后再确认一次新表数据完整
        for table in ["users", "bills", "bill_items"]:
            async with db.execute(f"SELECT COUNT(*) FROM {table}") as cur:
                count = (await cur.fetchone())[0]
            print(f"{table}: {count} 行")

        confirm = input("\n确认删除旧表？输入 'yes' 继续: ")
        if confirm != "yes":
            print("已取消")
            return

        for table in ["app_bill_items", "app_bills", "bill_items_v4",
                      "bills_v4", "app_users", "users_v4"]:
            await db.execute(f"DROP TABLE IF EXISTS {table}")
            print(f"已删除: {table}")

        await db.commit()
        print("清理完成")

asyncio.run(main())
