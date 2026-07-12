#!/usr/bin/env python3
"""对比 AFR_DEBATE_TWO_PHASE 开/关 — 同批股票、full LLM、no skip。"""
from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))


def _load_env() -> None:
    for env_path in (ROOT / "backend" / ".env", ROOT / ".env"):
        if not env_path.exists():
            continue
        for line in env_path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))


def _run_label(two_phase: bool, *, sample_ids: list[int] | None, limit: int) -> dict:
    import config
    import database as db

    config.DEBATE_TWO_PHASE = two_phase
    config.DEBATE_SKIP_UNCHANGED = False

    if not db.is_initialized():
        db.init()

    from services.debate_orchestrator import run_debate_batch

    stock_ids = sample_ids
    if stock_ids is None:
        import sqlite3

        conn = sqlite3.connect(config.DB_PATH)
        rows = conn.execute(
            "SELECT id FROM stocks WHERE is_active=1 ORDER BY id LIMIT ?",
            (limit,),
        ).fetchall()
        conn.close()
        stock_ids = [int(r[0]) for r in rows]

    t0 = time.perf_counter()
    out = run_debate_batch(
        mode="full",
        stock_ids=stock_ids,
        skip_unchanged=False,
        concurrency=config.DEBATE_CONCURRENCY,
        triggered_by="two_phase_benchmark",
    )
    wall_ms = int((time.perf_counter() - t0) * 1000)

    results = out.get("results") or []
    errors = [r for r in results if r.get("error")]
    ok = [r for r in results if not r.get("error") and not r.get("skipped")]

    escalated = 0
    synthetic = 0
    parse_fail = 0
    scores: dict[int, float] = {}
    for r in ok:
        sid = int(r["stock_id"])
        if r.get("adjusted_score") is not None:
            scores[sid] = float(r["adjusted_score"])
        debate = r.get("debate") or {}
        meta = debate.get("_meta") or {}
        if meta.get("judge_escalated"):
            escalated += 1
        elif meta.get("llm_phases") == 1 and two_phase:
            synthetic += 1
        if not debate.get("judge"):
            parse_fail += 1

    return {
        "two_phase": two_phase,
        "stock_count": len(stock_ids),
        "completed": len(ok),
        "errors": len(errors),
        "error_samples": errors[:3],
        "duration_ms": out.get("duration_ms", wall_ms),
        "wall_ms": wall_ms,
        "llm_count": out.get("llm_count"),
        "escalated_judge": escalated,
        "synthetic_judge": synthetic if two_phase else None,
        "missing_judge": parse_fail,
        "scores": scores,
    }


def main() -> int:
    _load_env()
    import argparse

    parser = argparse.ArgumentParser()
    parser.add_argument("--limit", type=int, default=12, help="抽样股票数（默认12，控成本）")
    parser.add_argument("--stock-ids", type=str, default=None, help="指定 id 逗号分隔")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()

    from services.llm_client import is_llm_available

    if not is_llm_available():
        print("错误: 未配置 LLM API Key，无法对比", file=sys.stderr)
        return 1

    sample_ids = None
    if args.stock_ids:
        sample_ids = [int(x.strip()) for x in args.stock_ids.split(",") if x.strip()]

    print(f"=== TWO_PHASE 对比 (full LLM, n={args.limit if not sample_ids else len(sample_ids)}) ===\n")

    single = _run_label(False, sample_ids=sample_ids, limit=args.limit)
    print(f"[单阶段] {single['duration_ms']}ms 成功={single['completed']} 失败={single['errors']}")

    two = _run_label(True, sample_ids=sample_ids, limit=args.limit)
    print(
        f"[两阶段] {two['duration_ms']}ms 成功={two['completed']} 失败={two['errors']} "
        f"escalate={two['escalated_judge']} synthetic_judge={two['synthetic_judge']}"
    )

    common = set(single["scores"]) & set(two["scores"])
    diffs: list[tuple[int, float, float, float]] = []
    for sid in sorted(common):
        a, b = single["scores"][sid], two["scores"][sid]
        diffs.append((sid, a, b, b - a))

    diffs.sort(key=lambda x: abs(x[3]), reverse=True)
    avg_abs = sum(abs(d[3]) for d in diffs) / len(diffs) if diffs else 0.0
    max_abs = max((abs(d[3]) for d in diffs), default=0.0)
    over3 = sum(1 for d in diffs if abs(d[3]) > 3)

    summary = {
        "single_phase": single,
        "two_phase_run": two,
        "score_compare": {
            "paired": len(common),
            "avg_abs_delta": round(avg_abs, 2),
            "max_abs_delta": round(max_abs, 2),
            "count_delta_gt_3": over3,
            "top_diffs": [
                {"stock_id": sid, "single": a, "two_phase": b, "delta": round(d, 2)}
                for sid, a, b, d in diffs[:8]
            ],
        },
        "recommendation": _recommend(single, two, avg_abs, over3, len(common)),
    }

    if args.json:
        # trim scores for readability
        summary["single_phase"] = {**single, "scores": f"{len(single['scores'])} entries"}
        summary["two_phase_run"] = {**two, "scores": f"{len(two['scores'])} entries"}
        print(json.dumps(summary, ensure_ascii=False, indent=2))
    else:
        print(f"\n--- 分数对比 (n={len(common)}) ---")
        print(f"平均 |Δ| = {avg_abs:.2f}  最大 |Δ| = {max_abs:.2f}  |Δ|>3 共 {over3} 股")
        for sid, a, b, d in diffs[:5]:
            print(f"  stock {sid}: 单阶段 {a:.1f} → 两阶段 {b:.1f} (Δ{d:+.1f})")
        print(f"\n>>> 建议: {summary['recommendation']}")

    return 0


def _recommend(single: dict, two: dict, avg_abs: float, over3: int, n: int) -> str:
    if single["errors"] > two["errors"] + 1:
        return "可尝试开启 TWO_PHASE（失败更少），但需全量回归"
    if two["errors"] > single["errors"] + 1:
        return "保持 TWO_PHASE=false（两阶段失败更多）"
    if n == 0:
        return "样本不足，无法结论"
    slower = two["duration_ms"] > single["duration_ms"] * 1.15
    if avg_abs > 2.5 or over3 >= max(2, n // 4):
        return "保持 TWO_PHASE=false（final_score 偏差偏大，synthetic judge 影响明显）"
    if slower and avg_abs > 1.0:
        return "保持 TWO_PHASE=false（更慢且分数有差异，性价比不高）"
    if not slower and avg_abs <= 1.5 and over3 == 0:
        return "可开启 TWO_PHASE=true（耗时与分数接近，可省 token）"
    return "保持 TWO_PHASE=false 为默认；高 LLM 成本场景可手动开启并监控 |Δ|"


if __name__ == "__main__":
    raise SystemExit(main())
