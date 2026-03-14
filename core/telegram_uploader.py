"""Telegram upload utilities - extracted to eliminate duplication in callbacks."""

import asyncio
import logging
import os
from typing import Any, Callable, Optional

from telegram import Update
from telegram.message import Message

logger = logging.getLogger(__name__)


async def upload_with_retry(
    query,
    file_path: str,
    send_method: Callable,
    max_retries: int = 3,
    initial_delay: float = 5.0,
    write_timeout: int = 600,
    **send_kwargs
) -> Optional[Message]:
    """
    Upload file to Telegram with exponential backoff retry.
    
    Args:
        query: CallbackQuery object for editing messages
        file_path: Path to file to upload
        send_method: Async function to send the file
        max_retries: Maximum retry attempts
        initial_delay: Initial delay between retries (seconds)
        write_timeout: Timeout for upload
        **send_kwargs: Additional arguments passed to send_method
    
    Returns:
        Message object if successful, None otherwise
    """
    retry_delay = initial_delay
    last_error = None
    
    for attempt in range(max_retries):
        try:
            logger.info(f"Upload attempt {attempt + 1}/{max_retries}...")
            with open(file_path, "rb") as f:
                sent_msg = await send_method(file=f, write_timeout=write_timeout, **send_kwargs)
            logger.info(f"Upload successful")
            return sent_msg
        except Exception as upload_err:
            last_error = upload_err
            logger.warning(f"Upload attempt {attempt + 1} failed: {upload_err}")
            if attempt < max_retries - 1:
                logger.info(f"Retrying in {retry_delay} seconds...")
                await asyncio.sleep(retry_delay)
                retry_delay *= 2
    
    raise last_error if last_error else RuntimeError("Upload failed")


async def upload_video(
    query,
    file_path: str,
    caption: str = None,
    max_retries: int = 3
) -> Optional[str]:
    """Upload video and return file_id."""
    async def send_video(file, **kwargs):
        return await query.message.reply_video(video=file, caption=caption, **kwargs)
    
    sent_msg = await upload_with_retry(
        query, file_path, send_video, 
        max_retries=max_retries, write_timeout=600
    )
    return sent_msg.video.file_id if sent_msg and sent_msg.video else None


async def upload_audio(
    query,
    file_path: str,
    title: str = None,
    caption: str = None,
    max_retries: int = 3
) -> Optional[str]:
    """Upload audio and return file_id."""
    async def send_audio(file, **kwargs):
        return await query.message.reply_audio(audio=file, title=title, caption=caption, **kwargs)
    
    sent_msg = await upload_with_retry(
        query, file_path, send_audio,
        max_retries=max_retries, write_timeout=300
    )
    return sent_msg.audio.file_id if sent_msg and sent_msg.audio else None


async def upload_photo(
    query,
    file_path: str,
    caption: str = None,
    max_retries: int = 3
) -> Optional[str]:
    """Upload photo and return file_id."""
    async def send_photo(file, **kwargs):
        return await query.message.reply_photo(photo=file, caption=caption, **kwargs)
    
    sent_msg = await upload_with_retry(
        query, file_path, send_photo,
        max_retries=max_retries
    )
    return sent_msg.photo[-1].file_id if sent_msg and sent_msg.photo else None
