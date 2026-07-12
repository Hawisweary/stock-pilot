"""龙虎榜 fetch 工具函数"""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from services.lhb_fetch import _format_pct, _norm_code, _norm_date, _yuan_to_wan


def test_norm_code():
    assert _norm_code("000001") == "000001"
    assert _norm_code("sz000001") == "000001"
    assert _norm_code("SH600519") == "600519"


def test_norm_date():
    assert _norm_date("20240315") == "2024-03-15"
    assert _norm_date("2024-03-15") == "2024-03-15"


def test_yuan_to_wan():
    assert _yuan_to_wan(50000000) == 5000.0


def test_format_pct():
    assert _format_pct(10.0179) == 10.02
    assert _format_pct(0.100179) == 10.02


@pytest.mark.network
def test_fetch_lhb_daily_eastmoney():
    from services.lhb_fetch import fetch_lhb_daily

    out = fetch_lhb_daily()
    assert out.get("count", 0) > 0
    assert out.get("source") == "eastmoney"
    assert out["items"][0].get("code")
    assert out["items"][0].get("change_pct") is not None
