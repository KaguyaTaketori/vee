# database/migrations.py
"""
所有迁移按版本号集中管理。
新增迁移：在 ALL_MIGRATIONS 列表末尾追加一个元组即可。
绝对不要修改已有迁移的内容——已跑过的不会重跑。
"""
from __future__ import annotations


# ── 001：基础 Bot 表 ──────────────────────────────────────────────────────

async def _v001_bot_tables(db) -> None:
    """原 init_db() 的内容，原样搬过来。"""
    await db.execute("PRAGMA journal_mode=WAL")
    await db.execute("PRAGMA foreign_keys=ON")

    await db.execute("""
        CREATE TABLE IF NOT EXISTS users (
            user_id    INTEGER PRIMARY KEY,
            username   TEXT,
            first_name TEXT,
            last_name  TEXT,
            lang       TEXT DEFAULT 'en',
            added_at   REAL,
            last_seen  REAL
        )
    """)
    await db.execute("""
        CREATE TABLE IF NOT EXISTS history (
            id            INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id       INTEGER,
            url           TEXT,
            download_type TEXT,
            status        TEXT,
            file_size     INTEGER,
            title         TEXT,
            file_path     TEXT,
            file_id       TEXT,
            timestamp     REAL,
            FOREIGN KEY (user_id) REFERENCES users(user_id)
        )
    """)
    await db.execute("""
        CREATE TABLE IF NOT EXISTS tasks (
            task_id       TEXT PRIMARY KEY,
            user_id       INTEGER NOT NULL,
            url           TEXT    NOT NULL,
            download_type TEXT    NOT NULL,
            format_id     TEXT,
            status        TEXT    NOT NULL DEFAULT 'queued',
            progress      REAL    DEFAULT 0.0,
            error         TEXT,
            file_path     TEXT,
            file_size     INTEGER,
            retry_count   INTEGER DEFAULT 0,
            created_at    REAL    NOT NULL,
            started_at    REAL,
            completed_at  REAL,
            FOREIGN KEY (user_id) REFERENCES users(user_id)
        )
    """)
    await db.execute("""
        CREATE TABLE IF NOT EXISTS user_rate_tiers (
            user_id      INTEGER PRIMARY KEY,
            tier         TEXT    NOT NULL DEFAULT 'normal',
            max_per_hour INTEGER,
            note         TEXT,
            set_by       INTEGER,
            set_at       REAL,
            FOREIGN KEY (user_id) REFERENCES users(user_id)
        )
    """)
    await db.execute("""
        CREATE TABLE IF NOT EXISTS rate_limit (
            user_id   INTEGER,
            timestamp REAL,
            FOREIGN KEY (user_id) REFERENCES users(user_id)
        )
    """)
    for ddl in [
        "CREATE INDEX IF NOT EXISTS idx_tasks_user_id   ON tasks(user_id)",
        "CREATE INDEX IF NOT EXISTS idx_tasks_status    ON tasks(status)",
        "CREATE INDEX IF NOT EXISTS idx_tasks_created   ON tasks(created_at)",
        "CREATE INDEX IF NOT EXISTS idx_history_user    ON history(user_id)",
        "CREATE INDEX IF NOT EXISTS idx_history_url     ON history(url)",
        "CREATE INDEX IF NOT EXISTS idx_history_ts      ON history(timestamp)",
        "CREATE INDEX IF NOT EXISTS idx_rl_user         ON rate_limit(user_id)",
        "CREATE INDEX IF NOT EXISTS idx_rl_ts           ON rate_limit(timestamp)",
    ]:
        await db.execute(ddl)
    await db.commit()


# ── 002：账单表（旧 Bot 侧，关联 users.user_id）────────────────────────────

async def _v002_bills_bot(db) -> None:
    await db.execute("""
        CREATE TABLE IF NOT EXISTS bills (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id         INTEGER NOT NULL,
            amount          REAL    NOT NULL,
            currency        TEXT    NOT NULL DEFAULT 'JPY',
            category        TEXT,
            description     TEXT,
            merchant        TEXT,
            bill_date       TEXT,
            raw_text        TEXT,
            receipt_file_id TEXT    NOT NULL DEFAULT '',
            receipt_url     TEXT    NOT NULL DEFAULT '',
            created_at      REAL    NOT NULL,
            updated_at      REAL    NOT NULL,
            FOREIGN KEY (user_id) REFERENCES users(user_id)
        )
    """)
    await db.execute("""
        CREATE TABLE IF NOT EXISTS bill_items (
            id         INTEGER PRIMARY KEY AUTOINCREMENT,
            bill_id    INTEGER NOT NULL,
            user_id    INTEGER NOT NULL,
            name       TEXT    NOT NULL,
            name_raw   TEXT    DEFAULT '',
            quantity   REAL    NOT NULL DEFAULT 1,
            unit_price REAL,
            amount     REAL    NOT NULL,
            item_type  TEXT    NOT NULL DEFAULT 'item',
            sort_order INTEGER NOT NULL DEFAULT 0,
            FOREIGN KEY (bill_id)  REFERENCES bills(id) ON DELETE CASCADE,
            FOREIGN KEY (user_id)  REFERENCES users(user_id)
        )
    """)
    for ddl in [
        "CREATE INDEX IF NOT EXISTS idx_bills_user     ON bills(user_id)",
        "CREATE INDEX IF NOT EXISTS idx_bills_date     ON bills(bill_date)",
        "CREATE INDEX IF NOT EXISTS idx_items_bill     ON bill_items(bill_id)",
    ]:
        await db.execute(ddl)
    await db.commit()


