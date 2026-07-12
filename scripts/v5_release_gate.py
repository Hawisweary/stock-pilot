#!/usr/bin/env python3
"""
v5_release_gate.py — v3.0 发布门禁（三门禁）

用法：
    # 生成基线
    python scripts/snapshot_v5_scores.py --out docs/reconciliation/baseline_20260621.json

    # 升级后跑门禁
    python scripts/v5_release_gate.py \\
        --before docs/reconciliation/baseline_20260621.json \\
        --after  docs/reconciliation/baseline_20260701.json

    # 携带已审批 allowlist 重跑
    python scripts/v5_release_gate.py \\
        --before docs/reconciliation/baseline_20260621.json \\
        --after  docs/reconciliation/baseline_20260701.json \\
        --allowlist docs/reconciliation/allowlist_jump_APPROVED.csv

退出码：
    0 — 全部门禁通过
    1 — 至少一项门禁失败
"""

import argparse
import csv
import json
import os
import sys
from datetime import date
from typing import Optional

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
RECON_DIR = os.path.join(BASE_DIR, "docs", "reconciliation")

# ── 门禁阈值 ──────────────────────────────────────────────────────────────
G_RANK_TOP_N = 10          # G-Rank：检查 Top-N 排名
G_RANK_MAX_SHIFT = 3       # G-Rank：单股最大允许排名偏移
G_RANK_MAX_VIOLATORS = 3   # G-Rank：允许超出的股票数
G_DELTA_MAX = 15.0         # G-Delta：单股最大允许分数跳变（绝对值）
G_DELTA_MAX_VIOLATORS = 5  # G-Delta：允许超出的股票数（须在 allowlist 内）
G_VETO_MAX_PP = 5.0        # G-Veto：veto 覆盖率最大允许变化（百分点）
G_VETO_MAX_EXCLUDE_PCT = 10.0  # G-Veto：exclude 数量最大允许变化百分比

# reason_code 可自动批准的阈值
AUTO_APPROVE_KNOWN_REASONS = {
    "missing_dim_fixed",
    "pe_capped",
    "loss_company_tagged",
    "veto_changed",
}
AUTO_APPROVE_MAX_DELTA = 25.0  # 已知原因且 |delta| ≤ 此值可自动批准


# ── 工具函数 ─────────────────────────────────────────────────────────────

def load_snapshot(path: str) -> dict:
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def index_by_code(snapshot: dict) -> dict[str, dict]:
    return {s["code"]: s for s in snapshot["stocks"]}


