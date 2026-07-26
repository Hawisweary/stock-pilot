"""分 horizon ML 特征向量测试。"""
import math
import os
import sqlite3
import sys
import tempfile
from datetime import date, timedelta

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from services.ml_feature_sets import (
    H5_FEATURES,
    MlFeatureContext,
    apply_cross_section_ranks,
    compute_base_features,
    feature_names_for,
    vectorize,
    feature_spec_summary,
)
from services.ml_impute import impute_v5_tables


def _synthetic_bars(n: int = 80, base: float = 10.0):
    bars = []
    px = base
    for i in range(n):
        d = f"2026-01-{i+1:02d}" if i < 31 else f"2026-02-{(i-30):02d}"
        px *= 1 + 0.002 * math.sin(i / 5)
        vol = 1e6 * (1 + 0.1 * math.sin(i / 3))
        bars.append((d, px, vol, px * 1.01, px * 0.99, 2.5 + 0.5 * math.sin(i), vol * px))
    return bars


def test_h5_vector_shape():
    ctx = MlFeatureContext()
    bars = _synthetic_bars(40)
    feats = compute_base_features(bars, len(bars) - 1, 5, 1, ctx)
    batch = [feats, feats]
    apply_cross_section_ranks(batch, 5)
    vec = vectorize(batch[0], 5)
    assert len(vec) == len(feature_names_for(5))
    assert all(math.isfinite(v) for v in vec)


def test_h20_vector_shape():
    ctx = MlFeatureContext()
    bars = _synthetic_bars(60)
    feats = compute_base_features(bars, len(bars) - 1, 20, 1, ctx)
    batch = [feats]
    apply_cross_section_ranks(batch, 20)
    vec = vectorize(batch[0], 20)
    assert len(vec) == len(feature_names_for(20))


def test_h60_beta_cross_section():
    ctx = MlFeatureContext()
    b1 = _synthetic_bars(280, 10)
    b2 = _synthetic_bars(280, 20)
    f1 = compute_base_features(b1, len(b1) - 1, 60, 1, ctx)
    f2 = compute_base_features(b2, len(b2) - 1, 60, 2, ctx)
    batch = [f1, f2]
    apply_cross_section_ranks(batch, 60)
    assert "beta_60" in batch[0]
    vec = vectorize(batch[0], 60)
    assert len(vec) == len(feature_names_for(60))


def test_context_load_empty_db():
    with tempfile.NamedTemporaryFile(suffix=".db") as f:
        conn = sqlite3.connect(f.name)
        conn.execute(
            """CREATE TABLE stocks (id INTEGER PRIMARY KEY, industry_sw2 TEXT, industry_sw TEXT, is_active INT)"""
        )
        conn.execute("INSERT INTO stocks VALUES (1, '银行', '', 1)")
        conn.commit()
        ctx = MlFeatureContext.load(conn, ["2026-06-01"])
        assert ctx.stock_industry[1] == "银行"
        conn.close()


def test_h5_v4_vector_shape():
    ctx = MlFeatureContext()
    # 构造 25 日 moneyflow 记录，末条与 bars 最后日期对齐
    mf = []
    for i in range(25):
        d = f"2026-01-{i+1:02d}" if i < 31 else f"2026-02-{(i-30):02d}"
        mf.append((d, {
            "buy_sm": 100 + i, "sell_sm": 50 + i // 2,
            "buy_md": 200 + i, "sell_md": 100 + i // 2,
            "buy_lg": 300 + i, "sell_lg": 150 + i // 2,
            "buy_elg": 400 + i, "sell_elg": 200 + i // 2,
            "net_mf": 500 + i * 10,
        }))
    ctx.moneyflow_by_stock[1] = mf
    bars = _synthetic_bars(40)
    feats = compute_base_features(bars, len(bars) - 1, 5, 1, ctx, variant="v4")
    for k in ("mf_net_pct", "mf_elg_pct", "mf_lg_elg_buy_pct", "mf_sm_pct",
              "mf_net_5d_pct", "mf_5d_20d_ratio", "mf_consec_inflow", "mf_smart_vs_dumb"):
        assert k in feats, f"missing {k}"
    batch = [feats, feats]
    apply_cross_section_ranks(batch, 5, variant="v4")
    for k in ("mf_net_pct_rank", "mf_elg_pct_rank", "mf_lg_elg_buy_pct_rank",
              "mf_sm_pct_rank", "mf_net_5d_pct_rank", "mf_5d_20d_ratio_rank",
              "mf_consec_inflow_rank", "mf_smart_vs_dumb_rank"):
        assert k in batch[0], f"missing {k}"
    vec = vectorize(batch[0], 5, variant="v4")
    assert len(vec) == len(feature_names_for(5, variant="v4"))
    assert all(math.isfinite(v) for v in vec)


def test_feature_spec_summary():
    spec = feature_spec_summary()
    assert set(spec.keys()) == {5, 20, 60}
    assert spec[5]["count"] == len(H5_FEATURES)


