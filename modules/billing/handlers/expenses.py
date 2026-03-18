"""
modules/billing/handlers/expenses.py
─────────────────────────────────────
/jz 手动记账命令 + 小票图片 OCR 识别。

Decoupling
──────────
原版的 handle_receipt_photo 直接调用了：
  • context.bot.get_file(photo.file_id)        —— PTB Bot API
  • file.download_to_drive(file_path)          —— PTB File 对象方法

本版本将图片字节的获取封装在 _download_photo_bytes()，
业务逻辑只接触 bytes，与 Telegram 完全解耦。
"""
from __future__ import annotations

import logging
import os
from typing import Protocol, runtime_checkable

from telegram import Update
from telegram.ext import CallbackContext

from config import TEMP_DIR
from core.handler_registry import command_handler
from modules.billing.services.ocr_providers import ocr_provider
from modules.billing.database.expense_repo import ExpenseRepository
from shared.services.platform_context import PlatformContext, TelegramContext
from utils.utils import require_message

logger = logging.getLogger(__name__)

_expense_repo = ExpenseRepository()


# ---------------------------------------------------------------------------
# Platform-agnostic photo provider Protocol
# ---------------------------------------------------------------------------

@runtime_checkable
class PhotoProvider(Protocol):
    """Provide the raw bytes of a user-uploaded photo.

    The concrete Telegram implementation fetches via PTB; tests can inject
    a simple mock that returns pre-baked bytes.
    """

    async def get_bytes(self) -> bytes:
        """Download and return the full image data."""
        ...

    @property
    def file_id(self) -> str:
        """Platform-native cache key (e.g. Telegram file_id)."""
        ...


class TelegramPhotoProvider:
    """Fetch photo bytes via PTB using a photo object and bot reference."""

    def __init__(self, photo, bot) -> None:
        self._photo = photo   # telegram.PhotoSize
        self._bot = bot

    @property
    def file_id(self) -> str:
        return self._photo.file_id

    async def get_bytes(self) -> bytes:
        from io import BytesIO
        tg_file = await self._bot.get_file(self._photo.file_id)
        buf = BytesIO()
        await tg_file.download_to_memory(buf)
        return buf.getvalue()


# ---------------------------------------------------------------------------
# /jz — manual expense
# ---------------------------------------------------------------------------

async def _jz_impl(ctx: PlatformContext) -> None:
    if len(ctx.args) < 2:
        await ctx.send(
            "❌ 格式错误。请使用: /jz <金额> <描述/类别>\n"
            "例如: /jz 15.5 便利店早餐"
        )
        return

    try:
        amount = float(ctx.args[0])
    except ValueError:
        await ctx.send("❌ 金额格式不正确，请输入数字。")
        return

    description = " ".join(ctx.args[1:])
    category = "日常"

    await _expense_repo.add_expense(ctx.user_id, amount, category, description)
    await ctx.send(f"✅ 记账成功！\n💰 金额: {amount}\n📝 描述: {description}")


@command_handler("jz")
@require_message
async def manual_expense_command(update: Update, context: CallbackContext) -> None:
    await _jz_impl(TelegramContext.from_message(update, context))


# ---------------------------------------------------------------------------
# Receipt photo — OCR recognition
# ---------------------------------------------------------------------------

async def _handle_receipt_impl(
    ctx: PlatformContext,
    provider: PhotoProvider,
) -> None:
    """Core OCR logic — operates on a PhotoProvider, not a PTB object."""
    await ctx.edit("👁️ AI 正在识别小票，请稍候...")

    # Download image bytes via provider (platform-agnostic)
    try:
        image_bytes = await provider.get_bytes()
    except Exception as exc:
        logger.error("Failed to download photo file_id=%s: %s", provider.file_id, exc, exc_info=True)
        await ctx.edit("❌ 图片下载失败，请重试。")
        return

    # Write to a temp file so the OCR provider (which may call a subprocess) can read it
    file_path = os.path.join(TEMP_DIR, f"receipt_{provider.file_id}.jpg")
    try:
        with open(file_path, "wb") as fh:
            fh.write(image_bytes)

        result = await ocr_provider.analyze_receipt(file_path)

        if not result or "amount" not in result:
            await ctx.edit("❌ AI 未能识别出小票上的有效信息，请确保小票清晰或尝试手动记账。")
            return

        amount = float(result.get("amount", 0.0))
        if amount <= 0:
            await ctx.edit("⚠️ 识别完成，但未发现有效金额。")
            return

        category = result.get("category", "未分类")
        description = result.get("description", "小票识别记录")

        await _expense_repo.add_expense(
            ctx.user_id, amount, category, description, provider.file_id
        )
        await ctx.send_markdown(
            f"✅ **记账成功 (AI自动识别)**\n\n"
            f"💰 金额: `{amount}`\n"
            f"🏷️ 类别: {category}\n"
            f"📝 详情: {description}"
        )

    except Exception as exc:
        logger.error("OCR failed for user=%s: %s", ctx.user_id, exc, exc_info=True)
        await ctx.edit("❌ 处理图片时发生系统错误。")
    finally:
        if os.path.exists(file_path):
            os.remove(file_path)


@require_message
async def handle_receipt_photo(update: Update, context: CallbackContext) -> None:
    if not update.message or not update.message.photo:
        return

    photo = update.message.photo[-1]   # highest resolution
    processing_msg = await update.message.reply_text("👁️ AI 正在识别小票，请稍候...")

    # Build a TelegramContext whose edit() targets the processing message
    async def _edit(text: str, **kw) -> None:
        await processing_msg.edit_text(text, **kw)

    user = update.message.from_user
    ctx = TelegramContext(
        user_id=user.id,
        username=user.username or "",
        display_name=f"{user.first_name} {user.last_name or ''}".strip(),
        args=[],
        _reply_fn=update.message.reply_text,
        _edit_fn=_edit,
        _bot_send_fn=lambda chat_id, t: context.bot.send_message(chat_id=chat_id, text=t),
    )

    provider = TelegramPhotoProvider(photo=photo, bot=context.bot)
    await _handle_receipt_impl(ctx, provider)
