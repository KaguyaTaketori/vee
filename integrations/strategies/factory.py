from __future__ import annotations

from .base import TaskStrategy, DownloadStrategy  # DownloadStrategy 为别名，向后兼容
from .video import VideoStrategy, VideoFormatStrategy
from .audio import AudioStrategy
from .spotify import SpotifyStrategy
from .thumbnail import ThumbnailStrategy
from .subtitle import SubtitleStrategy


class StrategyFactory:
    """Factory for creating task strategies with lazy loading."""

    _strategies: dict[str, TaskStrategy] = {}
    _strategy_classes: dict[str, type[TaskStrategy]] = {
        "download_video": VideoStrategy,
        "download_audio": AudioStrategy,
        "download_thumbnail": ThumbnailStrategy,
        "spotify": SpotifyStrategy,
        "subtitle": SubtitleStrategy,
    }

    @classmethod
    def get(cls, key: str) -> TaskStrategy | None:
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
    def register(cls, key: str, strategy_class: type[TaskStrategy]) -> None:
        cls._strategy_classes[key] = strategy_class
        cls._strategies.pop(key, None)  # 清除旧缓存，下次 get() 重新实例化

    @classmethod
    def clear_cache(cls) -> None:
        """Clear cached strategy instances (useful for testing)."""
        cls._strategies.clear()
