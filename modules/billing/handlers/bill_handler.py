# modules/billing/handlers/bill_handler.py
"""
modules/billing/handlers/bill_handler.py

Decoupling
──────────
``handle_bill_photo`` previously:
  • called photo.get_file() / download_to_memory() inside the handler body
  • built an InlineKeyboardMarkup directly and called processing_msg.edit_text()

Now it:
  • downloads photo bytes in the PTB adapter (handle_bill_photo) and passes
    image_b64 as a plain string to _bill_photo_impl
  • calls ctx.edit_keyboard() for the final confirmation message — keyboard
    is expressed as a platform-agnostic KeyboardLayout

All telegram.* usage is confined to the PTB adapter functions at the bottom
of this file.
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

    processing_msg = await update.message.reply_text("🤖 AI 正在解析账单，请稍候…")

    async def _edit(txt: str, **kw) -> None:
        await processing_msg.edit_text(txt, **kw)

    ctx = TelegramContext(
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

async def _bill_photo_impl(ctx: PlatformContext, image_b64: str) -> None:
    """Core photo-bill logic — receives base64 image bytes, no PTB objects.

    The processing message is already sent; ctx.edit() updates it.
    On success, ctx.edit_keyboard() replaces it with the confirmation card.
    """
    try:
        entry = await _get_parser().parse_image(user_id=ctx.user_id, image_base64=image_b64)
    except ValueError as exc:
        await ctx.edit(f"❌ 识别失败：{exc}\n\n请尝试发送更清晰的图片，或直接输入文字。")
        return
    except Exception as exc:
        logger.error("parse_image failed user=%s: %s", ctx.user_id, exc, exc_info=True)
        await ctx.edit("❌ 服务异常，请稍后重试。")
        return

    cache_id = await bill_cache.set(entry)
    await ctx.edit_keyboard(_build_confirmation_text(entry), _confirmation_keyboard(cache_id))
    logger.info("Bill photo confirmation sent: cache_id=%s user_id=%s", cache_id, ctx.user_id)


async def handle_bill_photo(update: Update, context: CallbackContext) -> None:
    """PTB adapter: download photo bytes, build a processing-ctx, delegate."""
    if not update.message or not update.message.photo:
        return

    user = update.message.from_user
    track_user(user)
    await warm_user_lang(user.id)

    processing_msg = await update.message.reply_text("🤖 AI 正在识别收据图片，请稍候…")

    # Download photo bytes in the adapter layer — _bill_photo_impl never sees PTB
    try:
        photo = update.message.photo[-1]
        tg_file = await photo.get_file()
        buf = BytesIO()
        await tg_file.download_to_memory(buf)
        image_b64 = base64.b64encode(buf.getvalue()).decode()
    except Exception as exc:
        logger.error("Photo download failed user=%s: %s", user.id, exc, exc_info=True)
        await processing_msg.edit_text("❌ 图片下载失败，请重试。")
        return

    # Build a TelegramContext whose edit() / edit_keyboard() target processing_msg
    async def _edit(txt: str, **kw) -> None:
        await processing_msg.edit_text(txt, **kw)

    ctx = TelegramContext(
        user_id=user.id,
        username=user.username or "",
        display_name=f"{user.first_name} {user.last_name or ''}".strip(),
        args=[],
        _reply_fn=update.message.reply_text,
        _edit_fn=_edit,
        _bot_send_fn=lambda chat_id, t: context.bot.send_message(chat_id=chat_id, text=t),
    )
    await _bill_photo_impl(ctx, image_b64)
