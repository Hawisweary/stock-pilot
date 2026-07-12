#!/usr/bin/env python3
"""v4 回测引擎基准：Python vs Polars vector"""
from __future__ import annotations

import json
import os
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "backend"))

import config

config.DB_PATH = os.environ.get("AFR_DB_PATH", os.path.join(ROOT, "data", "afr.db"))


def _bench(name: str, fn) -> dict:
    t0 = time.perf_counter()
    r = fn()
    ms = round((time.perf_counter() - t0) * 1000, 1)
    return {
        "engine": name,
        "ms": ms,
        "total_return_pct": r.get("total_return_pct"),
        "sharpe": r.get("sharpe"),
        "error": r.get("error"),
    }


def main() -> int:
    from services.backtest_engine import run_backtest

    days = int(os.environ.get("BENCH_DAYS", "360"))
    params = {"days": days, "top_n": 5, "lookback": 20, "strategy": "momentum"}

    results = [
        _bench(
            "python",
            lambda: run_backtest(
                days=days,
                top_n=5,
                lookback=20,
                strategy="momentum",
                rebalance="weekly",
            ),
        ),
    ]

    try:
        from services.backtest_vector import run_momentum_backtest_polars

        results.append(
            _bench("polars_vector", lambda: run_momentum_backtest_polars(days=days, top_n=5, lookback=20))
        )
    except ImportError:
        results.append({"engine": "polars_vector", "error": "polars not installed"})

    py_ms = results[0].get("ms") or 1
    pl = next((x for x in results if x["engine"] == "polars_vector"), {})
    if pl.get("ms"):
        pl["speedup_vs_python"] = round(py_ms / pl["ms"], 2)

    print(json.dumps({"params": params, "results": results}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
