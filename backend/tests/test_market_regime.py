"""市场状态分类测试。"""
import os
import sqlite3
import sys
import tempfile
from datetime import date, timedelta

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from services.market_regime import (
    RegimeThresholds,
    _is_high_volatility,
    _prepare_kline,
    apply_regime_persistence,
    classify_regime,
    classify_regime_state,
    compute_market_features,
    describe_regime_weight_deltas,
    default_regime_thresholds,
    detect_regime_dual,
    enrich_dual_regime_payload,
    get_regime_guidance,
    get_regime_layers_for_date,
    recompute_regime_persistence,
    regime_bucket,
    regime_bucket_label,
    regime_label,
    sync_regime,
)
import config


def _kline(n: int = 120, base: float = 100.0, regime: str = "oscillation") -> list[dict]:
    bars = []
    px = base
    for i in range(n):
        d = (date(2026, 7, 1) + timedelta(days=i)).isoformat()
        if regime == "strong_trend_up":
            px *= 1.005
        elif regime == "strong_trend_down":
            px *= 0.995
        elif regime == "high_volatility":
            px *= 1.0 + 0.04 * (1 if i % 2 == 0 else -1)
        else:
            px *= 1 + 0.002 * (1 if i % 5 < 3 else -1)
        high = px * 1.02
        low = px * 0.98
        bars.append({"date": d, "open": px, "high": high, "low": low, "close": px, "volume": 1e8})
    return bars


def test_classify_trend_up():
    kline = _kline(120, regime="strong_trend_up")
    result = classify_regime(kline)
    assert result["regime"] == "strong_trend_up"
    assert result["regime_label"] == "趋势上涨"
    assert result["return_20d"] > 0.05
    assert result["return_60d"] > 0.05


def test_classify_trend_down():
    kline = _kline(120, regime="strong_trend_down")
    result = classify_regime(kline)
    assert result["regime"] == "strong_trend_down"
    assert result["regime_label"] == "趋势下跌"


def test_classify_high_volatility():
    kline = _kline(120, regime="high_volatility")
    th = RegimeThresholds(vol_high=0.30, vol_expansion=False)
    result = classify_regime(kline, thresholds=th)
    assert result["regime"] == "high_volatility"
    assert result["regime_label"] == "高波动"
    assert result["volatility_20"] > th.vol_high


def test_high_vol_expansion_requires_vol_rising():
    th = RegimeThresholds(vol_high=0.10, vol_expansion=True)
    # 高 vol20 但低于 vol60 → 不应标高波动
    assert not _is_high_volatility(0.20, 0.25, 30, 0.01, th, avg_corr_20=None)
    assert _is_high_volatility(0.26, 0.20, 30, 0.01, th, avg_corr_20=None)


def test_prepare_kline_truncates_historical(monkeypatch):
    bars = []
    from datetime import date, timedelta
    for i in range(200):
        d = (date(2025, 1, 1) + timedelta(days=i)).isoformat()
        bars.append({"date": d, "open": 100, "high": 101, "low": 99, "close": 100 + i * 0.01, "volume": 1e8})

    monkeypatch.setattr(
        "services.market_regime.fetch_index_kline",
        lambda code, period="daily", days=400, with_technical=False, **kw: {"kline": bars},
    )
    kline, last, err = _prepare_kline("sh000906", "2025-07-11")
    assert err is None
    assert last == "2025-07-11"
    assert kline[-1]["date"] == "2025-07-11"
    assert len(kline) >= 65


def test_classify_liquidity_drought_with_features():
    kline = _kline(120, regime="oscillation")
    result = classify_regime(
        kline,
        features={
            "ad_ratio": 0.48,
            "amount_ratio_20": 0.50,
            "rotation_speed": 0.4,
            "avg_corr_20": 0.35,
            "liquidity_score": 0.50,
        },
    )
    assert result["regime"] == "liquidity_drought"
    assert result["regime_label"] == "流动性枯竭"


def test_regime_label_mapping():
    assert regime_label("weak_trend_up") == "趋势上涨"
    assert regime_label("oscillation") == "震荡"


def test_regime_guidance():
    g = get_regime_guidance("high_volatility")
    assert g["max_position"] == 0.40
    assert g["regime_label"] == "高波动"
    assert "波动" in g["note"]


def test_describe_weight_deltas():
    note = describe_regime_weight_deltas("strong_trend_down")
    assert "质量" in note
    assert "估值" in note
    assert describe_regime_weight_deltas("oscillation") == "权重保持基线，无额外调整"


