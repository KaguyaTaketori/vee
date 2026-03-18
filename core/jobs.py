"""
core/jobs.py
────────────
Scheduled job callbacks registered with PTB's JobQueue.

Changes from previous version
──────────────────────────────
* `_last_alert_level` was a bare module-level string — unsafe when
  multiple coroutines run concurrently.  It is now encapsulated in
  `_DiskAlertState` which uses `asyncio.Lock`.
"""
from __future__ import annotations

import asyncio
import logging

import psutil

from config import TEMP_FILE_MAX_AGE_HOURS, disk_config
from shared.services.user_service import cleanup_temp_files
from shared.services.analytics import get_daily_stats, format_daily_report
from shared.services.notifier import AdminNotifier

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Disk alert state  (replaces bare module-level string)
# ---------------------------------------------------------------------------

class _DiskAlertState:
    """Thread/coroutine-safe holder for the last-seen disk alert level."""

    def __init__(self) -> None:
        self._level: str = "ok"
        self._lock: asyncio.Lock | None = None   # created lazily inside the loop

    def _get_lock(self) -> asyncio.Lock:
        if self._lock is None:
            self._lock = asyncio.Lock()
        return self._lock

    async def get(self) -> str:
        async with self._get_lock():
            return self._level

    async def set(self, level: str) -> None:
        async with self._get_lock():
            self._level = level

    async def reset(self) -> None:
        await self.set("ok")


_disk_state = _DiskAlertState()


def reset_alert_level() -> None:
    """Synchronous shim — schedules an async reset on the running loop."""
    try:
        loop = asyncio.get_running_loop()
        loop.create_task(_disk_state.reset())
    except RuntimeError:
        # No running loop (e.g. called from a sync test)
        _disk_state._level = "ok"


# ---------------------------------------------------------------------------
# Job callbacks
# ---------------------------------------------------------------------------

async def cleanup_job(context) -> None:
    cleanup_temp_files(max_age_hours=TEMP_FILE_MAX_AGE_HOURS)


async def storage_alert_job(context) -> None:
    notifier: AdminNotifier = context.job.data["notifier"]

    disk = psutil.disk_usage("/")
    free_gb = disk.free / (1024 ** 3)
    percent = disk.percent
    current_level = disk_config.current_level(percent)

    last_level = await _disk_state.get()
    if current_level == last_level:
        return                        # no change — stay silent
    await _disk_state.set(current_level)

    from utils.i18n import t
    if current_level == "critical":
        msg = t("disk_critical", percent=f"{percent:.1f}",
                threshold=disk_config.crit_threshold, free_gb=f"{free_gb:.1f}")
    elif current_level == "warn":
        msg = t("disk_warning",  percent=f"{percent:.1f}",
                threshold=disk_config.warn_threshold, free_gb=f"{free_gb:.1f}")
    else:
        msg = t("disk_recovered", percent=f"{percent:.1f}")

    await notifier.notify_admins(msg)


async def daily_report_job(context) -> None:
    notifier: AdminNotifier = context.job.data["notifier"]
    stats = await get_daily_stats(days=1)
    msg = format_daily_report(stats, period="昨日")
    await notifier.notify_admins(msg)


async def bill_cache_gc_job(context) -> None:
    from modules.billing.services.bill_cache import bill_cache
    await bill_cache.purge_expired()
