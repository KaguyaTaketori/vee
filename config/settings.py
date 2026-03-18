import os
from dotenv import load_dotenv

load_dotenv()

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

def _parse_size(val: str) -> int:
    val = val.strip().upper()
    units = {"KB": 1024, "MB": 1024**2, "GB": 1024**3, "TB": 1024**4}
    for suffix, mult in units.items():
        if val.endswith(suffix):
            return int(float(val[:-len(suffix)]) * mult)
    return int(val)


TOKEN: str | None    = os.getenv("TELEGRAM_BOT_TOKEN")
DB_PATH: str         = os.getenv("DB_PATH", os.path.join(BASE_DIR, "bot_data.db"))
MAX_FILE_SIZE: int = _parse_size(os.getenv("MAX_FILE_SIZE", str(2 * 1024 * 1024 * 1024)))
MAX_CACHE_SIZE: int = _parse_size(os.getenv("MAX_CACHE_SIZE", str(500 * 1024 * 1024)))
BOT_API_URL: str     = os.getenv("BOT_API_URL", "http://127.0.0.1:8082/bot")
LOCAL_MODE: bool     = os.getenv("LOCAL_MODE", "true").lower() == "true"
COOKIE_FILE: str     = os.getenv("COOKIE_FILE", "")
COOKIES_DIR: str     = os.path.join(BASE_DIR, "cookies")
COOKIE_REFRESH_CMD: str            = os.getenv("COOKIE_REFRESH_CMD", "")
COOKIE_REFRESH_INTERVAL_HOURS: int = int(os.getenv("COOKIE_REFRESH_INTERVAL_HOURS", 12))
ADMIN_IDS: set[int]  = set(int(x) for x in os.getenv("ADMIN_IDS","").split(",") if x.strip())
ALLOWED_USERS_FILE: str            = os.getenv("ALLOWED_USERS_FILE", os.path.join(BASE_DIR, "allowed_users.txt"))
TEMP_DIR: str               = os.getenv("TEMP_DIR", "/tmp")
TEMP_FILE_MAX_AGE_HOURS: int = int(os.getenv("TEMP_FILE_MAX_AGE_HOURS", 24))
CLEANUP_INTERVAL_HOURS: int  = int(os.getenv("CLEANUP_INTERVAL_HOURS", 1))
USE_ARIA2: bool         = os.getenv("USE_ARIA2", "false").lower() == "true"
ARIA2_CONNECTIONS: int  = int(os.getenv("ARIA2_CONNECTIONS", "16"))
BOT_FILE_PREFIX: str    = "vee_"
DISK_WARN_THRESHOLD: int           = int(os.getenv("DISK_WARN_THRESHOLD", 80))
DISK_CRIT_THRESHOLD: int           = int(os.getenv("DISK_CRIT_THRESHOLD", 90))
DISK_CHECK_INTERVAL_MINUTES: int   = int(os.getenv("DISK_CHECK_INTERVAL_MINUTES", 60))
RATE_TIER_LIMITS: dict[str, int]   = {
    "normal":  int(os.getenv("RATE_LIMIT_NORMAL", "10")),
    "vip":     int(os.getenv("RATE_LIMIT_VIP",    "30")),
    "power":   int(os.getenv("RATE_LIMIT_POWER",  "60")),
    "blocked": 0,
}
CACHE_TTL: int = int(os.getenv("CACHE_TTL", "60"))
LLM_PROVIDER = os.getenv("LLM_PROVIDER", "openai_compatible")
LLM_API_KEY = os.getenv("LLM_API_KEY", "")
LLM_BASE_URL = os.getenv("LLM_BASE_URL", "https://api.openai.com/v1")
LLM_MODEL_NAME = os.getenv("LLM_MODEL_NAME", "gpt-4o-mini")


def get_config() -> dict:
    return {
        "max_file_size":           MAX_FILE_SIZE,
        "max_cache_size":          MAX_CACHE_SIZE,
        "cache_ttl":               CACHE_TTL,
        "temp_dir":                TEMP_DIR,
        "temp_file_max_age_hours": TEMP_FILE_MAX_AGE_HOURS,
        "cleanup_interval_hours":  CLEANUP_INTERVAL_HOURS,
    }

def get_temp_template() -> str:
    return os.path.join(TEMP_DIR, f"{BOT_FILE_PREFIX}%(title)s.%(ext)s")
