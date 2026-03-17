"""
handlers/bills/bill_callbacks.py

账单 InlineKeyboard 回调处理器。
遵循 handlers/downloads/inline_actions.py 的 @register 路由模式，
直接注册到同一个 _CALLBACK_HANDLERS 列表，无需修改 handle_callback() 入口。

在模块初始化时 import 此文件即可自动注册：
    # 在 handlers/downloads/inline_actions.py 末尾或 handlers/__init__.py 中添加：
    import handlers.bills.bill_callbacks  # noqa: F401  触发注册副作用
"""
from __future__ import annotations

import logging

from telegram import InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import CallbackContext

from modules.billing.database.bills import insert_bill
from modules.billing.services.bill_cache import bill_cache, BillEntry
from utils.i18n import t

# 复用现有路由注册机制
from modules.downloader.handlers.inline_actions import register

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# 内部工具
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
    """从 callback_data 中提取 cache_id，格式：bill_<action>:<cache_id>"""
    return data.split(":", 1)[1]


# ---------------------------------------------------------------------------
# ✅ 确认无误 → 写入数据库
# ---------------------------------------------------------------------------

@register(lambda d: d.startswith("bill_confirm:"))
async def _cb_bill_confirm(query, context: CallbackContext) -> None:
    user_id = query.from_user.id
    cache_id = _parse_cache_id(query.data)

    await query.answer()  # 先消除按钮 loading

    entry = await bill_cache.get(cache_id)
    if entry is None:
        await query.edit_message_text(
            "⏰ 确认超时，账单数据已过期，请重新发送。",
            parse_mode="Markdown",
        )
        return

    # 所有权校验：防止他人点击他人的确认按钮
    if entry.user_id != user_id:
        await query.answer("❌ 这不是你的账单。", show_alert=True)
        return

    try:
        rowid = await insert_bill(entry)
    except Exception as e:
        logger.error("insert_bill failed for user=%s cache_id=%s: %s", user_id, cache_id, e, exc_info=True)
        await query.edit_message_text(
            f"❌ 入库失败，请稍后重试。\n（错误：{e}）",
            parse_mode="Markdown",
        )
        return

    # 成功后删除缓存
    await bill_cache.delete(cache_id)

    await query.edit_message_text(
        f"✅ *账单已记录！*\n\n"
        f"💰 {entry.amount:.2f} {entry.currency}｜{entry.category}\n"
        f"🏪 {entry.merchant}｜{entry.bill_date}\n"
        f"📌 记录编号：`#{rowid}`",
        parse_mode="Markdown",
    )
    logger.info("Bill confirmed and inserted: rowid=%s user_id=%s cache_id=%s", rowid, user_id, cache_id)


# ---------------------------------------------------------------------------
# ✏️ 修改金额 → 触发 ForceReply，等待用户输入新金额
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

    # 在 context.user_data 中记录"等待修改金额"的状态
    context.user_data["bill_edit_cache_id"] = cache_id
    context.user_data["bill_edit_message_id"] = query.message.message_id

    from telegram import ForceReply
    await query.message.reply_text(
        f"当前金额：`{entry.amount:.2f} {entry.currency}`\n"
        f"请输入新的金额（纯数字，如 `128.5`）：",
        parse_mode="Markdown",
        reply_markup=ForceReply(selective=True, input_field_placeholder="请输入新金额"),
    )


async def handle_bill_edit_reply(update, context: CallbackContext) -> None:
    """
    处理用户回复修改金额的 ForceReply 消息。

    注册方式（在主 bot 中）：
        from telegram.ext import MessageHandler, filters
        from handlers.bills.bill_callbacks import handle_bill_edit_reply

        app.add_handler(
            MessageHandler(
                filters.TEXT & filters.REPLY & ~filters.COMMAND,
                handle_bill_edit_reply,
            ),
            group=1,   # 低于默认 group(0) 以避免与 handle_bill_text 冲突
        )

    也可以使用 ConversationHandler 替代此方案，视项目偏好选择。
    """
    if not update.message:
        return

    user_id = update.message.from_user.id
    cache_id = context.user_data.get("bill_edit_cache_id")
    original_message_id = context.user_data.get("bill_edit_message_id")

    if not cache_id:
        # 不是编辑账单的回复，忽略
        return

    # 清除等待状态
    context.user_data.pop("bill_edit_cache_id", None)
    context.user_data.pop("bill_edit_message_id", None)

    # 解析新金额
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

    # 更新金额并写回缓存
    entry.amount = new_amount
    success = await bill_cache.update(cache_id, entry)

    if not success:
        await update.message.reply_text("⏰ 账单已过期，请重新发送账单信息。")
        return

    # 编辑原确认卡片，显示更新后的信息
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
        # Fallback：发送新的确认卡片
        await update.message.reply_text(
            _build_confirmation_text(entry),
            parse_mode="Markdown",
            reply_markup=_build_confirmation_keyboard(cache_id),
        )

    # 删除 ForceReply 回复消息（保持聊天整洁，可选）
    try:
        await update.message.delete()
    except Exception:
        pass

    logger.info("Bill amount updated: cache_id=%s user_id=%s new_amount=%s", cache_id, user_id, new_amount)


# ---------------------------------------------------------------------------
# ❌ 取消记账 → 删除缓存，编辑消息反馈
# ---------------------------------------------------------------------------

@register(lambda d: d.startswith("bill_cancel:"))
async def _cb_bill_cancel(query, context: CallbackContext) -> None:
    user_id = query.from_user.id
    cache_id = _parse_cache_id(query.data)

    entry = await bill_cache.get(cache_id)

    # 即使缓存已过期，也允许取消（幂等）
    if entry is not None and entry.user_id != user_id:
        await query.answer("❌ 这不是你的账单。", show_alert=True)
        return

    await bill_cache.delete(cache_id)
    await query.answer()

    await query.edit_message_text(
        "🗑️ 已取消记账，本次账单未保存。",
        parse_mode="Markdown",
    )
    logger.info("Bill cancelled: cache_id=%s user_id=%s", cache_id, user_id)
