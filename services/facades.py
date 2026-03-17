"""
services/facades.py
────────────────────
Download 业务门面层。

变更摘要
--------
1. **TelegramSender 不再在此处实例化**。
   Handler 层（message_parser / inline_actions）在收到 Telegram 消息时
   立刻将 update.message / query 包装为 TelegramSender，然后把这个
   实现了 BotSender 协议的对象传进来。

   旧代码：_execute_download_task() 从 task_ctx["query"] / ["processing_msg"]
           硬编码地调用 TelegramSender(query, processing_msg)。
   新代码：_execute_download_task() 直接从 task_ctx["sender"] 取出 BotSender，
           不知道也不关心它是 TelegramSender 还是未来的 DiscordSender。

2. **process_download_request 不再做 Auth / RateLimit 检查**。
   这两个关卡已由 Handler 层的 ``default_pipeline.run()`` 统一处理。
   Facade 只负责「策略映射 → 入队 → 状态反馈」纯业务逻辑。

3. **enqueue_silent 签名变更**：
   旧：enqueue_silent(user, url, download_type, status_msg, context)
   新：enqueue_silent(sender: BotSender, url, download_type, context)
   Sender 由 Handler 层构造并传入，Facade 不再持有任何 Telegram 对象。

4. **task_ctx 结构简化**：
   只保留 {"sender": BotSender}，消除了 "query" / "processing_msg" 两个
   Telegram 专属字段。
"""

from __future__ import annotations

import asyncio
import logging
import traceback
import uuid

from integrations.strategies.factory import StrategyFactory

# BotSender 是协议类型，仅用于类型注解；不引用任何 Telegram 实现
from integrations.strategies.sender import BotSender

from models.domain_models import DownloadStatus, DownloadTask
from services.container import services
from utils.i18n import t
from utils.logger import log_user
from integrations.downloaders.ytdlp_client import is_spotify_url

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Queue executor（内部，由 TaskManager worker 调用）
# ---------------------------------------------------------------------------

async def _execute_download_task(task: DownloadTask) -> None:
    """队列 worker 的执行入口。

    从 task_ctx 取出 BotSender，委派给对应 Strategy 执行。
    Strategy 只调用 sender.send_*/edit_status() 等平台无关接口。
    """
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

    # ── Sender 直接从 ctx 取出，不再硬编码 TelegramSender ─────────────────
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
                await sender.edit_status(t("download_failed", task.user_id))
            except Exception:
                pass
        else:
            task.status = DownloadStatus.COMPLETED
            logger.info("Task %s completed successfully", task.task_id)


# ---------------------------------------------------------------------------
# Public facade
# ---------------------------------------------------------------------------

class DownloadFacade:
    """统一的下载业务门面。

    Handler 层负责：
        - 鉴权 / 限流（通过 Pipeline）
        - 包装 TelegramSender

    Facade 负责：
        - 策略映射
        - 任务入队
        - 队列状态回显

    两层之间通过 ``BotSender`` 协议解耦，Facade 不知道也不关心
    底层是 Telegram、Discord 还是测试用的 MockSender。
    """

    @staticmethod
    async def process_download_request(
        sender: BotSender,
        url: str,
        callback_data: str,
        context,
    ) -> tuple[bool, str | None]:
        """验证、入队、反馈。

        Parameters
        ----------
        sender:
            由 Handler 层构造好的 BotSender（已绑定 processing_msg）。
        url:
            待下载的 URL。
        callback_data:
            用户点击的 InlineKeyboard 按钮数据，用于映射 Strategy。
        context:
            python-telegram-bot 的 CallbackContext（仅用于日志）。

        Returns
        -------
        (True, None)        — 成功入队
        (False, error_key)  — 失败，附带 i18n key
        """
        user_id = sender.user_id

        # Auth / RateLimit 已在 Handler 层的 Pipeline 中完成，此处不重复。
        log_user_obj = getattr(sender, "_query", None)
        if log_user_obj:
            log_user(log_user_obj.from_user, f"download_request:{callback_data}")

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

            # ── 只存 sender，Queue / Worker 不再需要知道 Telegram 的存在 ──
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

    # ------------------------------------------------------------------ utils

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
        """批量/静默入队（无格式选择交互）。

        Parameters
        ----------
        sender:
            由 Handler 层构造、绑定了对应 status_msg 的 BotSender。
        url:
            待下载的 URL。
        download_type:
            策略 key，如 "download_audio" / "spotify"。
        context:
            python-telegram-bot CallbackContext（仅供日志使用）。
        """
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
            type("_U", (), {"id": user_id, "username": "batch"})(),
            f"batch_enqueue:{download_type}",
        )
