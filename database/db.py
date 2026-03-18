import os
import logging
import aiosqlite
from contextlib import asynccontextmanager
from config import DB_PATH

logger = logging.getLogger(__name__)


@asynccontextmanager
async def get_db():
    """Context manager for database connections."""
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        yield db

async def init_db():
    """Initialize database tables if they don't exist."""
    logger.info("Initializing database at %s", DB_PATH)
    
    async with get_db() as db:
        await db.execute("PRAGMA journal_mode=WAL")
        await db.execute("PRAGMA foreign_keys=ON")
        

        await db.execute("""
            CREATE TABLE IF NOT EXISTS users (
                user_id INTEGER PRIMARY KEY,
                username TEXT,
                first_name TEXT,
                last_name TEXT,
                lang TEXT DEFAULT 'en',
                added_at REAL,
                last_seen REAL
            )
        """)
        
        await db.execute("""
            CREATE TABLE IF NOT EXISTS history (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER,
                url TEXT,
                download_type TEXT,
                status TEXT,
                file_size INTEGER,
                title TEXT,
                file_path TEXT,
                file_id TEXT,
                timestamp REAL,
                FOREIGN KEY (user_id) REFERENCES users(user_id)
            )
        """)

        await db.execute("""
            CREATE TABLE IF NOT EXISTS tasks (
                task_id     TEXT PRIMARY KEY,
                user_id     INTEGER NOT NULL,
                url         TEXT NOT NULL,
                download_type TEXT NOT NULL,
                format_id   TEXT,
                status      TEXT NOT NULL DEFAULT 'queued',
                progress    REAL DEFAULT 0.0,
                error       TEXT,
                file_path   TEXT,
                file_size   INTEGER,
                retry_count INTEGER DEFAULT 0,
                created_at  REAL NOT NULL,
                started_at  REAL,
                completed_at REAL,
                FOREIGN KEY (user_id) REFERENCES users(user_id)
            )
        """)


        await db.execute("""
            CREATE TABLE IF NOT EXISTS user_rate_tiers (
                user_id       INTEGER PRIMARY KEY,
                tier          TEXT NOT NULL DEFAULT 'normal',
                max_per_hour  INTEGER,      
                note          TEXT,         
                set_by        INTEGER,          
                set_at        REAL,
                FOREIGN KEY (user_id) REFERENCES users(user_id)
            )
        """)


        await db.execute("""
            CREATE INDEX IF NOT EXISTS idx_tasks_user_id ON tasks(user_id)
        """)

        await db.execute("""
            CREATE INDEX IF NOT EXISTS idx_tasks_status ON tasks(status)
        """)

        await db.execute("""
            CREATE INDEX IF NOT EXISTS idx_tasks_created_at ON tasks(created_at)
        """)
        
        await db.execute("""
            CREATE INDEX IF NOT EXISTS idx_history_user_id ON history(user_id)
        """)
        
        await db.execute("""
            CREATE INDEX IF NOT EXISTS idx_history_url ON history(url)
        """)
        
        await db.execute("""
            CREATE INDEX IF NOT EXISTS idx_history_timestamp ON history(timestamp)
        """)
        
        await db.execute("""
            CREATE TABLE IF NOT EXISTS rate_limit (
                user_id INTEGER,
                timestamp REAL,
                FOREIGN KEY (user_id) REFERENCES users(user_id)
            )
        """)
        
        await db.execute("""
            CREATE INDEX IF NOT EXISTS idx_rate_limit_user_id ON rate_limit(user_id)
        """)
        
        await db.execute("""
            CREATE INDEX IF NOT EXISTS idx_rate_limit_timestamp ON rate_limit(timestamp)
        """)
        
        await db.commit()
        
    logger.info("Database initialized successfully")
