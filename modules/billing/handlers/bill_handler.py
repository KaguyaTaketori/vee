"""
handlers/bills/bill_handler.py

接收用户发送的账单文本或图片，调用 AI 解析，
暂存缓存后发送带 InlineKeyboard 的确认卡片。

注册方式（在 main.py / bot_setup 中）：
    from handlers.bills.bill_handler import handle_bill_text, handle_bill_photo
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND & BillModeFilter(), handle_bill_text))
    app.add_handler(MessageHandler(filters.PHOTO, handle_bill_photo))
    app.add_handler(CommandHandler("bill", handle_bill_command))
"""
from __future__ import annotations

import base64
import logging
from io import BytesIO

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import CallbackContext

from modules.billing.services.bill_cache import bill_cache, BillEntry
from services.bill_parser import BillParser
from services.user_service import track_user, warm_user_lang
from utils.i18n import t
from utils.utils import require_message

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# 懒加载依赖（避免循环导入）
# ---------------------------------------------------------------------------

def _get_parser() -> BillParser:
    """从全局单例获取 BillParser，确保 llm_manager 已初始化。"""
    from llm.manager import llm_manager
    if llm_manager is None:
        raise RuntimeError("llm_manager 未初始化，请在 bot 启动时调用 build_llm_manager_from_env()")
    return BillParser(llm_manager)


# ---------------------------------------------------------------------------
# 确认卡片构建
# ---------------------------------------------------------------------------

def _build_confirmation_keyboard(cache_id: str) -> InlineKeyboardMarkup:
    """构建账单确认的 InlineKeyboard。"""
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton("✅ 确认无误", callback_data=f"bill_confirm:{cache_id}"),
            InlineKeyboardButton("✏️ 修改金额", callback_data=f"bill_edit:{cache_id}"),
        ],
        [
            InlineKeyboardButton("❌ 取消记账", callback_data=f"bill_cancel:{cache_id}"),
        ],
    ])


def _build_confirmation_text(entry: BillEntry) -> str:
    """格式化账单确认卡片的文本内容。"""
    return (
        f"📋 *账单确认*\n\n"
        f"💰 金额：`{entry.amount:.2f} {entry.currency}`\n"
        f"🏷️ 类别：{entry.category}\n"
        f"🏪 商家：{entry.merchant}\n"
        f"📝 描述：{entry.description}\n"
        f"📅 日期：{entry.bill_date}\n\n"
        f"请确认以上信息是否正确："
    )


# ---------------------------------------------------------------------------
# 文本账单处理
# ---------------------------------------------------------------------------

@require_message
async def handle_bill_text(update: Update, context: CallbackContext) -> None:
    """处理用户发送的文本账单（如：「午餐 38元」「Starbucks $6.5」）。"""
    user = update.message.from_user
    user_id = user.id

    track_user(user)
    await warm_user_lang(user_id)

    text = update.message.text.strip()
    if not text:
        return

    # 发送"处理中"提示
    processing_msg = await update.message.reply_text("🤖 AI 正在解析账单，请稍候…")

    try:
        parser = _get_parser()
        entry = await parser.parse_text(user_id=user_id, text=text)
    except ValueError as e:
        await processing_msg.edit_text(f"❌ 解析失败：{e}\n\n请重新发送账单信息（如：星巴克咖啡 38元）。")
        return
    except Exception as e:
        logger.error("Unexpected error parsing bill for user=%s: %s", user_id, e, exc_info=True)
        await processing_msg.edit_text("❌ 服务异常，请稍后重试。")
        return

    # 暂存缓存
    cache_id = await bill_cache.set(entry)

    # 发送确认卡片（编辑掉"处理中"消息）
    await processing_msg.edit_text(
        _build_confirmation_text(entry),
        parse_mode="Markdown",
        reply_markup=_build_confirmation_keyboard(cache_id),
    )
    logger.info("Bill confirmation sent: cache_id=%s user_id=%s", cache_id, user_id)


# ---------------------------------------------------------------------------
# 图片账单处理
# ---------------------------------------------------------------------------

async def handle_bill_photo(update: Update, context: CallbackContext) -> None:
    """处理用户发送的收据图片。"""
    if not update.message or not update.message.photo:
        return

    user = update.message.from_user
    user_id = user.id

    track_user(user)
    await warm_user_lang(user_id)

    processing_msg = await update.message.reply_text("🤖 AI 正在识别收据图片，请稍候…")

    try:
        # 取最高分辨率图片
        photo = update.message.photo[-1]
        tg_file = await photo.get_file()
        buf = BytesIO()
        await tg_file.download_to_memory(buf)
        image_b64 = base64.b64encode(buf.getvalue()).decode()

        parser = _get_parser()
        entry = await parser.parse_image(user_id=user_id, image_base64=image_b64)

    except ValueError as e:
        await processing_msg.edit_text(f"❌ 识别失败：{e}\n\n请尝试发送更清晰的图片，或直接输入文字。")
        return
    except Exception as e:
        logger.error("Unexpected error parsing bill photo for user=%s: %s", user_id, e, exc_info=True)
        await processing_msg.edit_text("❌ 服务异常，请稍后重试。")
        return

    cache_id = await bill_cache.set(entry)

    await processing_msg.edit_text(
        _build_confirmation_text(entry),
        parse_mode="Markdown",
        reply_markup=_build_confirmation_keyboard(cache_id),
    )
    logger.info("Bill photo confirmation sent: cache_id=%s user_id=%s", cache_id, user_id)


# ---------------------------------------------------------------------------
# /bill 命令入口（方便测试）
# ---------------------------------------------------------------------------

@require_message
async def handle_bill_command(update: Update, context: CallbackContext) -> None:
    """/bill <文本> 命令快捷入口。"""
    if not context.args:
        await update.message.reply_text(
            "📝 *记账使用方式*\n\n"
            "• 直接发送文字：`星巴克拿铁 38元`\n"
            "• 发送收据图片（自动 OCR）\n"
            "• 或使用命令：`/bill 午餐 50元`",
            parse_mode="Markdown",
        )
        return

    # 模拟文本消息
    update.message.text = " ".join(context.args)
    await handle_bill_text(update, context)
