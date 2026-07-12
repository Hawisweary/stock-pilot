"""Rust 回测 worker — qars3 (QARS2) 或 Python 兼容模式（子进程隔离）"""
from __future__ import annotations

import json
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)


def _python_compat(payload: dict) -> dict:
    db_path = os.environ.get("AFR_DB_PATH", os.path.join(os.path.dirname(ROOT), "data", "afr.db"))
    import config

    config.DB_PATH = db_path
    strategy = payload.get("strategy", "composite")
    use_polars = os.environ.get("AFR_USE_POLARS", "").lower() in ("1", "true", "yes")

    if use_polars and strategy in ("momentum", "price", ""):
        from services.backtest_vector import run_momentum_backtest_polars

        r = run_momentum_backtest_polars(
            days=payload.get("days", 90),
            top_n=payload.get("top_n", 5),
            lookback=payload.get("lookback", 20),
        )
        if r and "error" not in r:
            return {
                "status": "done",
                "mode": "polars_vector",
                "total_return_pct": r.get("total_return_pct"),
                "max_drawdown_pct": r.get("max_drawdown_pct"),
                "trade_count": r.get("trade_count"),
                "sharpe": r.get("sharpe"),
                "win_rate": r.get("win_rate_pct"),
                "params": payload,
            }

    from services.backtest_engine import run_backtest

    params = {
        k: payload[k]
        for k in (
            "days",
            "top_n",
            "lookback",
            "pos_style",
            "strategy",
            "min_score",
            "rebalance",
            "apply_costs",
            "apply_t1",
            "combination_id",
            "benchmark_mode",
            "sector_window",
        )
        if k in payload
    }
    r = run_backtest(**params)
    if "error" in r:
        return {"status": "error", "mode": "python_compat", "error": r["error"]}
    return {
        "status": "done",
        "mode": "python_compat",
        "total_return_pct": r.get("total_return_pct"),
        "max_drawdown_pct": r.get("max_drawdown_pct"),
        "trade_count": r.get("trade_count"),
        "sharpe": r.get("sharpe"),
        "win_rate": r.get("win_rate"),
        "params": params,
    }


def _qars3_version() -> str | None:
    try:
        import qars3

        return getattr(qars3, "__version__", "unknown")
    except ImportError:
        return None


def main() -> int:
    payload = json.loads(sys.argv[1] if len(sys.argv) > 1 else sys.stdin.read())
    qars_ver = _qars3_version()

    # 评分驱动组合回测仍走 python_compat；qars3 用于账户/引擎加速（后续可接 QARSBacktest）
    result = _python_compat(payload)
    if qars_ver:
        result["mode"] = "python_compat"
        result["rust_core"] = True
        result["qars3_version"] = qars_ver
        result["note"] = "qars3 installed; score backtest uses python_compat until QARSBacktest wired"
    else:
        result["note"] = "qars3 not installed; python_compat subprocess (see scripts/install_qars_from_quantaxis.sh)"

    print(json.dumps(result, ensure_ascii=False))
    return 0 if result.get("status") == "done" else 1


if __name__ == "__main__":
    raise SystemExit(main())
