import os
import logging
from config import TEMP_DIR
from telegram import Update
from telegram.ext import CallbackContext
from core.handler_registry import command_handler
from modules.billing.services.ocr_service import analyze_receipt
from modules.billing.services.ocr_providers import ocr_provider
from modules.billing.database.expense_repo import ExpenseRepository
from utils.utils import require_message

_expense_repo = ExpenseRepository()

@command_handler("jz")
@require_message
async def manual_expense_command(update: Update, context: CallbackContext):
    user_id = update.message.from_user.id
    args = context.args

    if len(args) < 2:
        await update.message.reply_text("❌ 格式错误。请使用: /jz <金额> <描述/类别>\n例如: /jz 15.5 便利店早餐")
        return

    try:
        amount = float(args[0])
        description = " ".join(args[1:])
        
        # 简单分类逻辑（或调用 AI 分类）
        category = "日常" 
        
        await _expense_repo.add_expense(user_id, amount, category, description)
        await update.message.reply_text(f"✅ 记账成功！\n💰 金额: {amount}\n📝 描述: {description}")
    except ValueError:
        await update.message.reply_text("❌ 金额格式不正确，请输入数字。")

@require_message
async def handle_receipt_photo(update: Update, context: CallbackContext):
    user_id = update.message.from_user.id
    photo = update.message.photo[-1]
    
    status_msg = await update.message.reply_text("👁️ AI 正在识别小票，请稍候...")
    
    file = await context.bot.get_file(photo.file_id)
    file_path = os.path.join(TEMP_DIR, f"receipt_{photo.file_id}.jpg")
    await file.download_to_drive(file_path)

    try:
        # 调用抽象层
        result = await ocr_provider.analyze_receipt(file_path)
        
        if not result or "amount" not in result:
            await status_msg.edit_text("❌ AI 未能识别出小票上的有效信息，请确保小票清晰或尝试手动记账。")
            return
        
        amount = float(result.get("amount", 0.0))
        category = result.get("category", "未分类")
        description = result.get("description", "小票识别记录")

        if amount <= 0:
            await status_msg.edit_text("⚠️ 识别完成，但未发现有效金额。")
            return

        # 存入数据库
        await _expense_repo.add_expense(user_id, amount, category, description, photo.file_id)

        # 格式化回复
        reply_text = (
            f"✅ **记账成功 (AI自动识别)**\n\n"
            f"💰 金额: `{amount}`\n"
            f"🏷️ 类别: {category}\n"
            f"📝 详情: {description}"
        )
        await status_msg.edit_text(reply_text, parse_mode="Markdown")

    except Exception as e:
        await status_msg.edit_text("❌ 处理图片时发生系统错误。")
    finally:
        if os.path.exists(file_path):
            os.remove(file_path)
