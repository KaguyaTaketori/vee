import os
import sys
from dotenv import load_dotenv

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
load_dotenv()

TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
if not TOKEN:
    print("Error: TELEGRAM_BOT_TOKEN not set in .env")
    sys.exit(1)

MAX_FILE_SIZE = int(os.getenv("MAX_FILE_SIZE", 2 * 1024 * 1024 * 1024))
MAX_CACHE_SIZE = int(os.getenv("MAX_CACHE_SIZE", 500 * 1024 * 1024))
BOT_API_URL = os.getenv("BOT_API_URL", "http://127.0.0.1:8082/bot")
LOCAL_MODE = os.getenv("LOCAL_MODE", "true").lower() == "true"
COOKIE_FILE = os.getenv("COOKIE_FILE", "")
COOKIES_DIR = os.path.join(BASE_DIR, "cookies")
COOKIE_REFRESH_CMD = os.getenv("COOKIE_REFRESH_CMD", "")
COOKIE_REFRESH_INTERVAL_HOURS = int(os.getenv("COOKIE_REFRESH_INTERVAL_HOURS", 12))
ADMIN_IDS = set(int(x) for x in os.getenv("ADMIN_IDS", "").split(",") if x.strip())
ALLOWED_USERS_FILE = os.getenv("ALLOWED_USERS_FILE", os.path.join(BASE_DIR, "allowed_users.txt"))
TEMP_DIR = os.getenv("TEMP_DIR", "/tmp")
TEMP_FILE_MAX_AGE_HOURS = int(os.getenv("TEMP_FILE_MAX_AGE_HOURS", 24))
CLEANUP_INTERVAL_HOURS = int(os.getenv("CLEANUP_INTERVAL_HOURS", 1))
USE_ARIA2 = os.getenv("USE_ARIA2", "false").lower() == "true"
ARIA2_CONNECTIONS = int(os.getenv("ARIA2_CONNECTIONS", "16"))
BOT_FILE_PREFIX = "vee_"
DISK_WARN_THRESHOLD = int(os.getenv("DISK_WARN_THRESHOLD", 80))
DISK_CRIT_THRESHOLD = int(os.getenv("DISK_CRIT_THRESHOLD", 90))
DISK_CHECK_INTERVAL_MINUTES = int(os.getenv("DISK_CHECK_INTERVAL_MINUTES", 60))

RATE_TIER_LIMITS: dict[str, int] = {
    "normal":   int(os.getenv("RATE_LIMIT_NORMAL",   "10")),
    "vip":      int(os.getenv("RATE_LIMIT_VIP",      "30")),
    "power":    int(os.getenv("RATE_LIMIT_POWER",    "60")),
    "blocked":  0,
}

os.makedirs(COOKIES_DIR, exist_ok=True)

CACHE_TTL = int(os.getenv("CACHE_TTL", "60"))


def get_config() -> dict:
    return {
        "max_file_size": MAX_FILE_SIZE,
        "max_cache_size": MAX_CACHE_SIZE,
        "cache_ttl": CACHE_TTL,
        "temp_dir": TEMP_DIR,
        "temp_file_max_age_hours": TEMP_FILE_MAX_AGE_HOURS,
        "cleanup_interval_hours": CLEANUP_INTERVAL_HOURS,
    }


def get_temp_template():
    return os.path.join(TEMP_DIR, f"{BOT_FILE_PREFIX}%(title)s.%(ext)s")
