# handlers/admin/tasks.py
"""
handlers/admin/tasks.py

Commands: /queue, /failed, /refresh, /admcancel

Decoupling
──────────
Two-layer pattern throughout:

  _xxx_impl(ctx: PlatformContext, ...)  — pure business logic, no PTB
  xxx_command(update, context)          — thin PTB adapter

``_send_refresh_page`` previously used ``hasattr(message_or_query,
"reply_text")`` to branch between command and callback paths — the same
anti-pattern that existed in the old ``_send_history_page``.  It is now
replaced by ``_refresh_page_impl(ctx, user_id, page, *, edit=False)``
which uses ``ctx.send_keyboard`` / ``ctx.edit_keyboard`` exactly like the
migrated history helper.

The ``context.bot_data`` write (storing refresh URL list between pages)
stays in the PTB adapter layer; it is the only PTB-specific side-effect
required for the pagination flow.

Note on /rateinfo and /setrate
──────────────────────────────
These two commands were previously registered here AND in the new
handlers/admin/users.py (where they have been migrated to the two-layer
pattern).  The versions below are **removed** — only the users.py versions
are kept to avoid double-registration.
"""
from __future__ import annotations

import logging
from datetime import datetime

from telegram import Update
from telegram.ext import CallbackContext

from core.handler_registry import command_handler
from shared.services.container import services
from shared.services.platform_context import PlatformContext, TelegramContext, btn, KeyboardLayout
from shared.services.user_service import get_user_display_name
from database.history import clear_file_id_by_url, get_user_history, get_recent_cached_urls
from utils.i18n import t
from utils.utils import require_admin, require_message, format_history_list

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Format helpers (pure functions, no PTB)
# ---------------------------------------------------------------------------

_FORMAT_LABEL: dict[str, str] = {
    "137": "1080p", "248": "1080p",
    "136": "720p",  "247": "720p",
    "135": "480p",  "244": "480p",
    "134": "360p",  "243": "360p",
    "133": "240p",  "242": "240p",
    "160": "144p",  "278": "144p",
    "271": "1440p", "308": "1440p",
    "313": "2160p", "315": "2160p",
    "best": "最佳画质",
    "127": "8K",       "126": "Dolby 视界", "125": "HDR 真彩",
    "120": "4K 超清",  "116": "1080p60",   "112": "1080p+",
    "80":  "1080p",    "74":  "720p60",     "64":  "720p",
    "32":  "480p",     "16":  "360p",
    "30280": "8K",     "30250": "Dolby 视界", "30251": "Dolby 全景声",
    "30240": "HDR 真彩", "30232": "1080p60", "30080": "1080p+",
    "30064": "1080p",  "30032": "480p",     "30016": "360p",
}


def _format_download_type(download_type: str, file_size: int | None = None) -> str:
    size_str = f" {file_size / (1024 * 1024):.0f}MB" if file_size else ""
    if download_type == "audio":     return f"🎵 音频{size_str}"
    if download_type == "spotify":   return f"🎵 Spotify{size_str}"
    if download_type == "subtitle":  return f"📝 字幕{size_str}"
    if download_type == "thumbnail": return f"🖼️ 封面{size_str}"
    if download_type == "video":     return f"🎬 视频{size_str}"
    if download_type.startswith("video_"):
        label = _FORMAT_LABEL.get(download_type.removeprefix("video_"), download_type.removeprefix("video_"))
        return f"🎬 {label}{size_str}"
    return download_type


# ---------------------------------------------------------------------------
# /queue
# ---------------------------------------------------------------------------

async def _queue_impl(ctx: PlatformContext) -> None:
    active = services.task_manager.get_all_active_tasks()
    queued = services.task_manager.get_total_queued()

    msg = t("queue_title", ctx.user_id, active=len(active), queued=queued)
    if active:
        msg += "\n" + t("active_downloads", ctx.user_id) + "\n"
        for task in list(active)[:10]:
            user_name = await get_user_display_name(task.user_id)
            status_emoji = {
                "downloading": "⬇️",
                "processing":  "⚙️",
                "uploading":   "📤",
            }.get(task.status.value, "⏳")
            msg += f"{status_emoji} {task.download_type} - {user_name}\n"

    await ctx.send(msg)


@command_handler("queue", admin_only=True)
@require_admin
@require_message
async def queue_command(update: Update, context: CallbackContext) -> None:
    await _queue_impl(TelegramContext.from_message(update, context))


# ---------------------------------------------------------------------------
# /failed
# ---------------------------------------------------------------------------

async def _failed_impl(ctx: PlatformContext) -> None:
    if not ctx.args:
        await ctx.send(t("usage_failed", ctx.user_id))
        return
    try:
        target_id = int(ctx.args[0])
    except ValueError:
        await ctx.send(t("invalid_user_id", ctx.user_id))
        return

    history = await get_user_history(target_id, limit=50)
    failed = [h for h in history if h.get("status") == "failed"]

    if not failed:
        await ctx.send(t("failed_for_user", ctx.user_id, target_id=target_id))
        return

    msg = format_history_list(
        failed[:20],
        t("failed_title_simple", ctx.user_id, target_id=target_id) + "\n",
    )
    await ctx.send(msg)


@command_handler("failed", admin_only=True)
@require_admin
@require_message
async def failed_command(update: Update, context: CallbackContext) -> None:
    await _failed_impl(TelegramContext.from_message(update, context))


# ---------------------------------------------------------------------------
# /refresh  +  _refresh_page_impl  (replaces _send_refresh_page)
# ---------------------------------------------------------------------------

REFRESH_PAGE_SIZE = 5


