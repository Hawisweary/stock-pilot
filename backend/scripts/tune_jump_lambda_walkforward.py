#!/usr/bin/env python3
"""Jump Model Walk-Forward λ 选参 + 时间线导出。

用法:
  cd backend && python scripts/tune_jump_lambda_walkforward.py
  cd backend && python scripts/tune_jump_lambda_walkforward.py --persist
  cd backend && python scripts/tune_jump_lambda_walkforward.py --output data/lambda_timeline.csv --json
"""
from __future__ import annotations

import argparse
import csv
import json
import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import config
from migrations import run_migrations
from services.regime_jump import (
    format_walkforward_report_text,
    persist_lambda_timeline,
    walkforward_tune_lambda,
)


def _write_csv(path: Path, timeline: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=["trade_date", "jump_penalty"])
        w.writeheader()
        for row in timeline:
            w.writerow({
                "trade_date": row["trade_date"],
                "jump_penalty": row["jump_penalty"],
            })


def _write_windows_csv(path: Path, windows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fields = [
        "train_start", "train_end", "val_start", "val_end",
        "apply_start", "apply_end", "best_lambda", "best_score",
        "val_consistency_pct", "val_dwell_mean", "val_rule_dwell_mean", "backend",
    ]
    with path.open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=fields)
        w.writeheader()
        for win in windows:
            w.writerow({k: win.get(k) for k in fields})


def main() -> int:
    parser = argparse.ArgumentParser(description="Jump Model Walk-Forward λ 选参")
    parser.add_argument("--days", type=int, default=730)
    parser.add_argument("--train-days", type=int, default=500)
    parser.add_argument("--val-days", type=int, default=60)
    parser.add_argument("--step", type=int, default=20)
    parser.add_argument(
        "--candidates",
        type=str,
        default="5,10,15,20,25,30,35,40",
        help="候选 λ（逗号分隔）",
    )
    parser.add_argument(
        "--backend",
        choices=("auto", "jumpmodels", "simple"),
        default="simple",
    )
    parser.add_argument(
        "--output",
        type=str,
        default="data/lambda_timeline.csv",
        help="逐日 λ 时间线 CSV",
    )
    parser.add_argument(
        "--windows-output",
        type=str,
        default="data/lambda_windows.csv",
        help="窗口摘要 CSV",
    )
    parser.add_argument("--persist", action="store_true", help="写入 jump_lambda_walkforward 表")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()

    candidates = tuple(float(x.strip()) for x in args.candidates.split(",") if x.strip())
    backend_dir = Path(__file__).resolve().parents[1]
    out_path = backend_dir / args.output
    win_path = backend_dir / args.windows_output

    conn = sqlite3.connect(config.DB_PATH, timeout=120)
    run_migrations(conn)

    report = walkforward_tune_lambda(
        conn,
        days=max(560, min(args.days, 730)),
        train_days=args.train_days,
        val_days=args.val_days,
        step_days=args.step,
        candidates=candidates,
        backend=args.backend,
    )

    if report.get("error"):
        conn.close()
        print(report["error"], file=sys.stderr)
        return 1

    _write_csv(out_path, report.get("timeline") or [])
    _write_windows_csv(win_path, report.get("windows") or [])

    if args.persist:
        report["persisted_rows"] = persist_lambda_timeline(conn, report)

    conn.close()

    report["outputs"] = {
        "timeline_csv": str(out_path),
        "windows_csv": str(win_path),
    }

    if args.json:
        slim = {k: v for k, v in report.items() if k != "windows"}
        slim["windows"] = [
            {kk: vv for kk, vv in w.items() if kk != "trials"}
            for w in (report.get("windows") or [])
        ]
        print(json.dumps(slim, ensure_ascii=False, indent=2))
    else:
        print(format_walkforward_report_text(report))
        print(f"\n时间线: {out_path}")
        print(f"窗口表: {win_path}")
        if report.get("persisted_rows") is not None:
            print(f"落库: {report['persisted_rows']} 行")

    return 0


if __name__ == "__main__":
    sys.exit(main())
