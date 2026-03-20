# database/migrations/001_app_users.py
"""
Migration 001 — app_users, refresh_tokens, email_verifications
全新部署直接运行；旧库执行前请先备份。
"""
from __future__ import annotations
import logging
from database.db import get_db

logger = logging.getLogger(__name__)


async def run() -> None:
    async with get_db() as db:
        await db.execute("PRAGMA journal_mode=WAL")
        await db.execute("PRAGMA foreign_keys=ON")

        # ── 主账号表 ──────────────────────────────────────────────────────
        await db.execute("""
            CREATE TABLE IF NOT EXISTS app_users (
                id                  INTEGER PRIMARY KEY AUTOINCREMENT,
                username            TEXT    NOT NULL,
                email               TEXT    NOT NULL,
                password_hash       TEXT    NOT NULL,
                display_name        TEXT,
                avatar_url          TEXT,
                tg_user_id          INTEGER,
                is_active           INTEGER NOT NULL DEFAULT 0,
                ai_quota_monthly    INTEGER NOT NULL DEFAULT 100,
                ai_quota_used       INTEGER NOT NULL DEFAULT 0,
                ai_quota_reset_at   REAL    NOT NULL DEFAULT 0,
                created_at          REAL    NOT NULL,
                updated_at          REAL    NOT NULL,
                UNIQUE (username),
                UNIQUE (email),
                UNIQUE (tg_user_id)
            )
        """)

        # ── Refresh Token 表 ──────────────────────────────────────────────
        await db.execute("""
            CREATE TABLE IF NOT EXISTS refresh_tokens (
                id              INTEGER PRIMARY KEY AUTOINCREMENT,
                app_user_id     INTEGER NOT NULL,
                token_hash      TEXT    NOT NULL UNIQUE,
                expires_at      REAL    NOT NULL,
                is_revoked      INTEGER NOT NULL DEFAULT 0,
                device_hint     TEXT,
                created_at      REAL    NOT NULL,
                FOREIGN KEY (app_user_id) REFERENCES app_users(id) ON DELETE CASCADE
            )
        """)

        # ── 邮箱验证码表 ──────────────────────────────────────────────────
        await db.execute("""
            CREATE TABLE IF NOT EXISTS email_verifications (
                id              INTEGER PRIMARY KEY AUTOINCREMENT,
                app_user_id     INTEGER NOT NULL,
                code            TEXT    NOT NULL,
                purpose         TEXT    NOT NULL DEFAULT 'activation',
                expires_at      REAL    NOT NULL,
                is_used         INTEGER NOT NULL DEFAULT 0,
                created_at      REAL    NOT NULL,
                FOREIGN KEY (app_user_id) REFERENCES app_users(id) ON DELETE CASCADE
            )
        """)

        # ── TG 绑定码表（预留，本期接口不完整实现） ───────────────────────
        await db.execute("""
            CREATE TABLE IF NOT EXISTS tg_bind_codes (
                id              INTEGER PRIMARY KEY AUTOINCREMENT,
                app_user_id     INTEGER NOT NULL,
                code            TEXT    NOT NULL,
                expires_at      REAL    NOT NULL,
                is_used         INTEGER NOT NULL DEFAULT 0,
                created_at      REAL    NOT NULL,
                FOREIGN KEY (app_user_id) REFERENCES app_users(id) ON DELETE CASCADE
            )
        """)

        # ── bills 表（全新，关联 app_users.id） ──────────────────────────
        # 注意：这与旧 bills 表字段完全一致，仅外键改为 app_user_id
        await db.execute("""
            CREATE TABLE IF NOT EXISTS bills (
                id               INTEGER PRIMARY KEY AUTOINCREMENT,
                app_user_id      INTEGER NOT NULL,
                amount           REAL    NOT NULL,
                currency         TEXT    NOT NULL DEFAULT 'JPY',
                category         TEXT,
                description      TEXT,
                merchant         TEXT,
                bill_date        TEXT,
                raw_text         TEXT,
                receipt_file_id  TEXT    NOT NULL DEFAULT '',
                receipt_url      TEXT    NOT NULL DEFAULT '',
                created_at       REAL    NOT NULL,
                updated_at       REAL    NOT NULL,
                FOREIGN KEY (app_user_id) REFERENCES app_users(id) ON DELETE CASCADE
            )
        """)

        await db.execute("""
            CREATE TABLE IF NOT EXISTS bill_items (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                bill_id     INTEGER NOT NULL,
                app_user_id INTEGER NOT NULL,
                name        TEXT    NOT NULL,
                name_raw    TEXT    DEFAULT '',
                quantity    REAL    NOT NULL DEFAULT 1,
                unit_price  REAL,
                amount      REAL    NOT NULL,
                item_type   TEXT    NOT NULL DEFAULT 'item',
                sort_order  INTEGER NOT NULL DEFAULT 0,
                FOREIGN KEY (bill_id)     REFERENCES bills(id)     ON DELETE CASCADE,
                FOREIGN KEY (app_user_id) REFERENCES app_users(id) ON DELETE CASCADE
            )
        """)

        # ── 索引 ──────────────────────────────────────────────────────────
        for ddl in [
            "CREATE INDEX IF NOT EXISTS idx_app_users_email      ON app_users(email)",
            "CREATE INDEX IF NOT EXISTS idx_app_users_username   ON app_users(username)",
            "CREATE INDEX IF NOT EXISTS idx_app_users_tg         ON app_users(tg_user_id)",
            "CREATE INDEX IF NOT EXISTS idx_rt_user              ON refresh_tokens(app_user_id)",
            "CREATE INDEX IF NOT EXISTS idx_rt_hash              ON refresh_tokens(token_hash)",
            "CREATE INDEX IF NOT EXISTS idx_ev_user              ON email_verifications(app_user_id)",
            "CREATE INDEX IF NOT EXISTS idx_bills_app_user       ON bills(app_user_id)",
            "CREATE INDEX IF NOT EXISTS idx_bills_date           ON bills(bill_date)",
            "CREATE INDEX IF NOT EXISTS idx_bill_items_bill      ON bill_items(bill_id)",
        ]:
            await db.execute(ddl)

        await db.commit()
    logger.info("Migration 001 complete.")