def _make_quotes_db() -> sqlite3.Connection:
    f = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
    f.close()
    conn = sqlite3.connect(f.name)
    conn.execute("CREATE TABLE stocks (id INTEGER PRIMARY KEY, industry_sw TEXT, is_active INT)")
    conn.execute(
        """CREATE TABLE stock_daily_quotes (
            stock_id INTEGER, trade_date TEXT, close REAL, amount REAL,
            PRIMARY KEY (stock_id, trade_date))"""
    )
    conn.execute("INSERT INTO stocks VALUES (1, '银行', 1), (2, '银行', 1), (3, '医药', 1)")
    dates = [(date(2026, 7, 1) + timedelta(days=i)).isoformat() for i in range(15)]
    for i, d in enumerate(dates):
        conn.execute("INSERT INTO stock_daily_quotes VALUES (1, ?, ?, 1e9)", (d, 10 + i * 0.1))
        conn.execute("INSERT INTO stock_daily_quotes VALUES (2, ?, ?, 1e9)", (d, 10 - i * 0.05))
        conn.execute("INSERT INTO stock_daily_quotes VALUES (3, ?, ?, 5e8)", (d, 20 + i * 0.2))
    conn.commit()
    return conn


def test_compute_market_features():
    conn = _make_quotes_db()
    trade_date = (date(2026, 7, 1) + timedelta(days=14)).isoformat()
    feats = compute_market_features(conn, trade_date)
    assert feats["ad_ratio"] is not None
    assert 0 <= feats["ad_ratio"] <= 1
    assert feats["amount_ratio_20"] is not None
    conn.close()


def test_regime_bucket_mapping():
    assert regime_bucket("strong_trend_up", 0.02) == "trend_up"
    assert regime_bucket("high_volatility", 0.03) == "high_vol"
    assert regime_bucket("high_volatility", -0.03) == "high_vol"
    assert regime_bucket("weak_trend_down", -0.05) == "trend_down"
    assert regime_bucket("oscillation", 0.0) == "oscillation"
    assert regime_bucket_label("high_vol") == "高波动"
    assert regime_bucket_label("trend_up") == "趋势上涨"


def test_enrich_dual_regime_payload():
    payload = enrich_dual_regime_payload({
        "trade_date": "2026-07-25",
        "regime": "strong_trend_up",
        "regime_label": "趋势上涨",
        "price_vs_ma60": 0.05,
        "regime_csi800": "oscillation",
        "regime_csi800_label": "震荡",
        "price_vs_ma60_csi800": -0.01,
        "volatility_20": 0.18,
        "volatility_20_csi800": 0.22,
        "rsi_14": 58,
        "rsi_14_csi800": 52,
    })
    assert payload["regime_csi300"] == "strong_trend_up"
    assert payload["regime_csi800"] == "oscillation"
    assert payload["regime_label_agreement"] is False
    assert len(payload["indices"]) == 2
    assert payload["primary_regime"] == "oscillation"


def test_sync_regime_dual_writes_both(monkeypatch):
    import config
    import tempfile

    k300 = _kline(120, regime="strong_trend_up")
    k800 = _kline(120, regime="oscillation")

    monkeypatch.setattr(
        "services.market_regime._prepare_kline",
        lambda code, trade_date=None, **kw: (
            (k300, "2026-07-25", None) if "300" in code else (k800, "2026-07-25", None)
        ),
    )
    monkeypatch.setattr(
        "services.market_regime.compute_market_features",
        lambda conn, td: {"ad_ratio": 0.6, "amount_ratio_20": 1.0},
    )

    f = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
    f.close()
    conn = sqlite3.connect(f.name)
    conn.executescript("""
        CREATE TABLE market_regime_daily (
            trade_date TEXT PRIMARY KEY,
            index_code TEXT,
            regime TEXT,
            regime_label TEXT,
            rsi_14 REAL, volatility_20 REAL, adx REAL,
            return_20d REAL, return_60d REAL,
            price_vs_ma20 REAL, price_vs_ma60 REAL, ma20_slope REAL,
            ad_ratio REAL, amount_ratio_20 REAL, amount_slope_5 REAL,
            rotation_speed REAL, avg_corr_20 REAL, liquidity_score REAL,
            regime_csi800 TEXT, regime_csi800_label TEXT,
            regime_bucket_csi300 TEXT, regime_bucket_csi800 TEXT,
            regime_label_agreement INTEGER, regime_bucket_agreement INTEGER,
            rsi_14_csi800 REAL, volatility_20_csi800 REAL, adx_csi800 REAL,
            return_20d_csi800 REAL, return_60d_csi800 REAL,
            price_vs_ma20_csi800 REAL, price_vs_ma60_csi800 REAL, ma20_slope_csi800 REAL,
            regime_raw TEXT, regime_csi800_raw TEXT,
            regime_bucket_csi300_raw TEXT, regime_bucket_csi800_raw TEXT,
            updated_at TEXT
        );
    """)
    monkeypatch.setattr(config, "REGIME_PERSISTENCE_DAYS", 1)
    result = sync_regime(conn, trade_date="2026-07-25")
    row = conn.execute("SELECT regime, regime_csi800 FROM market_regime_daily WHERE trade_date='2026-07-25'").fetchone()
    conn.close()
    assert row[0] == "strong_trend_up"
    assert row[1] == "oscillation"
    assert result["regime_label_agreement"] is False


