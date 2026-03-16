import os
from config import BOT_FILE_PREFIX

def scan_temp_files(temp_dir: str) -> tuple[int, int, str | None, float | None]:
    count, total_size, oldest_file, oldest_time = 0, 0, None, None
    if not os.path.exists(temp_dir):
        return count, total_size, oldest_file, oldest_time
    for fname in os.listdir(temp_dir):
        fpath = os.path.join(temp_dir, fname)
        if not (os.path.isfile(fpath) and fname.startswith(BOT_FILE_PREFIX)):
            continue
        count += 1
        try:    total_size += os.path.getsize(fpath)
        except OSError: pass
        try:    mtime = os.path.getmtime(fpath)
        except OSError: continue
        if oldest_time is None or mtime < oldest_time:
            oldest_time, oldest_file = mtime, fname
    return count, total_size, oldest_file, oldest_time
