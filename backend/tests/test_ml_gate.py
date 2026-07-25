"""ML 指标门控单元测试。"""
import sqlite3
import tempfile
import os

from services.ml_gate import evaluate_metric_gate, is_ml_predictions_approved
from services.ml_train_store import ensure_ml_validation_tables, insert_train_run


def _seed_runs(db_path: str, ics: list[float]) -> None:
    conn = sqlite3.connect(db_path)
    ensure_ml_validation_tables(conn)
    for i, ic in enumerate(ics):
        insert_train_run(
            conn,
            {
                "horizon": 20,
                "train_start": "2024-01-01",
                "train_end": "2025-01-01",
                "test_start": f"2025-02-{i+1:02d}",
                "test_end": f"2025-03-{i+1:02d}",
                "model_version": "lightgbm_h20_wf_v1",
                "oos_rank_ic": ic,
                "fold": i,
            },
        )
    conn.commit()
    conn.close()


def test_gate_passes_with_good_ics():
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    try:
        _seed_runs(path, [0.03, 0.025, 0.028, 0.022, 0.031])
        g = evaluate_metric_gate(path, horizon=20)
        assert g["gate_passed"] is True
        assert g["folds_with_rank_ic"] == 5
    finally:
        os.unlink(path)


def test_gate_fails_low_mean():
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    try:
        _seed_runs(path, [0.01, 0.005, 0.008])
        g = evaluate_metric_gate(path, horizon=20)
        assert g["gate_passed"] is False
    finally:
        os.unlink(path)


def test_gate_fails_on_full_history_when_recent_lucky():
    """H20 场景:近 5 折看着好,但全历史均值≈0 → 全历史下限拦截,不放行。"""
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    try:
        # 前 20 折 ≈0/负(长期无 edge),末 5 折抽样运气偏正(骗过近窗)
        _seed_runs(path, [-0.02] * 10 + [0.0] * 10 + [0.03, 0.028, 0.031, 0.026, 0.03])
        g = evaluate_metric_gate(path, horizon=20)
        assert g["mean_rank_ic"] > 0.02          # 近窗自身通过
        assert g["full_mean_rank_ic"] < 0.01     # 全历史不及格
        assert g["gate_passed"] is False
        assert any("full_mean_rank_ic" in r for r in g["failure_reasons"])
    finally:
        os.unlink(path)


def test_gate_fails_on_drift_even_if_full_mean_ok():
    """全历史均值达标,但前后折漂移过大(近窗不代表全历史)→ 漂移上限拦截。"""
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    try:
        # 前 5 折 ~0、后 5 折 ~0.06:full_mean=0.03(过下限),drift=0.06(>0.05)
        _seed_runs(path, [0.0] * 5 + [0.06] * 5)
        g = evaluate_metric_gate(path, horizon=20)
        assert g["full_mean_rank_ic"] >= 0.01
        assert g["fold_drift"] is not None and abs(g["fold_drift"]) > 0.05
        assert g["gate_passed"] is False
        assert any("drift" in r for r in g["failure_reasons"])
    finally:
        os.unlink(path)