async def _refresh_page_impl(
    ctx: PlatformContext,
    user_id: int,
    page: int,
    *,
    edit: bool = False,
    store_urls_fn=None,
) -> None:
    """Render one page of cached-URL picker via a PlatformContext.

    Parameters
    ----------
    ctx:
        PlatformContext for the current interaction.
    user_id:
        Admin user whose refresh session to update.
    page:
        Zero-based page index.
    edit:
        When True, edit the existing message (callback path).
        When False, send a new message (command path).
    store_urls_fn:
        Optional ``(user_id, urls) -> None`` callback used to persist the
        URL list for the callback handler.  On Telegram this writes into
        ``context.bot_data``; tests can pass a plain dict setter.
        If None, URL storage is skipped (no pagination callbacks possible).
    """
    records, total = await get_recent_cached_urls(
        limit=REFRESH_PAGE_SIZE,
        offset=page * REFRESH_PAGE_SIZE,
    )
    total_pages = max(1, (total + REFRESH_PAGE_SIZE - 1) // REFRESH_PAGE_SIZE)

    if not records:
        text = t("refresh_no_cache", user_id)
        if edit:
            await ctx.edit(text)
        else:
            await ctx.send(text)
        return

    if store_urls_fn is not None:
        store_urls_fn(user_id, [r["url"] for r in records])

    base_index = page * REFRESH_PAGE_SIZE
    lines = [t("refresh_pick_prompt", user_id) + f"  ({page + 1}/{total_pages})\n"]
    for i, r in enumerate(records):
        title = (r.get("title") or r["url"])[:40]
        type_label = _format_download_type(r.get("download_type", ""), r.get("file_size"))
        dt = datetime.fromtimestamp(r["timestamp"]).strftime("%m-%d %H:%M")
        lines.append(f"{base_index + i + 1}. {title}\n   {type_label}  {dt}")

    text = "\n".join(lines)

    num_row: list = [
        btn(str(base_index + i + 1), f"refresh_do_{user_id}_{i}")
        for i in range(len(records))
    ]
    nav_row: list = []
    if page > 0:
        nav_row.append(btn("◀️", f"refresh_page_{user_id}_{page - 1}"))
    nav_row.append(btn(t("btn_close", user_id), "cancel_menu_close"))
    if page < total_pages - 1:
        nav_row.append(btn("▶️", f"refresh_page_{user_id}_{page + 1}"))

    buttons: KeyboardLayout = [num_row, nav_row]

    if edit:
        await ctx.edit_keyboard(text, buttons)
    else:
        await ctx.send_keyboard(text, buttons)


async def _refresh_impl(ctx: PlatformContext, *, store_urls_fn=None) -> None:
    if ctx.args:
        url = " ".join(ctx.args)
        await clear_file_id_by_url(url)
        await ctx.send(t("refresh_cleared", ctx.user_id, url=url))
        return
    await _refresh_page_impl(ctx, ctx.user_id, page=0, edit=False, store_urls_fn=store_urls_fn)


@command_handler("refresh", admin_only=True)
@require_admin
@require_message
async def refresh_command(update: Update, context: CallbackContext) -> None:
    ctx = TelegramContext.from_message(update, context)

    def _store(uid: int, urls: list[str]) -> None:
        context.bot_data[f"refresh_urls_{uid}"] = urls

    await _refresh_impl(ctx, store_urls_fn=_store)


# Public helper kept for inline_actions._cb_refresh_page
async def _send_refresh_page(query, user_id: int, page: int, ptb_context) -> None:
    """PTB-facing shim called from inline_actions._cb_refresh_page.

    Wraps ``_refresh_page_impl`` with a TelegramContext built from the
    callback query, so the callback handler stays PTB-free internally.
    """
    from shared.services.platform_context import TelegramContext as _TC
    ctx = _TC.from_callback_query(query, ptb_context)

    def _store(uid: int, urls: list[str]) -> None:
        ptb_context.bot_data[f"refresh_urls_{uid}"] = urls

    await _refresh_page_impl(ctx, user_id, page, edit=True, store_urls_fn=_store)


# ---------------------------------------------------------------------------
# /admcancel
# ---------------------------------------------------------------------------

async def _admcancel_impl(ctx: PlatformContext) -> None:
    if ctx.args:
        task_id = ctx.args[0]
        success = await services.task_manager.cancel_task(task_id)
        if success:
            await ctx.send(t("task_cancel_confirm", ctx.user_id, task_id=task_id))
        else:
            await ctx.send(t("task_cancel_error", ctx.user_id, task_id=task_id))
        return

    active = list(services.task_manager.get_all_active_tasks())
    queued_size = services.task_manager.get_total_queued()

    if not active and queued_size == 0:
        await ctx.send(t("queue_empty_cn", ctx.user_id))
        return

    buttons: KeyboardLayout = [
        [btn(
            t("btn_cancel_task", ctx.user_id,
              label=f"[{task.task_id}] UID:{task.user_id} "
                    f"{task.url[:25]}{'...' if len(task.url) > 25 else ''}"),
            f"cancel_task_{task.task_id}",
        )]
        for task in active
    ]
    buttons.append([btn(t("btn_close", ctx.user_id), "cancel_menu_close")])

    await ctx.send_keyboard(
        t("admin_tasks_title", ctx.user_id, active=len(active), queued=queued_size),
        buttons,
    )


@command_handler("admcancel", admin_only=True)
@require_admin
@require_message
async def admin_cancel_command(update: Update, context: CallbackContext) -> None:
    await _admcancel_impl(TelegramContext.from_message(update, context))
