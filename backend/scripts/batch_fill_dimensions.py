"""
批量补全全市场各维度评分（技术面、估值、资金面、情绪面等）
用法：
  cd backend
  ../venv-quant/bin/python scripts/batch_fill_dimensions.py
  ../venv-quant/bin/python scripts/batch_fill_dimensions.py --dims technical valuation
"""
from __future__ import annotations

import argparse
import sqlite3
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
import config
from config import latest_trading_date
import database
database.init()


def active_ids() -> list[int]:
    conn = sqlite3.connect(config.DB_PATH)
    ids = [r[0] for r in conn.execute("SELECT id FROM stocks WHERE is_active=1 ORDER BY id").fetchall()]
    conn.close()
    return ids


def run_technical(ids: list[int], today: str):
    from services.batch_score_compute import compute_technical
    print(f"[技术面] 计算 {len(ids)} 只...", flush=True)
    batch_size = 500
    total_filled = 0
    total_no_src = 0
    total_failed = 0
    for i in range(0, len(ids), batch_size):
        chunk = ids[i:i + batch_size]
        r = compute_technical(chunk, today)
        filled = r.get("filled", 0)
        no_src = len(r.get("still_no_source", []))
        failed = len(r.get("failed_stocks", []))
        total_filled += filled
        total_no_src += no_src
        total_failed += failed
        print(f"  [{i+len(chunk)}/{len(ids)}] 写入={filled} 无K线={no_src} 失败={failed}", flush=True)
    print(f"[技术面] 合计 写入={total_filled} 无K线={total_no_src} 失败={total_failed}\n", flush=True)


def run_valuation(ids: list[int], today: str):
    from services.batch_score_compute import compute_valuation
    print(f"[估值] 计算 {len(ids)} 只...", flush=True)
    r = compute_valuation(ids, today)
    print(f"[估值] computed={r.get('computed',0)} synced={r.get('synced',0)}\n", flush=True)


def run_capital(ids: list[int], today: str):
    from services.batch_score_compute import compute_capital
    print(f"[资金面] 计算 {len(ids)} 只...", flush=True)
    r = compute_capital(ids, today)
    synced = r.get('synced', 0)
    print(f"[资金面] computed={r.get('computed',0)} synced={synced if isinstance(synced, int) else len(synced)}\n", flush=True)


def run_mood(ids: list[int], today: str):
    from services.batch_score_compute import compute_mood
    print(f"[情绪面] 计算 {len(ids)} 只...", flush=True)
    r = compute_mood(ids, today)
    synced = r.get('synced', 0)
    print(f"[情绪面] computed={r.get('computed',0)} synced={synced if isinstance(synced, int) else len(synced)}\n", flush=True)


def run_policy(ids: list[int], today: str):
    from services.batch_score_compute import compute_policy
    print(f"[政策面] 计算 {len(ids)} 只...", flush=True)
    r = compute_policy(ids, today)
    errors = r.get('errors', [])
    print(f"[政策面] computed={r.get('computed',0)} errors={len(errors)}\n", flush=True)


def run_v5(ids: list[int]):
    from services.v5_scorer import compute_all_v5_scores
    print(f"[V5综合] 重算 {len(ids)} 只...", flush=True)
    r = compute_all_v5_scores(stock_ids=ids)
    print(f"[V5综合] computed={r.get('computed',0)}\n", flush=True)


DIM_MAP = {
    "technical": run_technical,
    "valuation": run_valuation,
    "capital": run_capital,
    "mood": run_mood,
    "policy": run_policy,
}

ALL_DIMS = list(DIM_MAP.keys())


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--dims", nargs="+", choices=ALL_DIMS + ["all"], default=["all"])
    args = parser.parse_args()

    dims = ALL_DIMS if "all" in args.dims else args.dims
    today = latest_trading_date()
    ids = active_ids()
    print(f"活跃股票: {len(ids)}，交易日: {today}")
    print(f"计划维度: {dims}\n")

    t0 = time.time()
    for dim in dims:
        try:
            DIM_MAP[dim](ids, today)
        except Exception as e:
            print(f"[{dim}] 出错: {e}\n", flush=True)

    # 最后重算V5综合分
    print("重算 V5 综合分...", flush=True)
    run_v5(ids)

    print(f"\n全部完成，耗时 {time.time()-t0:.0f}s")


if __name__ == "__main__":
    main()
