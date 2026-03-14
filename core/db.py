import os
import logging
import aiosqlite

logger = logging.getLogger(__name__)

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DB_PATH = os.path.join(BASE_DIR, "bot_data.db")


async def init_db():
    """Initialize database tables if they don't exist."""
    logger.info(f"Initializing database at {DB_PATH}")
    
    async with aiosqlite.connect(DB_PATH) as db:
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


async def get_db():
    """Context manager for database connections."""
    return aiosqlite.connect(DB_PATH)
