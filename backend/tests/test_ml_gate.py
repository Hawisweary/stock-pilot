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
