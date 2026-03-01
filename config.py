import os
import sys
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
ADMIN_IDS = set(int(x) for x in os.getenv("ADMIN_IDS", "").split(",") if x.strip())
ALLOWED_USERS_FILE = os.getenv("ALLOWED_USERS_FILE", "/home/ubuntu/vee/allowed_users.txt")

TEMP_DIR = os.getenv("TEMP_DIR", "/tmp")
TEMP_FILE_MAX_AGE_HOURS = int(os.getenv("TEMP_FILE_MAX_AGE_HOURS", 24))
CLEANUP_INTERVAL_HOURS = int(os.getenv("CLEANUP_INTERVAL_HOURS", 1))

from core.users import update_user as _update_user, get_all_users as _get_all_users, get_user_info as _get_user_info


def track_user(user):
    if user:
        _update_user(user.id, username=user.username, first_name=user.first_name, last_name=user.last_name)


def get_allowed_users():
    users = set()
    if os.path.exists(ALLOWED_USERS_FILE):
        with open(ALLOWED_USERS_FILE, "r") as f:
            for line in f:
                line = line.strip()
                if line and line.isdigit():
                    users.add(int(line))
    return users | ADMIN_IDS


def save_allowed_users(users):
    with open(ALLOWED_USERS_FILE, "w") as f:
        for uid in sorted(users):
            f.write(f"{uid}\n")


def get_all_users_info():
    return _get_all_users()


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
        "temp_dir": TEMP_DIR,
        "temp_file_max_age_hours": TEMP_FILE_MAX_AGE_HOURS,
        "cleanup_interval_hours": CLEANUP_INTERVAL_HOURS,
    }


def cleanup_temp_files(max_age_hours: int = None):
    import time
    max_age_hours = max_age_hours or TEMP_FILE_MAX_AGE_HOURS
    if not os.path.exists(TEMP_DIR):
        return
    cutoff = time.time() - (max_age_hours * 3600)
    try:
        for fname in os.listdir(TEMP_DIR):
            if fname.startswith("yt_dlp_"):
                fpath = os.path.join(TEMP_DIR, fname)
                if os.path.getmtime(fpath) < cutoff:
                    try:
                        os.remove(fpath)
                    except OSError:
                        pass
    except OSError:
        pass
