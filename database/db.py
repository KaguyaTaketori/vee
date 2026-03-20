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

