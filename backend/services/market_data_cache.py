"""市场行情页 — 统一内存缓存（短 TTL / 交易日键）"""
from __future__ import annotations

import time
from typing import Any, Callable, TypeVar

T = TypeVar("T")

# 盘中实时类
TTL_LIMIT_STATS_SEC = 45
TTL_INDEX_REALTIME_SEC = 30
TTL_BOARDS_SEC = 30

# 日级 / 算力类
TTL_SECTOR_ROTATION_SEC = 3600
TTL_MACRO_INDICATORS_SEC = 600
TTL_THS_HOTSPOTS_SEC = 600
TTL_LHB_MARKET_DB_SEC = 3600

# 指数中间地带
TTL_INDEX_SNAPSHOT_INTRADAY_SEC = 90
TTL_INDEX_SNAPSHOT_CLOSED_SEC = 3600
TTL_INDEX_KLINE_INTRADAY_SEC = 120
TTL_INDEX_KLINE_CLOSED_SEC = 3600
TTL_INDEX_KLINE_WEEKLY_SEC = 7200


class MemoryCache:
    """键值内存缓存，支持按 key 或全局 TTL。"""

    def __init__(self) -> None:
        self._entries: dict[str, dict[str, Any]] = {}

    def get(self, key: str) -> Any | None:
        hit = self._entries.get(key)
        if not hit:
            return None
        if time.time() > hit["expires"]:
            self._entries.pop(key, None)
            return None
        return hit["data"]

    def set(self, key: str, data: Any, ttl_sec: float) -> None:
        self._entries[key] = {"data": data, "expires": time.time() + ttl_sec}

    def invalidate(self, key: str | None = None) -> None:
        if key is None:
            self._entries.clear()
        else:
            self._entries.pop(key, None)

    def get_or_set(
        self,
        key: str,
        ttl_sec: float,
        factory: Callable[[], T],
        *,
        force: bool = False,
    ) -> T:
        if not force:
            cached = self.get(key)
            if cached is not None:
                return cached
        data = factory()
        self.set(key, data, ttl_sec)
        return data


_limit_stats_cache = MemoryCache()
_sector_rotation_cache = MemoryCache()
_macro_cache = MemoryCache()
_ths_hotspots_cache = MemoryCache()
_lhb_response_cache = MemoryCache()


def cached_limit_stats(force: bool, factory: Callable[[], dict], ttl_sec: float | None = None) -> dict:
    return _limit_stats_cache.get_or_set(
        "limit-stats", ttl_sec if ttl_sec is not None else TTL_LIMIT_STATS_SEC, factory, force=force
    )


def cached_sector_rotation(cache_key: str, force: bool, factory: Callable[[], dict]) -> dict:
    return _sector_rotation_cache.get_or_set(cache_key, TTL_SECTOR_ROTATION_SEC, factory, force=force)


def cached_macro_indicators(force: bool, factory: Callable[[], dict]) -> dict:
    return _macro_cache.get_or_set("macro-indicators", TTL_MACRO_INDICATORS_SEC, factory, force=force)


def cached_ths_hotspots(cache_key: str, force: bool, factory: Callable[[], dict]) -> dict:
    return _ths_hotspots_cache.get_or_set(cache_key, TTL_THS_HOTSPOTS_SEC, factory, force=force)


def cached_lhb_daily(cache_key: str, force: bool, factory: Callable[[], dict]) -> dict:
    return _lhb_response_cache.get_or_set(cache_key, TTL_LHB_MARKET_DB_SEC, factory, force=force)


def invalidate_market_page_caches() -> None:
    """sync-quotes 后清盘中短缓存，保留日级键。"""
    _limit_stats_cache.invalidate()
    _lhb_response_cache.invalidate()