def test_apply_regime_persistence_merges_short_runs():
    raw = ["oscillation"] * 3 + ["strong_trend_up"] * 8 + ["oscillation"] * 2
    confirmed = apply_regime_persistence(raw, min_days=5)
    assert confirmed[:3] == ["oscillation"] * 3
    assert confirmed[3:11] == ["strong_trend_up"] * 8
    # 末尾 2 日震荡不足 5 天，继承前段趋势
    assert confirmed[11:] == ["strong_trend_up"] * 2


def test_apply_regime_persistence_absorbs_isolated_spikes():
    raw = ["oscillation"] * 10 + ["strong_trend_down"] * 2 + ["oscillation"] * 10
    confirmed = apply_regime_persistence(raw, min_days=5)
    assert confirmed.count("strong_trend_down") == 0
    assert all(s == "oscillation" for s in confirmed)


def test_apply_regime_persistence_asymmetric_down_faster():
    raw = (
        ["oscillation"] * 5
        + ["weak_trend_down"] * 2
        + ["oscillation"] * 5
        + ["weak_trend_down"] * 3
        + ["oscillation"] * 5
    )
    confirmed = apply_regime_persistence(
        raw,
        min_days=5,
        min_days_for=lambda r: 2 if regime_bucket(r) == "trend_down" else 5,
    )
    assert confirmed[5:7] == ["weak_trend_down"] * 2
    assert confirmed[12:15] == ["weak_trend_down"] * 3
    assert confirmed.count("weak_trend_down") == 5
    th = default_regime_thresholds()
    regime = classify_regime_state(
        rsi=62,
        vol=0.53,
        vol60=0.34,
        adx=35,
        ret20=0.21,
        ret60=0.14,
        price_vs_ma20=0.08,
        price_vs_ma60=0.14,
        ma20_slope=0.05,
        features={"ad_ratio": 0.08, "amount_ratio_20": 1.1},
        thresholds=th,
    )
    assert regime in ("strong_trend_up", "weak_trend_up")
    assert regime_bucket(regime) == "trend_up"


