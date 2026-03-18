"""
modules/billing/handlers/bill_handler.py
"""
from __future__ import annotations

import base64
import logging
from io import BytesIO

from telegram import Update
from telegram.ext import CallbackContext

from modules.billing.services.bill_cache import bill_cache, BillEntry
from modules.billing.services.bill_parser import BillParser
from shared.services.platform_context import PlatformContext, TelegramContext, btn
from shared.services.user_service import track_user, warm_user_lang
from utils.i18n import t
from utils.utils import require_message

logger = logging.getLogger(__name__)


def _get_parser() -> BillParser:
    import shared.integrations.llm.manager as llm_mod
    if llm_mod.llm_manager is None:
        raise RuntimeError("llm_manager not initialised")
    return BillParser(llm_mod.llm_manager)


def _build_confirmation_text(entry: BillEntry) -> str:
    return (
        f"📋 *账单确认*\n\n"
        f"💰 金额：`{entry.amount:.2f} {entry.currency}`\n"
        f"🏷️ 类别：{entry.category}\n"
        f"🏪 商家：{entry.merchant}\n"
        f"📝 描述：{entry.description}\n"
        f"📅 日期：{entry.bill_date}\n\n"
        f"请确认以上信息是否正确："
    )


def _confirmation_keyboard(cache_id: str):
    return [
        [btn("✅ 确认无误", f"bill_confirm:{cache_id}"),
         btn("✏️ 修改金额", f"bill_edit:{cache_id}")],
        [btn("❌ 取消记账", f"bill_cancel:{cache_id}")],
    ]


# ── /bill command ──────────────────────────────────────────────────────────

async def _bill_command_impl(ctx: PlatformContext) -> None:
    if not ctx.args:
        await ctx.send_markdown(
            "📝 *记账使用方式*\n\n"
            "• 直接发送文字：`星巴克拿铁 38元`\n"
            "• 发送收据图片（自动 OCR）\n"
            "• 或使用命令：`/bill 午餐 50元`"
        )
        return
    # Reuse text handler logic with args joined as text
    await _bill_text_impl(ctx, text=" ".join(ctx.args))


@require_message
async def handle_bill_command(update: Update, context: CallbackContext) -> None:
    ctx = TelegramContext.from_message(update, context)
    await _bill_command_impl(ctx)


# ── Text bill ─────────────────────────────────────────────────────────────

async def _bill_text_impl(ctx: PlatformContext, text: str) -> None:
    await ctx.edit("🤖 AI 正在解析账单，请稍候…")

    try:
        entry = await _get_parser().parse_text(user_id=ctx.user_id, text=text)
    except ValueError as exc:
        await ctx.edit(f"❌ 解析失败：{exc}\n\n请重新发送账单信息（如：星巴克咖啡 38元）。")
        return
    except Exception as exc:
        logger.error("parse_text failed user=%s: %s", ctx.user_id, exc, exc_info=True)
        await ctx.edit("❌ 服务异常，请稍后重试。")
        return

    cache_id = await bill_cache.set(entry)
    await ctx.send_keyboard(_build_confirmation_text(entry), _confirmation_keyboard(cache_id))
    logger.info("Bill confirmation sent: cache_id=%s user_id=%s", cache_id, ctx.user_id)


@require_message
async def handle_bill_text(update: Update, context: CallbackContext) -> None:
    user = update.message.from_user
    track_user(user)
    await warm_user_lang(user.id)

    text = update.message.text.strip()
    if not text:
        return

    # Send processing message first, then hand off to _impl via edit()
    processing_msg = await update.message.reply_text("🤖 AI 正在解析账单，请稍候…")

    # Build a context whose edit() targets the processing message
    from shared.services.platform_context import TelegramContext as _TC
    import functools

    async def _edit(text: str, **kw) -> None:
        await processing_msg.edit_text(text, **kw)

    ctx = _TC(
        user_id=user.id,
        username=user.username or "",
        display_name=f"{user.first_name} {user.last_name or ''}".strip(),
        args=list(context.args or []),
        _reply_fn=update.message.reply_text,
        _edit_fn=_edit,
        _bot_send_fn=lambda chat_id, t: context.bot.send_message(chat_id=chat_id, text=t),
    )
    await _bill_text_impl(ctx, text=text)


# ── Photo bill ────────────────────────────────────────────────────────────

async def handle_bill_photo(update: Update, context: CallbackContext) -> None:
    if not update.message or not update.message.photo:
        return

    user = update.message.from_user
    track_user(user)
    await warm_user_lang(user.id)

    processing_msg = await update.message.reply_text("🤖 AI 正在识别收据图片，请稍候…")

    try:
        photo = update.message.photo[-1]
        tg_file = await photo.get_file()
        buf = BytesIO()
        await tg_file.download_to_memory(buf)
        image_b64 = base64.b64encode(buf.getvalue()).decode()

        entry = await _get_parser().parse_image(user_id=user.id, image_base64=image_b64)
    except ValueError as exc:
        await processing_msg.edit_text(f"❌ 识别失败：{exc}\n\n请尝试发送更清晰的图片，或直接输入文字。")
        return
    except Exception as exc:
        logger.error("parse_image failed user=%s: %s", user.id, exc, exc_info=True)
        await processing_msg.edit_text("❌ 服务异常，请稍后重试。")
        return

    cache_id = await bill_cache.set(entry)
    # Build keyboard and send as new message (photo handlers don't have a text message to edit into)
    from telegram import InlineKeyboardButton, InlineKeyboardMarkup
    keyboard = InlineKeyboardMarkup([
        [InlineKeyboardButton("✅ 确认无误", callback_data=f"bill_confirm:{cache_id}"),
         InlineKeyboardButton("✏️ 修改金额", callback_data=f"bill_edit:{cache_id}")],
        [InlineKeyboardButton("❌ 取消记账", callback_data=f"bill_cancel:{cache_id}")],
    ])
    await processing_msg.edit_text(
        _build_confirmation_text(entry),
        parse_mode="Markdown",
        reply_markup=keyboard,
    )
    logger.info("Bill photo confirmation sent: cache_id=%s user_id=%s", cache_id, user.id)
