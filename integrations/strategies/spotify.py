import logging

from .audio import AudioStrategy
from integrations.downloaders.spotify_client import download_spotify

logger = logging.getLogger(__name__)


class SpotifyStrategy(AudioStrategy):
    @property
    def download_type(self) -> str:
        return "spotify"
    
    async def _do_download(self, url: str, progress_hook) -> tuple[str, dict]:
        return await download_spotify(url, progress_hook=progress_hook)
