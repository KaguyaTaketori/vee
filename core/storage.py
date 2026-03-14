import os
import json
import time
import fcntl
import threading
from typing import Optional


class JsonStore:
    def __init__(self, data_file: str, lock_file: str, cache_ttl: float = 5.0):
        self.data_file = data_file
        self.lock_file = lock_file
        self.cache_ttl = cache_ttl
        
        self._cache = {"data": None, "dirty": False, "time": 0}
        self._cache_lock = threading.Lock()
        self._persist_interval = 30
        self._last_persist = 0
    
    def _load_unsafe(self) -> dict:
        if not os.path.exists(self.data_file):
            return {}
        try:
            with open(self.data_file, "r") as f:
                return json.load(f)
        except Exception:
            return {}
    
    def load(self) -> dict:
        with self._cache_lock:
            now = time.time()
            if self._cache["data"] is None or now - self._cache["time"] > self.cache_ttl:
                self._cache["data"] = self._load_unsafe()
                self._cache["time"] = now
            return self._cache["data"].copy()
    
    def _save_unsafe(self, data: dict):
        with open(self.lock_file, "w") as lock:
            fcntl.flock(lock.fileno(), fcntl.LOCK_EX)
            try:
                with open(self.data_file, "w") as f:
                    json.dump(data, f, indent=2)
            finally:
                fcntl.flock(lock.fileno(), fcntl.LOCK_UN)
    
    def persist(self):
        with self._cache_lock:
            if self._cache["dirty"]:
                self._save_unsafe(self._cache["data"])
                self._cache["dirty"] = False
                self._last_persist = time.time()
    
    def mark_dirty(self, data: dict):
        with self._cache_lock:
            self._cache["data"] = data
            self._cache["dirty"] = True
            self._cache["time"] = time.time()
    
    def force_persist(self):
        self.persist()
