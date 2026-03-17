from __future__ import annotations

import os
import logging

from .base import TaskStrategy
from .sender import BotSender
from integrations.downloaders import ytdlp_client
from utils.i18n import t

logger = logging.getLogger(__name__)


class AudioStrategy(TaskStrategy):

    @property
    def task_type(self) -> str:
        return "audio"

    def _get_caption(self, title: str, emoji: str = "🎵") -> str | None:
        return f"{emoji} {title}" if title else None

    async def _send_from_file_id(
        self,
        sender: BotSender,
        file_id: str,
        caption: str | None,
    ) -> None:
        await sender.send_audio(file_id, title=caption)
        await sender.send_message(
            t("sent_via_file_id_no_reupload", sender.user_id)
        )

    async def _upload_new_file(
        self,
        sender: BotSender,
        filename: str,
        caption: str | None,
    ) -> str | None:
        title = os.path.splitext(os.path.basename(filename))[0]
        logger.info("Opening audio file for upload: %s", filename)
        with open(filename, "rb") as f:
            file_id = await sender.send_audio(f, title=title)
        logger.info("Audio upload completed")
        return file_id

    async def _do_execute(self, url: str, progress_hook) -> tuple[str, dict]:
        return await ytdlp_client.download_audio(url, progress_hook)
