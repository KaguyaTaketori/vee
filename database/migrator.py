# database/migrator.py
"""
轻量版本迁移管理器。
每条迁移是一个 async def up(db) 函数，按版本号顺序执行，跑过的不重复执行。
"""
from __future__ import annotations

import logging
from typing import Callable, Awaitable

logger = logging.getLogger(__name__)


async def _ensure_migrations_table(db) -> None:
    await db.execute("""
        CREATE TABLE IF NOT EXISTS schema_migrations (
            version     INTEGER PRIMARY KEY,
            name        TEXT    NOT NULL,
            applied_at  REAL    NOT NULL DEFAULT (unixepoch())
        )
    """)
    await db.commit()


async def _get_applied(db) -> set[int]:
    async with db.execute("SELECT version FROM schema_migrations") as cur:
        rows = await cur.fetchall()
    return {r[0] for r in rows}


async def _mark_applied(db, version: int, name: str) -> None:
    await db.execute(
        "INSERT INTO schema_migrations (version, name) VALUES (?, ?)",
        (version, name),
    )
    await db.commit()


async def run_migrations(
    db,
    migrations: list[tuple[int, str, Callable]],
) -> None:
    """
    migrations 格式：[(版本号, 描述, async def up(db))]
    只执行尚未应用的版本，按版本号升序。
    """
    await _ensure_migrations_table(db)
    applied = await _get_applied(db)

    pending = sorted(
        [(v, name, fn) for v, name, fn in migrations if v not in applied],
        key=lambda x: x[0],
    )

    if not pending:
        logger.info("Migrations: all up to date.")
        return

    for version, name, fn in pending:
        logger.info("Applying migration %03d: %s ...", version, name)
        try:
            await fn(db)
            await _mark_applied(db, version, name)
            logger.info("Migration %03d applied.", version)
        except Exception as e:
            logger.error("Migration %03d FAILED: %s", version, e)
            raise
