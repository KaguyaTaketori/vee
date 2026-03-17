from __future__ import annotations

import logging

from .base import TaskStrategy
from .sender import BotSender
from integrations.downloaders import ytdlp_client
from utils.i18n import t

logger = logging.getLogger(__name__)


class VideoStrategy(TaskStrategy):

    @property
    def task_type(self) -> str:
        return "video"

    async def _send_from_file_id(
        self,
        sender: BotSender,
        file_id: str,
        caption: str | None,
    ) -> None:
        await sender.send_video(file_id, caption=caption)
        await sender.send_message(
            t("sent_via_file_id_no_reupload", sender.user_id)
        )

    async def _upload_new_file(
        self,
        sender: BotSender,
        filename: str,
        caption: str | None,
    ) -> str | None:
        logger.info("Opening video file for upload: %s", filename)
        with open(filename, "rb") as f:
            file_id = await sender.send_video(f, caption=caption)
        logger.info("Video upload completed")
        return file_id

    async def _do_execute(self, url: str, progress_hook) -> tuple[str, dict]:
        return await ytdlp_client.download_video(url, "best", progress_hook)


class VideoFormatStrategy(VideoStrategy):
    """VideoStrategy with an explicit format selection."""

    def __init__(self, format_id: str = "best") -> None:
        self._format_id = format_id
        super().__init__()

    @property
    def task_type(self) -> str:
        return f"video_{self._format_id}"

    async def _do_execute(self, url: str, progress_hook) -> tuple[str, dict]:
        return await ytdlp_client.download_video(url, self._format_id, progress_hook)
