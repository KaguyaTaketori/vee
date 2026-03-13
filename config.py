import os
import sys
import time
from dotenv import load_dotenv

load_dotenv()

TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
if not TOKEN:
    print("Error: TELEGRAM_BOT_TOKEN not set in .env")
    sys.exit(1)

MAX_FILE_SIZE = int(os.getenv("MAX_FILE_SIZE", 2 * 1024 * 1024 * 1024))

BOT_API_URL = os.getenv("BOT_API_URL", "http://127.0.0.1:8082/bot")
LOCAL_MODE = os.getenv("LOCAL_MODE", "true").lower() == "true"

COOKIE_FILE = os.getenv("COOKIE_FILE", "")
COOKIE_REFRESH_CMD = os.getenv("COOKIE_REFRESH_CMD", "")
COOKIE_REFRESH_INTERVAL_HOURS = int(os.getenv("COOKIE_REFRESH_INTERVAL_HOURS", 12))
ADMIN_IDS = set(int(x) for x in os.getenv("ADMIN_IDS", "").split(",") if x.strip())
ALLOWED_USERS_FILE = os.getenv("ALLOWED_USERS_FILE", "/home/ubuntu/vee/allowed_users.txt")

TEMP_DIR = os.getenv("TEMP_DIR", "/tmp")
TEMP_FILE_MAX_AGE_HOURS = int(os.getenv("TEMP_FILE_MAX_AGE_HOURS", 24))
CLEANUP_INTERVAL_HOURS = int(os.getenv("CLEANUP_INTERVAL_HOURS", 1))

USE_ARIA2 = os.getenv("USE_ARIA2", "true").lower() == "true"
ARIA2_CONNECTIONS = int(os.getenv("ARIA2_CONNECTIONS", "16"))

MAX_CACHE_SIZE = int(os.getenv("MAX_CACHE_SIZE", 500 * 1024 * 1024))

CACHE_TTL = 60

_allowed_users_cache = {"data": None, "time": 0}
_users_db_cache = {"data": None, "time": 0}

from core.users import update_user as _update_user, get_all_users as _get_all_users, get_user_info as _get_user_info, force_persist as _force_persist_users
from core.history import force_persist as _force_persist_history


def track_user(user):
    if user:
        _update_user(user.id, username=user.username, first_name=user.first_name, last_name=user.last_name)


def get_allowed_users():
    global _allowed_users_cache
    now = time.time()
    if _allowed_users_cache["data"] and (now - _allowed_users_cache["time"]) < CACHE_TTL:
        return _allowed_users_cache["data"]
    
    users = set()
    if os.path.exists(ALLOWED_USERS_FILE):
        with open(ALLOWED_USERS_FILE, "r") as f:
            for line in f:
                line = line.strip()
                if line and line.isdigit():
                    users.add(int(line))
    
    result = users | ADMIN_IDS
    _allowed_users_cache = {"data": result, "time": now}
    return result


def save_allowed_users(users):
    with open(ALLOWED_USERS_FILE, "w") as f:
        for uid in sorted(users):
            f.write(f"{uid}\n")
    global _allowed_users_cache
    _allowed_users_cache = {"data": None, "time": 0}


def get_all_users_info():
    global _users_db_cache
    now = time.time()
    if _users_db_cache["data"] and (now - _users_db_cache["time"]) < CACHE_TTL:
        return _users_db_cache["data"]
    
    data = _get_all_users()
    _users_db_cache = {"data": data, "time": now}
    return data


def get_user_display_name(user_id: int) -> str:
    info = _get_user_info(user_id)
    if info:
        if info.get("username"):
            return f"@{info['username']}"
        if info.get("first_name"):
            return info["first_name"]
    return str(user_id)


def get_config() -> dict:
    return {
        "max_file_size": MAX_FILE_SIZE,
        "max_cache_size": MAX_CACHE_SIZE,
        "temp_dir": TEMP_DIR,
        "temp_file_max_age_hours": TEMP_FILE_MAX_AGE_HOURS,
        "cleanup_interval_hours": CLEANUP_INTERVAL_HOURS,
    }


BOT_FILE_PREFIX = "vee_"


def get_temp_template():
    return os.path.join(TEMP_DIR, f"{BOT_FILE_PREFIX}%(title)s.%(ext)s")


def cleanup_temp_files(max_age_hours: int = 24):
    import time
    if max_age_hours is None:
        max_age_hours = TEMP_FILE_MAX_AGE_HOURS
    if not os.path.exists(TEMP_DIR):
        return
    cutoff = time.time() - (max_age_hours * 3600)
    try:
        for fname in os.listdir(TEMP_DIR):
            if fname.startswith(BOT_FILE_PREFIX):
                fpath = os.path.join(TEMP_DIR, fname)
                if os.path.getmtime(fpath) < cutoff:
                    try:
                        os.remove(fpath)
                    except OSError:
                        pass
    except OSError:
        pass


def persist_all_data():
    _force_persist_users()
    _force_persist_history()
