from datetime import datetime

def format_bytes(bytes_val: int | float | None) -> str:
    if bytes_val is None:
        return "?"
    for unit in ["B", "KB", "MB", "GB", "TB"]:
        if bytes_val < 1024:
            return f"{bytes_val:.1f}{unit}"
        bytes_val /= 1024
    return f"{bytes_val:.1f}PB"

def format_history_item(item: dict) -> str:
    dt     = datetime.fromtimestamp(item["timestamp"])
    status = "✅" if item.get("status") == "success" else "❌"
    size   = f" ({item['file_size'] // (1024*1024)}MB)" if item.get("file_size") else ""
    title  = (item.get("title") or "N/A")[:40]
    err    = (item.get("error") or "")[:50]
    return "\n".join([
        f"{status} {item['download_type']}{size}",
        f"   {title}", f"   {err}",
        f"   {dt.strftime('%Y-%m-%d %H:%M')}",
    ]) + "\n\n"

def format_history_list(history: list, header: str, empty_msg: str = "（暂无记录）") -> str:
    if not history:
        return header + empty_msg
    return header + "".join(format_history_item(i) for i in history)
