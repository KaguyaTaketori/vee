"""
modules/billing/handlers/bill_handler.py
"""
from __future__ import annotations

import base64
import logging
import os
from io import BytesIO

from telegram import Update
from telegram.ext import CallbackContext

from modules.billing.services.bill_cache import bill_cache, BillEntry
from modules.billing.services.bill_parser import BillParser
from shared.services.platform_context import PlatformContext, TelegramContext, btn
from shared.services.user_service import track_user, warm_user_lang
from utils.utils import require_message
from repositories import AppUserRepository as _AppUserRepo
_app_user_repo = _AppUserRepo()

logger = logging.getLogger(__name__)

_MAX_ITEMS_PREVIEW = 8

async def _check_ai_quota(tg_user_id: int) -> tuple[bool, str]:
    """
    检查 TG 用户的 AI 配额。
    - 已绑定 App 账号 → 走 app_users 统一配额
    - 未绑定 → 走旧 rate_limit 表（降级兼容）
    Returns (allowed, reason)
    """
    app_user = await _app_user_repo.get_by_tg_user_id(tg_user_id)

    if app_user:
        allowed, remaining = await _app_user_repo.check_and_deduct_ai_quota(
            app_user["id"]
        )
        if not allowed:
            return False, (
                "❌ AI 使用次数已达本月上限。\n"
                "请在 App 个人中心查看配额详情。"
            )
        return True, ""

    # 未绑定：不限制，但提示可以绑定
    return True, ""

def _get_parser() -> BillParser:
    import shared.integrations.llm.manager as llm_mod
    if llm_mod.llm_manager is None:
        raise RuntimeError("llm_manager not initialised")
    return BillParser(llm_mod.llm_manager)


def _format_items_preview(entry: BillEntry) -> str:
    if not entry.items:
        return ""
    lines: list[str] = ["", "━━━━━━ 明细 ━━━━━━"]
    for item in entry.items[:_MAX_ITEMS_PREVIEW]:
        if item.item_type == "discount":
            lines.append(f"  ➖ _{item.name}_　`{item.amount:+.0f}`")
        elif item.item_type == "tax":
            lines.append(f"  🧾 _{item.name}_　`{item.amount:.0f}`")
        else:
            qty_str = f" x{item.quantity:.0f}" if item.quantity != 1 else ""
            lines.append(f"  • {item.name}{qty_str}　`{item.amount:.0f}`")
    if len(entry.items) > _MAX_ITEMS_PREVIEW:
        lines.append(f"  _…共 {len(entry.items)} 项_")
    return "\n".join(lines)


def _build_confirmation_text(entry: BillEntry) -> str:
    category    = entry.category    or "未分类"
    description = entry.description or "—"
    merchant = (
        entry.merchant
        if entry.merchant and entry.merchant not in ("unknown", "未知商家", "")
        else "未知"
    )
    bill_date = entry.bill_date or "未知"
    receipt_hint = "📎 已附图片凭证" if entry.receipt_tmp_path or entry.receipt_url else "📎 无凭证"

    text = (
        f"📋 *账单确认*\n\n"
        f"💰 金额：`{entry.amount:.2f} {entry.currency}`\n"
        f"🏷️ 类别：{category}\n"
        f"🏪 商家：{merchant}\n"
        f"📝 描述：{description}\n"
        f"📅 日期：{bill_date}\n"
        f"{receipt_hint}"
    )
    items_preview = _format_items_preview(entry)
    if items_preview:
        text += items_preview
    text += "\n\n请确认以上信息是否正确："
    return text


def _confirmation_keyboard(cache_id: str):
    return [
        [btn("✅ 确认无误", f"bill_confirm:{cache_id}")],
        [btn("✏️ 改金额",   f"bill_edit:amount:{cache_id}"),
         btn("🏷️ 改类别",   f"bill_edit:category:{cache_id}")],
        [btn("🏪 改商家",   f"bill_edit:merchant:{cache_id}"),
         btn("📝 改描述",   f"bill_edit:description:{cache_id}")],
        [btn("📅 改日期",   f"bill_edit:bill_date:{cache_id}"),
         btn("🧾 改明细",   f"bill_edit:items:{cache_id}")],
        [btn("❌ 取消记账", f"bill_cancel:{cache_id}")],
    ]


