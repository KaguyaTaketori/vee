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


async def _v005_unified_schema(db) -> None:
    """
    先探测实际 DB 结构，再按实际列名执行迁移。
    同时兼容：
      - bills.user_id（TG Bot 原始结构）
      - bills.app_user_id（001_app_users.py 创建的结构）
    """
    import logging
    logger = logging.getLogger(__name__)

    # ── 0. 探测实际结构 ───────────────────────────────────────────────────

    async def get_columns(table: str) -> list[str]:
        async with db.execute(f"PRAGMA table_info({table})") as cur:
            return [r[1] for r in await cur.fetchall()]

    async def table_exists(table: str) -> bool:
        async with db.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name=?",
            (table,),
        ) as cur:
            return await cur.fetchone() is not None

    bills_exists      = await table_exists("bills")
    app_bills_exists  = await table_exists("app_bills")
    app_users_exists  = await table_exists("app_users")

    # bills 表的 user 列名
    bills_user_col = None
    if bills_exists:
        bills_cols = await get_columns("bills")
        if "user_id" in bills_cols:
            bills_user_col = "user_id"
        elif "app_user_id" in bills_cols:
            bills_user_col = "app_user_id"
        logger.info("bills user 列: %s", bills_user_col)

    # bill_items 表的 user 列名
    items_exists   = await table_exists("bill_items")
    items_user_col = None
    if items_exists:
        items_cols = await get_columns("bill_items")
        if "user_id" in items_cols:
            items_user_col = "user_id"
        elif "app_user_id" in items_cols:
            items_user_col = "app_user_id"
        logger.info("bill_items user 列: %s", items_user_col)

    # users 表的 PK 列名
    users_cols   = await get_columns("users")
    users_pk_col = "user_id" if "user_id" in users_cols else "id"
    logger.info("users PK 列: %s", users_pk_col)

    # ── 1. 新 users 表 ────────────────────────────────────────────────────

    await db.execute("""
        CREATE TABLE IF NOT EXISTS users_new (
            id                INTEGER PRIMARY KEY AUTOINCREMENT,
            email             TEXT    UNIQUE,
            password_hash     TEXT,
            app_username      TEXT    UNIQUE,
            display_name      TEXT,
            avatar_url        TEXT,
            tg_user_id        INTEGER UNIQUE,
            tg_username       TEXT,
            tg_first_name     TEXT,
            tg_last_name      TEXT,
            lang              TEXT    NOT NULL DEFAULT 'en',
            is_active         INTEGER NOT NULL DEFAULT 1,
            ai_quota_monthly  INTEGER NOT NULL DEFAULT 100,
            ai_quota_used     INTEGER NOT NULL DEFAULT 0,
            ai_quota_reset_at INTEGER NOT NULL DEFAULT 0,
            created_at        INTEGER NOT NULL,
            updated_at        INTEGER NOT NULL,
            last_seen         INTEGER
        )
    """)

    # 迁移旧 TG 用户（id 沿用 tg_user_id 的值，保证 history/rate_limit 外键不需要改）
    if users_pk_col == "user_id":
        await db.execute("""
            INSERT INTO users_new (
                id, tg_user_id, tg_username, tg_first_name, tg_last_name,
                lang, is_active, ai_quota_monthly, ai_quota_used,
                ai_quota_reset_at, created_at, updated_at, last_seen
            )
            SELECT
                user_id, user_id, username, first_name, last_name,
                COALESCE(lang, 'en'), 1, 100, 0, 0,
                CAST(COALESCE(added_at, 0) AS INTEGER),
                CAST(COALESCE(added_at, 0) AS INTEGER),
                CAST(COALESCE(last_seen, 0) AS INTEGER)
            FROM users
        """)
    else:
        # users 表已经是新结构（id 列）
        await db.execute("""
            INSERT INTO users_new (
                id, tg_user_id, tg_username, tg_first_name, tg_last_name,
                lang, is_active, ai_quota_monthly, ai_quota_used,
                ai_quota_reset_at, created_at, updated_at, last_seen
            )
            SELECT
                id, tg_user_id, tg_username, tg_first_name, tg_last_name,
                COALESCE(lang, 'en'), is_active, ai_quota_monthly,
                ai_quota_used, ai_quota_reset_at, created_at, updated_at, last_seen
            FROM users
        """)
    logger.info("TG 用户迁移完成（users PK列: %s）", users_pk_col)

    # 迁移 App 用户
    if app_users_exists:
        # 未绑定 TG 的纯 App 用户
        await db.execute("""
            INSERT OR IGNORE INTO users_new (
                email, password_hash, app_username, display_name, avatar_url,
                tg_user_id, is_active, ai_quota_monthly, ai_quota_used,
                ai_quota_reset_at, created_at, updated_at
            )
            SELECT
                email, password_hash, username, display_name, avatar_url,
                tg_user_id, is_active, ai_quota_monthly, ai_quota_used,
                CAST(ai_quota_reset_at AS INTEGER),
                CAST(created_at AS INTEGER),
                CAST(updated_at AS INTEGER)
            FROM app_users
            WHERE tg_user_id IS NULL
               OR tg_user_id NOT IN (
                   SELECT tg_user_id FROM users_new WHERE tg_user_id IS NOT NULL
               )
        """)

        # 已绑定 TG 的 App 用户：UPDATE 到已有 TG 行
        await db.execute("""
            UPDATE users_new
            SET email             = au.email,
                password_hash     = au.password_hash,
                app_username      = au.username,
                display_name      = au.display_name,
                avatar_url        = au.avatar_url,
                is_active         = au.is_active,
                ai_quota_monthly  = au.ai_quota_monthly,
                ai_quota_used     = au.ai_quota_used,
                ai_quota_reset_at = CAST(au.ai_quota_reset_at AS INTEGER),
                updated_at        = CAST(au.updated_at AS INTEGER)
            FROM app_users au
            WHERE users_new.tg_user_id = au.tg_user_id
              AND au.tg_user_id IS NOT NULL
        """)
        logger.info("App 用户迁移完成")

    # ── 2. 新 bills 表（带临时列，用于 bill_items 关联）──────────────────

    await db.execute("""
        CREATE TABLE IF NOT EXISTS bills_new (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id         INTEGER NOT NULL,
            amount          INTEGER NOT NULL,
            currency        TEXT    NOT NULL DEFAULT 'JPY',
            category        TEXT,
            description     TEXT,
            merchant        TEXT,
            bill_date       TEXT,
            raw_text        TEXT,
            source          TEXT    NOT NULL DEFAULT 'bot',
            receipt_file_id TEXT    NOT NULL DEFAULT '',
            receipt_url     TEXT    NOT NULL DEFAULT '',
            created_at      INTEGER NOT NULL,
            updated_at      INTEGER NOT NULL,
            _old_id         INTEGER,
            _old_source     TEXT,
            FOREIGN KEY (user_id) REFERENCES users_new(id) ON DELETE CASCADE
        )
    """)

    # 迁移旧 bills
    if bills_exists and bills_user_col:
        if bills_user_col == "user_id":
            await db.execute("""
                INSERT INTO bills_new (
                    user_id, amount, currency, category, description, merchant,
                    bill_date, raw_text, source, receipt_file_id, receipt_url,
                    created_at, updated_at, _old_id, _old_source
                )
                SELECT
                    COALESCE(un_tg.id, un_app.id) AS resolved_user_id,
                    CASE b.currency
                        WHEN 'JPY' THEN CAST(ROUND(b.amount) AS INTEGER)
                        ELSE CAST(ROUND(b.amount * 100) AS INTEGER)
                    END,
                    b.currency, b.category, b.description, b.merchant,
                    b.bill_date, b.raw_text,
                    CASE
                        WHEN un_tg.id IS NOT NULL THEN 'bot'
                        ELSE 'app'
                    END,
                    COALESCE(b.receipt_file_id, ''),
                    COALESCE(b.receipt_url, ''),
                    CAST(b.created_at AS INTEGER),
                    CAST(COALESCE(b.updated_at, b.created_at) AS INTEGER),
                    b.id, 'bot'
                FROM bills b
                -- 路径1：bills.user_id 是 tg_user_id
                LEFT JOIN users_new un_tg
                    ON un_tg.tg_user_id = b.user_id
                -- 路径2：bills.user_id 是 app_users.id
                LEFT JOIN app_users au
                    ON au.id = b.user_id
                LEFT JOIN users_new un_app
                    ON (un_app.email = au.email
                        OR (un_app.tg_user_id IS NOT NULL
                            AND un_app.tg_user_id = au.tg_user_id))
                -- 两条路径都找不到的才真正丢弃
                WHERE COALESCE(un_tg.id, un_app.id) IS NOT NULL
            """)

            # 诊断：列出找不到用户的孤立账单
            async with db.execute("""
                SELECT b.id, b.user_id, b.amount, b.bill_date
                FROM bills b
                LEFT JOIN users_new un_tg
                    ON un_tg.tg_user_id = b.user_id
                LEFT JOIN app_users au
                    ON au.id = b.user_id
                LEFT JOIN users_new un_app
                    ON (un_app.email = au.email
                        OR (un_app.tg_user_id IS NOT NULL
                            AND un_app.tg_user_id = au.tg_user_id))
                WHERE COALESCE(un_tg.id, un_app.id) IS NULL
            """) as cur:
                orphans = await cur.fetchall()
            if orphans:
                logger.warning("以下账单找不到对应用户，已跳过：")
                for row in orphans:
                    logger.warning(
                        "  bill.id=%s user_id=%s amount=%s date=%s",
                        row[0], row[1], row[2], row[3],
                    )
            else:
                logger.info("bills 全部关联成功，无孤立账单")
    # 迁移 app_bills
    if app_bills_exists and app_users_exists:
        await db.execute("""
            INSERT INTO bills_new (
                user_id, amount, currency, category, description, merchant,
                bill_date, raw_text, source, receipt_file_id, receipt_url,
                created_at, updated_at, _old_id, _old_source
            )
            SELECT
                un.id,
                CASE ab.currency
                    WHEN 'JPY' THEN CAST(ROUND(ab.amount) AS INTEGER)
                    ELSE CAST(ROUND(ab.amount * 100) AS INTEGER)
                END,
                ab.currency, ab.category, ab.description, ab.merchant,
                ab.bill_date, ab.raw_text, 'app',
                COALESCE(ab.receipt_file_id, ''),
                COALESCE(ab.receipt_url, ''),
                CAST(ab.created_at AS INTEGER),
                CAST(COALESCE(ab.updated_at, ab.created_at) AS INTEGER),
                ab.id, 'app'
            FROM app_bills ab
            JOIN app_users au ON ab.app_user_id = au.id
            JOIN users_new un ON (
                un.email = au.email
                OR (un.tg_user_id IS NOT NULL
                    AND un.tg_user_id = au.tg_user_id)
            )
        """)
        logger.info("app_bills 迁移完成")

    # ── 3. 新 bill_items 表 ───────────────────────────────────────────────

    await db.execute("""
        CREATE TABLE IF NOT EXISTS bill_items_new (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            bill_id     INTEGER NOT NULL,
            user_id     INTEGER NOT NULL,
            name        TEXT    NOT NULL,
            name_raw    TEXT    DEFAULT '',
            quantity    REAL    NOT NULL DEFAULT 1,
            unit_price  INTEGER,
            amount      INTEGER NOT NULL,
            item_type   TEXT    NOT NULL DEFAULT 'item',
            sort_order  INTEGER NOT NULL DEFAULT 0,
            FOREIGN KEY (bill_id)  REFERENCES bills_new(id) ON DELETE CASCADE,
            FOREIGN KEY (user_id)  REFERENCES users_new(id) ON DELETE CASCADE
        )
    """)

    # 迁移旧 bill_items
    if items_exists and items_user_col:
        # user 列名不影响迁移逻辑，因为 user_id 直接从 bills_new 取
        await db.execute("""
            INSERT INTO bill_items_new (
                bill_id, user_id, name, name_raw, quantity,
                unit_price, amount, item_type, sort_order
            )
            SELECT
                bn.id,
                bn.user_id,
                bi.name, COALESCE(bi.name_raw, ''), bi.quantity,
                CASE bn.currency
                    WHEN 'JPY' THEN
                        CASE WHEN bi.unit_price IS NOT NULL
                             THEN CAST(ROUND(bi.unit_price) AS INTEGER)
                             ELSE NULL END
                    ELSE
                        CASE WHEN bi.unit_price IS NOT NULL
                             THEN CAST(ROUND(bi.unit_price * 100) AS INTEGER)
                             ELSE NULL END
                END,
                CASE bn.currency
                    WHEN 'JPY' THEN CAST(ROUND(bi.amount) AS INTEGER)
                    ELSE CAST(ROUND(bi.amount * 100) AS INTEGER)
                END,
                bi.item_type, bi.sort_order
            FROM bill_items bi
            JOIN bills_new bn ON bn._old_id = bi.bill_id
                AND bn._old_source = 'bot'
        """)
        logger.info("bill_items 迁移完成")

    # 迁移 app_bill_items
    if await table_exists("app_bill_items"):
        await db.execute("""
            INSERT INTO bill_items_new (
                bill_id, user_id, name, name_raw, quantity,
                unit_price, amount, item_type, sort_order
            )
            SELECT
                bn.id,
                bn.user_id,
                abi.name, COALESCE(abi.name_raw, ''), abi.quantity,
                CASE bn.currency
                    WHEN 'JPY' THEN
                        CASE WHEN abi.unit_price IS NOT NULL
                             THEN CAST(ROUND(abi.unit_price) AS INTEGER)
                             ELSE NULL END
                    ELSE
                        CASE WHEN abi.unit_price IS NOT NULL
                             THEN CAST(ROUND(abi.unit_price * 100) AS INTEGER)
                             ELSE NULL END
                END,
                CASE bn.currency
                    WHEN 'JPY' THEN CAST(ROUND(abi.amount) AS INTEGER)
                    ELSE CAST(ROUND(abi.amount * 100) AS INTEGER)
                END,
                abi.item_type, abi.sort_order
            FROM app_bill_items abi
            JOIN bills_new bn ON bn._old_id = abi.bill_id
                AND bn._old_source = 'app'
        """)
        logger.info("app_bill_items 迁移完成")

    # ── 4. 去掉临时列（SQLite 3.35 以下不支持 DROP COLUMN，用重建法）────────

    await db.execute("""
        CREATE TABLE bills_clean AS
        SELECT id, user_id, amount, currency, category, description,
               merchant, bill_date, raw_text, source,
               receipt_file_id, receipt_url, created_at, updated_at
        FROM bills_new
    """)
    await db.execute("DROP TABLE bills_new")
    await db.execute("ALTER TABLE bills_clean RENAME TO bills_new")

    # ── 5. 旧表改名备份 ───────────────────────────────────────────────────

    for old, backup in [
        ("users",      "users_v4"),
        ("bills",      "bills_v4"),
        ("bill_items", "bill_items_v4"),
    ]:
        if not await table_exists(old):
            logger.info("原表 %s 不存在，跳过", old)
            continue

        if await table_exists(backup):
            # 备份表已存在：直接删除原表（数据已迁移到 *_new，备份表已有旧数据）
            logger.info("备份表 %s 已存在，直接删除原表 %s", backup, old)
            await db.execute(f"DROP TABLE {old}")
        else:
            # 正常情况：改名备份
            await db.execute(f"ALTER TABLE {old} RENAME TO {backup}")
            logger.info("原表 %s 已备份为 %s", old, backup)

    # 新表正式命名
    await db.execute("ALTER TABLE users_new      RENAME TO users")
    await db.execute("ALTER TABLE bills_new      RENAME TO bills")
    await db.execute("ALTER TABLE bill_items_new RENAME TO bill_items")

    # ── 6. 创建索引 ───────────────────────────────────────────────────────

    for ddl in [
        "CREATE UNIQUE INDEX IF NOT EXISTS idx_users_email        ON users(email)",
        "CREATE UNIQUE INDEX IF NOT EXISTS idx_users_app_username ON users(app_username)",
        "CREATE UNIQUE INDEX IF NOT EXISTS idx_users_tg           ON users(tg_user_id)",
        "CREATE INDEX IF NOT EXISTS idx_bills_user                ON bills(user_id)",
        "CREATE INDEX IF NOT EXISTS idx_bills_date                ON bills(bill_date)",
        "CREATE INDEX IF NOT EXISTS idx_bills_source              ON bills(source)",
        "CREATE INDEX IF NOT EXISTS idx_bill_items_bill           ON bill_items(bill_id)",
        "CREATE INDEX IF NOT EXISTS idx_bill_items_user           ON bill_items(user_id)",
    ]:
        await db.execute(ddl)

    await db.commit()
    logger.info("v5 迁移全部完成")


