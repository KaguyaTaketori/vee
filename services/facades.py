import logging
import asyncio
import uuid

from services.user_service import get_allowed_users
from services.ratelimit import rate_limiter
from utils.logger import log_user
from core.strategies import StrategyFactory
from services.queue import download_queue
from models.domain_models import DownloadTask, DownloadStatus
from utils.i18n import t
from utils.utils import is_user_allowed
from core.downloader import is_spotify_url

logger = logging.getLogger(__name__)


async def _execute_download_task(task: DownloadTask):
    """Executor function called by the queue worker."""
    user_id = task.user_id
    url = task.url

    strategy = StrategyFactory.get(task.download_type)
    if not strategy:
        task.status = DownloadStatus.FAILED
        task.error = f"No strategy found: {task.download_type}"
        return
    
    task.status = DownloadStatus.PROCESSING
   
    ctx = download_queue.get_task_context(task.task_id)
    if not ctx:
        task.status = DownloadStatus.FAILED
        task.error = "Task context missing"
        return
    
    processing_msg = ctx["processing_msg"]
    context = ctx["context"]
    query = ctx["query"]

    cancel_event = download_queue.get_cancel_event(task.task_id)

    strategy_future = asyncio.ensure_future(
        strategy.execute(query, url, processing_msg, context)
    )

    if cancel_event:
        cancel_future = asyncio.ensure_future(cancel_event.wait())
    else:
        cancel_future = asyncio.ensure_future(asyncio.sleep(float("inf")))

    try:
        done, pending = await asyncio.wait(
            [strategy_future, cancel_future],
            return_when=asyncio.FIRST_COMPLETED
        )
    except asyncio.CancelledError:
        strategy_future.cancel()
        cancel_future.cancel()
        task.status = DownloadStatus.CANCELLED
        task.error = "Queue stopped"
        try:
            await processing_msg.edit_text(t("bot_shutting_down", task.user_id))
        except Exception:
            pass
        return

    for f in pending:
        f.cancel()
        try:
            await f
        except (asyncio.CancelledError, Exception):
            pass

    if cancel_future in done:
        task.status = DownloadStatus.CANCELLED
        logger.info(f"Task {task.task_id} cancelled by user {user_id}")
        try:
            await processing_msg.edit_text(t("download_cancelled", task.user_id))
        except Exception:
            pass

    elif strategy_future in done:
        exc = strategy_future.exception()
        if exc:
            logger.error(f"Strategy execution failed: {type(exc).__name__}: {exc}")
            task.status = DownloadStatus.FAILED
            task.error = str(exc)
            try:
                await processing_msg.edit_text(t("download_failed", user_id))
            except Exception:
                pass
        else:
            task.status = DownloadStatus.COMPLETED
            logger.info(f"Task {task.task_id} completed successfully")


class DownloadFacade:
    """Facade for orchestrating the download process.
    
    Handles cross-cutting concerns:
    - Authorization check
    - Rate limiting check
    - Logging
    - Strategy selection and execution via queue
    """
    
    @staticmethod
    async def process_download_request(query, url: str, callback_data: str, context, processing_msg) -> tuple[bool, str | None]:
        """Process download request through the facade with queue support.
        
        Args:
            query: Telegram callback query
            url: URL to download
            callback_data: Callback data determining download type (e.g., "download_audio", "quality_1080")
            context: Telegram callback context
            processing_msg: Message to update with progress
            
        Returns:
            Tuple of (success: bool, error_msg_key: str | None)
            If success is False, error_msg_key contains the i18n key for the error message.
        """
        user = query.from_user
        user_id = user.id
        
        if not is_user_allowed(user_id):
            logger.warning(f"Unauthorized user {user_id} attempted to download {url}")
            return False, "unauthorized"
        
        can_download, rate_limit_msg = await rate_limiter.check_limit(user_id)
        if not can_download:
            logger.warning(f"User {user_id} blocked by rate limit: {rate_limit_msg}")
            return False, "rate_limit_exceeded"
        
        log_user(user, f"download_request:{callback_data}")
        
        strategy_key = DownloadFacade._map_callback_to_strategy(callback_data, url)
        if not strategy_key:
            logger.error(f"Unknown callback_data: {callback_data}")
            return False, "unknown_download_type"
        
        strategy = StrategyFactory.get(strategy_key)
        if not strategy:
            logger.error(f"No strategy found for key: {strategy_key}")
            return False, "unknown_download_type"
        
        try:
            task_id = str(uuid.uuid4())[:8]
            download_type = strategy_key
            
            task = DownloadTask(
                task_id=task_id,
                user_id=user_id,
                url=url,
                download_type=download_type,
            )
            
            telegram_ctx = {
                "query": query,
                "processing_msg": processing_msg,
                "context": context,
            }
            
            await download_queue.add_task(task, telegram_ctx)
            
            position = download_queue.get_queue_position(user_id)
            active = download_queue.get_active_count()
            
            if position > 0 or active >= download_queue.max_concurrent:
                await processing_msg.edit_text(
                    t("queued", user_id, position=position + 1)
                )
            else:
                await processing_msg.edit_text(t("downloading", user_id))
            
            return True, None
            
        except Exception as e:
            logger.error(f"Failed to queue task: {type(e).__name__}: {e}")
            import traceback
            logger.error(f"Traceback: {traceback.format_exc()}")
            return False, "download_failed"
    
    @staticmethod
    def _map_callback_to_strategy(callback_data: str, url: str | None = None) -> str | None:
        """Map callback_data to strategy key.
        
        Examples:
            "download_audio" -> "download_audio" (or "spotify" if Spotify URL)
            "download_video" -> "download_video" 
            "download_thumbnail" -> "download_thumbnail"
            "quality_1080" -> "video_1080"
            "quality_best" -> "video_best"
            "spotify" -> "spotify"
        """
        if callback_data == "download_audio" or is_spotify_url(url):
            return "spotify" if is_spotify_url(url) else "audio"
        if callback_data == "download_thumbnail":
            return "thumbnail"
        if callback_data == "download_subtitle":
            return "subtitle"
        if callback_data.startswith("quality_"):
            return "video"
        if callback_data == "download_video":
            return "video"
        return None

    @staticmethod
    async def enqueue_silent(user, url: str, download_type: str, status_msg, context):
        user_id = user.id

        strategy = StrategyFactory.get(download_type)
        if not strategy:
            await status_msg.edit_text(f"❌ Unsupported type: {download_type}")
            return

        task_id = str(uuid.uuid4())[:8]
        task = DownloadTask(
            task_id=task_id,
            user_id=user_id,
            url=url,
            download_type=download_type,
        )

        class _SilentQuery:
            from_user = user
            message = status_msg

            async def edit_message_text(self, text, **kwargs):
                try:
                    await status_msg.edit_text(text, **kwargs)
                except Exception:
                    pass

        task_ctx = {
            "query": _SilentQuery(),
            "processing_msg": status_msg,
            "context": context,
        }
        await download_queue.add_task(task, telegram_ctx=task_ctx)
        log_user(user, f"batch_enqueue:{download_type}")
