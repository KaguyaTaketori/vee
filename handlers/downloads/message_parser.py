"""
handlers/downloads/message_parser.py
──────────────────────────────────────
消息入口 Handler。

变更摘要
--------
1. **Pipeline 过滤**：Auth / RateLimit 两个中间件通过 ``default_pipeline.run()``
   统一处理，handle_link() 里再无裸露的 ``if not is_user_allowed`` /
   ``if not can_download`` 分支。

2. **TelegramSender 在 Handler 层包装**：收到消息后立刻将
   ``update.message`` 包装为 ``TelegramSender``（实现 ``BotSender`` 协议），
   然后把这个平台无关的 Sender 对象传给 Facade 和 Queue。
   底层策略只调用 ``sender.send_message()`` 等接口，彻底与 Telegram 解耦。

3. **批量 URL**：每条 URL 同样走 enqueue_silent，Sender 在此处创建并注入。
"""

from __future__ import annotations

import logging
import os
import re
from urllib.parse import urlparse

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import CallbackContext

from config import MAX_CACHE_SIZE
from database.history import check_recent_download
from integrations.downloaders.ytdlp_client import is_spotify_url

# ── 平台 Sender（Handler 层唯一接触 Telegram 的地方）────────────────────────
from integrations.strategies.sender import TelegramSender

from services.facades import DownloadFacade
from services.middleware import RequestContext, default_pipeline
from services.session import UserSession
from services.user_service import track_user, warm_user_lang
from utils.i18n import t
from utils.utils import require_message

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# URL 匹配
# ---------------------------------------------------------------------------

URL_PATTERN = re.compile(r'https?://[^\s<>"{}|\\^`\[\]]+')

SUPPORTED_DOMAINS = {
    "youtube.com", "youtu.be", "youtube-nocookie.com", "music.youtube.com",
    "instagram.com", "twitter.com", "x.com",
    "tiktok.com", "facebook.com", "reddit.com",
    "vk.com", "snapchat.com", "tumblr.com",
    "pin.it", "threads.net",
    "bilibili.com", "b23.tv",
    "spotify.com", "open.spotify.com",
}

ALLOWED_URL_PATTERN = re.compile(
    r'^https?://'
    r'(?:(?:[A-Z0-9](?:[A-Z0-9-]{0,61}[A-Z0-9])?\.)+[A-Z]{2,6}\.?|'
    r'localhost|'
    r'\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3})'
    r'(?::\d+)?'
    r'(?:/?|[/?]\S+)$', re.IGNORECASE)

MAX_BATCH_SIZE = 5


# ---------------------------------------------------------------------------
# 工具函数
# ---------------------------------------------------------------------------

def extract_url(text: str) -> str | None:
    match = URL_PATTERN.search(text)
    return match.group(0) if match else None


def is_valid_url(url: str) -> bool:
    if not ALLOWED_URL_PATTERN.match(url):
        return False
    try:
        domain = urlparse(url).netloc.lower().replace("www.", "")
        return any(domain.endswith(d) or domain == d for d in SUPPORTED_DOMAINS)
    except Exception:
        return False


def _infer_download_type(url: str) -> str:
    return "spotify" if is_spotify_url(url) else "download_audio"


# ---------------------------------------------------------------------------
# 主 Handler
# ---------------------------------------------------------------------------

@require_message
async def handle_link(update: Update, context: CallbackContext) -> None:
    """文本消息入口。

    流程：
        1. 提取 URL
        2. Pipeline 过滤（Auth → RateLimit）
        3. 将 update.message 包装为 TelegramSender（唯一的 Telegram 耦合点）
        4. 分发给 _handle_single_url / _handle_batch_urls
    """
    user    = update.message.from_user
    user_id = user.id

    track_user(user)
    await warm_user_lang(user_id)

    # ── 1. URL 提取 ──────────────────────────────────────────────────────────
    text = update.message.text.strip()
    urls = [u for u in URL_PATTERN.findall(text) if is_valid_url(u)]

    if not urls:
        await update.message.reply_text(t("unsupported_url", user_id))
        return

    # ── 2. 统一 Pipeline 过滤（Auth + RateLimit）────────────────────────────
    ctx = RequestContext(user=user, reply=update.message.reply_text)
    result = await default_pipeline.run(ctx)
    if not result.ok:
        await update.message.reply_text(t(result.error_key, user_id))
        return

    # ── 3. 立刻包装 Sender（此后不再暴露任何 Telegram 对象给下层）──────────
    #   此处仅创建「轻量」Sender 用于回复选项菜单；
    #   真正的下载 Sender 在 _handle_single_url / _handle_batch_urls 内
    #   针对各自的 processing_msg 重新包装。

    # ── 4. 分发 ─────────────────────────────────────────────────────────────
    if len(urls) == 1:
        await _handle_single_url(update, context, user_id, urls[0])
    else:
        await _handle_batch_urls(update, context, user_id, urls)


# ---------------------------------------------------------------------------
# 单 URL：展示下载选项
# ---------------------------------------------------------------------------

