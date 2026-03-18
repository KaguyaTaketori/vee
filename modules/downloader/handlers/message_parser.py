"""
modules/downloader/handlers/message_parser.py
"""
from __future__ import annotations

import logging
import os
import re
from urllib.parse import urlparse

from telegram import Update
from telegram.ext import CallbackContext

from modules.downloader.services.facades import DownloadFacade
from modules.downloader.strategies.sender import TelegramSender
from modules.downloader.services.domain_config import SUPPORTED_DOMAINS, is_spotify_url
from shared.services.middleware import RequestContext, default_pipeline
from shared.services.platform_context import PlatformContext, TelegramContext, btn
from shared.services.user_service import track_user, warm_user_lang
from shared.services.session import UserSession
from database.history import check_recent_download
from utils.i18n import t
from utils.utils import require_message

logger = logging.getLogger(__name__)

URL_PATTERN = re.compile(
    r'(?:https?://)'
    r'(?:(?:[A-Z0-9](?:[A-Z0-9-]{0,61}[A-Z0-9])?\.)+[A-Z]{2,6}\.?|'
    r'localhost|'
    r'\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3})'
    r'(?::\d+)?'
    r'(?:/?|[/?]\S+)$', re.IGNORECASE)

ALLOWED_URL_PATTERN = re.compile(
    r'^(?:https?://)'
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


# ── Business logic (_impl functions) ──────────────────────────────────────

async def _handle_link_impl(ctx: PlatformContext, urls: list[str], update, context) -> None:
    """Core dispatch after URL extraction and middleware checks."""
    if len(urls) == 1:
        await _handle_single_url(ctx, update, context, urls[0])
    else:
        await _handle_batch_urls(ctx, update, context, urls)


async def _handle_single_url(
    ctx: PlatformContext,
    update: Update,
    context: CallbackContext,
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
        if file_size > 0:
            cached_file_path = file_path

    if cached_file_path:
        download_type = recent_download.get("download_type", "video")
        processing_msg = await update.message.reply_text(t("processing_please_wait", ctx.user_id))
        sender = TelegramSender.from_message(update.message, processing_msg)
        session = UserSession(url=url, user_id=ctx.user_id, sender=sender)
        await DownloadFacade.send_cached(session, cached_file_path, download_type)
        return

    # Show download-type selection keyboard
    session_key = UserSession.store(url=url, user_id=ctx.user_id)
    is_spotify = is_spotify_url(url)

    if is_spotify:
        buttons = [
            [btn(t("audio", ctx.user_id), f"dl_spotify_{session_key}")],
        ]
    else:
        buttons = [
            [btn(t("video", ctx.user_id), f"dl_video_{session_key}"),
             btn(t("audio", ctx.user_id), f"dl_audio_{session_key}")],
            [btn(t("thumbnail", ctx.user_id), f"dl_thumb_{session_key}")],
        ]

    await ctx.send_keyboard(t("what_download", ctx.user_id), buttons)


async def _handle_batch_urls(
    ctx: PlatformContext,
    update: Update,
    context: CallbackContext,
    urls: list[str],
) -> None:
    if len(urls) > MAX_BATCH_SIZE:
        await ctx.send(t("batch_limit_exceeded", ctx.user_id, max=MAX_BATCH_SIZE, count=len(urls)))
        urls = urls[:MAX_BATCH_SIZE]

    await ctx.send(t("batch_start", ctx.user_id, count=len(urls)))

    for i, url in enumerate(urls, 1):
        processing_msg = await update.message.reply_text(
            t("batch_item_queued", ctx.user_id, index=i, total=len(urls), url=url[:50])
        )
        sender = TelegramSender.from_message(update.message, processing_msg)
        download_type = _infer_download_type(url)
        session = UserSession(url=url, user_id=ctx.user_id, sender=sender)
        await DownloadFacade.enqueue(session, download_type)


# ── PTB entry point ────────────────────────────────────────────────────────

@require_message
async def handle_link(update: Update, context: CallbackContext) -> None:
    user = update.message.from_user
    track_user(user)
    await warm_user_lang(user.id)

    text = update.message.text.strip()
    urls = [u for u in URL_PATTERN.findall(text) if is_valid_url(u)]

    ctx = TelegramContext.from_message(update, context)

    if not urls:
        await ctx.send(t("unsupported_url", ctx.user_id))
        return

    # Middleware check (auth + rate limit)
    mw_ctx = RequestContext(user=user, reply=update.message.reply_text)
    result = await default_pipeline.run(mw_ctx)
    if not result.ok:
        await ctx.send(t(result.error_key, ctx.user_id))
        return

    await _handle_link_impl(ctx, urls, update, context)