# ── 003：App 独立账号体系 ──────────────────────────────────────────────────

async def _v003_app_users(db) -> None:
    await db.execute("""
        CREATE TABLE IF NOT EXISTS app_users (
            id                INTEGER PRIMARY KEY AUTOINCREMENT,
            username          TEXT    NOT NULL UNIQUE,
            email             TEXT    NOT NULL UNIQUE,
            password_hash     TEXT    NOT NULL,
            display_name      TEXT,
            avatar_url        TEXT,
            tg_user_id        INTEGER UNIQUE,
            is_active         INTEGER NOT NULL DEFAULT 0,
            ai_quota_monthly  INTEGER NOT NULL DEFAULT 100,
            ai_quota_used     INTEGER NOT NULL DEFAULT 0,
            ai_quota_reset_at REAL    NOT NULL DEFAULT 0,
            created_at        REAL    NOT NULL,
            updated_at        REAL    NOT NULL
        )
    """)
    await db.execute("""
        CREATE TABLE IF NOT EXISTS refresh_tokens (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            app_user_id INTEGER NOT NULL,
            token_hash  TEXT    NOT NULL UNIQUE,
            expires_at  REAL    NOT NULL,
            is_revoked  INTEGER NOT NULL DEFAULT 0,
            device_hint TEXT,
            created_at  REAL    NOT NULL,
            FOREIGN KEY (app_user_id) REFERENCES app_users(id) ON DELETE CASCADE
        )
    """)
    await db.execute("""
        CREATE TABLE IF NOT EXISTS email_verifications (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            app_user_id INTEGER NOT NULL,
            code        TEXT    NOT NULL,
            purpose     TEXT    NOT NULL DEFAULT 'activation',
            expires_at  REAL    NOT NULL,
            is_used     INTEGER NOT NULL DEFAULT 0,
            created_at  REAL    NOT NULL,
            FOREIGN KEY (app_user_id) REFERENCES app_users(id) ON DELETE CASCADE
        )
    """)
    await db.execute("""
        CREATE TABLE IF NOT EXISTS tg_bind_codes (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            app_user_id INTEGER NOT NULL,
            code        TEXT    NOT NULL,
            expires_at  REAL    NOT NULL,
            is_used     INTEGER NOT NULL DEFAULT 0,
            created_at  REAL    NOT NULL,
            FOREIGN KEY (app_user_id) REFERENCES app_users(id) ON DELETE CASCADE
        )
    """)
    for ddl in [
        "CREATE INDEX IF NOT EXISTS idx_au_email    ON app_users(email)",
        "CREATE INDEX IF NOT EXISTS idx_au_username ON app_users(username)",
        "CREATE INDEX IF NOT EXISTS idx_au_tg       ON app_users(tg_user_id)",
        "CREATE INDEX IF NOT EXISTS idx_rt_user     ON refresh_tokens(app_user_id)",
        "CREATE INDEX IF NOT EXISTS idx_rt_hash     ON refresh_tokens(token_hash)",
        "CREATE INDEX IF NOT EXISTS idx_ev_user     ON email_verifications(app_user_id)",
    ]:
        await db.execute(ddl)
    await db.commit()


# ── 004：App 账单表（关联 app_users.id，全新部署用这张）──────────────────

async def _v004_app_bills(db) -> None:
    """
    全新部署：直接建关联 app_users 的 bills 表。
    已有旧 bills 表的库：此迁移会静默跳过（IF NOT EXISTS）。
    需要数据迁移时，在这里写 INSERT INTO ... SELECT ... 语句。
    """
    await db.execute("""
        CREATE TABLE IF NOT EXISTS app_bills (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            app_user_id     INTEGER NOT NULL,
            amount          REAL    NOT NULL,
            currency        TEXT    NOT NULL DEFAULT 'JPY',
            category        TEXT,
            description     TEXT,
            merchant        TEXT,
            bill_date       TEXT,
            raw_text        TEXT,
            receipt_file_id TEXT    NOT NULL DEFAULT '',
            receipt_url     TEXT    NOT NULL DEFAULT '',
            created_at      REAL    NOT NULL,
            updated_at      REAL    NOT NULL,
            FOREIGN KEY (app_user_id) REFERENCES app_users(id) ON DELETE CASCADE
        )
    """)
    await db.execute("""
        CREATE TABLE IF NOT EXISTS app_bill_items (
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
            FOREIGN KEY (bill_id)     REFERENCES app_bills(id) ON DELETE CASCADE,
            FOREIGN KEY (app_user_id) REFERENCES app_users(id) ON DELETE CASCADE
        )
    """)
    for ddl in [
        "CREATE INDEX IF NOT EXISTS idx_ab_user  ON app_bills(app_user_id)",
        "CREATE INDEX IF NOT EXISTS idx_ab_date  ON app_bills(bill_date)",
        "CREATE INDEX IF NOT EXISTS idx_abi_bill ON app_bill_items(bill_id)",
    ]:
        await db.execute(ddl)
    await db.commit()






# ── 迁移注册表（按版本号升序，只追加，不修改已有条目）────────────────────

ALL_MIGRATIONS: list[tuple[int, str, object]] = [
    (1,  "bot_base_tables",  _v001_bot_tables),
    (2,  "bills_bot_side",   _v002_bills_bot),
    (3,  "app_users",        _v003_app_users),
    (4,  "app_bills",        _v004_app_bills),
]
