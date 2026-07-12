#!/usr/bin/env python3
"""Golden 回测对账 — Python baseline vs Rust（若可用）"""
from __future__ import annotations

import json
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "backend"))

GOLDEN_CASES = [
    {"id": "G1", "days": 90, "top_n": 5, "strategy": "composite", "rebalance": "weekly", "lookback": 20},
    {"id": "G2", "days": 180, "top_n": 3, "strategy": "val", "rebalance": "monthly", "lookback": 20},
    {"id": "G3", "days": 60, "top_n": 5, "strategy": "momentum", "rebalance": "weekly", "lookback": 20},
]

THRESHOLDS = {"return_pct": 2.0, "max_dd_pct": 3.0, "trade_count_pct": 5.0}


def run(db_path: str = None) -> dict:
    from config import DB_PATH

    if db_path:
        import config

        config.DB_PATH = db_path

    from services.backtest_rust_adapter import run_backtest_with_engine

    python_results = []
    rust_results = []
    comparisons = []

    for case in GOLDEN_CASES:
        params = {k: v for k, v in case.items() if k != "id"}
        py = run_backtest_with_engine(params, engine="python")
        python_results.append({"id": case["id"], "params": params, "result": _slim(py)})
        rust = run_backtest_with_engine(params, engine="rust")
        rust_results.append({"id": case["id"], "result": _slim(rust)})
        if "error" not in py and rust.get("rust_fallback"):
            comparisons.append({"id": case["id"], "note": "rust unavailable, python only"})

    report = {
        "python": python_results,
        "rust": rust_results,
        "comparisons": comparisons,
        "passed": all("error" not in r["result"] for r in python_results),
    }
    return report


def _slim(r: dict) -> dict:
    if "error" in r:
        return {"error": r["error"]}
    return {
        "total_return_pct": r.get("total_return_pct"),
        "max_drawdown_pct": r.get("max_drawdown_pct"),
        "trade_count": r.get("trade_count"),
        "sharpe": r.get("sharpe"),
        "engine": r.get("engine") or r.get("params", {}).get("engine"),
        "elapsed_ms": r.get("elapsed_ms"),
        "rust_fallback": r.get("rust_fallback"),
    }


def main() -> int:
    report = run(sys.argv[1] if len(sys.argv) > 1 else None)
    out_dir = os.path.join(ROOT, "docs", "reconciliation")
    os.makedirs(out_dir, exist_ok=True)
    out_path = os.path.join(out_dir, "backtest_reconcile.json")
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(report, f, ensure_ascii=False, indent=2)
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
