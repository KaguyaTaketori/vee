"""
modules/downloader/handlers/inline_actions.py
"""
from __future__ import annotations

import logging

from config import ADMIN_IDS
from core.callback_bus import register, CallbackContext
from core.callback_bus import handle_callback  # re-export for DownloaderModule.setup()
from modules.downloader.services.facades import DownloadFacade
from shared.services.middleware import RequestContext, default_pipeline
from shared.services.container import services
from shared.services.user_service import set_user_language, warm_user_lang
from shared.services.session import UserSession
from shared.services.platform_context import btn, KeyboardLayout
from database.history import get_user_history, clear_file_id_by_url
from utils.i18n import LANGUAGES, t
from utils.utils import format_history_list
from utils.auth import check_admin
from handlers.user.history import _send_history_page

logger = logging.getLogger(__name__)

# Format ID → 显示标签
_FORMAT_LABEL: dict[str, str] = {
    "137": "1080p", "248": "1080p",
    "136": "720p",  "247": "720p",
    "135": "480p",  "244": "480p",
    "134": "360p",  "243": "360p",
    "133": "240p",  "242": "240p",
    "160": "144p",  "278": "144p",
    "271": "1440p", "308": "1440p",
    "313": "2160p", "315": "2160p",
    # Bilibili
    "30280": "8K",    "30250": "Dolby视界", "30251": "Dolby全景声",
    "30240": "HDR真彩","30232": "1080p60",  "30080": "1080p+",
    "30064": "1080p", "30032": "480p",      "30016": "360p",
}


def _label_for(fmt: dict) -> str:
    """从 yt-dlp format dict 生成可读标签。"""
    fid = str(fmt.get("format_id", ""))
    if fid in _FORMAT_LABEL:
        return _FORMAT_LABEL[fid]
    # 尝试从 height
    height = fmt.get("height")
    if height:
        fps = fmt.get("fps") or 0
        fps_str = f"{int(fps)}fps" if fps and int(fps) > 30 else ""
        return f"{height}p{fps_str}".strip()
    note = fmt.get("format_note") or fmt.get("format", fid)
    return note[:20]


def _build_quality_buttons(formats: list, session_key: str) -> KeyboardLayout:
    """
    从 yt-dlp formats 列表筛选视频格式，构建画质选择键盘。
    每行最多 2 个按钮，最后一行始终是"⭐ 最佳画质（自动）"。
    """
    seen_heights: set = set()
    quality_fmts: list[dict] = []

    for fmt in reversed(formats):  # reversed → 高质量在前
        vcodec = fmt.get("vcodec", "none")
        if vcodec in (None, "none"):
            continue
        height = fmt.get("height")
        if height and height in seen_heights:
            continue
        if height:
            seen_heights.add(height)
        quality_fmts.append(fmt)
        if len(quality_fmts) >= 8:  # 最多展示 8 个画质
            break

    rows: KeyboardLayout = []
    row: list = []
    for fmt in quality_fmts:
        label = _label_for(fmt)
        fid = str(fmt.get("format_id", "best"))
        row.append(btn(f"🎬 {label}", f"dl_fmt_{session_key}_{fid}"))
        if len(row) == 2:
            rows.append(row)
            row = []
    if row:
        rows.append(row)

    # 始终追加最佳画质选项
    rows.append([btn(t("best_quality", 0), f"dl_fmt_{session_key}_best")])
    return rows


# ── Language picker ────────────────────────────────────────────────────────

@register(lambda d: d.startswith("lang_"))
async def _cb_lang(ctx: CallbackContext) -> None:
    lang_code = ctx.data.replace("lang_", "")
    if lang_code in LANGUAGES:
        await set_user_language(ctx.user_id, lang_code)
        await ctx.platform_ctx.edit(t("language_changed", ctx.user_id))
    await ctx.answer()


# ── Admin: view user history ───────────────────────────────────────────────

@register(lambda d: d.startswith("uh_"))
async def _cb_admin_history(ctx: CallbackContext) -> None:
    if not check_admin(ctx.user_id):
        await ctx.answer_alert(t("admin_only", ctx.user_id))
        return
    target_id = int(ctx.data.replace("uh_", ""))
    history = await get_user_history(target_id, limit=20)
    msg = format_history_list(history, f"Download history for user {target_id}:\n\n")
    await ctx.platform_ctx.edit(msg)
    await ctx.answer()


# ── History pagination ─────────────────────────────────────────────────────

@register(lambda d: d.startswith("history_page_"))
async def _cb_history_page(ctx: CallbackContext) -> None:
    parts = ctx.data.split("_")
    target_user_id = int(parts[2])
    page = int(parts[3])
    if ctx.user_id != target_user_id:
        await ctx.answer_alert(t("not_your_history", ctx.user_id))
        return
    await ctx.answer()
    await _send_history_page(ctx.platform_ctx, target_user_id, page, edit=True)


# ── Close / cancel menu ────────────────────────────────────────────────────

@register(lambda d: d == "cancel_menu_close")
async def _cb_cancel_close(ctx: CallbackContext) -> None:
    try:
        await ctx.delete_message()
    except Exception:
        await ctx.platform_ctx.edit(t("closed", ctx.user_id))


# ── Cancel own task ────────────────────────────────────────────────────────

