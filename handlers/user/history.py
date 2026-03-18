# handlers/user/history.py
"""
handlers/user/history.py

Decoupling
──────────
``_send_history_page`` now accepts a ``PlatformContext`` instead of a raw
PTB message-or-query object.  All inline keyboard construction uses the
platform-agnostic ``KeyboardLayout`` / ``btn`` API; the PTB
InlineKeyboard types are confined to ``TelegramContext.send_keyboard /
edit_keyboard``.

Two-layer pattern
─────────────────
  _xxx_impl(ctx: PlatformContext, ...)  — pure business logic, no PTB
  xxx_command(update, context)          — thin PTB adapter
"""
from __future__ import annotations

import logging

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import CallbackContext

from core.handler_registry import command_handler
from shared.services.container import services
from shared.services.platform_context import PlatformContext, TelegramContext, btn, KeyboardLayout
from models.domain_models import STATUS_EMOJI, DownloadStatus
from utils.i18n import t
from utils.utils import format_history_item, require_message
from database.history import get_user_history_page
from shared.repositories.task_store import get_user_tasks

logger = logging.getLogger(__name__)

PAGE_SIZE = 5


# ---------------------------------------------------------------------------
# _send_history_page  — platform-agnostic core
# ---------------------------------------------------------------------------

async def _send_history_page(
    ctx: PlatformContext,
    user_id: int,
    page: int,
    *,
    edit: bool = False,
) -> None:
    """Render one page of download history via a PlatformContext.

    Parameters
    ----------
    ctx:
        PlatformContext for the current interaction.
    user_id:
        The user whose history to display.
    page:
        Zero-based page index.
    edit:
        When True, edit the existing message in-place (callback-query path).
        When False, send a new message (command path).
    """
    records, total = await get_user_history_page(user_id, page=page, page_size=PAGE_SIZE)
    total_pages = max(1, (total + PAGE_SIZE - 1) // PAGE_SIZE)

    if not records:
        text = t("no_history", user_id)
        nav_buttons: KeyboardLayout = []
    else:
        header = t("history_header", user_id, page=page + 1, total=total_pages, count=total)
        body = "".join(format_history_item(r) for r in records)
        text = header + body

        nav_row = []
        if page > 0:
            nav_row.append(btn(t("prev_page", user_id), f"history_page_{user_id}_{page - 1}"))
        if page < total_pages - 1:
            nav_row.append(btn(t("next_page", user_id), f"history_page_{user_id}_{page + 1}"))
        nav_buttons = [nav_row] if nav_row else []

    if nav_buttons:
        if edit:
            await ctx.edit_keyboard(text, nav_buttons)
        else:
            await ctx.send_keyboard(text, nav_buttons)
    else:
        if edit:
            await ctx.edit(text)
        else:
            await ctx.send(text)


# ---------------------------------------------------------------------------
# /history
# ---------------------------------------------------------------------------

async def _history_impl(ctx: PlatformContext) -> None:
    await _send_history_page(ctx, ctx.user_id, page=0, edit=False)


@command_handler("history")
@require_message
async def history_command(update: Update, context: CallbackContext) -> None:
    ctx = TelegramContext.from_message(update, context)
    await _history_impl(ctx)


# ---------------------------------------------------------------------------
# /tasks
# ---------------------------------------------------------------------------

async def _tasks_impl(ctx: PlatformContext) -> None:
    records = await get_user_tasks(ctx.user_id, limit=10)
    if not records:
        await ctx.send(t("no_tasks", ctx.user_id))
        return

    lines = [f"📋 {t('recent_tasks', ctx.user_id)}\n"]
    for r in records:
        emoji = STATUS_EMOJI.get(DownloadStatus(r["status"]), "❓")
        title = (r.get("title") or r["url"])[:35]
        retry_count = r.get("retry_count", 0)
        retry_info = (
            f" ({t('retry_count', ctx.user_id, count=retry_count)})"
            if retry_count else ""
        )
        err_info = f"\n   ⚠️ {r['error'][:40]}" if r.get("error") else ""
        lines.append(f"{emoji} [{r['download_type']}] {title}{retry_info}{err_info}")

    await ctx.send("\n".join(lines))


@command_handler("tasks")
@require_message
async def tasks_command(update: Update, context: CallbackContext) -> None:
    ctx = TelegramContext.from_message(update, context)
    await _tasks_impl(ctx)


# ---------------------------------------------------------------------------
# /cancel
# ---------------------------------------------------------------------------

async def _cancel_impl(ctx: PlatformContext) -> None:
    user_tasks = services.task_manager.get_user_tasks(ctx.user_id)
    active = [
        task for task in user_tasks
        if task.status in (
            DownloadStatus.QUEUED,
            DownloadStatus.DOWNLOADING,
            DownloadStatus.PROCESSING,
        )
    ]
    if not active:
        await ctx.send(t("no_active_tasks", ctx.user_id))
        return

    status_emoji = {
        DownloadStatus.QUEUED:      "⏳",
        DownloadStatus.DOWNLOADING: "⬇️",
        DownloadStatus.PROCESSING:  "⚙️",
    }
    buttons: KeyboardLayout = [
        [btn(
            f"{status_emoji.get(task.status, '❓')} {task.download_type} | "
            f"{task.url[:30]}{'...' if len(task.url) > 30 else ''}",
            f"cancel_task_{task.task_id}",
        )]
        for task in active
    ]
    buttons.append([btn("❌ " + t("close_menu", ctx.user_id), "cancel_menu_close")])

    await ctx.send_keyboard(t("select_cancel_task", ctx.user_id), buttons)


@command_handler("cancel")
@require_message
async def cancel_command(update: Update, context: CallbackContext) -> None:
    ctx = TelegramContext.from_message(update, context)
    await _cancel_impl(ctx)