def test_sync_regime_persists_jump_daily(monkeypatch):
    """15:30 sync_regime 末尾应写入 market_regime_jump_daily（动态 λ Jump）。"""
    k300 = _kline(120, regime="strong_trend_up")
    k800 = _kline(120, regime="oscillation")

    monkeypatch.setattr(
        "services.market_regime._prepare_kline",
        lambda code, trade_date=None, **kw: (
            (k300, "2026-07-25", None) if "300" in code else (k800, "2026-07-25", None)
        ),
    )
    monkeypatch.setattr(
        "services.market_regime.compute_market_features",
        lambda conn, td: {"ad_ratio": 0.6, "amount_ratio_20": 1.0},
    )
    monkeypatch.setattr(
        "services.regime_jump.predict_jump_with_dynamic_lambda",
        lambda conn, td, **kw: {
            "trade_date": td,
            "regime_bucket": "oscillation",
            "regime_bucket_label": "震荡",
            "jump_state": 1,
            "jump_penalty": 25.0,
            "jump_penalty_source": "walkforward",
            "backend": "simple",
        },
    )

    f = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
    f.close()
    conn = sqlite3.connect(f.name)
    conn.executescript("""
        CREATE TABLE market_regime_daily (
            trade_date TEXT PRIMARY KEY,
            index_code TEXT,
            regime TEXT,
            regime_label TEXT,
            rsi_14 REAL, volatility_20 REAL, adx REAL,
            return_20d REAL, return_60d REAL,
            price_vs_ma20 REAL, price_vs_ma60 REAL, ma20_slope REAL,
            ad_ratio REAL, amount_ratio_20 REAL, amount_slope_5 REAL,
            rotation_speed REAL, avg_corr_20 REAL, liquidity_score REAL,
            regime_csi800 TEXT, regime_csi800_label TEXT,
            regime_bucket_csi300 TEXT, regime_bucket_csi800 TEXT,
            regime_label_agreement INTEGER, regime_bucket_agreement INTEGER,
            rsi_14_csi800 REAL, volatility_20_csi800 REAL, adx_csi800 REAL,
            return_20d_csi800 REAL, return_60d_csi800 REAL,
            price_vs_ma20_csi800 REAL, price_vs_ma60_csi800 REAL, ma20_slope_csi800 REAL,
            regime_raw TEXT, regime_csi800_raw TEXT,
            regime_bucket_csi300_raw TEXT, regime_bucket_csi800_raw TEXT,
            updated_at TEXT
        );
        CREATE TABLE market_regime_jump_daily (
            trade_date TEXT PRIMARY KEY,
            index_code TEXT NOT NULL DEFAULT 'sh000906',
            jump_state INTEGER,
            regime_bucket TEXT,
            jump_penalty REAL,
            backend TEXT,
            model_version TEXT,
            updated_at TEXT NOT NULL DEFAULT (datetime('now'))
        );
    """)
    monkeypatch.setattr(config, "REGIME_PERSISTENCE_DAYS", 1)

    result = sync_regime(conn, trade_date="2026-07-25")
    row = conn.execute(
        """SELECT regime_bucket, jump_penalty, model_version
           FROM market_regime_jump_daily WHERE trade_date='2026-07-25'""",
    ).fetchone()
    conn.close()

    assert row is not None
    assert row[0] == "oscillation"
    assert row[1] == 25.0
    assert row[2] == "jump_dynamic_wf_v1"
    assert result["jump_regime"]["regime_bucket"] == "oscillation"
    assert result["jump_regime"]["jump_penalty"] == 25.0


def test_get_regime_layers_for_date():
    f = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
    f.close()
    conn = sqlite3.connect(f.name)
    conn.executescript("""
        CREATE TABLE market_regime_daily (
            trade_date TEXT PRIMARY KEY,
            regime TEXT, regime_label TEXT,
            regime_csi800 TEXT, regime_csi800_label TEXT,
            regime_bucket_csi800 TEXT, regime_bucket_csi300 TEXT,
            rsi_14 REAL, volatility_20 REAL, adx REAL,
            return_20d REAL, return_60d REAL,
            price_vs_ma20 REAL, price_vs_ma60 REAL, ma20_slope REAL,
            ad_ratio REAL, amount_ratio_20 REAL, amount_slope_5 REAL,
            rotation_speed REAL, avg_corr_20 REAL, liquidity_score REAL,
            updated_at TEXT
        );
        CREATE TABLE market_regime_jump_daily (
            trade_date TEXT PRIMARY KEY,
            regime_bucket TEXT, jump_penalty REAL, model_version TEXT, backend TEXT
        );
        CREATE TABLE market_regime_hmm_daily (
            trade_date TEXT PRIMARY KEY,
            regime_bucket TEXT, hmm_state INTEGER
        );
    """)
    conn.execute(
        """INSERT INTO market_regime_daily
           (trade_date, regime, regime_label, regime_csi800, regime_csi800_label, regime_bucket_csi800)
           VALUES ('2026-07-27', 'high_volatility', '高波动', 'high_volatility', '高波动', 'high_vol')""",
    )
    conn.execute(
        "INSERT INTO market_regime_jump_daily VALUES ('2026-07-27', 'high_vol', 25.0, 'v1', 'simple')",
    )
    conn.execute(
        "INSERT INTO market_regime_hmm_daily VALUES ('2026-07-27', 'oscillation', 2)",
    )
    conn.commit()

    layers = get_regime_layers_for_date(conn, "2026-07-27")
    conn.close()

    assert layers["trade_date"] == "2026-07-27"
    assert layers["layers"]["rules"]["bucket"] == "high_vol"
    assert layers["layers"]["jump"]["bucket"] == "high_vol"
    assert layers["layers"]["hmm"]["bucket"] == "oscillation"
    assert layers["all_aligned"] is False
    assert "hmm" in layers["diverged_layers"]
