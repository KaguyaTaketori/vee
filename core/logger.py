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

def log_user(user, action, extra=None):
    if not user:
        return
    
    user_id = user.id
    username = user.username or "N/A"
    name = f"{user.first_name} {user.last_name or ''}".strip()
    
    extra_info = f" | {extra}" if extra else ""
    entry = f"{datetime.now()} | ID: {user_id} | @{username} | {name} | {action}{extra_info}"
    
    logging.getLogger("audit").info(entry)


def log_download(user, action, url, status, file_size=None, format_id=None):
    extra = f"url={url[:50]}... | status={status}"
    if file_size:
        mb = file_size / (1024 * 1024)
        extra += f" | size={mb:.1f}MB"
    if format_id:
        extra += f" | format={format_id}"
    log_user(user, action, extra)


def get_user_stats():
    if not os.path.exists(LOG_FILE):
        return "No users yet."
    
    users = {}
    with open(LOG_FILE, "r") as f:
        for line in f:
            parts = line.split("|")
            if len(parts) >= 4:
                user_id = parts[1].strip().replace("ID: ", "")
                action = parts[-1].strip()
                if user_id not in users:
                    users[user_id] = {"count": 0, "actions": set()}
                users[user_id]["count"] += 1
                users[user_id]["actions"].add(action)
    
    if not users:
        return "No users yet."
    
    msg = f"Total users: {len(users)}\n\n"
    for uid, data in list(users.items())[:10]:
        msg += f"ID: {uid} - {data['count']} actions: {', '.join(data['actions'])}\n"
    
    return msg
