# services/analytics.py
"""
Analytics service
-----------------
Formatting and business logic only.  All SQL now lives in
repositories.AnalyticsRepository – this module never imports get_db.
"""

from utils.utils import format_bytes

_repo = None


def _get_repo():
    global _repo
    if _repo is None:
        from repositories.analytics_repo import AnalyticsRepository
        _repo = AnalyticsRepository()
    return _repo


async def get_daily_stats(days: int = 1) -> dict:
    """Return aggregated download statistics for the last *days* day(s)."""
    return await _get_repo().get_daily_stats(days=days)


def format_daily_report(stats: dict, period: str = "今日") -> str:
    success_rate = (
        f"{stats['success'] / stats['total'] * 100:.1f}%"
        if stats["total"] > 0
        else "N/A"
    )

    type_lines = "\n".join(
        f"  • {k}: {v} 次"
        for k, v in sorted(stats["type_dist"].items(), key=lambda x: -x[1])
    )

    top_lines = "\n".join(
        f"  {i + 1}. {row[1] or row[0] or 'User'} ({row[2]}): {row[3]} 次"
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


async def get_bot_stats() -> str:
    """
    High-level statistics summary for the /stats admin command.
    Previously called get_user_stats() in utils/logger.py.
    """
    data = await _get_repo().get_summary_stats()
    return (
        f"📊 Bot Statistics\n"
        f"Total registered users: {data['total_users']}\n"
        f"Total downloads: {data['total_downloads']}\n"
        f"Recent failures: {len(data['recent_failures'])}\n"
    )