async def _v006_admin_permissions(db) -> None:
    """
    v006 — 管理员权限体系、精细化功能权限、IP 审计、系统配置表

    变更内容
    --------
    1. users 表新增四列（全部幂等，用 PRAGMA table_info 先探测）：
       - role             TEXT  DEFAULT 'user'   —— 'user' | 'admin'
       - permissions      TEXT  DEFAULT '[]'     —— JSON 数组，功能标识白名单
       - registration_ip  TEXT  DEFAULT ''       —— 首次注册 / 绑定时记录的 IP
       - last_login_ip    TEXT  DEFAULT ''       —— 最近一次登录 IP

    2. 新建 system_configs 表（IF NOT EXISTS，幂等）：
       动态存储全局配置项（AI Prompt、默认权限模板、默认模型等）。

    注意
    ----
    - 封禁逻辑沿用 is_active = 0，无需新字段
    - role 目前仅有两级，如需多级可在未来迁移中扩展
    - permissions 存储为 JSON 字符串，ORM 层负责 loads/dumps
    """
    import logging
    logger = logging.getLogger(__name__)

    # ── 1. 探测 users 表现有列 ───────────────────────────────────────────
    async with db.execute("PRAGMA table_info(users)") as cur:
        existing_cols = {row[1] for row in await cur.fetchall()}

    new_columns = [
        ("role",            "TEXT NOT NULL DEFAULT 'user'"),
        ("permissions",     "TEXT NOT NULL DEFAULT '[]'"),
        ("registration_ip", "TEXT NOT NULL DEFAULT ''"),
        ("last_login_ip",   "TEXT NOT NULL DEFAULT ''"),
    ]

    for col_name, col_def in new_columns:
        if col_name not in existing_cols:
            await db.execute(
                f"ALTER TABLE users ADD COLUMN {col_name} {col_def}"
            )
            logger.info("users: 新增列 %s", col_name)
        else:
            logger.info("users: 列 %s 已存在，跳过", col_name)

    # ── 2. 初始化已有管理员的 role（对接 ADMIN_IDS 环境变量）─────────────
    #
    # 如果环境变量 ADMIN_IDS 中已配置了管理员的 tg_user_id，
    # 则自动将这些用户的 role 升级为 'admin'，保证存量数据一致。
    try:
        import os
        raw_admin_ids = os.getenv("ADMIN_IDS", "")
        tg_admin_ids = [
            int(x.strip()) for x in raw_admin_ids.split(",")
            if x.strip().isdigit()
        ]
        if tg_admin_ids:
            placeholders = ",".join("?" * len(tg_admin_ids))
            await db.execute(
                f"""
                UPDATE users SET role = 'admin'
                WHERE tg_user_id IN ({placeholders})
                  AND role != 'admin'
                """,
                tg_admin_ids,
            )
            logger.info(
                "v006: 自动升级 %d 个 TG 管理员的 role → admin",
                len(tg_admin_ids),
            )
    except Exception as e:
        logger.warning("v006: 自动升级管理员 role 时出错（非致命）: %s", e)

    # ── 3. system_configs 表 ─────────────────────────────────────────────
    await db.execute("""
        CREATE TABLE IF NOT EXISTS system_configs (
            config_key   TEXT PRIMARY KEY,
            config_value TEXT NOT NULL DEFAULT '',
            description  TEXT NOT NULL DEFAULT '',
            updated_by   INTEGER,           -- users.id，NULL 表示系统默认值
            updated_at   INTEGER NOT NULL DEFAULT 0,
            FOREIGN KEY (updated_by) REFERENCES users(id) ON DELETE SET NULL
        )
    """)
    logger.info("system_configs 表已就绪")

    # ── 4. 写入默认配置项（INSERT OR IGNORE，不覆盖已有值）───────────────
    default_configs = [
        (
            "default_permissions",
            '["bot_text","bot_receipt"]',
            "新用户注册时自动分配的默认功能权限（JSON 数组）",
        ),
        (
            "ai_default_model",
            "gpt-4o-mini",
            "全局默认 AI 模型标识，对应 llm.yaml 中的 provider/model",
        ),
        (
            "bill_ocr_system_prompt",
            "",   # 空字符串表示使用代码内置 prompt，非空时覆盖
            "账单 OCR 的自定义 System Prompt，空字符串=使用代码默认值",
        ),
        (
            "registration_open",
            "true",
            "是否开放 App 端新用户注册，false 时 /register 接口返回 403",
        ),
        (
            "bot_welcome_message",
            "",
            "Bot /start 命令的欢迎语，空字符串=使用代码默认值",
        ),
        (
            "max_bills_per_user",
            "500",
            "每位用户最多保存的账单条数，超出后最旧记录将被清理",
        ),
    ]

    import time as _time
    now = int(_time.time())

    await db.executemany(
        """
        INSERT OR IGNORE INTO system_configs
            (config_key, config_value, description, updated_at)
        VALUES (?, ?, ?, ?)
        """,
        [(k, v, d, now) for k, v, d in default_configs],
    )
    logger.info("system_configs 已写入 %d 条默认配置", len(default_configs))

    # ── 5. 索引 ───────────────────────────────────────────────────────────
    await db.execute(
        "CREATE INDEX IF NOT EXISTS idx_users_role ON users(role)"
    )

    await db.commit()
    logger.info("v006 迁移完成：role/permissions/IP 字段 + system_configs 表")


ALL_MIGRATIONS: list[tuple[int, str, object]] = [
    (1, "bot_base_tables",  _v001_bot_tables),
    (2, "bills_bot_side",   _v002_bills_bot),
    (3, "app_users",        _v003_app_users),
    (4, "app_bills",        _v004_app_bills),
    (5, "unified_schema",   _v005_unified_schema),
    (6, "admin_permissions_and_configs", _v006_admin_permissions),
]