async def _handle_single_url(
    update: Update,
    context: CallbackContext,
    user_id: int,
    url: str,
) -> None:
    """检查缓存、存 session、弹出格式选择键盘。"""
    recent_download = await check_recent_download(url, max_age_hours=24, download_type=None)
    cached_file_path: str | None = None
    if recent_download:
        file_path = recent_download.get("file_path")
        file_size = (
            os.path.getsize(file_path)
            if file_path and os.path.exists(file_path)
            else 0
        )
        if file_size <= MAX_CACHE_SIZE:
            cached_file_path = file_path

    UserSession.set_pending(context, user_id, url, cached_file_path)

    cached_msg = f"\n\n{t('cached_file_used', user_id)}" if cached_file_path else ""

    if is_spotify_url(url):
        keyboard = [
            [InlineKeyboardButton(t("download_mp3_320k", user_id), callback_data="download_audio")],
        ]
    else:
        keyboard = [
            [
                InlineKeyboardButton(t("video", user_id),    callback_data="download_video"),
                InlineKeyboardButton(t("audio", user_id),    callback_data="download_audio"),
            ],
            [
                InlineKeyboardButton(t("thumbnail", user_id),             callback_data="download_thumbnail"),
                InlineKeyboardButton("📝 " + t("subtitle", user_id),      callback_data="download_subtitle"),
            ],
        ]

    await update.message.reply_text(
        t("what_download", user_id) + cached_msg,
        reply_markup=InlineKeyboardMarkup(keyboard),
    )


# ---------------------------------------------------------------------------
# 批量 URL：静默入队
# ---------------------------------------------------------------------------

async def _handle_batch_urls(
    update: Update,
    context: CallbackContext,
    user_id: int,
    urls: list[str],
) -> None:
    """把多个 URL 逐条入队（静默模式，不弹格式选择）。

    对每条 URL：
        1. 发送一条「已入队」状态消息，拿到 status_msg；
        2. 立刻将 status_msg 包装为 TelegramSender；
        3. 把 Sender 传给 enqueue_silent，Facade 和 Queue 全程不碰 Telegram。
    """
    if len(urls) > MAX_BATCH_SIZE:
        await update.message.reply_text(
            t("batch_limit_exceeded", user_id, max=MAX_BATCH_SIZE, count=len(urls))
        )
        urls = urls[:MAX_BATCH_SIZE]

    await update.message.reply_text(t("batch_start", user_id, count=len(urls)))

    for i, url in enumerate(urls, 1):
        status_msg = await update.message.reply_text(
            t("batch_item_queued", user_id, index=i, total=len(urls), url=url[:40])
        )

        # ── Handler 层包装 Sender，下层只见 BotSender 接口 ──────────────────
        # 批量模式没有 CallbackQuery，用 SilentMessageQuery 作为 query 适配器。
        from services.query_adapters import SilentMessageQuery
        silent_query = SilentMessageQuery(update.message.from_user, status_msg)
        sender = TelegramSender(query=silent_query, processing_msg=status_msg)

        await DownloadFacade.enqueue_silent(
            sender=sender,
            url=url,
            download_type=_infer_download_type(url),
            context=context,
        )


# ---------------------------------------------------------------------------
# 画质选项弹窗（供 inline_actions 调用）
# ---------------------------------------------------------------------------

async def _show_quality_options(query, url: str) -> None:
    """拉取可用分辨率并呈现选择键盘。"""
    from integrations.downloaders.helpers import mask_url
    from integrations.downloaders.ytdlp_client import CookieExpiredError, get_formats
    from config import MAX_FILE_SIZE

    user_id = query.from_user.id

    try:
        await query.edit_message_text(t("loading_quality", user_id))
    except Exception as exc:
        logger.debug("Error editing message: %s", exc)

    try:
        formats, info = await get_formats(url)
    except CookieExpiredError:
        logger.warning("Cookie expired when fetching formats for %s", mask_url(url))
        try:
            await query.edit_message_text(t("cookie_expired_error", user_id))
        except Exception:
            pass
        return
    except Exception as exc:
        logger.error("Failed to fetch formats for %s: %s", mask_url(url), exc)
        try:
            await query.edit_message_text(t("format_fetch_error", user_id))
        except Exception:
            pass
        return

    resolutions: dict[int, tuple] = {}
    for f in formats:
        height   = f.get("height")
        filesize = f.get("filesize") or f.get("filesize_approx", 0)
        acodec   = f.get("acodec", "none")
        has_audio = acodec and acodec != "none"
        if height and height in [2160, 1440, 1080, 720, 480, 360, 240]:
            if height not in resolutions:
                resolutions[height] = (f.get("format_id"), has_audio)
            elif filesize and filesize < MAX_FILE_SIZE:
                current = resolutions[height]
                if current and not current[1] and has_audio:
                    resolutions[height] = (f.get("format_id"), has_audio)

    keyboard = []
    for height in [1080, 720, 480, 360, 240, 2160, 1440]:
        if height in resolutions:
            format_data = resolutions[height]
            format_id   = format_data[0] if isinstance(format_data, tuple) else format_data
            label       = f"{height}p HD" if height >= 720 else f"{height}p"
            keyboard.append([InlineKeyboardButton(label, callback_data=f"quality_{format_id}")])

    keyboard.append([InlineKeyboardButton(t("best_quality", user_id), callback_data="quality_best")])

    title = (info.get("title") or "this video") if info else "this video"
    try:
        await query.edit_message_text(
            t("select_quality", user_id, title=title[:50]),
            reply_markup=InlineKeyboardMarkup(keyboard),
        )
    except Exception as exc:
        logger.error("Error editing message: %s", exc)
