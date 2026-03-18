# modules/billing/handlers/bill_handler.py
"""
modules/billing/handlers/bill_handler.py

变更说明（相对原版）：
1. _bill_text_impl：删除开头重复的 ctx.edit()，避免 BadRequest: Message is not modified
2. _build_confirmation_text：对 category/description/merchant/bill_date 做兜底，防止显示空值或 unknown
3. _confirmation_keyboard：改为支持多字段编辑的按钮布局
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
from utils.utils import require_message

logger = logging.getLogger(__name__)


def _get_parser() -> BillParser:
    import shared.integrations.llm.manager as llm_mod
    if llm_mod.llm_manager is None:
        raise RuntimeError("llm_manager not initialised")
    return BillParser(llm_mod.llm_manager)


def _build_confirmation_text(entry: BillEntry) -> str:
    # 兜底处理，防止空值或 unknown 直接显示给用户
    category    = entry.category    or "未分类"
    description = entry.description or "—"
    merchant    = entry.merchant if entry.merchant and entry.merchant != "unknown" else "未知"
    bill_date   = entry.bill_date   or "未知"

    return (
        f"📋 *账单确认*\n\n"
        f"💰 金额：`{entry.amount:.2f} {entry.currency}`\n"
        f"🏷️ 类别：{category}\n"
        f"🏪 商家：{merchant}\n"
        f"📝 描述：{description}\n"
        f"📅 日期：{bill_date}\n\n"
        f"请确认以上信息是否正确："
    )


def _confirmation_keyboard(cache_id: str):
    """多字段编辑键盘。callback_data 格式：bill_edit:{field}:{cache_id}"""
    return [
        [btn("✅ 确认无误", f"bill_confirm:{cache_id}")],
        [
            btn("✏️ 改金额", f"bill_edit:amount:{cache_id}"),
            btn("🏷️ 改类别", f"bill_edit:category:{cache_id}"),
        ],
        [
            btn("🏪 改商家", f"bill_edit:merchant:{cache_id}"),
            btn("📝 改描述", f"bill_edit:description:{cache_id}"),
        ],
        [
            btn("📅 改日期", f"bill_edit:bill_date:{cache_id}"),
            btn("❌ 取消记账", f"bill_cancel:{cache_id}"),
        ],
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
    # ⚠️ 不在这里再次 edit "正在解析"——调用方（handle_bill_text / _bill_command_impl）
    # 已经发出或 edit 了等待消息，重复 edit 相同内容会触发 BadRequest: Message is not modified

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
    await ctx.edit_keyboard(_build_confirmation_text(entry), _confirmation_keyboard(cache_id))
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


@require_message
async def handle_jz_command(update: Update, context: CallbackContext) -> None:
    ctx = TelegramContext.from_message(update, context)
    await _bill_command_impl(ctx)
