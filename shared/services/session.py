from __future__ import annotations

import secrets
import time
from dataclasses import dataclass, field
from typing import Optional, Any


@dataclass
class UserSession:
    url: str
    user_id: int
    sender: Any = None
    created_at: float = field(default_factory=time.time)

    _store: dict[str, "UserSession"] = {}  # class-level store
    _TTL: float = 600.0  # 10 minutes

    @classmethod
    def store(cls, *, url: str, user_id: int) -> str:
        """Persist a URL for later retrieval; return the session key."""
        cls._evict_expired()
        key = secrets.token_urlsafe(12)
        cls._store[key] = cls(url=url, user_id=user_id)
        return key

    @classmethod
    def load(cls, key: str) -> Optional["UserSession"]:
        """Retrieve and validate a session by key."""
        session = cls._store.get(key)
        if session is None:
            return None
        if time.time() - session.created_at > cls._TTL:
            del cls._store[key]
            return None
        return session

    @classmethod
    def _evict_expired(cls) -> None:
        now = time.time()
        expired = [k for k, v in cls._store.items()
                   if now - v.created_at > cls._TTL]
        for k in expired:
            del cls._store[k]