def test_tushare_earnings_signals_in_h20_vector():
    with tempfile.NamedTemporaryFile(suffix=".db") as f:
        conn = sqlite3.connect(f.name)
        conn.execute(
            """CREATE TABLE stocks (id INTEGER PRIMARY KEY, industry_sw2 TEXT, industry_sw TEXT, is_active INT)"""
        )
        conn.execute("INSERT INTO stocks VALUES (1, '银行', '', 1)")
        conn.execute(
            """CREATE TABLE earnings_forecast (
                stock_id INTEGER, period_end_date TEXT, ann_date TEXT,
                p_change_min REAL, p_change_max REAL,
                PRIMARY KEY (stock_id, period_end_date))"""
        )
        conn.execute(
            """CREATE TABLE earnings_express (
                stock_id INTEGER, period_end_date TEXT, ann_date TEXT,
                yoy_sales REAL, yoy_dedu_np REAL,
                PRIMARY KEY (stock_id, period_end_date))"""
        )
        # 两期预告 + 快报实际
        conn.execute("INSERT INTO earnings_forecast VALUES (1, '2025-06-30', '2025-07-15', 10, 20)")
        conn.execute("INSERT INTO earnings_forecast VALUES (1, '2025-12-31', '2026-01-27', 30, 50)")
        conn.execute("INSERT INTO earnings_express VALUES (1, '2025-12-31', '2026-02-28', 45, 40)")
        conn.commit()

        ctx = MlFeatureContext.load(conn, ["2026-07-24"])
        sig = ctx._earnings_signals(1, "2026-07-24")
        assert sig["forecast_mid"] == 40.0
        assert sig["earnings_revision"] == 25.0  # 40 - (10+20)/2
        assert sig["yoy_dedu_np"] == 40.0
        assert sig["yoy_sales"] == 45.0
        assert sig["earnings_surprise"] == 0.0  # 40 - 40

        conn.close()


def test_h20_vector_includes_tushare_earnings():
    ctx = MlFeatureContext()
    bars = _synthetic_bars(60)
    feats = compute_base_features(bars, len(bars) - 1, 20, 1, ctx)
    batch = [feats]
    apply_cross_section_ranks(batch, 20)
    vec = vectorize(batch[0], 20)
    names = feature_names_for(20)
    assert len(vec) == len(names)
    for key in ("forecast_mid", "earnings_surprise", "earnings_revision", "yoy_dedu_np", "yoy_sales", "miss_earnings"):
        assert key in names, f"missing {key} in H20 feature names"
        assert key in batch[0], f"missing {key} in H20 features"


def test_impute_v5_tables():
    with tempfile.NamedTemporaryFile(suffix=".db") as f:
        conn = sqlite3.connect(f.name)
        conn.execute(
            """CREATE TABLE stocks (id INTEGER PRIMARY KEY, industry_sw2 TEXT, industry_sw TEXT, is_active INT)"""
        )
        conn.execute("INSERT INTO stocks VALUES (1, '银行', '', 1)")
        conn.execute("INSERT INTO stocks VALUES (2, '银行', '', 1)")
        conn.execute(
            """CREATE TABLE valuation_snapshots (
                stock_id INTEGER, as_of_date TEXT, pe_ttm REAL, pb REAL, dividend_yield REAL,
                PRIMARY KEY (stock_id, as_of_date))"""
        )
        conn.execute(
            """CREATE TABLE stock_v5_metrics (
                stock_id INTEGER, calc_date TEXT, revenue_yoy_q REAL, cfo_np REAL, debt_ratio REAL, quality_tier REAL,
                PRIMARY KEY (stock_id, calc_date))"""
        )
        today = date.today().isoformat()
        yesterday = (date.today() - timedelta(days=1)).isoformat()
        # Stock 1: today missing pe_ttm, yesterday has it
        conn.execute("INSERT INTO valuation_snapshots VALUES (1, ?, 15.0, 1.5, 3.0)", (yesterday,))
        conn.execute("INSERT INTO valuation_snapshots VALUES (1, ?, NULL, 1.6, NULL)", (today,))
        # Stock 2: both missing pe_ttm, should get industry median from stock 1
        conn.execute("INSERT INTO valuation_snapshots VALUES (2, ?, NULL, 2.0, 2.0)", (today,))
        # v5 metrics: stock 2 missing cfo_np, should get industry median from stock 1
        conn.execute("INSERT INTO stock_v5_metrics VALUES (1, ?, 10.0, 0.5, 30.0, 3.0)", (today,))
        conn.execute("INSERT INTO stock_v5_metrics VALUES (2, ?, 12.0, NULL, 40.0, 2.0)", (today,))
        conn.commit()

        result = impute_v5_tables(conn, days=90)
        assert result["valuation_snapshots"]["pe_ttm"] == 2
        assert result["stock_v5_metrics"]["cfo_np"] == 1

        pe1 = conn.execute("SELECT pe_ttm FROM valuation_snapshots WHERE stock_id=1 AND as_of_date=?", (today,)).fetchone()[0]
        pe2 = conn.execute("SELECT pe_ttm FROM valuation_snapshots WHERE stock_id=2 AND as_of_date=?", (today,)).fetchone()[0]
        cfo2 = conn.execute("SELECT cfo_np FROM stock_v5_metrics WHERE stock_id=2 AND calc_date=?", (today,)).fetchone()[0]
        assert pe1 == 15.0  # forward fill
        assert pe2 == 15.0  # industry median
        assert cfo2 == 0.5  # industry median

        conn.close()
