import os
import asyncio
import logging
import re
from urllib.parse import urlparse
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import CallbackContext

from config import MAX_FILE_SIZE, get_allowed_users
from core.downloader import get_formats, download_video, download_audio, get_thumbnail
from core.logger import log_user, log_download
from app.download import _make_progress_hook, download_executor

logger = logging.getLogger(__name__)

SUPPORTED_DOMAINS = {
    "youtube.com", "youtu.be", "youtube-nocookie.com",
    "instagram.com", "twitter.com", "x.com",
    "tiktok.com", "facebook.com", "reddit.com",
    "vk.com", "snapchat.com", "tumblr.com",
    "pin.it", "threads.net", "instagram.com"
}

ALLOWED_URL_PATTERN = re.compile(
    r'^https?://'  # http:// or https://
    r'(?:(?:[A-Z0-9](?:[A-Z0-9-]{0,61}[A-Z0-9])?\.)+[A-Z]{2,6}\.?|'  # domain
    r'localhost|'  # localhost
    r'\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3})'  # ip
    r'(?::\d+)?'  # optional port
    r'(?:/?|[/?]\S+)$', re.IGNORECASE)


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
    
    user_id = update.message.from_user.id
    allowed = get_allowed_users()
    if allowed and user_id not in allowed:
        await update.message.reply_text("You are not authorized to use this bot.")
        return
    
    url = update.message.text.strip()
    
    if not is_valid_url(url):
        await update.message.reply_text("Unsupported URL. Supported: YouTube, TikTok, Instagram, Twitter, etc.")
        return
    
    log_user(update.message.from_user, "sent_link")

    context.user_data["pending_url"] = url

    keyboard = [
        [
            InlineKeyboardButton("🎬 Video", callback_data="download_video"),
            InlineKeyboardButton("🎵 Audio (MP3)", callback_data="download_audio"),
        ],
        [InlineKeyboardButton("🖼️ Thumbnail", callback_data="download_thumbnail")],
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)

    await update.message.reply_text(
        "What would you like to download?",
        reply_markup=reply_markup
    )


async def handle_callback(update: Update, context: CallbackContext):
    query = update.callback_query
    if not query:
        return

    user_id = query.from_user.id
    allowed = get_allowed_users()
    if allowed and user_id not in allowed:
        await query.answer("You are not authorized.", show_alert=True)
        return

    await query.answer()
    log_user(query.from_user, query.data)
    
    url = context.user_data.get("pending_url")
    if not url:
        try:
            await query.edit_message_text("Session expired. Please send the link again.")
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
            await send_video_with_format(query, url, processing_msg, format_id)
        elif query.data == "download_audio":
            try:
                processing_msg = await query.edit_message_text("Processing... Please wait.")
            except Exception:
                processing_msg = query.message
            await send_audio(query, url, processing_msg)
        elif query.data == "download_thumbnail":
            try:
                processing_msg = await query.edit_message_text("Processing... Please wait.")
            except Exception:
                processing_msg = query.message
            await send_thumbnail(query, url, processing_msg)
    except Exception as e:
        error_msg = str(e)
        logger.error(f"Error: {e}")
        try:
            if "No video could be found" in error_msg or "No video" in error_msg:
                await query.edit_message_text(
                    "No video found in this tweet.\n\n"
                    "Try: 🖼️ Thumbnail to get the image, or 🎵 Audio (MP3) if it's a video with audio issues."
                )
            else:
                await query.edit_message_text(f"Error: {error_msg[:200]}")
        except:
            pass


async def show_quality_options(query, url):
    formats, info = get_formats(url)
    logger.error(f"Available formats: {[(f.get('format_id'), f.get('height'), f.get('ext')) for f in formats[:30]]}")

    resolutions = {}
    for f in formats:
        height = f.get("height")
        filesize = f.get("filesize") or f.get("filesize_approx", 0)
        if height and height in [2160, 1440, 1080, 720, 480, 360, 240]:
            if height not in resolutions:
                resolutions[height] = f.get("format_id")
            elif filesize and filesize < MAX_FILE_SIZE:
                resolutions[height] = f.get("format_id")

    keyboard = []
    priority = [1080, 720, 480, 360, 240, 2160, 1440]
    
    for height in priority:
        if height in resolutions:
            label = f"{height}p HD" if height >= 720 else f"{height}p"
            keyboard.append([InlineKeyboardButton(label, callback_data=f"quality_{resolutions[height]}")])
    
    keyboard.append([InlineKeyboardButton("⭐ Best Quality (Auto)", callback_data="quality_best")])
    
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    title = info.get("title") or "this video"
    try:
        await query.edit_message_text(f"Select quality for:\n{title[:50]}...", reply_markup=reply_markup)
    except Exception as e:
        logger.error(f"Error editing message: {e}")


