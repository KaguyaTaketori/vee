"""
scripts/reindex_bills.py
──────────────────────────
将 SQLite 中已有的账单全量导入 Meilisearch 索引。

使用方式
--------
    python -m scripts.reindex_bills

注意
----
- 幂等操作，重复执行安全（add_documents 会覆盖同 id 的文档）
- 大数据量时分批写入，每批 500 条
"""
from __future__ import annotations

import asyncio
import logging
import sys
import os

# 确保项目根目录在 path 中
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
)
logger = logging.getLogger(__name__)

_BATCH_SIZE = 500


async def main() -> None:
    from database.db import get_db
    from shared.services.search_service import init_index, index_bills_bulk

    logger.info("Initializing Meilisearch index...")
    await init_index()

    logger.info("Loading bills from SQLite...")
    async with get_db() as db:
        cursor = await db.execute(
            """
            SELECT id, user_id, amount, currency, category,
                   description, merchant, bill_date, receipt_url, created_at
            FROM bills
            ORDER BY id ASC
            """
        )
        rows = [dict(r) for r in await cursor.fetchall()]

    total = len(rows)
    if total == 0:
        logger.info("No bills found, nothing to index.")
        return

    logger.info("Found %d bills, indexing in batches of %d...", total, _BATCH_SIZE)

    indexed = 0
    for i in range(0, total, _BATCH_SIZE):
        batch = rows[i: i + _BATCH_SIZE]
        await index_bills_bulk(batch)
        indexed += len(batch)
        logger.info("Progress: %d / %d", indexed, total)

    logger.info("Done. %d bills indexed into Meilisearch.", total)


if __name__ == "__main__":
    asyncio.run(main())
