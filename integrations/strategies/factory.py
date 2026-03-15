from .base import DownloadStrategy
from .video import VideoStrategy, VideoFormatStrategy
from .audio import AudioStrategy
from .spotify import SpotifyStrategy
from .thumbnail import ThumbnailStrategy
from .subtitle import SubtitleStrategy


class StrategyFactory:
    """Factory for creating download strategies with lazy loading."""
    
    _strategies: dict[str, DownloadStrategy] = {}
    _strategy_classes = {
        "download_video": VideoStrategy,
        "download_audio": AudioStrategy,
        "download_thumbnail": ThumbnailStrategy,
        "spotify": SpotifyStrategy,
        "subtitle": SubtitleStrategy,
    }
    
    @classmethod
    def get(cls, key: str) -> DownloadStrategy | None:
        if key not in cls._strategies:
            if key in cls._strategy_classes:
                cls._strategies[key] = cls._strategy_classes[key]()
            elif key.startswith("video_"):
                format_id = key.replace("video_", "")
                cls._strategies[key] = VideoFormatStrategy(format_id)
            else:
                return None
        return cls._strategies.get(key)
    
    @classmethod
    def register(cls, key: str, strategy_class: type[DownloadStrategy]):
        cls._strategy_classes[key] = strategy_class
        if key in cls._strategies:
            del cls._strategies[key]
    
    @classmethod
    def clear_cache(cls):
        """Clear cached strategy instances (useful for testing)."""
        cls._strategies.clear()
