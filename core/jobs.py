# core/jobs.py
import logging
import psutil
from config import DISK_CRIT_THRESHOLD, DISK_WARN_THRESHOLD, TEMP_FILE_MAX_AGE_HOURS, disk_config
from services.user_service import cleanup_temp_files
from services.analytics import get_daily_stats, format_daily_report
from services.notifier import AdminNotifier

logger = logging.getLogger(__name__)

_last_alert_level: str = "ok"


def reset_alert_level() -> None:
    global _last_alert_level
    _last_alert_level = "ok"


async def cleanup_job(context) -> None:
    cleanup_temp_files(max_age_hours=TEMP_FILE_MAX_AGE_HOURS)


async def storage_alert_job(context) -> None:
    global _last_alert_level

    notifier: AdminNotifier = context.job.data["notifier"]

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
        from utils.i18n import t
        msg = t("disk_critical", percent=f"{percent:.1f}",
                threshold=disk_config.crit_threshold, free_gb=free_gb)
    elif current_level == "warn":
        from utils.i18n import t
        msg = t("disk_warning", percent=f"{percent:.1f}",
                threshold=disk_config.warn_threshold, free_gb=free_gb)
    else:
        from utils.i18n import t
        msg = t("disk_recovered", percent=f"{percent:.1f}")

    await notifier.notify_admins(msg)


async def daily_report_job(context) -> None:
    notifier: AdminNotifier = context.job.data["notifier"]
    stats = await get_daily_stats(days=1)
    msg = format_daily_report(stats, period="昨日")
    await notifier.notify_admins(msg)
