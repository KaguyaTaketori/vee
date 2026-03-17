from __future__ import annotations

import logging

from .audio import AudioStrategy
from integrations.downloaders.spotify_client import download_spotify

logger = logging.getLogger(__name__)


class SpotifyStrategy(AudioStrategy):
    """AudioStrategy variant that routes to the Spotify downloader."""

    @property
    def task_type(self) -> str:
        return "spotify"

    async def _do_execute(self, url: str, progress_hook) -> tuple[str, dict]:
        return await download_spotify(url, progress_hook=progress_hook)
