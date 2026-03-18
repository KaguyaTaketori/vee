# handlers/admin/llm.py
"""
handlers/admin/llm.py
─────────────────────
/setllm — 管理员命令，运行时切换 LLM Provider 并持久化。

用法：
  /setllm              — 列出可用 provider 及当前状态
  /setllm openai       — 切换到指定 provider
"""
from __future__ import annotations

import logging

from telegram import Update
from telegram.ext import CallbackContext

from config.llm_config import set_active_provider, get_available_providers
from core.handler_registry import command_handler
from shared.services.platform_context import PlatformContext, TelegramContext
from utils.utils import require_admin, require_message
import shared.integrations.llm.manager as llm_mod

logger = logging.getLogger(__name__)


async def _setllm_impl(ctx: PlatformContext) -> None:
    manager = llm_mod.llm_manager
    if manager is None:
        await ctx.send("❌ LLM Manager 尚未初始化。")
        return

    available = get_available_providers()
    current = manager._active_provider_name

    # 无参数 → 显示状态
    if not ctx.args:
        status = manager.get_status()
        lines = ["🤖 *LLM Provider 状态*\n"]
        for name, info in status.items():
            active_mark = " ◀ 当前" if info["active"] else ""
            key_count = len(info["keys"])
            available_keys = sum(1 for k in info["keys"] if k["available"])
            lines.append(
                f"• `{name}`{active_mark}\n"
                f"  模型: {info['model']}\n"
                f"  Keys: {available_keys}/{key_count} 可用"
            )
        lines.append(f"\n可用 provider: {', '.join(f'`{p}`' for p in available)}")
        lines.append("切换命令: `/setllm <provider名>`")
        await ctx.send_markdown("\n".join(lines))
        return

    # 有参数 → 切换
    target = ctx.args[0].lower()

    if target not in available:
        await ctx.send(
            f"❌ Provider `{target}` 不存在或未配置 Key。\n"
            f"可用: {', '.join(available)}"
        )
        return

    if target == current:
        await ctx.send(f"ℹ️ 当前已经是 `{target}`，无需切换。")
        return

    try:
        set_active_provider(target)
        await ctx.send_markdown(
            f"✅ LLM Provider 已切换\n\n"
            f"`{current}` → `{target}`\n\n"
            f"已持久化，重启后继续生效。"
        )
        logger.info("Admin %s switched LLM provider: %s -> %s", ctx.user_id, current, target)
    except ValueError as e:
        await ctx.send(f"❌ 切换失败：{e}")


@command_handler("setllm", admin_only=True)
@require_admin
@require_message
async def setllm_command(update: Update, context: CallbackContext) -> None:
    await _setllm_impl(TelegramContext.from_message(update, context))