# ---------------------------------------------------------------------------
# /bill command
# ---------------------------------------------------------------------------

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


@require_message
async def handle_jz_command(update: Update, context: CallbackContext) -> None:
    ctx = TelegramContext.from_message(update, context)
    await _bill_command_impl(ctx)


# ---------------------------------------------------------------------------
# Text bill
# ---------------------------------------------------------------------------

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
    ctx = TelegramContext.from_message_with_status(update, context, processing_msg)
    await _bill_text_impl(ctx, text=text)


# ---------------------------------------------------------------------------
# Photo bill
# ---------------------------------------------------------------------------

async def _save_receipt_tmp(image_bytes: bytes, cache_id: str) -> str:
    """
    将图片保存到临时目录。

    Returns
    -------
    tmp_identifier : "pending/xxx.jpg" 格式的临时标识，失败时返回空字符串
    """
    from shared.services.container import services
    try:
        tmp_id = await services.receipt_storage.save_tmp(
            data=image_bytes,
            ext=".jpg",
            hint=cache_id,
        )
        return tmp_id
    except Exception as e:
        logger.warning("Failed to save receipt tmp for cache_id=%s: %s", cache_id, e)
        return ""

async def _bill_photo_impl(
    ctx: PlatformContext,
    image_b64: str,
    image_bytes: bytes,
    file_id: str = "",
    tg_user_id: int | None = None,
) -> None:
    if tg_user_id:
        allowed, reason = await _check_ai_quota(tg_user_id)
        if not allowed:
            await ctx.edit(reason)
            return

    try:
        entry = await _get_parser().parse_image(
            user_id=ctx.user_id,
            image_base64=image_b64,
        )
    except ValueError as exc:
        await ctx.edit(f"❌ 识别失败：{exc}\n\n请尝试发送更清晰的图片，或直接输入文字。")
        return
    except Exception as exc:
        logger.error("parse_image failed user=%s: %s", ctx.user_id, exc, exc_info=True)
        await ctx.edit("❌ 服务异常，请稍后重试。")
        return

    if file_id:
        entry.receipt_file_id = file_id

    # 先 set 到缓存拿到 cache_id，再用 cache_id 作为临时文件名 hint
    cache_id = await bill_cache.set(entry)

    # 保存临时图片，用 cache_id 关联，方便取消时清理
    tmp_path = await _save_receipt_tmp(image_bytes, cache_id)
    if tmp_path:
        entry.receipt_tmp_path = tmp_path
        # 更新缓存中的 entry（写入 tmp_path）
        await bill_cache.update(cache_id, entry)

    await ctx.edit_keyboard(
        _build_confirmation_text(entry),
        _confirmation_keyboard(cache_id),
    )
    logger.info(
        "Bill photo confirmation sent: cache_id=%s user_id=%s items=%d tmp_path=%s",
        cache_id, ctx.user_id, len(entry.items), tmp_path,
    )

async def handle_bill_photo(update: Update, context: CallbackContext) -> None:
    if not update.message or not update.message.photo:
        return

    user = update.message.from_user
    track_user(user)
    await warm_user_lang(user.id)

    processing_msg = await update.message.reply_text("🤖 AI 正在识别收据图片，请稍候…")

    try:
        photo    = update.message.photo[-1]
        file_id  = photo.file_id
        tg_file  = await photo.get_file()
        buf      = BytesIO()
        await tg_file.download_to_memory(buf)
        image_bytes = buf.getvalue()
        image_b64   = base64.b64encode(image_bytes).decode()
    except Exception as exc:
        logger.error("Photo download failed user=%s: %s", user.id, exc, exc_info=True)
        await processing_msg.edit_text("❌ 图片下载失败，请重试。")
        return

    ctx = TelegramContext.from_message_with_status(update, context, processing_msg)
    await _bill_photo_impl(
        ctx,
        image_b64,
        image_bytes,
        file_id=file_id,
        tg_user_id=user.id,    # ← 传入
    )
