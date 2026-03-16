import os
import json
import time
import threading
import asyncio
from filelock import FileLock
from concurrent.futures import ThreadPoolExecutor
from typing import Optional
from utils.utils import get_running_loop as _get_running_loop


_executor = ThreadPoolExecutor(max_workers=4)


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
        return self._sync_load()
    
    async def load_async(self) -> dict:
        loop = _get_running_loop()
        if loop:
            return await loop.run_in_executor(_executor, self._sync_load)
        return self._sync_load()
    
    def _sync_load(self) -> dict:
        with self._cache_lock:
            now = time.time()
            if self._cache["data"] is None or now - self._cache["time"] > self.cache_ttl:
                self._cache["data"] = self._load_unsafe()
                self._cache["time"] = now
            return self._cache["data"].copy()
    
    def _save_unsafe(self, data: dict):
        lock = FileLock(self.lock_file, timeout=10)
        with lock:
            with open(self.data_file, "w") as f:
                json.dump(data, f, indent=2)
    
    def persist(self):
        self._sync_persist()
    
    async def persist_async(self):
        loop = _get_running_loop()
        if loop:
            await loop.run_in_executor(_executor, self._sync_persist)
        else:
            self._sync_persist()
    
    def _sync_persist(self):
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
            if time.time() - self._last_persist >= self._persist_interval:
                self._save_unsafe(data)
                self._cache["dirty"] = False
                self._last_persist = time.time()
    
