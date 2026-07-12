"""全局主机级令牌桶 — 替代固定 sleep，动态限流东财/akshare。"""
from __future__ import annotations

import threading
import time

import config


class TokenBucket:
    def __init__(self, rate_per_sec: float, burst: int):
        self.rate = max(rate_per_sec, 0.1)
        self.burst = max(burst, 1)
        self._tokens = float(burst)
        self._last = time.monotonic()
        self._lock = threading.Lock()

    def acquire(self) -> None:
        with self._lock:
            now = time.monotonic()
            elapsed = now - self._last
            self._last = now
            self._tokens = min(self.burst, self._tokens + elapsed * self.rate)
            if self._tokens >= 1.0:
                self._tokens -= 1.0
                return
            wait = (1.0 - self._tokens) / self.rate
        if wait > 0:
            time.sleep(wait)
        with self._lock:
            self._tokens = max(0.0, self._tokens - 1.0)


_buckets: dict[str, TokenBucket] = {}
_buckets_lock = threading.Lock()


def wait_host(host: str = "eastmoney") -> None:
    """在发起东财/akshare 请求前调用。"""
    with _buckets_lock:
        bucket = _buckets.get(host)
        if bucket is None:
            if host == "eastmoney":
                bucket = TokenBucket(config.EASTMONEY_RATE_PER_SEC, config.EASTMONEY_RATE_BURST)
            else:
                bucket = TokenBucket(
                    max(config.EASTMONEY_RATE_PER_SEC, config.AKSHARE_SLEEP_MS / 1000.0),
                    config.EASTMONEY_RATE_BURST,
                )
            _buckets[host] = bucket
    bucket.acquire()
