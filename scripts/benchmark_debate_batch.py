#!/usr/bin/env python3
"""辩论批量 benchmark — dry-run 计划 + 可选实跑对比。"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))


def _load_env() -> None:
    env_path = ROOT / "backend" / ".env"
    if not env_path.exists():
        return
    for line in env_path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))


def _run_mode(mode: str, *, execute: bool, concurrency: int | None) -> dict:
    import database as db
    from services.debate_orchestrator import run_debate_batch

    if not db.is_initialized():
        db.init()

    t0 = time.perf_counter()
    if execute:
        out = run_debate_batch(mode=mode, concurrency=concurrency, skip_unchanged=True)
    else:
        out = run_debate_batch(mode=mode, dry_run=True, skip_unchanged=True)
    out["wall_ms"] = int((time.perf_counter() - t0) * 1000)
    out["benchmark_mode"] = mode
    out["executed"] = execute
    return out


def main() -> None:
    _load_env()
    parser = argparse.ArgumentParser(description="辩论批量 benchmark")
    parser.add_argument(
        "--modes",
        default="tiered,full",
        help="逗号分隔: tiered,full,changed_only",
    )
    parser.add_argument("--execute", action="store_true", help="实跑（默认仅 dry-run）")
    parser.add_argument("--concurrency", type=int, default=None)
    parser.add_argument("--json", action="store_true", dest="as_json")
    args = parser.parse_args()

    modes = [m.strip() for m in args.modes.split(",") if m.strip()]
    reports: list[dict] = []
    for mode in modes:
        reports.append(_run_mode(mode, execute=args.execute, concurrency=args.concurrency))

    if args.as_json:
        print(json.dumps(reports, ensure_ascii=False, indent=2))
        return

    print("=== Debate Batch Benchmark ===")
    for r in reports:
        plan = r if r.get("dry_run") else r.get("plan", r)
        print(
            f"\n[{r['benchmark_mode']}] executed={r['executed']} wall={r['wall_ms']}ms"
        )
        print(
            f"  total={plan.get('total')} run={plan.get('to_run')} "
            f"llm={plan.get('llm_count')} light={plan.get('light_count')} "
            f"skipped={plan.get('skipped')} est={plan.get('est_ms')}ms"
        )
        if r.get("executed"):
            print(
                f"  completed={r.get('completed')} errors={len(r.get('errors') or [])} "
                f"duration={r.get('duration_ms')}ms retries={r.get('batch_retry_passes')}"
            )


if __name__ == "__main__":
    main()
