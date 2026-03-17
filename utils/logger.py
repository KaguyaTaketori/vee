import logging
import os
from datetime import datetime
from logging.handlers import RotatingFileHandler

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
LOG_DIR = os.path.join(BASE_DIR, "logs")
os.makedirs(LOG_DIR, exist_ok=True)

LOG_FILE = os.path.join(LOG_DIR, "bot_users.log")

def setup_logging():
    log_format = "%(asctime)s - %(name)s - %(levelname)s - %(message)s"

    root_logger = logging.getLogger()
    root_logger.setLevel(logging.INFO)

    console_handler = logging.StreamHandler()
    console_handler.setFormatter(logging.Formatter(log_format))
    root_logger.addHandler(console_handler)

    file_handler = RotatingFileHandler(
        os.path.join(LOG_DIR, "app.log"),
        maxBytes=5 * 1024 * 1024,
        backupCount=3
    )
    file_handler.setFormatter(logging.Formatter(log_format))
    root_logger.addHandler(file_handler)

    audit_logger = logging.getLogger("audit")
    audit_logger.setLevel(logging.INFO)
    audit_logger.propagate = False

    audit_handler = RotatingFileHandler(
        LOG_FILE,
        maxBytes=5 * 1024 * 1024,
        backupCount=5
    )
    audit_handler.setFormatter(logging.Formatter(log_format))
    audit_logger.addHandler(audit_handler)


def log_user(
    user_id: int,
    username: str,
    name: str,
    action: str,
    extra: str | None = None,
) -> None:
    extra_info = f" | {extra}" if extra else ""
    entry = f"{datetime.now()} | ID: {user_id} | @{username} | {name} | {action}{extra_info}"
    logging.getLogger("audit").info(entry)


def log_download(
    user_id: int,
    username: str,
    name: str,
    action: str,
    url: str,
    status: str,
    file_size: int | None = None,
    format_id: str | None = None,
) -> None:
    _MAX_URL_LEN = 50
    url_display = url[:_MAX_URL_LEN] + ("..." if len(url) > _MAX_URL_LEN else "")
    extra = f"url={url_display} | status={status}"
    if file_size:
        mb = file_size / (1024 * 1024)
        extra += f" | size={mb:.1f}MB"
    if format_id:
        extra += f" | format={format_id}"
    log_user(user_id, username, name, action, extra)


