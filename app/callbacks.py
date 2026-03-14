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
from core.logger import log_user, log_download
from core.history import check_recent_download, get_file_id_by_url, add_history, get_user_history
from core.i18n import t
from core.downloader import get_formats, download_video, download_audio, get_thumbnail, download_spotify, is_spotify_url
from app.download import _make_progress_hook

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
    
    allowed, reason = rate_limiter.check_limit(user_id)
    if not allowed:
        await update.message.reply_text(t("rate_limit_exceeded", user_id))
        return
    
    text = update.message.text.strip()
    url = extract_url(text)
    
    if not url or not is_valid_url(url):
        await update.message.reply_text(t("unsupported_url", user_id))
        return
    
    log_user(update.message.from_user, "sent_link")

    recent_download = check_recent_download(url, max_age_hours=24)
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
        from core.i18n import set_user_lang, LANGUAGES, t
        lang_code = query.data.replace("lang_", "")
        if lang_code in LANGUAGES:
            set_user_lang(user_id, lang_code)
            await query.edit_message_text(t("language_changed", user_id))
        return
    
    if query.data.startswith("uh_"):
        from config import ADMIN_IDS
        if ADMIN_IDS and user_id not in ADMIN_IDS:
            await query.answer("Admin only.", show_alert=True)
            return
        target_id = int(query.data.replace("uh_", ""))
        history = get_user_history(target_id, limit=20)
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
            msg += f"{status} {item['type']}{size}\n"
            msg += f"   {item.get('title', 'N/A')[:40]}\n"
            msg += f"   {dt.strftime('%Y-%m-%d %H:%M')}\n\n"
        await query.edit_message_text(msg)
        return
    
    allowed = get_allowed_users()
    if allowed and user_id not in allowed:
        await query.answer(t("not_authorized", user_id), show_alert=True)
        return

    log_user(query.from_user, query.data)
    
    url = context.user_data.get(f"pending_url_{user_id}")
    if not url:
        try:
            await query.edit_message_text(t("session_expired", user_id))
        except Exception:
            pass
        return

    try:
        if query.data == "download_video":
            await show_quality_options(query, url)
        elif query.data.startswith("quality_"):
            format_id = query.data.replace("quality_", "")
            try:
                processing_msg = await query.edit_message_text("Processing... Please wait.")
            except Exception:
                processing_msg = query.message
            asyncio.create_task(send_video_with_format(query, url, processing_msg, format_id, context))
        elif query.data in ("download_audio", "download_thumbnail"):
            from core.strategies import StrategyFactory
            strategy_key = "spotify" if (query.data == "download_audio" and is_spotify_url(url)) else query.data
            strategy = StrategyFactory.get(strategy_key)
            if strategy:
                try:
                    processing_msg = await query.edit_message_text("Processing... Please wait.")
                except Exception:
                    processing_msg = query.message
                asyncio.create_task(strategy.execute(query, url, processing_msg, context))
    except Exception as e:
        error_msg = str(e)
        logger.error(f"Error: {e}")
        try:
            if "No video could be found" in error_msg or "No video" in error_msg:
                await query.edit_message_text(t("no_video_found", user_id))
            else:
                await query.edit_message_text(f"Error: {error_msg[:200]}")
        except:
            pass


async def show_quality_options(query, url):
    user_id = query.from_user.id
    try:
        await query.edit_message_text(t("loading_quality", user_id))
    except:
        pass
    
    formats, info = await get_formats(url)
    logger.error(f"Available formats: {[(f.get('format_id'), f.get('height'), f.get('ext'), f.get('acodec')) for f in formats[:30]]}")

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


