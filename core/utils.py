"""Common utilities for the bot - extracted to reduce duplication."""

import os
from functools import wraps
from telegram import Update
from telegram.ext import CallbackContext

from config import ADMIN_IDS, BOT_FILE_PREFIX


def format_bytes(bytes_val: int | float) -> str:
    """Format bytes into human-readable string."""
    if bytes_val is None:
        return "?"
    for unit in ['B', 'KB', 'MB', 'GB', 'TB']:
        if bytes_val < 1024:
            return f"{bytes_val:.1f}{unit}"
        bytes_val /= 1024
    return f"{bytes_val:.1f}PB"


def check_admin(user_id: int) -> bool:
    """Check if user is admin."""
    return ADMIN_IDS and user_id in ADMIN_IDS


def require_admin(func):
    """Decorator to require admin access for commands."""
    @wraps(func)
    async def wrapper(update: Update, context: CallbackContext):
        if not update.message:
            return
        user_id = update.message.from_user.id
        if ADMIN_IDS and user_id not in ADMIN_IDS:
            return
        return await func(update, context)
    return wrapper


def scan_temp_files(temp_dir: str) -> tuple[int, int, str | None, float | None]:
    """
    Scan temp directory for bot files.
    Returns: (file_count, total_size, oldest_file, oldest_time)
    """
    count = 0
    total_size = 0
    oldest_file = None
    oldest_time = None
    
    if not os.path.exists(temp_dir):
        return count, total_size, oldest_file, oldest_time
    
    for fname in os.listdir(temp_dir):
        fpath = os.path.join(temp_dir, fname)
        if os.path.isfile(fpath):
            if fname.startswith(BOT_FILE_PREFIX):
                count += 1
                try:
                    total_size += os.path.getsize(fpath)
                except:
                    pass
                mtime = os.path.getmtime(fpath)
                if oldest_time is None or mtime < oldest_time:
                    oldest_time = mtime
                    oldest_file = fname
    
    return count, total_size, oldest_file, oldest_time