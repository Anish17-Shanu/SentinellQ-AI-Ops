"""In-memory token-bucket rate limiting keyed by API key or client IP."""

from __future__ import annotations

import math
import threading
import time
from dataclasses import dataclass


@dataclass(frozen=True)
class RateLimitResult:
    allowed: bool
    limit: int
    remaining: int
    retry_after: int


class TokenBucketLimiter:
    def __init__(self, requests_per_minute: int, burst: int | None = None, clock=time.monotonic) -> None:
        self.rpm = max(0, requests_per_minute)
        self.capacity = float(burst if burst is not None else max(self.rpm, 1))
        self.refill_per_second = self.rpm / 60.0
        self.clock = clock
        self._lock = threading.Lock()
        self._buckets: dict[str, tuple[float, float]] = {}

    @property
    def enabled(self) -> bool:
        return self.rpm > 0

    def check(self, key: str) -> RateLimitResult:
        if not self.enabled:
            return RateLimitResult(True, 0, 0, 0)
        now = self.clock()
        with self._lock:
            tokens, updated = self._buckets.get(key, (self.capacity, now))
            tokens = min(self.capacity, tokens + (now - updated) * self.refill_per_second)
            if tokens >= 1.0:
                tokens -= 1.0
                self._buckets[key] = (tokens, now)
                return RateLimitResult(True, self.rpm, int(tokens), 0)
            self._buckets[key] = (tokens, now)
            if len(self._buckets) > 10000:
                self._prune(now)
            retry_after = max(1, math.ceil((1.0 - tokens) / self.refill_per_second)) if self.refill_per_second else 60
            return RateLimitResult(False, self.rpm, 0, retry_after)

    def _prune(self, now: float) -> None:
        stale = [key for key, (_, updated) in self._buckets.items() if now - updated > 600]
        for key in stale:
            self._buckets.pop(key, None)
