import time

class TTLCache:

    _MISSING = object()

    def __init__(self, ttl: float):
        self._ttl = ttl
        self._data = TTLCache._MISSING
        self._ts = 0.0

    def get(self):
        if self._data is TTLCache._MISSING:
            return TTLCache._MISSING
        if time.time() - self._ts >= self._ttl:
            return TTLCache._MISSING
        return self._data

    def set(self, data):
        self._data = data
        self._ts = time.time()

    def invalidate(self):
        self._data = TTLCache._MISSING
        self._ts = 0.0

    @property
    def is_valid(self) -> bool:
        return (
            self._data is not TTLCache._MISSING
            and time.time() - self._ts < self._ttl
        )
