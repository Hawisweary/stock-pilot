#!/usr/bin/env python3
"""Polars vs MyTT 指标对账 — G3 门禁"""
from __future__ import annotations

import json
import os
import sqlite3
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "backend"))

from config import DB_PATH  # noqa: E402

RSI_TOLERANCE = 0.5  # 百分点


def _mytt_rsi(closes: list, period: int = 14) -> float:
    if len(closes) < period + 1:
        return 50.0
    gains, losses = [], []
    for i in range(period):
        diff = closes[-(i + 1)] - closes[-(i + 2)]
        gains.append(max(diff, 0))
        losses.append(max(-diff, 0))
    avg_gain = sum(gains) / period
    avg_loss = sum(losses) / period
    if avg_loss == 0:
        return 100.0
    rs = avg_gain / avg_loss
    return 100 - 100 / (1 + rs)


def run(db_path: str = None) -> dict:
    os.environ["AFR_USE_POLARS"] = "1"
    path = db_path or DB_PATH

    try:
        import polars as pl
        from services.data_bridge import read_quotes_polars
        from services.polars_accel import calc_indicators_polars
    except ImportError as e:
        return {"passed": False, "error": f"polars not installed: {e}"}

    conn = sqlite3.connect(path)
    stocks = conn.execute("SELECT id, code FROM stocks WHERE is_active=1").fetchall()
    conn.close()

    mismatches = []
    checked = 0
    for sid, code in stocks:
        df = read_quotes_polars(stock_id=sid, days=120)
        if df is None or df.is_empty() or len(df) < 30:
            continue
        ind = calc_indicators_polars(df)
        pl_rsi = float(ind["rsi14"][-1]) if ind["rsi14"][-1] is not None else None
        closes = [float(x) for x in df.sort("date")["close"].to_list()]
        mytt_rsi = _mytt_rsi(closes)
        if pl_rsi is None:
            continue
        checked += 1
        diff = abs(pl_rsi - mytt_rsi)
        if diff > RSI_TOLERANCE:
            mismatches.append({"code": code, "polars_rsi": round(pl_rsi, 2), "mytt_rsi": round(mytt_rsi, 2), "diff": round(diff, 2)})

    report = {
        "checked": checked,
        "mismatches": mismatches[:20],
        "mismatch_count": len(mismatches),
        "tolerance_pct": RSI_TOLERANCE,
        "passed": len(mismatches) == 0 and checked >= 3,
    }
    return report


def main() -> int:
    report = run(sys.argv[1] if len(sys.argv) > 1 else None)
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if report.get("passed") else 1


if __name__ == "__main__":
    raise SystemExit(main())
