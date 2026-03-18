"""
utils/utils.py
──────────────
Single re-export hub for the utils package.

RULE: all application code imports from `utils.utils` (this file).
Sub-modules (auth, formatters, fs) are implementation details and
should NOT be imported directly from outside the utils package.

This avoids the "two valid import paths for the same symbol" confusion
that was present before (e.g. both `from utils.auth import require_admin`
and `from utils.utils import require_admin` worked, creating ambiguity).
"""
from __future__ import annotations

import asyncio
from typing import Optional

# ── Auth ──────────────────────────────────────────────────────────────────
from utils.auth import (
    is_user_allowed,
    check_admin,
    require_admin,
    require_message,
    guard_require_admin,
)

# ── Formatting ────────────────────────────────────────────────────────────
from utils.formatters import (
    format_bytes,
    format_history_item,
    format_history_list,
)

# ── File system ───────────────────────────────────────────────────────────
from utils.fs import scan_temp_files


# ── Async helpers ─────────────────────────────────────────────────────────

def get_running_loop() -> Optional[asyncio.AbstractEventLoop]:
    """Return the running event loop, or None if there is none."""
    try:
        return asyncio.get_running_loop()
    except RuntimeError:
        return None


__all__ = [
    # auth
    "is_user_allowed",
    "check_admin",
    "require_admin",
    "require_message",
    "guard_require_admin",
    # formatters
    "format_bytes",
    "format_history_item",
    "format_history_list",
    # fs
    "scan_temp_files",
    # async
    "get_running_loop",
]
