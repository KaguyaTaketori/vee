import logging
import psutil
import config as cfg
from datetime import datetime
from telegram import Update
from telegram.ext import CallbackContext
from config import get_config, DISK_WARN_THRESHOLD, DISK_CRIT_THRESHOLD, DISK_CHECK_INTERVAL_MINUTES, save_disk_config, reload_disk_config
from core import jobs as core_jobs
from core.handler_registry import command_handler
from services.container import services
from services.user_service import cleanup_temp_files
from services.analytics import get_daily_stats, format_daily_report, get_bot_stats
from utils.i18n import t
from utils.utils import require_admin, require_message, scan_temp_files, format_bytes

logger = logging.getLogger(__name__)


@command_handler("stats", admin_only=True)
@require_admin
@require_message
async def stats_command(update: Update, context: CallbackContext):
    msg = await get_bot_stats()
    await update.message.reply_text(msg)


@command_handler("status", admin_only=True)
@require_admin
@require_message
async def status_command(update: Update, context: CallbackContext):
    config = get_config()
    rate_status = services.limiter.get_status()

    temp_files, temp_size, _, _ = scan_temp_files(config["temp_dir"])

    msg = "📊 Bot Status\n\n"
    msg += f"CPU: {psutil.cpu_percent()}%\n"
    mem = psutil.virtual_memory()
    msg += f"Memory: {mem.percent}% ({format_bytes(mem.available)} available)\n\n"
    msg += f"Temp size: {format_bytes(temp_size)}\n\n"
    msg += f"Rate limit: {rate_status['max_downloads_per_hour']}/hour\n"
    msg += f"Rate enabled: {rate_status['enabled']}\n"
    msg += f"Cleanup interval: {config['cleanup_interval_hours']}h\n"
    msg += f"Temp file max age: {config['temp_file_max_age_hours']}h\n"
    msg += f"Max cache size: {format_bytes(config.get('max_cache_size', 500 * 1024 * 1024))}"

    await update.message.reply_text(msg)


@command_handler("storage", admin_only=True)
@require_admin
@require_message
async def storage_command(update: Update, context: CallbackContext):
    from config import disk_config
    disk = psutil.disk_usage("/")
    disk_percent = disk.percent
    total, used, free = disk.total, disk.used, disk.free

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
        f"Total disk: {format_bytes(total)}",
        f"Used: {format_bytes(used)} ({disk_percent:.1f}%)",
        f"Free: {format_bytes(free)}\n",
        f"Temp directory ({temp_dir}):",
        f"Files: {temp_files}",
        f"Size: {format_bytes(temp_size)}",
    ]

    if oldest_file and oldest_time:
        lines.append(f"Oldest: {oldest_file}")
        lines.append(f"   {datetime.fromtimestamp(oldest_time).strftime('%Y-%m-%d %H:%M')}")

    await update.message.reply_text("\n".join(lines))


@command_handler("setdisk", admin_only=True)
@require_admin
@require_message
async def setdisk_command(update: Update, context: CallbackContext):
    user_id = update.message.from_user.id

    if not context.args:
        await update.message.reply_text(
            t("setdisk_config", user_id, warn=DISK_WARN_THRESHOLD, crit=DISK_CRIT_THRESHOLD, interval=DISK_CHECK_INTERVAL_MINUTES)
        )
        return

    try:
        warn = int(context.args[0])
        crit = int(context.args[1]) if len(context.args) > 1 else warn + 10

        if not (0 < warn < crit <= 100):
            raise ValueError("warn must be < crit")
    except ValueError:
        await update.message.reply_text(t("setdisk_invalid", user_id))
        return

    save_disk_config(warn, crit)
    reload_disk_config()
    core_jobs.reset_alert_level()

    await update.message.reply_text(
        t("setdisk_updated", user_id, warn=warn, crit=crit)
    )


@command_handler("cleanup", admin_only=True)
@require_admin
@require_message
async def cleanup_command(update: Update, context: CallbackContext):
    cleanup_temp_files()
    user_id = update.message.from_user.id
    await update.message.reply_text(t("temp_cleaned", user_id))


@command_handler("report", admin_only=True)
@require_admin
@require_message
async def report_command(update: Update, context: CallbackContext):
    days = 1
    if context.args:
        try:
            days = max(1, min(90, int(context.args[0])))
        except ValueError:
            pass

    stats = await get_daily_stats(days=days)
    period = f"近 {days} 天" if days > 1 else "今日"
    await update.message.reply_text(format_daily_report(stats, period=period))
