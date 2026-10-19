"""Token-bucket rate limiter keyed by capability id."""

import time
from typing import Callable


class RateLimiter:
    def __init__(self, rate_per_second: float, burst: int,
                 overrides: dict[str, tuple[float, int]] | None = None,
                 clock: Callable[[], float] = time.monotonic) -> None:
        if rate_per_second < 0 or burst < 1:
            raise ValueError("rate must be >= 0 and burst >= 1")
        self._default = (rate_per_second, burst)
        self._overrides = dict(overrides or {})
        self._clock = clock
        self._buckets: dict[str, tuple[float, float]] = {}

    def allow(self, key: str) -> bool:
        rate, burst = self._overrides.get(key, self._default)
        now = self._clock()
        tokens, last = self._buckets.get(key, (float(burst), now))
        tokens = min(float(burst), tokens + (now - last) * rate)
        if tokens < 1.0:
            self._buckets[key] = (tokens, now)
            return False
        self._buckets[key] = (tokens - 1.0, now)
        return True
