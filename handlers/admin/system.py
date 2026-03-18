# handlers/admin/system.py
"""
handlers/admin/system.py

Decoupling
──────────
Two-layer pattern throughout:

  _xxx_impl(ctx: PlatformContext, ...)  — pure business logic, no PTB
  xxx_command(update, context)          — thin PTB adapter

All telegram.* imports are used only in the PTB adapter functions.
Business logic depends solely on PlatformContext and shared services.
"""
from __future__ import annotations

import logging
import psutil
from datetime import datetime

from telegram import Update
from telegram.ext import CallbackContext

from config import (
    get_config,
    DISK_WARN_THRESHOLD, DISK_CRIT_THRESHOLD, DISK_CHECK_INTERVAL_MINUTES,
    save_disk_config, reload_disk_config,
)
from core import jobs as core_jobs
from core.handler_registry import command_handler
from shared.services.container import services
from shared.services.platform_context import PlatformContext, TelegramContext
from shared.services.user_service import cleanup_temp_files
from shared.services.analytics import get_daily_stats, format_daily_report, get_bot_stats
from utils.i18n import t
from utils.utils import require_admin, require_message, scan_temp_files, format_bytes

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# /stats
# ---------------------------------------------------------------------------

async def _stats_impl(ctx: PlatformContext) -> None:
    msg = await get_bot_stats()
    await ctx.send(msg)


@command_handler("stats", admin_only=True)
@require_admin
@require_message
async def stats_command(update: Update, context: CallbackContext) -> None:
    await _stats_impl(TelegramContext.from_message(update, context))


# ---------------------------------------------------------------------------
# /status
# ---------------------------------------------------------------------------

async def _status_impl(ctx: PlatformContext) -> None:
    config = get_config()
    rate_status = services.limiter.get_status()
    temp_files, temp_size, _, _ = scan_temp_files(config["temp_dir"])

    mem = psutil.virtual_memory()
    lines = [
        "📊 Bot Status\n",
        f"CPU: {psutil.cpu_percent()}%",
        f"Memory: {mem.percent}% ({format_bytes(mem.available)} available)\n",
        f"Temp size: {format_bytes(temp_size)}\n",
        f"Rate limit: {rate_status['max_downloads_per_hour']}/hour",
        f"Rate enabled: {rate_status['enabled']}",
        f"Cleanup interval: {config['cleanup_interval_hours']}h",
        f"Temp file max age: {config['temp_file_max_age_hours']}h",
        f"Max cache size: {format_bytes(config.get('max_cache_size', 500 * 1024 * 1024))}",
    ]
    await ctx.send("\n".join(lines))


@command_handler("status", admin_only=True)
@require_admin
@require_message
async def status_command(update: Update, context: CallbackContext) -> None:
    await _status_impl(TelegramContext.from_message(update, context))


# ---------------------------------------------------------------------------
# /storage
# ---------------------------------------------------------------------------

async def _storage_impl(ctx: PlatformContext) -> None:
    from config import disk_config
    disk = psutil.disk_usage("/")
    disk_percent = disk.percent
    config = get_config()
    temp_dir = config["temp_dir"]
    temp_files, temp_size, oldest_file, oldest_time = scan_temp_files(temp_dir)

    level = disk_config.current_level(disk_percent)
    if level == "critical":
        alert = f"\n🚨 CRITICAL: 磁盘使用率已超过 {disk_config.crit_threshold}%!"
    elif level == "warn":
        alert = f"\n⚠️ WARNING: 磁盘使用率已超过 {disk_config.warn_threshold}%"
    else:
        alert = ""

    lines = [
        f"💾 Storage Status{alert}\n",
        f"Total disk: {format_bytes(disk.total)}",
        f"Used: {format_bytes(disk.used)} ({disk_percent:.1f}%)",
        f"Free: {format_bytes(disk.free)}\n",
        f"Temp directory ({temp_dir}):",
        f"Files: {temp_files}",
        f"Size: {format_bytes(temp_size)}",
    ]
    if oldest_file and oldest_time:
        lines.append(f"Oldest: {oldest_file}")
        lines.append(f"   {datetime.fromtimestamp(oldest_time).strftime('%Y-%m-%d %H:%M')}")

    await ctx.send("\n".join(lines))


@command_handler("storage", admin_only=True)
@require_admin
@require_message
async def storage_command(update: Update, context: CallbackContext) -> None:
    await _storage_impl(TelegramContext.from_message(update, context))


# ---------------------------------------------------------------------------
# /setdisk
# ---------------------------------------------------------------------------

async def _setdisk_impl(ctx: PlatformContext) -> None:
    if not ctx.args:
        await ctx.send(
            t("setdisk_config", ctx.user_id,
              warn=DISK_WARN_THRESHOLD,
              crit=DISK_CRIT_THRESHOLD,
              interval=DISK_CHECK_INTERVAL_MINUTES)
        )
        return

    try:
        warn = int(ctx.args[0])
        crit = int(ctx.args[1]) if len(ctx.args) > 1 else warn + 10
        if not (0 < warn < crit <= 100):
            raise ValueError("warn must be < crit")
    except ValueError:
        await ctx.send(t("setdisk_invalid", ctx.user_id))
        return

    save_disk_config(warn, crit)
    reload_disk_config()
    core_jobs.reset_alert_level()
    await ctx.send(t("setdisk_updated", ctx.user_id, warn=warn, crit=crit))


@command_handler("setdisk", admin_only=True)
@require_admin
@require_message
async def setdisk_command(update: Update, context: CallbackContext) -> None:
    await _setdisk_impl(TelegramContext.from_message(update, context))


# ---------------------------------------------------------------------------
# /cleanup
# ---------------------------------------------------------------------------

async def _cleanup_impl(ctx: PlatformContext) -> None:
    cleanup_temp_files()
    await ctx.send(t("temp_cleaned", ctx.user_id))


@command_handler("cleanup", admin_only=True)
@require_admin
@require_message
async def cleanup_command(update: Update, context: CallbackContext) -> None:
    await _cleanup_impl(TelegramContext.from_message(update, context))


# ---------------------------------------------------------------------------
# /report
# ---------------------------------------------------------------------------

async def _report_impl(ctx: PlatformContext) -> None:
    days = 1
    if ctx.args:
        try:
            days = max(1, min(90, int(ctx.args[0])))
        except ValueError:
            pass
    stats = await get_daily_stats(days=days)
    period = f"近 {days} 天" if days > 1 else "今日"
    await ctx.send(format_daily_report(stats, period=period))


@command_handler("report", admin_only=True)
@require_admin
@require_message
async def report_command(update: Update, context: CallbackContext) -> None:
    await _report_impl(TelegramContext.from_message(update, context))
