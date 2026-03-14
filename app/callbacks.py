import os
import asyncio
import logging
import re
from urllib.parse import urlparse
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import CallbackContext

logger = logging.getLogger(__name__)

from config import track_user, get_allowed_users, MAX_CACHE_SIZE, MAX_FILE_SIZE
from core.ratelimit import rate_limiter
from core.logger import log_user
from core.history import check_recent_download, get_user_history
from core.i18n import t
from core.downloader import get_formats, is_spotify_url

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
    
    allowed = get_allowed_users()
    if allowed and user_id not in allowed:
        await update.message.reply_text(t("not_authorized", user_id))
        return
    
    allowed, reason = await rate_limiter.check_limit(user_id)
    if not allowed:
        await update.message.reply_text(t("rate_limit_exceeded", user_id))
        return
    
    text = update.message.text.strip()
    url = extract_url(text)
    
    if not url or not is_valid_url(url):
        await update.message.reply_text(t("unsupported_url", user_id))
        return
    
    log_user(update.message.from_user, "sent_link")

    recent_download = await check_recent_download(url, max_age_hours=24)
    cached_file_path = None
    if recent_download:
        file_path = recent_download.get("file_path")
        file_size = os.path.getsize(file_path) if file_path and os.path.exists(file_path) else 0
        if file_size <= MAX_CACHE_SIZE:
            cached_file_path = file_path
    
    context.user_data[f"pending_url_{user_id}"] = url
    context.user_data[f"cached_file_{user_id}"] = cached_file_path

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
            [InlineKeyboardButton(t("thumbnail", user_id), callback_data="download_thumbnail")],
        ]
    
    reply_markup = InlineKeyboardMarkup(keyboard)

    await update.message.reply_text(
        t("what_download", user_id) + cached_msg,
        reply_markup=reply_markup
    )


async def handle_callback(update: Update, context: CallbackContext):
    query = update.callback_query
    if not query:
        return

    await query.answer()
    
    user_id = query.from_user.id
    
    if query.data.startswith("lang_"):
        from core.i18n import set_user_lang as i18n_set_user_lang, LANGUAGES, t
        lang_code = query.data.replace("lang_", "")
        if lang_code in LANGUAGES:
            await i18n_set_user_lang(user_id, lang_code)
            await query.edit_message_text(t("language_changed", user_id))
        return
    
    if query.data.startswith("uh_"):
        from config import ADMIN_IDS
        if ADMIN_IDS and user_id not in ADMIN_IDS:
            await query.answer("Admin only.", show_alert=True)
            return
        target_id = int(query.data.replace("uh_", ""))
        history = await get_user_history(target_id, limit=20)
        if not history:
            await query.edit_message_text(f"No history for user {target_id}.")
            return
        msg = f"Download history for user {target_id}:\n\n"
        from datetime import datetime
        for item in history:
            dt = datetime.fromtimestamp(item["timestamp"])
            status = "✅" if item.get("status") == "success" else "❌"
            size = ""
            if item.get("file_size"):
                size = f" ({item['file_size'] // (1024*1024)}MB)"
            msg += f"{status} {item['download_type']}{size}\n"
            msg += f"   {item.get('title', 'N/A')[:40]}\n"
            msg += f"   {dt.strftime('%Y-%m-%d %H:%M')}\n\n"
        await query.edit_message_text(msg)
        return
    
    url = context.user_data.get(f"pending_url_{user_id}")
    if not url:
        try:
            await query.edit_message_text(t("session_expired", user_id))
        except Exception as e:
            logger.debug(f"Failed to edit message: {e}")
        return

    if query.data == "download_video":
        await show_quality_options(query, url)
        return
    
    if query.data.startswith("quality_") or query.data in ("download_audio", "download_thumbnail"):
        try:
            processing_msg = await query.edit_message_text("Processing... Please wait.")
        except Exception:
            processing_msg = query.message
        
        from core.facades import DownloadFacade
        success, error_key = await DownloadFacade.process_download_request(
            query, url, query.data, context, processing_msg
        )
        
        if not success:
            try:
                await processing_msg.edit_text(t(error_key, user_id))
            except Exception:
                pass


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
