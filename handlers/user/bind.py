# handlers/user/bind.py
"""
/bind <code>  — 将当前 TG 账号绑定到 App 账号。

流程：
  1. 用户在 App 个人中心点「申请绑定码」，得到 6 位数字码
  2. 用户在 Bot 发 /bind 123456
  3. Bot 调服务端内部接口核销绑定码，写入 tg_user_id
  4. 回复成功/失败信息
"""
from __future__ import annotations

import logging
import os

import httpx
from telegram import Update
from telegram.ext import CallbackContext

from utils.utils import require_message

logger = logging.getLogger(__name__)

_API_BASE         = os.getenv("INTERNAL_API_BASE", "http://127.0.0.1:8000")
_INTERNAL_SECRET  = os.getenv("INTERNAL_API_SECRET", "")


async def _call_bind_api(tg_user_id: int, code: str) -> tuple[bool, str]:
    """
    调服务端内部绑定接口。
    Returns (success, message)
    """
    url = f"{_API_BASE}/v1/me/internal/tg-bind"
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.post(url, params={
                "tg_user_id":      tg_user_id,
                "code":            code,
                "internal_secret": _INTERNAL_SECRET,
            })

        if resp.status_code == 204:
            return True, "ok"

        # 尝试解析错误信息
        try:
            detail = resp.json().get("detail", "未知错误")
        except Exception:
            detail = resp.text[:100]
        return False, detail

    except httpx.TimeoutException:
        return False, "服务连接超时，请稍后重试"
    except Exception as e:
        logger.error("bind api error: %s", e)
        return False, "服务异常，请稍后重试"


@require_message
async def handle_bind_command(update: Update, context: CallbackContext) -> None:
    user    = update.message.from_user
    args    = context.args or []

    # 没有传验证码
    if not args:
        await update.message.reply_text(
            "🔗 *绑定 App 账号*\n\n"
            "使用方式：`/bind <验证码>`\n\n"
            "请先在 App 个人中心 → Telegram 绑定 → 申请验证码，\n"
            "然后将 6 位验证码发送到这里。",
            parse_mode="Markdown",
        )
        return

    code = args[0].strip()

    # 简单校验格式
    if not code.isdigit() or len(code) != 6:
        await update.message.reply_text("❌ 验证码格式不正确，应为 6 位数字。")
        return

    processing = await update.message.reply_text("🔄 正在验证...")

    success, message = await _call_bind_api(user.id, code)

    if success:
        await processing.edit_text(
            "✅ *绑定成功！*\n\n"
            "您的 Telegram 账号已与 App 账号关联，\n"
            "Bot 和 App 现在共享同一个 AI 使用配额。",
            parse_mode="Markdown",
        )
        logger.info("TG bind success: tg_user_id=%d code=%s", user.id, code)
    else:
        await processing.edit_text(
            f"❌ 绑定失败：{message}\n\n"
            "请重新在 App 申请验证码后再试。"
        )
        logger.warning(
            "TG bind failed: tg_user_id=%d code=%s reason=%s",
            user.id, code, message,
        )
