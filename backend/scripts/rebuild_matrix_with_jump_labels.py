#!/usr/bin/env python3
"""P0：用 Jump Model L1 标签重跑 L2 矩阵，并对比规则 L2。

用法:
  cd backend && python scripts/rebuild_matrix_with_jump_labels.py
  cd backend && python scripts/rebuild_matrix_with_jump_labels.py --persist-jump
  cd backend && python scripts/rebuild_matrix_with_jump_labels.py --json
"""
from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import config
from migrations import run_migrations
from services.regime_jump import fit_and_persist_full_sample
from services.regime_validation import load_jump_regime_rows
from services.strategy_regime_performance import (
    JUMP_MATRIX_SOURCE,
    compare_rule_vs_jump_matrix,
    format_matrix_compare_report_text,
    refresh_strategy_regime_matrix,
    refresh_strategy_regime_matrix_jump,
)


def main() -> int:
    parser = argparse.ArgumentParser(description="Jump L1 标签 → L2 矩阵重建 + 规则对比")
    parser.add_argument(
        "--lookback-days",
        type=int,
        default=config.REGIME_MATRIX_LOOKBACK_DAYS,
        help="Jump 标签回看天数",
    )
    parser.add_argument(
        "--backtest-days",
        type=int,
        default=config.REGIME_MATRIX_BACKTEST_DAYS,
        help="单策略回测窗口",
    )
    parser.add_argument(
        "--persist-jump",
        action="store_true",
        help="若 market_regime_jump_daily 为空，先全样本 Jump 落库",
    )
    parser.add_argument(
        "--jump-penalty",
        type=float,
        default=25.0,
        help="--persist-jump 使用的 λ（默认 25）",
    )
    parser.add_argument(
        "--refresh-rules",
        action="store_true",
        help="同时刷新规则 L2（便于 as_of_date 对齐对比）",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="只检查标签样本，不写入 Jump L2",
    )
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()

    conn = sqlite3.connect(config.DB_PATH, timeout=300)
    run_migrations(conn)

    jump_rows = load_jump_regime_rows(conn, days=args.lookback_days)
    if len(jump_rows) < 30:
        if not args.persist_jump:
            print(
                f"market_regime_jump_daily 仅 {len(jump_rows)} 天，需 ≥30。"
                " 请加 --persist-jump 先落库 Jump 标签。",
                file=sys.stderr,
            )
            conn.close()
            return 1
        print(f"Jump 标签不足 ({len(jump_rows)} 天)，执行全样本落库 λ={args.jump_penalty}…")
        persist = fit_and_persist_full_sample(
            conn,
            days=args.lookback_days,
            jump_penalty=args.jump_penalty,
            backend="simple",
        )
        if persist.get("error"):
            print(persist["error"], file=sys.stderr)
            conn.close()
            return 1
        print(f"  落库 {persist.get('persisted')} 天 · backend={persist.get('backend')}")
        jump_rows = load_jump_regime_rows(conn, days=args.lookback_days)

    print(
        f"Jump 标签: {len(jump_rows)} 天 "
        f"({jump_rows[0]['trade_date']} → {jump_rows[-1]['trade_date']})"
    )

    if args.dry_run:
        conn.close()
        print("dry-run：跳过 L2 写入")
        return 0

    if args.refresh_rules:
        print("刷新规则 L2…")
        rule_r = refresh_strategy_regime_matrix(
            conn,
            lookback_days=args.lookback_days,
            backtest_days=args.backtest_days,
        )
        if rule_r.get("error"):
            print(f"规则 L2 刷新失败: {rule_r['error']}", file=sys.stderr)
        else:
            print(f"  规则 L2: {rule_r.get('updated_cells')} cells @ {rule_r.get('as_of_date')}")

    print("重建 Jump L2 矩阵…")
    jump_r = refresh_strategy_regime_matrix_jump(
        conn,
        lookback_days=args.lookback_days,
        backtest_days=args.backtest_days,
    )
    if jump_r.get("error"):
        print(jump_r["error"], file=sys.stderr)
        conn.close()
        return 1

    print(
        f"  Jump L2: {jump_r.get('updated_cells')} cells @ {jump_r.get('as_of_date')} "
        f"(source={JUMP_MATRIX_SOURCE})"
    )

    compare = compare_rule_vs_jump_matrix(conn, as_of_date=jump_r.get("as_of_date"))
    conn.close()

    if compare.get("error"):
        print(compare["error"], file=sys.stderr)
        return 1

    if args.json:
        print(json.dumps({"jump_refresh": jump_r, "compare": compare}, ensure_ascii=False, indent=2))
    else:
        print()
        print(format_matrix_compare_report_text(compare))

    return 0


if __name__ == "__main__":
    sys.exit(main())
