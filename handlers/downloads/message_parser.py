import os
import asyncio
import logging
import re
from urllib.parse import urlparse

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import CallbackContext

from config import MAX_CACHE_SIZE, MAX_FILE_SIZE
from services.user_service import track_user
from services.ratelimit import rate_limiter
from database.history import check_recent_download
from utils.i18n import t
from utils.utils import is_user_allowed, require_message
from integrations.downloaders.ytdlp_client import is_spotify_url
from services.session import UserSession
from services.facades import DownloadFacade

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

def _infer_download_type(url: str) -> str:
    if is_spotify_url(url):
        return "spotify"
    return "download_audio"


def extract_url(text: str) -> str | None:
    """Extract first URL from text message."""
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


async def _show_quality_options(query, url):
    from integrations.downloaders.ytdlp_client import get_formats, CookieExpiredError
    from integrations.downloaders.helpers import mask_url
    
    user_id = query.from_user.id
    try:
        await query.edit_message_text(t("loading_quality", user_id))
    except Exception as e:
        logger.debug(f"Error editing message: {e}")

    try:
        formats, info = await get_formats(url)
    except CookieExpiredError:
        logger.warning(f"Cookie expired when fetching formats for {mask_url(url)}")
        try:
            await query.edit_message_text(t("cookie_expired_error", user_id))
        except Exception:
            pass
        return
    except Exception as e:
        logger.error(f"Failed to fetch formats for {mask_url(url)}: {e}")
        try:
            await query.edit_message_text(t("format_fetch_error", user_id))
        except Exception:
            pass
        return

    resolutions = {}
    for f in formats:
        height = f.get("height")
        filesize = f.get("filesize") or f.get("filesize_approx", 0)
        acodec = f.get("acodec", "none")
        has_audio = acodec and acodec != "none"
        if height and height in [2160, 1440, 1080, 720, 480, 360, 240]:
            if height not in resolutions:
                resolutions[height] = (f.get("format_id"), has_audio)
            elif filesize and filesize < MAX_FILE_SIZE:
                current = resolutions.get(height)
                if current and not current[1] and has_audio:
                    resolutions[height] = (f.get("format_id"), has_audio)

    keyboard = []
    priority = [1080, 720, 480, 360, 240, 2160, 1440]
    
    for height in priority:
        if height in resolutions:
            format_data = resolutions[height]
            format_id = format_data[0] if isinstance(format_data, tuple) else format_data
            label = f"{height}p HD" if height >= 720 else f"{height}p"
            keyboard.append([InlineKeyboardButton(label, callback_data=f"quality_{format_id}")])
    
    keyboard.append([InlineKeyboardButton(t("best_quality", user_id), callback_data="quality_best")])
    
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    title = info.get("title") or "this video"
    try:
        await query.edit_message_text(t("select_quality", user_id, title=title[:50]), reply_markup=reply_markup)
    except Exception as e:
        logger.error(f"Error editing message: {e}")


@require_message
async def handle_link(update: Update, context: CallbackContext):
    user = update.message.from_user
    user_id = user.id
    track_user(user)
    from utils.i18n import warm_user_lang
    await warm_user_lang(user_id)

    if not is_user_allowed(user_id):
        await update.message.reply_text(t("not_authorized", user_id))
        return
    
    can_download, reason = await rate_limiter.check_limit(user_id)
    if not can_download:
        await update.message.reply_text(t("rate_limit_exceeded", user_id))
        return
    
    text = update.message.text.strip()

    urls = [u for u in URL_PATTERN.findall(text) if is_valid_url(u)]

    if not urls:
        await update.message.reply_text(t("unsupported_url", user_id))
        return

    if len(urls) == 1:
        await _handle_single_url(update, context, user_id, urls[0])
        return

    await _handle_batch_urls(update, context, user_id, urls)


async def _handle_single_url(update, context, user_id, url):
    recent_download = await check_recent_download(url, max_age_hours=24, download_type=None,)
    cached_file_path = None
    if recent_download:
        file_path = recent_download.get("file_path")
        file_size = os.path.getsize(file_path) if file_path and os.path.exists(file_path) else 0
        if file_size <= MAX_CACHE_SIZE:
            cached_file_path = file_path
    
    UserSession.set_pending(context, user_id, url, cached_file_path)

    cached_msg = f"\n\n{t('cached_file_used', user_id)}" if cached_file_path else ""
    
    is_spotify = is_spotify_url(url)
    
    if is_spotify:
        keyboard = [
            [InlineKeyboardButton(t("download_mp3_320k", user_id), callback_data="download_audio")],
        ]
    else:
        keyboard = [
            [
                InlineKeyboardButton(t("video", user_id), callback_data="download_video"),
                InlineKeyboardButton(t("audio", user_id), callback_data="download_audio"),
            ],
            [
                InlineKeyboardButton(t("thumbnail", user_id), callback_data="download_thumbnail"),
                InlineKeyboardButton("📝 " + t("subtitle", user_id), callback_data="download_subtitle"),
            ],
        ]
    
    await update.message.reply_text(
        t("what_download", user_id) + cached_msg,
        reply_markup=InlineKeyboardMarkup(keyboard)
    )

MAX_BATCH_SIZE = 5

async def _handle_batch_urls(update, context, user_id, urls: list[str]):
    if len(urls) > MAX_BATCH_SIZE:
        await update.message.reply_text(
            t("batch_limit_exceeded", user_id, max=MAX_BATCH_SIZE, count=len(urls))
        )
        urls = urls[:MAX_BATCH_SIZE]

    await update.message.reply_text(
        t("batch_start", user_id, count=len(urls))
    )

    for i, url in enumerate(urls, 1):
        status_msg = await update.message.reply_text(
            t("batch_item_queued", user_id, index=i, total=len(urls), url=url[:40])
        )
        await DownloadFacade.enqueue_silent(
            user=update.message.from_user,
            url=url,
            download_type=_infer_download_type(url),
            status_msg=status_msg,
            context=context,
        )
