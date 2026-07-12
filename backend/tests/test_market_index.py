"""大盘指数摘要"""
from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from services.market_index import (
    format_market_index_text,
    market_hash_part,
    resolve_index_code,
    snapshot_to_api_payload,
    fetch_index_realtime_quotes,
)


def test_format_market_index_text():
    snap = {
        "上证指数": {
            "code": "sh000001",
            "daily": {
                "close": 3200.5,
                "ma5": 3180.0,
                "ma20": 3150.0,
                "macd_bar": -12.3,
                "rsi14": 42.5,
                "boll_upper": 3250,
                "boll_mid": 3180,
                "boll_lower": 3100,
                "change_5d_pct": -1.2,
                "change_20d_pct": 2.5,
            },
            "weekly": {"close": 3190, "rsi14": 45, "macd_bar": -5},
        }
    }
    text = format_market_index_text(snap)
    assert "上证指数" in text
    assert "3200.5" in text
    assert "5日涨跌" in text
    assert market_hash_part(snap)


def test_resolve_index_code():
    assert resolve_index_code("上证指数") == ("sh000001", "上证指数")
    assert resolve_index_code("sh000300") == ("sh000300", "沪深300")
    assert resolve_index_code("399006") == ("sz399006", "创业板指")
    assert resolve_index_code("深证成指") == ("sz399001", "深证成指")
    assert resolve_index_code("399001") == ("sz399001", "深证成指")
    assert resolve_index_code("unknown") is None


def test_snapshot_to_api_payload():
    snap = {
        "上证指数": {
            "code": "sh000001",
            "daily": {
                "close": 3200.5,
                "ma5": 3190.0,
                "ma20": 3150.0,
                "macd_bar": -12.3,
                "rsi14": 55.0,
                "change_5d_pct": 2.1,
                "change_20d_pct": 3.0,
            },
            "weekly": {"rsi14": 52},
        },
        "沪深300": {
            "code": "sh000300",
            "daily": {
                "close": 3800.0,
                "ma5": 3780.0,
                "ma20": 3750.0,
                "rsi14": 54.0,
                "change_5d_pct": 1.8,
                "change_20d_pct": 2.0,
            },
            "weekly": {},
        },
    }
    payload = snapshot_to_api_payload(snap)
    assert payload["available"] is True
    assert len(payload["indices"]) == 2
    assert payload["environment"] in ("偏多", "偏空", "震荡")
    assert payload["indices"][0]["name"] == "上证指数"
    assert payload["indices"][0]["signal"] in ("偏多", "偏空", "震荡")
    assert "calendar_date" in payload


def test_snapshot_realtime_overlay(monkeypatch):
    snap = {
        "上证指数": {
            "code": "sh000001",
            "daily": {
                "close": 3200.5,
                "trade_date": "2026-06-09",
                "ma5": 3190.0,
                "ma20": 3150.0,
                "macd_bar": -12.3,
                "rsi14": 55.0,
                "change_5d_pct": 2.1,
                "change_20d_pct": 3.0,
            },
            "weekly": {"rsi14": 52},
        },
    }

    monkeypatch.setattr(
        "services.market_index.fetch_index_realtime_quotes",
        lambda ash_codes=None: {
            "sh000001": {"price": 3250.0, "change_amt": 49.5, "change_pct": 1.55},
        },
    )
    monkeypatch.setattr("services.market_index._calendar_today", lambda: "2026-06-10")
    monkeypatch.setattr("services.market_index._expected_trade_date", lambda: "2026-06-09")

    payload = snapshot_to_api_payload(snap)
    row = payload["indices"][0]
    assert row["last"] == 3250.0
    assert row["change_pct_today"] == 1.55
    assert row["change_1d_pct"] == 1.55
    assert payload["quote_mode"] == "realtime"
    assert payload["stale"] is False


def test_snapshot_1d_from_daily_when_no_realtime(monkeypatch):
    snap = {
        "上证指数": {
            "code": "sh000001",
            "daily": {
                "close": 4010.03,
                "trade_date": "2026-06-09",
                "change_1d_pct": 1.28,
                "change_5d_pct": -1.6,
                "ma5": 3190.0,
                "ma20": 3150.0,
                "rsi14": 41.0,
            },
            "weekly": {},
        },
    }
    monkeypatch.setattr("services.market_index.fetch_index_realtime_quotes", lambda ash_codes=None: {})
    monkeypatch.setattr("services.market_index._calendar_today", lambda: "2026-06-09")
    monkeypatch.setattr("services.market_index._expected_trade_date", lambda: "2026-06-09")

    row = snapshot_to_api_payload(snap)["indices"][0]
    assert row["change_1d_pct"] == 1.28
