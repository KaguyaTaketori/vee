import os, json, logging
from dataclasses import dataclass
from config.settings import BASE_DIR, DISK_WARN_THRESHOLD, DISK_CRIT_THRESHOLD

logger = logging.getLogger(__name__)
_DISK_CONFIG_FILE = os.path.join(BASE_DIR, "disk_config.json")


@dataclass
class DiskConfig:
    warn_threshold: int = DISK_WARN_THRESHOLD
    crit_threshold: int = DISK_CRIT_THRESHOLD

    def is_critical(self, percent: float) -> bool:
        return percent >= self.crit_threshold

    def is_warning(self, percent: float) -> bool:
        return percent >= self.warn_threshold

    def current_level(self, percent: float) -> str:
        """返回 'critical' | 'warn' | 'ok'"""
        if self.is_critical(percent): return "critical"
        if self.is_warning(percent):  return "warn"
        return "ok"


def load_disk_config() -> dict:
    if os.path.exists(_DISK_CONFIG_FILE):
        try:
            with open(_DISK_CONFIG_FILE) as f:
                data = json.load(f)
            warn = int(data.get("warn_threshold", DISK_WARN_THRESHOLD))
            crit = int(data.get("crit_threshold", DISK_CRIT_THRESHOLD))
            if 0 < warn < crit <= 100:
                return {"warn_threshold": warn, "crit_threshold": crit}
            logger.warning("disk_config.json 值无效，已回退到默认值")
        except Exception as e:
            logger.error("读取 disk_config.json 失败: %s", e)
    return {"warn_threshold": DISK_WARN_THRESHOLD, "crit_threshold": DISK_CRIT_THRESHOLD}


def save_disk_config(warn: int, crit: int) -> None:
    with open(_DISK_CONFIG_FILE, "w") as f:
        json.dump({"warn_threshold": warn, "crit_threshold": crit}, f)


disk_config = DiskConfig()


def reload_disk_config() -> None:
    cfg = load_disk_config()
    disk_config.warn_threshold = cfg["warn_threshold"]
    disk_config.crit_threshold = cfg["crit_threshold"]
    logger.info("磁盘配置已重载：warn=%d%%, crit=%d%%",
                disk_config.warn_threshold, disk_config.crit_threshold)
