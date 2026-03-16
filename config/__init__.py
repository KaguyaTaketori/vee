from config.settings import *
from config.disk_config import DiskConfig, disk_config, load_disk_config, save_disk_config, reload_disk_config

def init_config():
    import os
    _validate()
    os.makedirs(COOKIES_DIR, exist_ok=True)
    reload_disk_config()

def _validate():
    if not TOKEN:
        raise EnvironmentError("TELEGRAM_BOT_TOKEN 未在 .env 中配置")
