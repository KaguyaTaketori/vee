from __future__ import annotations

import asyncio
import logging
import traceback
import uuid

from modules.downloader.strategies.factory import StrategyFactory

from modules.downloader.strategies.sender import BotSender

from models.domain_models import DownloadStatus, DownloadTask
from shared.services.container import services
from utils.i18n import t
from utils.logger import log_user
from modules.downloader.integrations.downloaders.ytdlp_client import is_spotify_url

logger = logging.getLogger(__name__)


async def _execute_download_task(task: DownloadTask) -> None:
    strategy = StrategyFactory.get(task.download_type)
    if not strategy:
        task.status = DownloadStatus.FAILED
        task.error  = f"No strategy found: {task.download_type}"
        return

    task.status = DownloadStatus.PROCESSING

    ctx = services.queue.get_task_context(task.task_id)
    if not ctx:
        task.status = DownloadStatus.FAILED
        task.error  = "Task context missing"
        return

    sender: BotSender = ctx["sender"]

    cancel_event    = services.queue.get_cancel_event(task.task_id)
    strategy_future = asyncio.ensure_future(strategy.execute(sender, task.url))
    cancel_future   = (
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
        task.error  = "Queue stopped"
        try:
            await sender.edit_status(t("bot_shutting_down", task.user_id))
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
        logger.info("Task %s cancelled by user %s", task.task_id, task.user_id)
        try:
            await sender.edit_status(t("download_cancelled", task.user_id))
        except Exception:
            pass

    elif strategy_future in done:
        exc = strategy_future.exception()
        if exc:
            logger.error("Strategy execution failed: %s: %s", type(exc).__name__, exc)
            task.status = DownloadStatus.FAILED
            task.error  = str(exc)
            try:
                if not getattr(exc, "_status_already_edited", False):
                    await sender.edit_status(
                        t("download_failed", task.user_id, error=str(exc))
                    )
            except Exception:
                pass
        else:
            task.status = DownloadStatus.COMPLETED
            logger.info("Task %s completed successfully", task.task_id)


class DownloadFacade:
    @staticmethod
    async def process_download_request(
        sender: BotSender,
        url: str,
        callback_data: str,
        context,
    ) -> tuple[bool, str | None]:
        user_id = sender.user_id

        log_user(
            user_id=sender.user_id,
            username=getattr(sender, "username", "N/A"),
            name=getattr(sender, "display_name", "N/A"),
            action=f"download_request:{callback_data}",
        )

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

            task_ctx = {"sender": sender, "context": context}
            await services.queue.add_task(task, task_ctx)

            position = services.queue.get_queue_position(user_id)
            active   = services.queue.get_active_count()

            if position > 0 or active >= services.queue.max_concurrent:
                await sender.edit_status(t("queued", user_id, position=position + 1))
            else:
                await sender.edit_status(t("downloading", user_id))

            return True, None

        except Exception as exc:
            logger.error("Failed to queue task: %s: %s", type(exc).__name__, exc)
            logger.error("Traceback: %s", traceback.format_exc())
            return False, "download_failed"


    @staticmethod
    def _map_callback_to_strategy(callback_data: str, url: str | None = None) -> str | None:
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
        sender: BotSender,
        url: str,
        download_type: str,
        context,
    ) -> None:
        user_id = sender.user_id

        strategy = StrategyFactory.get(download_type)
        if not strategy:
            await sender.edit_status(f"❌ Unsupported type: {download_type}")
            return

        task = DownloadTask(
            task_id=uuid.uuid4().hex[:16],
            user_id=user_id,
            url=url,
            download_type=download_type,
        )

        task_ctx = {"sender": sender, "context": context}
        await services.queue.add_task(task, task_ctx)
        log_user(
            user_id=sender.user_id,
            username="batch",
            name="batch",
            action=f"batch_enqueue:{download_type}",
        )
