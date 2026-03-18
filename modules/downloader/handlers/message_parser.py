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

from modules.downloader.strategies.sender import TelegramSender

from modules.downloader.services.facades import DownloadFacade
from services.middleware import RequestContext, default_pipeline
from shared.services.session import UserSession
from shared.services.user_service import track_user, warm_user_lang
from utils.i18n import t
from utils.utils import require_message

logger = logging.getLogger(__name__)


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


@require_message
async def handle_link(update: Update, context: CallbackContext) -> None:
    user    = update.message.from_user
    user_id = user.id

    track_user(user)
    await warm_user_lang(user_id)

    text = update.message.text.strip()
    urls = [u for u in URL_PATTERN.findall(text) if is_valid_url(u)]

    if not urls:
        await update.message.reply_text(t("unsupported_url", user_id))
        return

    ctx = RequestContext(user=user, reply=update.message.reply_text)
    result = await default_pipeline.run(ctx)
    if not result.ok:
        await update.message.reply_text(t(result.error_key, user_id))
        return

    if len(urls) == 1:
        await _handle_single_url(update, context, user_id, urls[0])
    else:
        await _handle_batch_urls(update, context, user_id, urls)


async def _handle_single_url(
    update: Update,
    context: CallbackContext,
    user_id: int,
    url: str,
) -> None:
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



async def _handle_batch_urls(
    update: Update,
    context: CallbackContext,
    user_id: int,
    urls: list[str],
) -> None:
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

        sender = TelegramSender.from_message(status_msg, processing_msg=status_msg)

        await DownloadFacade.enqueue_silent(
            sender=sender,
            url=url,
            download_type=_infer_download_type(url),
            context=context,
        )


async def _show_quality_options(query, url: str) -> None:
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
        filesize_val = f.get("filesize") or f.get("filesize_approx", 0)
        filesize = int(filesize_val) if isinstance(filesize_val, (int, float)) else 0
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
