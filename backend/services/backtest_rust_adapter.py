"""Rust 回测适配器 — 子进程调用 worker，失败 fallback Python"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from typing import Optional

from config import QUANT_WORKERS_DIR, RUST_BACKTEST_APPROVED, VENV_QUANT_PYTHON, effective_backtest_engine


def run_rust_backtest(params: dict) -> Optional[dict]:
    if effective_backtest_engine("rust") != "rust":
        return None

    worker = os.path.join(QUANT_WORKERS_DIR, "rust_backtest_worker.py")
    if not os.path.isfile(worker):
        return {"error": "rust worker missing", "engine": "rust"}

    python = VENV_QUANT_PYTHON or sys.executable
    t0 = time.perf_counter()
    try:
        proc = subprocess.run(
            [python, worker, json.dumps(params)],
            capture_output=True,
            text=True,
            timeout=int(params.get("timeout_sec", 120)),
        )
    except subprocess.TimeoutExpired:
        return {"error": "rust worker timeout", "engine": "rust"}

    elapsed_ms = round((time.perf_counter() - t0) * 1000)
    if proc.returncode != 0:
        try:
            err = json.loads(proc.stdout.strip().splitlines()[-1])
        except Exception:
            err = {"stderr": proc.stderr[-500:]}
        err["engine"] = "rust"
        err["elapsed_ms"] = elapsed_ms
        return err

    try:
        result = json.loads(proc.stdout.strip().splitlines()[-1])
        result["engine"] = "rust"
        result["elapsed_ms"] = elapsed_ms
        return result
    except json.JSONDecodeError:
        return {"error": "invalid rust worker output", "engine": "rust", "elapsed_ms": elapsed_ms}


def run_backtest_with_engine(params: dict, engine: str = "python") -> dict:
    """统一入口：rust 不可用则 fallback python"""
    from services.backtest_engine import run_backtest

    requested = engine if engine == "rust" else "python"
    py_only = {
        "factor_combination",
        "turtle",
        "sector_rotation",
        "index_enhance",
        "momentum",
        "dual_ma",
    }
    if params.get("combination_id") or params.get("strategy") in py_only:
        requested = "python"
    if requested == "rust" and RUST_BACKTEST_APPROVED:
        rust_result = run_rust_backtest(params)
        if rust_result and rust_result.get("status") == "done":
            return rust_result
        if rust_result and "error" not in rust_result and rust_result.get("status") != "unavailable":
            return rust_result

    t0 = time.perf_counter()
    result = run_backtest(
        days=params.get("days", 90),
        top_n=params.get("top_n", 5),
        lookback=params.get("lookback", 20),
        pos_style=params.get("pos_style", "equal"),
        strategy=params.get("strategy", "composite"),
        min_score=params.get("min_score", 50),
        rebalance=params.get("rebalance", "weekly"),
        apply_costs=params.get("apply_costs", True),
        apply_t1=params.get("apply_t1", True),
        combination_id=params.get("combination_id"),
        ml_horizon=params.get("ml_horizon"),
        benchmark_mode=params.get("benchmark_mode", "equal"),
        sector_window=params.get("sector_window", 5),
        exec_mode=params.get("exec_mode", "next_open"),
        initial_cash=params.get("initial_cash", 100_000.0),
        commission_override=params.get("commission_override"),
        slippage_override=params.get("slippage_override"),
        stamp_tax_override=params.get("stamp_tax_override"),
        risk_free_rate=params.get("risk_free_rate", 0.02),
    )
    if isinstance(result.get("params"), dict):
        result["params"]["engine"] = "python"
    else:
        result["engine"] = "python"
    result["elapsed_ms"] = round((time.perf_counter() - t0) * 1000)
    if requested == "rust":
        result["rust_fallback"] = True
    return result
