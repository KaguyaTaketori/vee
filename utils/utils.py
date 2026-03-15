"""Common utilities for the bot - extracted to reduce duplication."""

import os
import asyncio
from typing import Optional
from datetime import datetime
from functools import wraps
from telegram import Update
from telegram.ext import CallbackContext
from services.user_service import get_allowed_users
from config import ADMIN_IDS, BOT_FILE_PREFIX

def is_user_allowed(user_id: int) -> bool:
    allowed = get_allowed_users()
    return not allowed or user_id in allowed

def get_running_loop() -> Optional[asyncio.AbstractEventLoop]:
    """Safely get the running event loop, falling back to the default loop."""
    try:
        return asyncio.get_running_loop()
    except RuntimeError:
        try:
            return asyncio.get_event_loop()
        except RuntimeError:
            return None

def format_history_item(item: dict) -> str:
    dt = datetime.fromtimestamp(item["timestamp"])
    status = "✅" if item.get("status") == "success" else "❌"
    size = f" ({item['file_size'] // (1024 * 1024)}MB)" if item.get("file_size") else ""
    title = (item.get("title") or "N/A")[:40]
    error_msg = (item.get("error") or "")[:50]

    lines = [
        f"{status} {item['download_type']}{size}",
        f"   {title}",
        f"   {error_msg}",
        f"   {dt.strftime('%Y-%m-%d %H:%M')}",
    ]
    return "\n".join(lines) + "\n\n"

def format_history_list(history: list, header: str) -> str:
    if not history:
        return header + "（暂无记录）"
    body = "".join(format_history_item(item) for item in history)
    return header + body

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
            logger.warning(
                f"Unauthorized admin command attempt: "
                f"user_id={user_id}, command={update.message.text}"
            )
            await update.message.reply_text(t("not_authorized", user_id))
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
