"""YoY 可信度清洗单元测试"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from services.data_processor import compute_yoy_meta, enrich_reports_with_yoy


def test_normal_yoy():
    meta = compute_yoy_meta(110, 100)
    assert meta["yoy_reliable"] is True
    assert meta["yoy_pct"] == 10.0
    assert meta["yoy_decimal"] == 0.1


def test_extreme_yoy_filtered():
    # 凡拓数创 2024Q1 近似场景
    meta = compute_yoy_meta(-36580065, -418866)
    assert meta["yoy_reliable"] is False
    assert meta["yoy_pct"] is None
    assert meta["yoy_raw_pct"] == -8633.1
    assert meta["change_ratio"] > 10


def test_zero_prev():
    meta = compute_yoy_meta(100, 0)
    assert meta["yoy_reliable"] is False
    assert meta["yoy_pct"] is None


def test_enrich_reports_with_yoy():
    reports = [
        {"period_end_date": "2023-03-31", "revenue": 100, "net_profit_parent": -418866},
        {"period_end_date": "2024-03-31", "revenue": 120, "net_profit_parent": -36580065},
    ]
    enrich_reports_with_yoy(reports)
    latest = reports[-1]
    assert latest["profit_yoy"] is None
    assert latest["profit_yoy_reliable"] is False
    assert latest["profit_yoy_raw"] is not None
    assert latest["revenue_yoy"] == 20.0


def test_factor_engine_decimal():
    meta = compute_yoy_meta(110, 100)
    assert meta["yoy_decimal"] == 0.1
    meta2 = compute_yoy_meta(-36580065, -418866)
    assert meta2["yoy_decimal"] is None
