"""市场行情缓存层"""
from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from services.market_data_cache import MemoryCache, cached_limit_stats


def test_memory_cache_ttl():
    c = MemoryCache()
    calls = {"n": 0}

    def factory():
        calls["n"] += 1
        return {"v": calls["n"]}

    assert cached_limit_stats(False, factory)["v"] == 1
    assert cached_limit_stats(False, factory)["v"] == 1
    assert calls["n"] == 1
    assert cached_limit_stats(True, factory)["v"] == 2


def test_lhb_load_from_db(monkeypatch):
    from services.lhb_fetch import fetch_lhb_daily

    sample = [
        {
            "date": "2026-06-09",
            "code": "600000",
            "name": "浦发银行",
            "net_buy": 1000.0,
            "change_pct": 5.0,
        }
    ]
    monkeypatch.setattr(
        "services.lhb_sync.load_lhb_market_from_db",
        lambda d: sample if d == "2026-06-09" else [],
    )
    monkeypatch.setattr("services.lhb_sync.save_lhb_market_to_db", lambda *a, **k: 0)
    monkeypatch.setattr(
        "services.market_data_cache._lhb_response_cache",
        __import__("services.market_data_cache", fromlist=["MemoryCache"]).MemoryCache(),
    )

    monkeypatch.setattr(
        "services.lhb_fetch._fetch_lhb_daily_live",
        lambda d: (d, [], "", []),
    )
    out = fetch_lhb_daily("2026-06-09", limit=10, force=False)
    assert out["count"] == 1
    assert out["items"][0]["code"] == "600000"
    assert out["source"] == "db"


def test_sector_rotation_cache():
    import time as _time
    from services import sector_rotation as sr
    from services.sector_rotation import clear_sector_rotation_cache, compute_sector_rotation_signals

    clear_sector_rotation_cache()
    payload = {
        "date": "2026-06-09",
        "as_of_trade_date": "2026-06-09",
        "base_trade_date": "2026-06-02",
        "all": [],
        "add": [],
        "reduce": [],
    }
    sr._rotation_cache_key = "2026-06-09:5"
    sr._rotation_cache_data = {**payload, "_cached_at": _time.time(), "cached": True}

    with patch("services.sector_rotation.latest_trading_date", return_value="2026-06-09"):
        hit = compute_sector_rotation_signals(window_days=5, force=False)
    assert hit.get("cached") is True
    assert hit["as_of_trade_date"] == "2026-06-09"
    clear_sector_rotation_cache()
