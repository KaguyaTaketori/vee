"""
modules/downloader/strategies/factory.py
─────────────────────────────────────────
Strategy factory with thread-safe instance cache.

Previous version stored _strategies as a bare class variable dict —
concurrent coroutines could race on writes, and tests could not
isolate their strategy registrations.

Changes
───────
* A threading.Lock guards all reads/writes to _strategies.
* register() and clear_cache() are properly locked.
* Tests should call StrategyFactory.clear_cache() in tearDown.
"""
from __future__ import annotations

import threading
from typing import Optional

from .base import TaskStrategy, DownloadStrategy
from .video import VideoStrategy, VideoFormatStrategy
from .audio import AudioStrategy
from .spotify import SpotifyStrategy
from .thumbnail import ThumbnailStrategy
from .subtitle import SubtitleStrategy


class StrategyFactory:
    """Factory for creating and caching TaskStrategy instances."""

    _lock: threading.Lock = threading.Lock()

    _strategies: dict[str, TaskStrategy] = {}

    _strategy_classes: dict[str, type[TaskStrategy]] = {
        "download_video":     VideoStrategy,
        "download_audio":     AudioStrategy,
        "download_thumbnail": ThumbnailStrategy,
        "spotify":            SpotifyStrategy,
        "subtitle":           SubtitleStrategy,
    }

    # ── video alias kept for backward-compat ──────────────────────────────
    _strategy_classes["video"] = VideoStrategy

    @classmethod
    def get(cls, key: str) -> Optional[TaskStrategy]:
        with cls._lock:
            if key not in cls._strategies:
                if key in cls._strategy_classes:
                    cls._strategies[key] = cls._strategy_classes[key]()
                elif key.startswith("video_"):
                    format_id = key.removeprefix("video_")
                    cls._strategies[key] = VideoFormatStrategy(format_id)
                else:
                    return None
            return cls._strategies[key]

    @classmethod
    def register(cls, key: str, strategy_class: type[TaskStrategy]) -> None:
        """Register a custom strategy class (also used for testing overrides)."""
        with cls._lock:
            cls._strategy_classes[key] = strategy_class
            cls._strategies.pop(key, None)   # force re-instantiation on next get()

    @classmethod
    def clear_cache(cls) -> None:
        """
        Discard all cached strategy instances.

        Call this in test tearDown to prevent cross-test contamination:

            def tearDown(self):
                StrategyFactory.clear_cache()
        """
        with cls._lock:
            cls._strategies.clear()
