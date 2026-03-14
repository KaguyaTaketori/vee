import os
import json
from datetime import datetime
from logging.handlers import RotatingFileHandler
import logging

LOG_FILE = "/home/ubuntu/vee/bot_users.log"
LOG_MAX_BYTES = 5 * 1024 * 1024
LOG_BACKUP_COUNT = 3

class UserLogger:
    def __init__(self):
        self._handler = None
        self._setup_handler()
    
    def _setup_handler(self):
        os.makedirs(os.path.dirname(LOG_FILE), exist_ok=True)
        self._handler = RotatingFileHandler(
            LOG_FILE,
            maxBytes=LOG_MAX_BYTES,
            backupCount=LOG_BACKUP_COUNT,
            encoding="utf-8"
        )
        self._handler.setFormatter(logging.Formatter("%(message)s"))
        
        self._logger = logging.getLogger("user_logger")
        self._logger.setLevel(logging.INFO)
        self._logger.addHandler(self._handler)
        self._logger.propagate = False
    
    def log_user(self, user, action, extra=None):
        if not user:
            return
        
        try:
            user_id = user.id
            username = user.username or "N/A"
            name = f"{user.first_name} {user.last_name or ''}".strip()
            
            entry = {
                "timestamp": datetime.now().isoformat(),
                "user_id": user_id,
                "username": username,
                "name": name,
                "action": action,
                "extra": extra
            }
            
            self._logger.info(json.dumps(entry))
        except Exception:
            pass
    
    def log_download(self, user, action, url, status, file_size=None, format_id=None):
        extra = {
            "url": url[:50] + "..." if len(url) > 50 else url,
            "status": status
        }
        if file_size:
            extra["size_mb"] = round(file_size / (1024 * 1024), 1)
        if format_id:
            extra["format_id"] = format_id
        self.log_user(user, action, extra)
    
    def get_user_stats(self):
        if not os.path.exists(LOG_FILE):
            return "No users yet."
        
        users = {}
        try:
            with open(LOG_FILE, "r", encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        entry = json.loads(line)
                        user_id = str(entry.get("user_id", ""))
                        action = entry.get("action", "")
                        if user_id and user_id not in users:
                            users[user_id] = {"count": 0, "actions": set()}
                        users[user_id]["count"] += 1
                        users[user_id]["actions"].add(action)
                    except (json.JSONDecodeError, KeyError):
                        continue
        except Exception:
            return "Error reading log file."
        
        if not users:
            return "No users yet."
        
        msg = f"Total users: {len(users)}\n\n"
        for uid, data in list(users.items())[:10]:
            msg += f"ID: {uid} - {data['count']} actions: {', '.join(data['actions'])}\n"
        
        return msg

user_logger = UserLogger()

def log_user(user, action, extra=None):
    user_logger.log_user(user, action, extra)

def log_download(user, action, url, status, file_size=None, format_id=None):
    user_logger.log_download(user, action, url, status, file_size, format_id)

def get_user_stats():
    return user_logger.get_user_stats()
