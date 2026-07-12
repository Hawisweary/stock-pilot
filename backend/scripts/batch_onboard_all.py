"""
批量补全全市场 V5 评分数据（高效版）
策略：并发拉取财务数据到内存，再串行批量写入 DB，绕过全局 write_lock 瓶颈。

用法：
  cd backend
  ../venv-quant/bin/python scripts/batch_onboard_all.py
  ../venv-quant/bin/python scripts/batch_onboard_all.py --workers 16 --batch 200
"""
from __future__ import annotations

import argparse
import sqlite3
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
import config


def _fetch_financials_only(code: str) -> dict:
    """只做网络请求，不写 DB，返回原始数据"""
    try:
        from services.adata_adapter import get_core_finance
        core = get_core_finance(code, count=8)
        return {"code": code, "core": core, "error": None}
    except Exception as e:
        return {"code": code, "core": [], "error": str(e)[:80]}


def _write_batch(rows: list[dict], today: str) -> dict:
    """把拉到的数据写入 DB，串行执行"""
    conn = sqlite3.connect(config.DB_PATH, timeout=60)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=30000")

    code_to_id = {r[0]: r[1] for r in conn.execute("SELECT code, id FROM stocks WHERE is_active=1").fetchall()}

    ind_written = 0
    rep_written = 0
    for item in rows:
        code = item["code"]
        core = item.get("core", [])
        sid = code_to_id.get(code)
        if not sid or not core:
            continue

        for c in core:
            calc_date = str(c.get("date", ""))[:10]
            if not calc_date:
                continue
            import math
            debt = c.get("debt_ratio")
            if debt and math.isnan(debt):
                debt = None
            roe = c.get("roe")
            if roe and math.isnan(roe):
                roe = None
            roa = c.get("roa")
            if roa and math.isnan(roa):
                roa = None
            try:
                conn.execute("""
                    INSERT OR REPLACE INTO financial_indicators
                    (stock_id, calc_date, roe, roa, gross_margin, net_margin, debt_to_equity)
                    VALUES (?,?,?,?,?,?,?)
                """, (
                    sid, calc_date, roe, roa,
                    c.get("gross_margin"), c.get("net_margin"),
                    round(debt / (100 - debt), 4) if debt is not None and debt < 100 else None,
                ))
                ind_written += 1
            except Exception as e:
                print(f"  插入失败 {code}/{calc_date}: {e}")
                pass

    conn.commit()
    conn.close()
    return {"ind": ind_written, "rep": rep_written}


def _run_factor_batch(stock_ids: list[int]) -> str:
    try:
        db = sqlite3.connect(config.DB_PATH, timeout=60)
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA journal_mode=WAL")
        from services.factor_engine import FactorEngine
        FactorEngine(db).calculate_all(stock_ids)
        db.commit()
        db.close()
        return "ok"
    except Exception as e:
        return str(e)[:60]


def _run_v5_batch(stock_ids: list[int], today: str) -> int:
    try:
        from services.v5_scorer import compute_all_v5_scores
        r = compute_all_v5_scores(stock_ids=stock_ids)
        return r.get("scored", r.get("affected", len(stock_ids)))
    except Exception as e:
        print(f"  V5评分错误: {e}")
        return 0


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--workers", type=int, default=16, help="并发拉取线程数")
    parser.add_argument("--batch", type=int, default=200, help="每批股票数")
    args = parser.parse_args()

    conn = sqlite3.connect(config.DB_PATH)
    conn.row_factory = sqlite3.Row
    stocks = conn.execute("""
        SELECT s.id, s.code FROM stocks s
        WHERE s.is_active=1
          AND s.id NOT IN (SELECT DISTINCT stock_id FROM financial_indicators)
        ORDER BY s.id
    """).fetchall()
    total_active = conn.execute("SELECT COUNT(*) FROM stocks WHERE is_active=1").fetchone()[0]
    conn.close()

    unscored = [{"id": r["id"], "code": r["code"]} for r in stocks]
    total = len(unscored)
    print(f"活跃股票: {total_active}，待补全: {total}，并发: {args.workers} 线程")
    if not total:
        print("所有股票已有评分数据")
        return

    from config import latest_trading_date
    today = latest_trading_date()

    t0 = time.time()
    done = 0

    for i in range(0, total, args.batch):
        batch = unscored[i: i + args.batch]
        batch_t = time.time()

        # Step 1: 并发拉取（纯网络，不写DB）
        fetched = []
        with ThreadPoolExecutor(max_workers=args.workers) as pool:
            futs = {pool.submit(_fetch_financials_only, s["code"]): s for s in batch}
            for fut in as_completed(futs):
                fetched.append(fut.result())

        # Step 2: 串行写入DB
        write_result = _write_batch(fetched, today)

        # Step 3: 因子计算
        ok_ids = [s["id"] for s in batch]
        factor_status = _run_factor_batch(ok_ids)

        # Step 4: V5 评分
        v5_n = _run_v5_batch(ok_ids, today)

        done += len(batch)
        elapsed = time.time() - t0
        eta = (total - done) / done * elapsed if done else 0
        no_data = sum(1 for f in fetched if not f.get("core"))
        errors = sum(1 for f in fetched if f.get("error"))
        print(
            f"  [{done}/{total}] 批={time.time()-batch_t:.0f}s  "
            f"写入指标={write_result['ind']}  无数据={no_data}  错误={errors}  "
            f"因子={factor_status}  V5={v5_n}  ETA={eta:.0f}s"
        )

    print(f"\n完成：{done} 只，总耗时 {time.time()-t0:.0f}s")


if __name__ == "__main__":
    main()
