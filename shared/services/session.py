"""
shared/services/session.py
──────────────────────────
Platform-agnostic in-memory URL session store.

Replaces the old PTB user_data-based approach (which depended on
context.user_data — a Telegram-specific concept) with a simple
dict keyed by a short random token.

Usage
-----
# Store a URL and get a session key (e.g. when user sends a link)
session_key = UserSession.store(url="https://youtu.be/xxx", user_id=123)

# Later, when the user taps an inline button:
session = UserSession.load(session_key)
if session is None:
    await ctx.send(t("session_expired", user_id))
    return

await DownloadFacade.enqueue(session, download_type)
"""
from __future__ import annotations

import secrets
import time
from dataclasses import dataclass, field
from typing import Any, ClassVar, Optional


@dataclass
class UserSession:
    url: str
    user_id: int
    sender: Any = None
    created_at: float = field(default_factory=time.time)

    # ── class-level storage ────────────────────────────────────────────────
    _store: ClassVar[dict[str, "UserSession"]] = {}
    _TTL:   ClassVar[float] = 600.0   # 10 minutes

    # ── public API ─────────────────────────────────────────────────────────

    @classmethod
    def store(cls, *, url: str, user_id: int) -> str:
        """Persist a URL for later retrieval; return the opaque session key."""
        cls._evict_expired()
        key = secrets.token_urlsafe(12)
        cls._store[key] = cls(url=url, user_id=user_id)
        return key

    @classmethod
    def load(cls, key: str) -> Optional["UserSession"]:
        """Retrieve a session by key, or *None* if missing/expired."""
        session = cls._store.get(key)
        if session is None:
            return None
        if time.time() - session.created_at > cls._TTL:
            del cls._store[key]
            return None
        return session

    @classmethod
    def delete(cls, key: str) -> None:
        """Explicitly remove a session (e.g. after download starts)."""
        cls._store.pop(key, None)

    @classmethod
    def _evict_expired(cls) -> None:
        """Remove all sessions whose TTL has elapsed."""
        now = time.time()
        expired = [k for k, v in cls._store.items()
                   if now - v.created_at > cls._TTL]
        for k in expired:
            del cls._store[k]
