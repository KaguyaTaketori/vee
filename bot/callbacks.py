import os
import asyncio
import logging
import re
from urllib.parse import urlparse

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import CallbackContext

from config import MAX_CACHE_SIZE, MAX_FILE_SIZE, ADMIN_IDS
from services.user_service import track_user, get_allowed_users
from services.ratelimit import rate_limiter
from utils.logger import log_user
from database.history import check_recent_download, get_user_history
from utils.i18n import warm_user_lang, set_user_lang as i18n_set_user_lang, LANGUAGES, t
from integrations.downloaders.ytdlp_client import get_formats, is_spotify_url
from utils.utils import format_history_item, format_history_list, is_user_allowed
from bot.user_commands import _send_history_page
from services.session import UserSession
from services.queue import download_queue

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

_CALLBACK_HANDLERS: list[tuple[callable, callable]] = []

def register(matcher: callable):
    def decorator(func: callable):
        _CALLBACK_HANDLERS.append((matcher, func))
        return func
    return decorator

@register(lambda d: d.startswith("lang_"))
async def _cb_lang(query, context):
    lang_code = query.data.replace("lang_", "")
    if lang_code in LANGUAGES:
        await i18n_set_user_lang(query.from_user.id, lang_code)
        await query.edit_message_text(t("language_changed", query.from_user.id))



@register(lambda d: d.startswith("uh_"))
async def _cb_admin_history(query, context):
    user_id = query.from_user.id
    if ADMIN_IDS and user_id not in ADMIN_IDS:
        await query.answer("Admin only.", show_alert=True)
        return
    target_id = int(query.data.replace("uh_", ""))
    history = await get_user_history(target_id, limit=20)
    msg = format_history_list(history, f"Download history for user {target_id}:\n\n")
    await query.edit_message_text(msg)

@register(lambda d: d == "cancel_menu_close")
async def _cb_cancel_close(query, context):
    try:
        await query.delete_message()
    except Exception:
        await query.edit_message_text(t("closed", query.from_user.id))


@register(lambda d: d.startswith("cancel_task_"))
async def _cb_cancel_task(query, context):
    user_id = query.from_user.id
    task_id = query.data.replace("cancel_task_", "")
    task = download_queue.get_task(task_id)
    if not task:
        await query.edit_message_text(t("task_not_found", user_id))
        return
    is_admin = user_id in ADMIN_IDS if ADMIN_IDS else False
    if task.user_id != user_id and not is_admin:
        await query.answer(t("cancel_own_only", user_id), show_alert=True)
        return
    success = await download_queue.cancel_task(task_id)
    key = "task_cancelled" if success else "cancel_failed"
    await query.edit_message_text(t(key, user_id))

@register(lambda d: d == "download_video")
async def _cb_download_video(query, context):
    url = UserSession.get_pending_url(context, query.from_user.id)
    if url:
        await show_quality_options(query, url)


@register(lambda d: d.startswith("quality_") or d in ("download_audio", "download_thumbnail"))
async def _cb_download(query, context):
    user_id = query.from_user.id
    url = UserSession.get_pending_url(context, user_id)
    if not url:
        await query.edit_message_text(t("session_expired", user_id))
        return
    try:
        processing_msg = await query.edit_message_text(t("processing", user_id))
    except Exception:
        processing_msg = query.message
    from services.facades import DownloadFacade
    success, error_key = await DownloadFacade.process_download_request(
        query, url, query.data, context, processing_msg
    )
    if not success:
        try:
            await processing_msg.edit_text(t(error_key, user_id))
        except Exception:
            pass

@register(lambda d: d.startswith("history_page_"))
async def _cb_history_page(query, context):
    # callback_data 格式: history_page_{user_id}_{page}
    parts = query.data.split("_")
    target_user_id = int(parts[2])
    page = int(parts[3])

    if query.from_user.id != target_user_id:
        await query.answer("Not your history.", show_alert=True)
        return

    await query.answer()
    await _send_history_page(query, target_user_id, page)

def extract_url(text: str) -> str | None:
    """Extract first URL from text message."""
    match = URL_PATTERN.search(text)
    return match.group(0) if match else None


def is_valid_url(url: str) -> bool:
    if not ALLOWED_URL_PATTERN.match(url):
        return False
    try:
        domain = urlparse(url).netloc.lower()
        domain = domain.replace("www.", "")
        if not any(domain.endswith(d) or domain == d for d in SUPPORTED_DOMAINS):
            return False
        return True
    except Exception:
        return False


async def handle_link(update: Update, context: CallbackContext):
    if not update.message:
        return
    
    user = update.message.from_user
    user_id = user.id
    track_user(user)
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
    recent_download = await check_recent_download(url, max_age_hours=24)
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
            [InlineKeyboardButton("🎵 Download MP3 (320k)", callback_data="download_audio")],
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
        from services.facades import DownloadFacade
        await DownloadFacade.enqueue_silent(
            user=update.message.from_user,
            url=url,
            download_type="audio",
            status_msg=status_msg,
            context=context,
        )

async def handle_callback(update: Update, context: CallbackContext):
    query = update.callback_query
    if not query:
        return
    await query.answer()

    for matcher, handler in _CALLBACK_HANDLERS:
        if matcher(query.data):
            await handler(query, context)
            return

    logger.warning(f"Unhandled callback_data: {query.data}")


async def show_quality_options(query, url):
    user_id = query.from_user.id
    try:
        await query.edit_message_text(t("loading_quality", user_id))
    except Exception as e:
        logger.debug(f"Error editing message: {e}")
    
    formats, info = await get_formats(url)
    logger.info(f"Available formats: {[(f.get('format_id'), f.get('height'), f.get('ext'), f.get('acodec')) for f in formats[:30]]}")

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
    
    keyboard.append([InlineKeyboardButton("⭐ Best Quality (Auto)", callback_data="quality_best")])
    
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    title = info.get("title") or "this video"
    try:
        await query.edit_message_text(t("select_quality", user_id, title=title[:50]), reply_markup=reply_markup)
    except Exception as e:
        logger.error(f"Error editing message: {e}")
