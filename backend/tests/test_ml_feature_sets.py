"""分 horizon ML 特征向量测试。"""
import math
import sqlite3
import tempfile
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from services.ml_feature_sets import (
    MlFeatureContext,
    apply_cross_section_ranks,
    compute_base_features,
    feature_names_for,
    vectorize,
    feature_spec_summary,
)


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


def test_feature_spec_summary():
    spec = feature_spec_summary()
    assert set(spec.keys()) == {5, 20, 60}
    assert spec[5]["count"] == 10
