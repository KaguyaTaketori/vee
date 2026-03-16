import logging
import psutil
from config import ADMIN_IDS, DISK_CRIT_THRESHOLD, DISK_WARN_THRESHOLD, TEMP_FILE_MAX_AGE_HOURS, disk_config
from services.user_service import cleanup_temp_files
from services.analytics import get_daily_stats, format_daily_report

logger = logging.getLogger(__name__)

_last_alert_level: str = "ok"

def reset_alert_level() -> None:
    global _last_alert_level
    _last_alert_level = "ok"


async def cleanup_job(context):
    cleanup_temp_files(max_age_hours=TEMP_FILE_MAX_AGE_HOURS)


async def broadcast_to_admins(
    context,
    msg: str,
    tag: str = "message",
    parse_mode: str = None,
) -> tuple[int, int]:
    if not ADMIN_IDS:
        return 0, 0

    success, failed = 0, 0
    for admin_id in ADMIN_IDS:
        try:
            await context.bot.send_message(
                chat_id=admin_id,
                text=msg,
                parse_mode=parse_mode,
            )
            success += 1
        except Exception as e:
            logger.warning(f"Failed to send {tag} to admin {admin_id}: {e}")
            failed += 1

    return success, failed


async def storage_alert_job(context):
    global _last_alert_level

    if not ADMIN_IDS:
        return

    disk = psutil.disk_usage("/")
    free_gb = disk.free / (1024 ** 3)
    percent = disk.percent

    if disk_config.is_critical(percent):
        current_level = "critical"
    elif disk_config.is_warning(percent):
        current_level = "warn"
    else:
        current_level = "ok"

    if current_level == _last_alert_level:
        return

    _last_alert_level = current_level

    if current_level == "critical":
        msg = t("disk_critical", percent=f"{percent:.1f}",
                threshold=disk_config.crit_threshold, free_gb=free_gb)
    elif current_level == "warn":
        msg = t("disk_warning",  percent=f"{percent:.1f}",
                threshold=disk_config.warn_threshold,  free_gb=free_gb)
    else:
        msg = t("disk_recovered", percent=f"{percent:.1f}")

    await broadcast_to_admins(context, msg, tag="storage alert")


async def daily_report_job(context):
    if not ADMIN_IDS:
        return
    stats = await get_daily_stats(days=1)
    msg = format_daily_report(stats, period="昨日")
    await broadcast_to_admins(context, msg, tag="daily report")
