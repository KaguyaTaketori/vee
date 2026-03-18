# modules/billing/handlers/bill_callbacks.py
"""
账单 InlineKeyboard 回调处理器。

注册方式：在 BillingModule.setup() 中 import 本文件即可触发注册副作用：
    import modules.billing.handlers.bill_callbacks  # noqa: F401
"""
from __future__ import annotations

import logging

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, ForceReply
from telegram.ext import CallbackContext

from core.callback_bus import register          # ← 从 core 层导入，不再依赖 downloader
from modules.billing.database.bills import insert_bill
from modules.billing.services.bill_cache import bill_cache, BillEntry
from utils.i18n import t

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# 工具函数
# ---------------------------------------------------------------------------

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


def _build_confirmation_keyboard(cache_id: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton("✅ 确认无误", callback_data=f"bill_confirm:{cache_id}"),
            InlineKeyboardButton("✏️ 修改金额", callback_data=f"bill_edit:{cache_id}"),
        ],
        [
            InlineKeyboardButton("❌ 取消记账", callback_data=f"bill_cancel:{cache_id}"),
        ],
    ])


def _parse_cache_id(data: str) -> str:
    return data.split(":", 1)[1]


# ---------------------------------------------------------------------------
# ✅ 确认
# ---------------------------------------------------------------------------

@register(lambda d: d.startswith("bill_confirm:"))
async def _cb_bill_confirm(query, context: CallbackContext) -> None:
    user_id = query.from_user.id
    cache_id = _parse_cache_id(query.data)

    await query.answer()

    entry = await bill_cache.get(cache_id)
    if entry is None:
        await query.edit_message_text("⏰ 确认超时，账单数据已过期，请重新发送。")
        return

    if entry.user_id != user_id:
        await query.answer("❌ 这不是你的账单。", show_alert=True)
        return

    try:
        rowid = await insert_bill(entry)
    except Exception as e:
        logger.error("insert_bill failed user=%s cache_id=%s: %s", user_id, cache_id, e, exc_info=True)
        await query.edit_message_text(f"❌ 入库失败，请稍后重试。\n（错误：{e}）")
        return

    await bill_cache.delete(cache_id)
    await query.edit_message_text(
        f"✅ *账单已记录！*\n\n"
        f"💰 {entry.amount:.2f} {entry.currency}｜{entry.category}\n"
        f"🏪 {entry.merchant}｜{entry.bill_date}\n"
        f"📌 记录编号：`#{rowid}`",
        parse_mode="Markdown",
    )
    logger.info("Bill confirmed: rowid=%s user_id=%s cache_id=%s", rowid, user_id, cache_id)


# ---------------------------------------------------------------------------
# ✏️ 修改金额
# ---------------------------------------------------------------------------

@register(lambda d: d.startswith("bill_edit:"))
async def _cb_bill_edit(query, context: CallbackContext) -> None:
    user_id = query.from_user.id
    cache_id = _parse_cache_id(query.data)

    entry = await bill_cache.get(cache_id)
    if entry is None:
        await query.answer("⏰ 账单已过期，请重新发送。", show_alert=True)
        return

    if entry.user_id != user_id:
        await query.answer("❌ 这不是你的账单。", show_alert=True)
        return

    await query.answer()

    context.user_data["bill_edit_cache_id"] = cache_id
    context.user_data["bill_edit_message_id"] = query.message.message_id

    await query.message.reply_text(
        f"当前金额：`{entry.amount:.2f} {entry.currency}`\n"
        f"请输入新的金额（纯数字，如 `128.5`）：",
        parse_mode="Markdown",
        reply_markup=ForceReply(selective=True, input_field_placeholder="请输入新金额"),
    )


async def handle_bill_edit_reply(update, context: CallbackContext) -> None:
    """处理修改金额的 ForceReply 回复，在 BillingModule.setup() 中注册为 MessageHandler。"""
    if not update.message:
        return

    user_id = update.message.from_user.id
    cache_id = context.user_data.get("bill_edit_cache_id")
    original_message_id = context.user_data.get("bill_edit_message_id")

    if not cache_id:
        return

    context.user_data.pop("bill_edit_cache_id", None)
    context.user_data.pop("bill_edit_message_id", None)

    text = update.message.text.strip()
    try:
        new_amount = float(text.replace(",", "."))
        if new_amount <= 0:
            raise ValueError("金额必须大于 0")
    except ValueError:
        await update.message.reply_text(
            f"❌ 无效金额：`{text}`\n请输入正数，如 `128.50`。",
            parse_mode="Markdown",
        )
        return

    entry = await bill_cache.get(cache_id)
    if entry is None:
        await update.message.reply_text("⏰ 账单已过期，请重新发送账单信息。")
        return

    if entry.user_id != user_id:
        return

    entry.amount = new_amount
    success = await bill_cache.update(cache_id, entry)
    if not success:
        await update.message.reply_text("⏰ 账单已过期，请重新发送账单信息。")
        return

    try:
        await context.bot.edit_message_text(
            chat_id=update.message.chat_id,
            message_id=original_message_id,
            text=_build_confirmation_text(entry),
            parse_mode="Markdown",
            reply_markup=_build_confirmation_keyboard(cache_id),
        )
    except Exception as e:
        logger.warning("Failed to edit original confirmation message: %s", e)
        await update.message.reply_text(
            _build_confirmation_text(entry),
            parse_mode="Markdown",
            reply_markup=_build_confirmation_keyboard(cache_id),
        )

    try:
        await update.message.delete()
    except Exception:
        pass

    logger.info("Bill amount updated: cache_id=%s user_id=%s new_amount=%s", cache_id, user_id, new_amount)


# ---------------------------------------------------------------------------
# ❌ 取消
# ---------------------------------------------------------------------------

@register(lambda d: d.startswith("bill_cancel:"))
async def _cb_bill_cancel(query, context: CallbackContext) -> None:
    user_id = query.from_user.id
    cache_id = _parse_cache_id(query.data)

    entry = await bill_cache.get(cache_id)
    if entry is not None and entry.user_id != user_id:
        await query.answer("❌ 这不是你的账单。", show_alert=True)
        return

    await bill_cache.delete(cache_id)
    await query.answer()
    await query.edit_message_text("🗑️ 已取消记账，本次账单未保存。")
    logger.info("Bill cancelled: cache_id=%s user_id=%s", cache_id, user_id)
