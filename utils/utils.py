import asyncio
from typing import Optional
from utils.auth       import is_user_allowed, check_admin, require_admin, require_message
from utils.formatters import format_bytes, format_history_item, format_history_list
from utils.fs         import scan_temp_files                                               

def get_running_loop() -> Optional[asyncio.AbstractEventLoop]:
    try:
        return asyncio.get_running_loop()
    except RuntimeError:
        return None
