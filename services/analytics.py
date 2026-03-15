import time
from database.db import get_db

async def get_daily_stats(days: int = 1) -> dict:
    since = time.time() - days * 86400

    async with get_db() as db:
        async with db.execute(
            "SELECT COUNT(*) FROM history WHERE timestamp > ?", (since,)
        ) as cur:
            total = (await cur.fetchone())[0]

        async with db.execute(
            "SELECT status, COUNT(*) FROM history WHERE timestamp > ? GROUP BY status",
            (since,),
        ) as cur:
            status_counts = {row[0]: row[1] for row in await cur.fetchall()}

        async with db.execute(
            "SELECT COUNT(DISTINCT user_id) FROM history WHERE timestamp > ?", (since,)
        ) as cur:
            active_users = (await cur.fetchone())[0]

        async with db.execute(
            "SELECT download_type, COUNT(*) FROM history WHERE timestamp > ? GROUP BY download_type",
            (since,),
        ) as cur:
            type_dist = {row[0]: row[1] for row in await cur.fetchall()}

        async with db.execute(
            """
            SELECT u.username, u.first_name, h.user_id, COUNT(*) as cnt
            FROM history h
            LEFT JOIN users u ON h.user_id = u.user_id
            WHERE h.timestamp > ?
            GROUP BY h.user_id
            ORDER BY cnt DESC LIMIT 5
            """,
            (since,),
        ) as cur:
            top_users = await cur.fetchall()

        async with db.execute(
            "SELECT SUM(file_size) FROM history WHERE timestamp > ? AND status = 'success'",
            (since,),
        ) as cur:
            total_bytes = (await cur.fetchone())[0] or 0

    return {
        "total": total,
        "success": status_counts.get("success", 0),
        "failed": status_counts.get("failed", 0),
        "active_users": active_users,
        "type_dist": type_dist,
        "top_users": top_users,
        "total_bytes": total_bytes,
    }


def format_daily_report(stats: dict, period: str = "今日") -> str:
    from utils.utils import format_bytes

    success_rate = (
        f"{stats['success'] / stats['total'] * 100:.1f}%"
        if stats["total"] > 0 else "N/A"
    )

    type_lines = "\n".join(
        f"  • {k}: {v} 次" for k, v in sorted(stats["type_dist"].items(), key=lambda x: -x[1])
    )

    top_lines = "\n".join(
        f"  {i+1}. {row[1] or row[0] or 'User'} ({row[2]}): {row[3]} 次"
        for i, row in enumerate(stats["top_users"])
    )

    return (
        f"📊 {period}统计报告\n"
        f"{'─' * 24}\n"
        f"📥 总下载：{stats['total']} 次\n"
        f"✅ 成功：{stats['success']}  ❌ 失败：{stats['failed']}\n"
        f"📈 成功率：{success_rate}\n"
        f"💾 总体积：{format_bytes(stats['total_bytes'])}\n"
        f"👥 活跃用户：{stats['active_users']} 人\n"
        f"\n📂 类型分布：\n{type_lines or '  暂无'}\n"
        f"\n🏆 活跃榜 Top5：\n{top_lines or '  暂无'}"
    )
