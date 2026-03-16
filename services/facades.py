from __future__ import annotations

import asyncio
import logging
import traceback
import uuid

from integrations.strategies.factory import StrategyFactory
from integrations.strategies.sender import TelegramSender
from models.domain_models import DownloadStatus, DownloadTask
from services.query_adapters import SilentMessageQuery
from services.queue import download_queue
from services.ratelimit import rate_limiter
from utils.i18n import t
from utils.logger import log_user
from utils.utils import is_user_allowed
from integrations.downloaders.ytdlp_client import is_spotify_url

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Queue executor
# ---------------------------------------------------------------------------

async def _execute_download_task(task: DownloadTask) -> None:
    """Called by the queue worker for each task.

    Builds a TelegramSender from the stored task context, then delegates
    execution to the appropriate strategy.  The strategy receives only a
    BotSender — it never touches Telegram objects directly.
    """
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
    query = ctx["query"]

    # ── Build the platform sender here, not inside the strategy ──────────────
    sender = TelegramSender(query, processing_msg)

    cancel_event = download_queue.get_cancel_event(task.task_id)

    strategy_future = asyncio.ensure_future(strategy.execute(sender, url))
    cancel_future = (
        asyncio.ensure_future(cancel_event.wait())
        if cancel_event
        else asyncio.ensure_future(asyncio.sleep(float("inf")))
    )

    try:
        done, pending = await asyncio.wait(
            [strategy_future, cancel_future],
            return_when=asyncio.FIRST_COMPLETED,
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
        logger.info("Task %s cancelled by user %s", task.task_id, user_id)
        try:
            await processing_msg.edit_text(t("download_cancelled", task.user_id))
        except Exception:
            pass

    elif strategy_future in done:
        exc = strategy_future.exception()
        if exc:
            logger.error(
                "Strategy execution failed: %s: %s", type(exc).__name__, exc
            )
            task.status = DownloadStatus.FAILED
            task.error = str(exc)
            try:
                await processing_msg.edit_text(t("download_failed", user_id))
            except Exception:
                pass
        else:
            task.status = DownloadStatus.COMPLETED
            logger.info("Task %s completed successfully", task.task_id)


# ---------------------------------------------------------------------------
# Public facade
# ---------------------------------------------------------------------------

class DownloadFacade:
    """Orchestrates the download flow with auth, rate-limiting, and queuing.

    The handler layer calls process_download_request() or enqueue_silent().
    Both store the Telegram objects in the queue context dict; the actual
    TelegramSender is created in _execute_download_task() above, so
    strategies are never aware of which platform they are running on.
    """

    @staticmethod
    async def process_download_request(
        query,
        url: str,
        callback_data: str,
        context,
        processing_msg,
    ) -> tuple[bool, str | None]:
        """Validate, queue, and acknowledge a download request.

        Returns (True, None) on success, or (False, i18n_error_key) on failure.
        """
        user = query.from_user
        user_id = user.id

        if not is_user_allowed(user_id):
            logger.warning("Unauthorized user %s attempted to download %s", user_id, url)
            return False, "unauthorized"

        can_download, rate_limit_msg = await rate_limiter.check_limit(user_id)
        if not can_download:
            logger.warning("User %s blocked by rate limit: %s", user_id, rate_limit_msg)
            return False, "rate_limit_exceeded"

        log_user(user, f"download_request:{callback_data}")

        strategy_key = DownloadFacade._map_callback_to_strategy(callback_data, url)
        if not strategy_key:
            logger.error("Unknown callback_data: %s", callback_data)
            return False, "unknown_download_type"

        strategy = StrategyFactory.get(strategy_key)
        if not strategy:
            logger.error("No strategy found for key: %s", strategy_key)
            return False, "unknown_download_type"

        try:
            task = DownloadTask(
                task_id=uuid.uuid4().hex[:16],
                user_id=user_id,
                url=url,
                download_type=strategy_key,
            )

            # Store platform-specific objects in the queue context.
            # _execute_download_task() will wrap them in a TelegramSender.
            task_ctx = {
                "query": query,
                "processing_msg": processing_msg,
                "context": context,
            }
            await download_queue.add_task(task, task_ctx)

            position = download_queue.get_queue_position(user_id)
            active = download_queue.get_active_count()

            if position > 0 or active >= download_queue.max_concurrent:
                await processing_msg.edit_text(
                    t("queued", user_id, position=position + 1)
                )
            else:
                await processing_msg.edit_text(t("downloading", user_id))

            return True, None

        except Exception as exc:
            logger.error("Failed to queue task: %s: %s", type(exc).__name__, exc)
            logger.error("Traceback: %s", traceback.format_exc())
            return False, "download_failed"

    @staticmethod
    def _map_callback_to_strategy(
        callback_data: str, url: str | None = None
    ) -> str | None:
        if callback_data == "download_audio":
            return "spotify" if (url and is_spotify_url(url)) else "download_audio"
        if url and is_spotify_url(url):
            return "spotify"
        if callback_data == "download_thumbnail":
            return "thumbnail"
        if callback_data == "download_subtitle":
            return "subtitle"
        if callback_data.startswith("quality_"):
            return f"video_{callback_data.removeprefix('quality_')}"
        if callback_data == "download_video":
            return "download_video"
        return None

    @staticmethod
    async def enqueue_silent(
        user, url: str, download_type: str, status_msg, context
    ) -> None:
        """Queue a download without interactive format selection (batch mode)."""
        user_id = user.id

        strategy = StrategyFactory.get(download_type)
        if not strategy:
            await status_msg.edit_text(f"❌ Unsupported type: {download_type}")
            return

        task = DownloadTask(
            task_id=uuid.uuid4().hex[:16],
            user_id=user_id,
            url=url,
            download_type=download_type,
        )

        # SilentMessageQuery satisfies the query interface expected by
        # TelegramSender: it exposes .from_user and .message.
        query = SilentMessageQuery(user, status_msg)
        task_ctx = {
            "query": query,
            "processing_msg": status_msg,
            "context": context,
        }
        await download_queue.add_task(task, task_ctx)
        log_user(user, f"batch_enqueue:{download_type}")