async def send_video_with_format(query, url, processing_msg, format_id, context):
    """Handle video download with format selection - uses Strategy pattern."""
    from core.strategies import VideoStrategy
    
    user_id = query.from_user.id
    cached_file = context.user_data.get(f"cached_file_{user_id}")
    
    if cached_file and os.path.exists(cached_file):
        logger.info(f"Using cached file for {url}: {cached_file}")
        filename = cached_file
        title = os.path.splitext(os.path.basename(filename))[0]
        info = {"title": title}
    else:
        try:
            await processing_msg.edit_text(t("downloading", user_id))
            formats, info = await get_formats(url)
            available_ids = [f.get("format_id") for f in formats]
            logger.error(f"Requested format: {format_id}, Available: {available_ids[:20]}")
            
            from core.downloader import download_video
            progress_hook = _make_progress_hook(processing_msg)
            filename, info = await download_video(url, format_id, progress_hook)
        except Exception as e:
            await processing_msg.edit_text(t("download_failed", user_id, error=str(e)))
            return

        if not os.path.exists(filename):
            await processing_msg.edit_text(t("download_failed", user_id, error="File not found"))
            return
    
    file_size = os.path.getsize(filename)
    if file_size > MAX_FILE_SIZE:
        await processing_msg.edit_text(
            t("file_too_large", user_id, size=f"{file_size // (1024*1024)}MB")
        )
        if not cached_file and os.path.exists(filename):
            os.remove(filename)
        return

    title = info.get("title")
    caption = f"🎬 {title}" if title else None
    
    await processing_msg.edit_text(t("uploading", user_id))
    try:
        existing_file_id = get_file_id_by_url(url)
        
        if existing_file_id:
            logger.info(f"Using file_id for {url}: {existing_file_id}")
            await query.message.reply_video(video=existing_file_id, caption=caption)
            msg = await query.message.reply_text("✅ Sent via file ID (no re-upload)")
            await processing_msg.delete()
            log_download(query.from_user, "video_downloaded", url, "success", file_size, format_id)
            add_history(query.from_user.id, url, "video", file_size, info.get("title"), "success", filename, existing_file_id)
        else:
            with open(filename, "rb") as f:
                sent_msg = await query.message.reply_video(video=f, caption=caption)
            
            new_file_id = sent_msg.video.file_id if sent_msg.video else None
            await processing_msg.delete()
            log_download(query.from_user, "video_downloaded", url, "success", file_size, format_id)
            add_history(query.from_user.id, url, "video", file_size, info.get("title"), "success", filename, new_file_id)
    except Exception as e:
        await processing_msg.edit_text(t("upload_failed", user_id, error=str(e)))
        log_download(query.from_user, "video_downloaded", url, f"upload_failed: {e}", file_size, format_id)
        add_history(query.from_user.id, url, "video", file_size, info.get("title"), "failed")
    
    if os.path.exists(filename) and not cached_file:
        os.remove(filename)


async def send_audio(query, url, processing_msg, context):
    """Handle audio download - now delegates to Strategy pattern."""
    from core.strategies import StrategyFactory
    
    strategy = StrategyFactory.get("download_audio")
    if strategy:
        asyncio.create_task(strategy.execute(query, url, processing_msg, context))
    else:
        await _send_audio_legacy(query, url, processing_msg, context)