async def send_video_with_format(query, url, processing_msg, format_id):
    async def _download():
        try:
            formats, info = get_formats(url)
            available_ids = [f.get("format_id") for f in formats]
            logger.error(f"Requested format: {format_id}, Available: {available_ids[:20]}")
            
            progress_hook = _make_progress_hook(processing_msg)
            loop = asyncio.get_event_loop()
            filename, info = await loop.run_in_executor(
                download_executor, 
                lambda: download_video(url, format_id, progress_hook)
            )
            return filename, info
        except Exception as e:
            raise

    try:
        await processing_msg.edit_text("⬇️ Downloading...")
        filename, info = await _download()
    except Exception as e:
        await processing_msg.edit_text(f"Download failed: {str(e)}")
        return

    if not os.path.exists(filename):
        await processing_msg.edit_text("Download failed. Try another quality.")
        return

    file_size = os.path.getsize(filename)
    if file_size > MAX_FILE_SIZE:
        await processing_msg.edit_text(
            f"File too large ({file_size // (1024*1024)}MB). Maximum is 2GB."
        )
        os.remove(filename)
        return

    title = info.get("title")
    caption = f"🎬 {title}" if title else None
    
    await processing_msg.edit_text("Uploading...")
    try:
        with open(filename, "rb") as f:
            await query.message.reply_video(video=f, caption=caption)
        await processing_msg.delete()
        log_download(query.from_user, "video_downloaded", url, "success", file_size, format_id)
    except Exception as e:
        await processing_msg.edit_text(f"Upload failed: {str(e)}")
        log_download(query.from_user, "video_downloaded", url, f"upload_failed: {e}", file_size, format_id)
    
    if os.path.exists(filename):
        os.remove(filename)


async def send_audio(query, url, processing_msg):
    async def _download():
        try:
            progress_hook = _make_progress_hook(processing_msg)
            loop = asyncio.get_event_loop()
            filename, info = await loop.run_in_executor(
                download_executor,
                lambda: download_audio(url, progress_hook)
            )
            return filename, info
        except Exception as e:
            raise

    try:
        await processing_msg.edit_text("⬇️ Downloading...")
        filename, info = await _download()
    except Exception as e:
        await processing_msg.edit_text(f"Download failed: {str(e)}")
        return

    if not os.path.exists(filename):
        await processing_msg.edit_text("Download failed.")
        log_download(query.from_user, "audio_downloaded", url, "file_not_found")
        return

    file_size = os.path.getsize(filename)
    title = info.get("title")
    
    await processing_msg.edit_text("Uploading...")
    try:
        with open(filename, "rb") as f:
            await query.message.reply_audio(audio=f, title=title)
        await processing_msg.delete()
        log_download(query.from_user, "audio_downloaded", url, "success", file_size)
    except Exception as e:
        await processing_msg.edit_text(f"Upload failed: {str(e)}")
        log_download(query.from_user, "audio_downloaded", url, f"upload_failed: {e}", file_size)
    
    if os.path.exists(filename):
        os.remove(filename)


async def send_thumbnail(query, url, processing_msg):
    try:
        thumbnail_url, info = get_thumbnail(url)
    except Exception as e:
        await processing_msg.edit_text(f"Error: {str(e)}")
        return

    if not thumbnail_url:
        await processing_msg.edit_text("No thumbnail available.")
        return

    title = info.get("title")
    caption = f"🖼️ {title}" if title else None

    await processing_msg.edit_text("Fetching thumbnail...")
    await query.message.reply_photo(photo=thumbnail_url, caption=caption)
    await processing_msg.delete()
