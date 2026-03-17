# repositories/base.py
"""
BaseRepository
--------------
Thin wrapper around get_db() so that concrete repositories never import
get_db directly.  If the DB backend changes (e.g. aiosqlite → asyncpg),
only this file and database/db.py need touching.
"""

from database.db import get_db


class BaseRepository:
    """All repositories inherit from this class."""

    @staticmethod
    def _db():
        """Return the async context-manager for a DB connection."""
        return get_db()