@register(lambda d: d.startswith("cancel_task_"))
async def _cb_cancel_task(ctx: CallbackContext) -> None:
    task_id = ctx.data.replace("cancel_task_", "")
    task = services.queue.get_task(task_id)

    if not task:
        await ctx.platform_ctx.edit(t("task_not_found", ctx.user_id))
        return

    is_admin = ctx.user_id in ADMIN_IDS if ADMIN_IDS else False
    if task.user_id != ctx.user_id and not is_admin:
        await ctx.answer_alert(t("cancel_own_only", ctx.user_id))
        return

    cancelled = await services.queue.cancel_task(task_id)
    await ctx.platform_ctx.edit(
        t("task_cancelled", ctx.user_id) if cancelled else t("cancel_failed", ctx.user_id)
    )


# ── Admin cancel task ──────────────────────────────────────────────────────

@register(lambda d: d.startswith("admcancel_task_"))
async def _cb_admcancel_task(ctx: CallbackContext) -> None:
    if not check_admin(ctx.user_id):
        await ctx.answer_alert(t("admin_only", ctx.user_id))
        return
    task_id = ctx.data.replace("admcancel_task_", "")
    cancelled = await services.queue.cancel_task(task_id)
    await ctx.platform_ctx.edit(
        t("task_cancelled", ctx.user_id) if cancelled else t("cancel_failed", ctx.user_id)
    )


# ── Download: video / audio / thumbnail 初始选择 ─────────────────────────

@register(lambda d: d.startswith("dl_video_") or d.startswith("dl_audio_") or
          d.startswith("dl_thumb_"))
async def _cb_download_select(ctx: CallbackContext) -> None:
    data = ctx.data

    if data.startswith("dl_video_"):
        session_key = data[len("dl_video_"):]
        download_type = "video"
    elif data.startswith("dl_audio_"):
        session_key = data[len("dl_audio_"):]
        download_type = "audio"
    else:
        session_key = data[len("dl_thumb_"):]
        download_type = "thumbnail"

    session = UserSession.load(session_key)
    if session is None:
        await ctx.platform_ctx.edit(t("session_expired", ctx.user_id))
        return

    mw_ctx = RequestContext(user=ctx.user, reply=ctx.platform_ctx.send)
    result = await default_pipeline.run(mw_ctx)
    if not result.ok:
        await ctx.answer_alert(t(result.error_key, ctx.user_id))
        return

    await ctx.answer()

    if download_type != "video":
        await ctx.platform_ctx.edit(t("downloading", ctx.user_id))
        sender = ctx.create_sender()
        await DownloadFacade.enqueue(
            UserSession(url=session.url, user_id=ctx.user_id, sender=sender),
            download_type,
        )
        return

    await ctx.platform_ctx.edit(t("loading_quality", ctx.user_id))

    try:
        from modules.downloader.integrations.downloaders.ytdlp_client import get_formats_cached
        formats, info = await get_formats_cached(session.url)
    except Exception as exc:
        logger.error("get_formats_cached failed: %s", exc, exc_info=True)
        await ctx.platform_ctx.edit(t("format_fetch_error", ctx.user_id))
        return

    title = (info.get("title") or "")[:40]
    quality_buttons = _build_quality_buttons(formats, session_key)

    await ctx.platform_ctx.edit_keyboard(
        t("select_quality", ctx.user_id, title=title),
        quality_buttons,
    )


# ── 画质确认后真正入队 ────────────────────────────────────────────────────

@register(lambda d: d.startswith("dl_fmt_"))
async def _cb_format_select(ctx: CallbackContext) -> None:
    without_prefix = ctx.data[len("dl_fmt_"):]
    session_key = without_prefix[:16]
    format_id   = without_prefix[17:]

    session = UserSession.load(session_key)
    if session is None:
        await ctx.platform_ctx.edit(t("session_expired", ctx.user_id))
        return

    await ctx.answer()

    await ctx.platform_ctx.edit(t("downloading", ctx.user_id))
    sender = ctx.create_sender()

    download_type = f"video_{format_id}" if format_id != "best" else "video"
    await DownloadFacade.enqueue(
        UserSession(url=session.url, user_id=ctx.user_id, sender=sender),
        download_type,
    )


# ── Refresh cache: pick URL ────────────────────────────────────────────────

@register(lambda d: d.startswith("refresh_do_"))
async def _cb_refresh_do(ctx: CallbackContext) -> None:
    if not check_admin(ctx.user_id):
        await ctx.answer_alert(t("admin_only", ctx.user_id))
        return
    parts = ctx.data.split("_")
    admin_id = int(parts[2])
    index = int(parts[3])
    urls = ctx.raw_context.bot_data.get(f"refresh_urls_{admin_id}")
    if not urls or index >= len(urls):
        await ctx.platform_ctx.edit(t("refresh_session_expired", ctx.user_id))
        return
    url = urls[index]
    await clear_file_id_by_url(url)
    ctx.raw_context.bot_data.pop(f"refresh_urls_{admin_id}", None)
    await ctx.platform_ctx.edit(t("refresh_cleared", ctx.user_id, url=url))


@register(lambda d: d.startswith("refresh_page_"))
async def _cb_refresh_page(ctx: CallbackContext) -> None:
    if not check_admin(ctx.user_id):
        await ctx.answer_alert(t("admin_only", ctx.user_id))
        return
    parts = ctx.data.split("_")
    admin_id = int(parts[2])
    page = int(parts[3])
    from handlers.admin.tasks import _refresh_page_impl
    await ctx.answer()

    def _store(uid: int, urls: list) -> None:
        ctx.raw_context.bot_data[f"refresh_urls_{uid}"] = urls

    await _refresh_page_impl(ctx.platform_ctx, admin_id, page, edit=True, store_urls_fn=_store)