async def _send_audio_legacy(query, url, processing_msg, context):
    user_id = query.from_user.id
    cached_file = context.user_data.get(f"cached_file_{user_id}")
    
    if cached_file and os.path.exists(cached_file) and cached_file.endswith('.mp3'):
        logger.info(f"Using cached audio file for {url}: {cached_file}")
        filename = cached_file
        title = os.path.splitext(os.path.basename(filename))[0]
    else:
        try:
            await processing_msg.edit_text(t("downloading", user_id))
            progress_hook = _make_progress_hook(processing_msg)
            filename, info = await download_audio(url, progress_hook)
        except Exception as e:
            await processing_msg.edit_text(t("download_failed", user_id, error=str(e)))
            return

        if not os.path.exists(filename):
            await processing_msg.edit_text(t("download_failed", user_id, error="File not found"))
            log_download(query.from_user, "audio_downloaded", url, "file_not_found")
            add_history(query.from_user.id, url, "audio", None, info.get("title"), "failed")
            return

        title = info.get("title")

    file_size = os.path.getsize(filename)
    
    await processing_msg.edit_text(t("uploading", user_id))
    try:
        existing_file_id = get_file_id_by_url(url)
        
        if existing_file_id:
            logger.info(f"Using file_id for audio {url}: {existing_file_id}")
            await query.message.reply_audio(audio=existing_file_id, title=title)
            msg = await query.message.reply_text("✅ Sent via file ID (no re-upload)")
            await processing_msg.delete()
            log_download(query.from_user, "audio_downloaded", url, "success", file_size)
            add_history(query.from_user.id, url, "audio", file_size, title, "success", filename, existing_file_id)
        else:
            with open(filename, "rb") as f:
                sent_msg = await query.message.reply_audio(audio=f, title=title)
            
            new_file_id = sent_msg.audio.file_id if sent_msg.audio else None
            await processing_msg.delete()
            log_download(query.from_user, "audio_downloaded", url, "success", file_size)
            add_history(query.from_user.id, url, "audio", file_size, title, "success", filename, new_file_id)
    except Exception as e:
        await processing_msg.edit_text(t("upload_failed", user_id, error=str(e)))
        log_download(query.from_user, "audio_downloaded", url, f"upload_failed: {e}", file_size)
        add_history(query.from_user.id, url, "audio", file_size, title, "failed")
    
    if os.path.exists(filename) and not cached_file:
        os.remove(filename)


async def send_thumbnail(query, url, processing_msg):
    user_id = query.from_user.id
    try:
        thumbnail_url, info = await get_thumbnail(url)
    except Exception as e:
        await processing_msg.edit_text(f"Error: {str(e)}")
        return

    if not thumbnail_url:
        await processing_msg.edit_text(t("no_thumbnail", user_id))
        return

    title = info.get("title")
    caption = f"🖼️ {title}" if title else None

    await processing_msg.edit_text(t("fetching_thumbnail", user_id))
    await query.message.reply_photo(photo=thumbnail_url, caption=caption)
    await processing_msg.delete()


async def _download_spotify(query, url, processing_msg):
    """Handle Spotify download."""
    user_id = query.from_user.id
    
    try:
        await processing_msg.edit_text("🎵 Downloading from Spotify via YouTube...")
        
        filename, info = await download_spotify(url)
        
        if not os.path.exists(filename):
            await processing_msg.edit_text(t("download_failed", user_id, error="File not found"))
            return
        
        file_size = os.path.getsize(filename)
        
        if file_size > MAX_FILE_SIZE:
            await processing_msg.edit_text(
                t("file_too_large", user_id, size=f"{file_size // (1024*1024)}MB")
            )
            os.remove(filename)
            return
        
        title = info.get("title")
        caption = f"🎵 {title}" if title else None
        
        await processing_msg.edit_text(t("uploading", user_id))
        
        existing_file_id = get_file_id_by_url(url)
        
        if existing_file_id:
            logger.info(f"Using file_id for {url}: {existing_file_id}")
            await query.message.reply_audio(audio=existing_file_id, caption=caption)
            msg = await query.message.reply_text("✅ Sent via file ID")
            await processing_msg.delete()
        else:
            with open(filename, "rb") as f:
                sent_msg = await query.message.reply_audio(audio=f, title=title)
            
            new_file_id = sent_msg.audio.file_id if sent_msg.audio else None
            await processing_msg.delete()
            add_history(query.from_user.id, url, "audio", file_size, title, "success", filename, new_file_id)
        
        if os.path.exists(filename):
            os.remove(filename)
            
    except Exception as e:
        logger.error(f"Spotify download error: {e}")
        await processing_msg.edit_text(f"Error: {str(e)[:200]}")