def load_allowlist(path: Optional[str]) -> set[str]:
    """返回已批准的 code 集合"""
    if not path or not os.path.exists(path):
        return set()
    approved = set()
    with open(path, encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for row in reader:
            if row.get("approved", "").lower() in ("true", "1", "yes"):
                approved.add(row["code"])
    return approved


def infer_reason_code(code: str, before: dict, after: dict) -> str:
    """从 v5_breakdown 推断跳变原因"""
    bd_before = before.get("v5_breakdown") or {}
    bd_after = after.get("v5_breakdown") or {}

    # PE 截断
    val_after = bd_after.get("valuation", {}) or {}
    if val_after.get("pe_capped") or val_after.get("loss_company"):
        return "pe_capped" if val_after.get("pe_capped") else "loss_company_tagged"

    # 缺维度修复（after 比 before 多了有效维度）
    missing_before = {k for k, v in bd_before.items() if isinstance(v, dict) and v.get("status") in ("missing", "skipped")}
    missing_after = {k for k, v in bd_after.items() if isinstance(v, dict) and v.get("status") in ("missing", "skipped")}
    if missing_before - missing_after:
        return "missing_dim_fixed"

    # veto 状态变化
    if before.get("veto_status") != after.get("veto_status"):
        return "veto_changed"

    return "unknown"


# ── 三门禁 ────────────────────────────────────────────────────────────────

def gate_rank(before_idx: dict, after_idx: dict) -> tuple[bool, list[str]]:
    """G-Rank：Top-N 排名变化"""
    before_top = sorted(
        [s for s in before_idx.values() if s["score"] is not None],
        key=lambda s: s["score"], reverse=True
    )[:G_RANK_TOP_N]

    after_top = sorted(
        [s for s in after_idx.values() if s["score"] is not None],
        key=lambda s: s["score"], reverse=True
    )[:G_RANK_TOP_N]

    before_rank = {s["code"]: i + 1 for i, s in enumerate(before_top)}
    after_rank = {s["code"]: i + 1 for i, s in enumerate(after_top)}

    violators = []
    all_codes = set(before_rank) | set(after_rank)
    for code in all_codes:
        r_before = before_rank.get(code, G_RANK_TOP_N + 5)
        r_after = after_rank.get(code, G_RANK_TOP_N + 5)
        shift = abs(r_after - r_before)
        if shift > G_RANK_MAX_SHIFT:
            name = (before_idx.get(code) or after_idx.get(code, {})).get("name", "")
            violators.append(f"  {code}({name}): {r_before}→{r_after} (Δ{shift})")

    passed = len(violators) <= G_RANK_MAX_VIOLATORS
    return passed, violators


def gate_delta(
    before_idx: dict,
    after_idx: dict,
    allowlist: set[str],
) -> tuple[bool, list[str], list[dict]]:
    """G-Delta：分数跳变检测；返回 (passed, violations, draft_allowlist_rows)"""
    common = set(before_idx) & set(after_idx)
    violations = []
    draft_rows = []

    for code in common:
        b = before_idx[code]
        a = after_idx[code]
        if b["score"] is None or a["score"] is None:
            continue
        delta = a["score"] - b["score"]
        if abs(delta) > G_DELTA_MAX:
            reason = infer_reason_code(code, b, a)
            auto_ok = (
                reason in AUTO_APPROVE_KNOWN_REASONS
                and abs(delta) <= AUTO_APPROVE_MAX_DELTA
            )
            draft_rows.append(
                {
                    "stock_id": a["stock_id"],
                    "code": code,
                    "name": a.get("name", ""),
                    "score_before": round(b["score"], 2),
                    "score_after": round(a["score"], 2),
                    "delta": round(delta, 2),
                    "reason_code": reason,
                    "reason_detail": _reason_detail(reason, b, a),
                    "auto_approve": "true" if auto_ok else "false",
                    "approved": "true" if auto_ok else "false",
                }
            )
            if code not in allowlist:
                violations.append(
                    f"  {code}({a.get('name','')}): {b['score']:.1f}→{a['score']:.1f}"
                    f" (Δ{delta:+.1f}) reason={reason}"
                )

    passed = len(violations) <= G_DELTA_MAX_VIOLATORS
    return passed, violations, draft_rows


def _reason_detail(reason: str, before: dict, after: dict) -> str:
    bd = after.get("v5_breakdown") or {}
    if reason == "pe_capped":
        return f"valuation.pe_capped=true, bd={json.dumps(bd.get('valuation', {}), ensure_ascii=False)}"
    if reason == "loss_company_tagged":
        return "EPS≤0, 标记为亏损企业，PE 不参与分位"
    if reason == "missing_dim_fixed":
        bd_b = before.get("v5_breakdown") or {}
        fixed = [k for k, v in (bd_b or {}).items()
                 if isinstance(v, dict) and v.get("status") in ("missing", "skipped")
                 and (bd.get(k) or {}).get("status") == "ok"]
        return f"修复维度: {fixed}"
    if reason == "veto_changed":
        return f"veto: {before.get('veto_status')} → {after.get('veto_status')}"
    return "未知原因，需人工审查"


def gate_veto(before_idx: dict, after_idx: dict) -> tuple[bool, list[str]]:
    """G-Veto：veto 覆盖率稳定性"""
    def veto_stats(idx: dict):
        total = len(idx)
        excluded = sum(1 for s in idx.values() if s.get("veto_status") == "excluded")
        coverage = (excluded / total * 100) if total else 0.0
        return total, excluded, coverage

    b_total, b_excl, b_cov = veto_stats(before_idx)
    a_total, a_excl, a_cov = veto_stats(after_idx)

    delta_pp = abs(a_cov - b_cov)
    excl_change_pct = (
        abs(a_excl - b_excl) / max(b_excl, 1) * 100 if b_excl > 0 else 0.0
    )

    violations = []
    if delta_pp > G_VETO_MAX_PP:
        violations.append(
            f"  veto 覆盖率变化: {b_cov:.1f}%→{a_cov:.1f}% (Δ{delta_pp:.1f}pp > {G_VETO_MAX_PP}pp)"
        )
    if excl_change_pct > G_VETO_MAX_EXCLUDE_PCT:
        violations.append(
            f"  exclude 数量变化: {b_excl}→{a_excl} ({excl_change_pct:.1f}% > {G_VETO_MAX_EXCLUDE_PCT}%)"
        )

    return len(violations) == 0, violations


# ── allowlist 草稿写入 ─────────────────────────────────────────────────────

def write_draft_allowlist(rows: list[dict]) -> str:
    today = date.today().strftime("%Y%m%d")
    path = os.path.join(RECON_DIR, f"allowlist_jump_DRAFT_{today}.csv")
    os.makedirs(RECON_DIR, exist_ok=True)

    fieldnames = [
        "stock_id", "code", "name",
        "score_before", "score_after", "delta",
        "reason_code", "reason_detail",
        "auto_approve", "approved",
    ]
    with open(path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)

    return path


# ── 主函数 ────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="v3.0 V5-only 发布门禁（三门禁）")
    parser.add_argument("--before", required=True, help="升级前基线 JSON（snapshot_v5_scores.py 生成）")
    parser.add_argument("--after", required=True, help="升级后快照 JSON")
    parser.add_argument("--allowlist", default=None, help="已审批 allowlist CSV 路径")
    args = parser.parse_args()

    before = load_snapshot(args.before)
    after = load_snapshot(args.after)
    before_idx = index_by_code(before)
    after_idx = index_by_code(after)
    allowlist = load_allowlist(args.allowlist)

    print(f"\n{'='*60}")
    print(f"  v3.0 发布门禁  |  before={args.before}  after={args.after}")
    print(f"{'='*60}")
    print(f"  基线股票数: {before['total_captured']}  当前股票数: {after['total_captured']}")
    if allowlist:
        print(f"  已批准 allowlist: {len(allowlist)} 只")
    print()

    results = {}

    # G-Rank
    rank_ok, rank_v = gate_rank(before_idx, after_idx)
    results["G-Rank"] = rank_ok
    status = "✅ PASS" if rank_ok else "❌ FAIL"
    print(f"[G-Rank]  Top{G_RANK_TOP_N} 排名稳定性  {status}")
    if rank_v:
        print(f"  违规（≤{G_RANK_MAX_VIOLATORS} 只可过）共 {len(rank_v)} 只：")
        for v in rank_v:
            print(v)
    print()

    # G-Delta
    delta_ok, delta_v, draft_rows = gate_delta(before_idx, after_idx, allowlist)
    results["G-Delta"] = delta_ok
    status = "✅ PASS" if delta_ok else "❌ FAIL"
    print(f"[G-Delta] 分数跳变检测（|Δ|>{G_DELTA_MAX}）  {status}")
    if delta_v:
        print(f"  未在 allowlist 的违规共 {len(delta_v)} 只（≤{G_DELTA_MAX_VIOLATORS} 可过）：")
        for v in delta_v:
            print(v)
    if draft_rows:
        draft_path = write_draft_allowlist(draft_rows)
        print(f"\n  [DRAFT] allowlist 草稿已生成：{draft_path}")
        auto_cnt = sum(1 for r in draft_rows if r["auto_approve"] == "true")
        manual_cnt = len(draft_rows) - auto_cnt
        print(f"  自动批准: {auto_cnt} 只  |  需人工 review: {manual_cnt} 只")
        if manual_cnt:
            print(f"  ⚠️  请检查草稿中 auto_approve=false 的行，确认后合并到 allowlist_jump_APPROVED.csv")
    print()

    # G-Veto
    veto_ok, veto_v = gate_veto(before_idx, after_idx)
    results["G-Veto"] = veto_ok
    status = "✅ PASS" if veto_ok else "❌ FAIL"
    print(f"[G-Veto]  veto 覆盖率稳定性  {status}")
    if veto_v:
        for v in veto_v:
            print(v)
    print()

    # 汇总
    all_pass = all(results.values())
    print("=" * 60)
    if all_pass:
        print("  🎉 三门禁全绿 — 可以发布 v3.0")
    else:
        failed = [k for k, v in results.items() if not v]
        print(f"  ❌ 门禁未通过: {', '.join(failed)}")
        print("  请修复上述问题后重跑 gate")
    print("=" * 60)

    sys.exit(0 if all_pass else 1)


if __name__ == "__main__":
    main()
