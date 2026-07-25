"""ML 训练 / OOS 验证落库。"""
from __future__ import annotations

import json
import sqlite3
from typing import Any, Optional


def configure_sqlite_conn(conn: sqlite3.Connection, *, busy_timeout_ms: int = 120_000) -> None:
    """WAL + 长 busy_timeout，减轻 WF 长任务与后端并发写锁。"""
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute(f"PRAGMA busy_timeout={int(busy_timeout_ms)}")


def ensure_ml_validation_tables(conn: sqlite3.Connection) -> None:
    configure_sqlite_conn(conn)
    conn.execute(
        """CREATE TABLE IF NOT EXISTS ml_train_runs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            horizon INTEGER NOT NULL,
            train_start TEXT NOT NULL,
            train_end TEXT NOT NULL,
            test_start TEXT NOT NULL,
            test_end TEXT NOT NULL,
            model_version TEXT NOT NULL,
            oos_rank_ic REAL,
            oos_ic REAL,
            oos_long_short_return REAL,
            feature_importance_json TEXT,
            train_rmse REAL,
            n_oos INTEGER,
            fold INTEGER,
            created_at TEXT DEFAULT (datetime('now'))
        )"""
    )
    conn.execute(
        """CREATE TABLE IF NOT EXISTS ml_oos_predictions_daily (
            horizon INTEGER NOT NULL,
            pred_date TEXT NOT NULL,
            stock_id INTEGER NOT NULL,
            pred_raw REAL NOT NULL,
            pred_score REAL NOT NULL,
            label REAL,
            model_version TEXT NOT NULL,
            fold INTEGER,
            PRIMARY KEY (horizon, pred_date, stock_id, model_version)
        )"""
    )
    conn.commit()


def insert_train_run(conn: sqlite3.Connection, row: dict[str, Any]) -> int:
    ensure_ml_validation_tables(conn)
    conn.execute(
        """INSERT INTO ml_train_runs (
            horizon, train_start, train_end, test_start, test_end, model_version,
            oos_rank_ic, oos_ic, oos_long_short_return, feature_importance_json,
            train_rmse, n_oos, fold
        ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)""",
        (
            row["horizon"],
            row["train_start"],
            row["train_end"],
            row["test_start"],
            row["test_end"],
            row["model_version"],
            row.get("oos_rank_ic"),
            row.get("oos_ic"),
            row.get("oos_long_short_return"),
            json.dumps(row.get("feature_importance") or {}, ensure_ascii=False),
            row.get("train_rmse"),
            row.get("n_oos"),
            row.get("fold"),
        ),
    )
    rid = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
    return int(rid)


def upsert_oos_daily(
    conn: sqlite3.Connection,
    *,
    horizon: int,
    pred_date: str,
    stock_id: int,
    pred_raw: float,
    pred_score: float,
    label: float | None,
    model_version: str,
    fold: int,
) -> None:
    ensure_ml_validation_tables(conn)
    conn.execute(
        """INSERT OR REPLACE INTO ml_oos_predictions_daily
           (horizon, pred_date, stock_id, pred_raw, pred_score, label, model_version, fold)
           VALUES (?,?,?,?,?,?,?,?)""",
        (horizon, pred_date, stock_id, pred_raw, pred_score, label, model_version, fold),
    )


def get_latest_train_runs(
    db_path: str,
    horizon: int = 20,
    limit: int = 5,
) -> list[dict]:
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    ensure_ml_validation_tables(conn)
    rows = conn.execute(
        """SELECT * FROM ml_train_runs WHERE horizon=?
           ORDER BY id DESC LIMIT ?""",
        (horizon, limit),
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def get_all_train_runs(db_path: str, horizon: int = 20) -> list[dict]:
    """按时间顺序(id 升序)返回某 horizon 的全部 walk-forward 折，用于全历史均值与前后漂移。"""
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    ensure_ml_validation_tables(conn)
    rows = conn.execute(
        """SELECT * FROM ml_train_runs WHERE horizon=? ORDER BY id ASC""",
        (horizon,),
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def get_validation_summary(
    db_path: str,
    horizon: int = 20,
    *,
    recent_windows: int = 5,
    rank_ic_threshold: float = 0.02,
    min_folds: int = 3,
) -> dict:
    runs = get_latest_train_runs(db_path, horizon=horizon, limit=recent_windows)
    if not runs:
        return {
            "horizon": horizon,
            "has_runs": False,
            "validation_status": "none",
            "is_metrics_approved": False,
            "latest_run": None,
            "recent_mean_rank_ic": None,
            "recent_rank_ic_std": None,
            "folds_with_rank_ic": 0,
        }
    ics = [float(r["oos_rank_ic"]) for r in runs if r.get("oos_rank_ic") is not None]
    import statistics

    mean_ic = sum(ics) / len(ics) if ics else None
    std_ic = statistics.stdev(ics) if len(ics) >= 2 else None
    approved = (
        mean_ic is not None
        and len(ics) >= min_folds
        and mean_ic >= rank_ic_threshold
    )
    latest = runs[0]
    return {
        "horizon": horizon,
        "has_runs": True,
        "validation_status": "validated" if approved else "experimental",
        "is_metrics_approved": approved,
        "recent_mean_rank_ic": round(mean_ic, 4) if mean_ic is not None else None,
        "recent_rank_ic_std": round(std_ic, 4) if std_ic is not None else None,
        "folds_with_rank_ic": len(ics),
        "latest_run": {
            "horizon": latest["horizon"],
            "oos_rank_ic": latest.get("oos_rank_ic"),
            "oos_ic": latest.get("oos_ic"),
            "oos_long_short_return": latest.get("oos_long_short_return"),
            "model_version": latest.get("model_version"),
            "test_window": f"{latest.get('test_start')} ~ {latest.get('test_end')}",
            "train_window": f"{latest.get('train_start')} ~ {latest.get('train_end')}",
            "n_oos": latest.get("n_oos"),
            "fold": latest.get("fold"),
        },
    }
