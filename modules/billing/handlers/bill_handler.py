"""
modules/billing/handlers/bill_handler.py

变更说明（items 支持版本）：
1. _build_confirmation_text
   - 新增 items 明细展示区：商品名 + 金额，折扣用红色标注（负数前缀 ➖）
   - 超过 8 行时折叠，只显示前 8 行 + "…共 N 项"
2. merchant 兜底：过滤 "未知商家" / "unknown" 显示为 "未知"
3. 其余逻辑（键盘、命令处理、图片处理）不变
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

_MAX_ITEMS_PREVIEW = 8  # 确认卡最多展示的明细行数


def _get_parser() -> BillParser:
    import shared.integrations.llm.manager as llm_mod
    if llm_mod.llm_manager is None:
        raise RuntimeError("llm_manager not initialised")
    return BillParser(llm_mod.llm_manager)


def _format_items_preview(entry: BillEntry) -> str:
    """将 items 格式化为确认卡中的明细区块，返回 Markdown 字符串。"""
    if not entry.items:
        return ""

    lines: list[str] = ["", "━━━━━━ 明细 ━━━━━━"]
    display_items = entry.items[:_MAX_ITEMS_PREVIEW]

    for item in display_items:
        if item.item_type == "discount":
            # 折扣行：红色标记（Markdown 用斜体区分）
            lines.append(f"  ➖ _{item.name}_　`{item.amount:+.0f}`")
        elif item.item_type == "tax":
            lines.append(f"  🧾 _{item.name}_　`{item.amount:.0f}`")
        else:
            qty_str = f" x{item.quantity:.0f}" if item.quantity != 1 else ""
            lines.append(f"  • {item.name}{qty_str}　`{item.amount:.0f}`")

    if len(entry.items) > _MAX_ITEMS_PREVIEW:
        remaining = len(entry.items) - _MAX_ITEMS_PREVIEW
        lines.append(f"  _…共 {len(entry.items)} 项，另有 {remaining} 项_")

    return "\n".join(lines)


def _build_confirmation_text(entry: BillEntry) -> str:
    """构建账单确认卡 Markdown 文本。"""
    category    = entry.category    or "未分类"
    description = entry.description or "—"
    merchant    = (
        entry.merchant
        if entry.merchant and entry.merchant not in ("unknown", "未知商家", "")
        else "未知"
    )
    bill_date   = entry.bill_date   or "未知"

    text = (
        f"📋 *账单确认*\n\n"
        f"💰 金额：`{entry.amount:.2f} {entry.currency}`\n"
        f"🏷️ 类别：{category}\n"
        f"🏪 商家：{merchant}\n"
        f"📝 描述：{description}\n"
        f"📅 日期：{bill_date}\n"
        f"📎 凭证：{'已附图片' if entry.receipt_file_id else '无'}"
    )

    items_preview = _format_items_preview(entry)
    if items_preview:
        text += items_preview

    text += "\n\n请确认以上信息是否正确："
    return text


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
            "• 发送收据图片（自动 OCR + 明细识别）\n"
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

async def _bill_photo_impl(ctx: PlatformContext, image_b64: str, file_id: str = "") -> None:
    try:
        entry = await _get_parser().parse_image(user_id=ctx.user_id, image_base64=image_b64)
    except ValueError as exc:
        await ctx.edit(f"❌ 识别失败：{exc}\n\n请尝试发送更清晰的图片，或直接输入文字。")
        return
    except Exception as exc:
        logger.error("parse_image failed user=%s: %s", ctx.user_id, exc, exc_info=True)
        await ctx.edit("❌ 服务异常，请稍后重试。")
        return

    # 保存 Telegram file_id，用户确认后一并写库
    if file_id:
        entry.receipt_file_id = file_id

    cache_id = await bill_cache.set(entry)
    await ctx.edit_keyboard(_build_confirmation_text(entry), _confirmation_keyboard(cache_id))
    logger.info(
        "Bill photo confirmation sent: cache_id=%s user_id=%s items=%d has_receipt=%s",
        cache_id, ctx.user_id, len(entry.items), bool(file_id),
    )


async def handle_bill_photo(update: Update, context: CallbackContext) -> None:
    """PTB adapter：下载图片字节，构建 ctx，委托给 _bill_photo_impl。"""
    if not update.message or not update.message.photo:
        return

    user = update.message.from_user
    track_user(user)
    await warm_user_lang(user.id)

    processing_msg = await update.message.reply_text("🤖 AI 正在识别收据图片，请稍候…")

    try:
        # 取最高分辨率（列表最后一项），同时保留 file_id 用于凭证存档
        photo = update.message.photo[-1]
        file_id = photo.file_id                 # Telegram 永久 file_id
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
    await _bill_photo_impl(ctx, image_b64, file_id=file_id)


@require_message
async def handle_jz_command(update: Update, context: CallbackContext) -> None:
    ctx = TelegramContext.from_message(update, context)
    await _bill_command_impl(ctx)
