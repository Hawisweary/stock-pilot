"""批量补齐 financial_reports — 快路径 + 多线程并发

用法:
    python scripts/batch_fetch_financials.py             # 只跑缺失的股票
    python scripts/batch_fetch_financials.py --all       # 全部重跑
    python scripts/batch_fetch_financials.py --workers 8 # 指定并发数（默认8）
    python scripts/batch_fetch_financials.py --limit 100 # 只跑前N只（测试用）
"""
from __future__ import annotations

import argparse
import sqlite3
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from threading import Lock

sys.path.insert(0, str(Path(__file__).parent.parent))
import config

DB_PATH = config.DB_PATH
_print_lock = Lock()
_counter_lock = Lock()
_stats = {"ok": 0, "skip": 0, "fail": 0, "total": 0}


def _log(msg: str) -> None:
    with _print_lock:
        print(msg, flush=True)


def _inc(key: str) -> None:
    with _counter_lock:
        _stats[key] += 1


def _fetch_one(stock_id: int, code: str, market: str) -> dict:
    """单股快路径：adata → mootdx → 东财年报兜底。每条路独立 try，互不影响。"""
    result = {"stock_id": stock_id, "code": code, "fin": 0, "ind": 0, "source": []}

    # ── 路径 1: adata (对 A 股效果最好) ──────────────────────────────────
    if market in ("A", "SH", "SZ", ""):
        try:
            from services.adata_adapter import get_core_finance, safe_float
            from services.data_processor import transform_financial_reports

            core = get_core_finance(code, count=8)
            if core:
                conn = sqlite3.connect(DB_PATH, timeout=30)
                conn.execute("PRAGMA journal_mode=WAL")
                conn.execute("PRAGMA busy_timeout=20000")
                ind_rows, rep_rows = [], []
                for c in core:
                    d = str(c.get("date", ""))[:10]
                    if not d:
                        continue
                    debt = c.get("debt_ratio")
                    ind_rows.append({
                        "stock_id": stock_id, "calc_date": d,
                        "roe": c.get("roe"), "roa": c.get("roa"),
                        "gross_margin": c.get("gross_margin"),
                        "net_margin": c.get("net_margin"),
                        "debt_to_equity": round(debt / (100 - debt), 4)
                        if debt is not None and 0 < debt < 100 else None,
                    })
                    month = d[5:7] if len(d) >= 7 else "12"
                    rep_rows.append({
                        "stock_id": stock_id,
                        "report_date": d, "period_end_date": d,
                        "report_type": "annual" if month == "12" else "quarterly",
                        "eps": c.get("eps"),
                        "net_profit_parent": None, "revenue": None,
                    })
                if ind_rows:
                    ph = ",".join(["(?,?,?,?,?,?,?)"] * len(ind_rows))
                    vals = []
                    for r in ind_rows:
                        vals += [r["stock_id"], r["calc_date"], r["roe"], r["roa"],
                                 r["gross_margin"], r["net_margin"], r["debt_to_equity"]]
                    conn.executemany(
                        "INSERT OR REPLACE INTO financial_indicators"
                        "(stock_id,calc_date,roe,roa,gross_margin,net_margin,debt_to_equity)"
                        " VALUES (?,?,?,?,?,?,?)", [
                            (r["stock_id"], r["calc_date"], r["roe"], r["roa"],
                             r["gross_margin"], r["net_margin"], r["debt_to_equity"])
                            for r in ind_rows
                        ]
                    )
                    result["ind"] = len(ind_rows)
                if rep_rows:
                    conn.executemany(
                        "INSERT OR REPLACE INTO financial_reports"
                        "(stock_id,report_date,period_end_date,report_type,eps,net_profit_parent,revenue)"
                        " VALUES (?,?,?,?,?,?,?)", [
                            (r["stock_id"], r["report_date"], r["period_end_date"],
                             r["report_type"], r["eps"], r["net_profit_parent"], r["revenue"])
                            for r in rep_rows
                        ]
                    )
                    result["fin"] = len(rep_rows)
                    result["source"].append("adata")
                conn.commit()
                conn.close()
        except Exception as e:
            pass  # 静默失败，下一条路接续

    # ── 路径 2: mootdx (A 股 + 北交所) ───────────────────────────────────
    if market in ("A", "SH", "SZ", "BJ", ""):
        try:
            from services.astock_data import sync_mootdx_financials

            r = sync_mootdx_financials(code, stock_id=stock_id)
            added = int(r.get("reports_count") or r.get("records") or 0)
            if added > 0:
                if result["fin"] == 0:
                    result["fin"] = added
                result["source"].append("mootdx")
        except Exception:
            pass

    # ── 路径 3: 东财年报兜底（仅当前两路均无数据时）─────────────────────
    if result["fin"] == 0 and market in ("A", "SH", "SZ"):
        try:
            from services import eastmoney_finance as em_fin
            from services.data_processor import transform_financial_reports
            from services.rate_limiter import wait_host

            wait_host("eastmoney")
            df, _ = None, None
            try:
                df = em_fin.fetch_profit_sheet(code, period="yearly")
            except Exception:
                pass
            if df is not None and not df.empty:
                records = transform_financial_reports(df, None, None, stock_id)
                if records:
                    conn = sqlite3.connect(DB_PATH, timeout=30)
                    conn.execute("PRAGMA journal_mode=WAL")
                    conn.execute("PRAGMA busy_timeout=20000")
                    conn.executemany(
                        "INSERT OR REPLACE INTO financial_reports"
                        "(stock_id,report_date,period_end_date,report_type,"
                        "revenue,net_profit,net_profit_parent,total_assets,total_liabilities,eps)"
                        " VALUES (?,?,?,?,?,?,?,?,?,?)",
                        [
                            (r.get("stock_id", stock_id), r.get("report_date"),
                             r.get("period_end_date"), r.get("report_type"),
                             r.get("revenue"), r.get("net_profit"),
                             r.get("net_profit_parent"), r.get("total_assets"),
                             r.get("total_liabilities"), r.get("eps"))
                            for r in records
                        ]
                    )
                    conn.commit()
                    conn.close()
                    result["fin"] = len(records)
                    result["source"].append("eastmoney")
        except Exception:
            pass

    return result


def _needs_fetch(stock_id: int, conn: sqlite3.Connection) -> bool:
    """已有 ≥4 期 financial_reports 的股票跳过。"""
    n = conn.execute(
        "SELECT COUNT(*) FROM financial_reports WHERE stock_id=?", (stock_id,)
    ).fetchone()[0]
    return n < 4


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--all", action="store_true", help="重跑全部（不跳过已有数据）")
    parser.add_argument("--workers", type=int, default=8, help="并发线程数（默认8）")
    parser.add_argument("--limit", type=int, default=0, help="最多处理N只（0=全部）")
    parser.add_argument("--market", default="", help="只处理指定市场 A/SH/SZ/BJ")
    args = parser.parse_args()

    conn = sqlite3.connect(DB_PATH)
    where = "WHERE is_active=1"
    params: list = []
    if args.market:
        where += " AND market=?"
        params.append(args.market.upper())
    stocks = conn.execute(
        f"SELECT id, code, COALESCE(market,'A') AS market FROM stocks {where} ORDER BY id",
        params,
    ).fetchall()

    if not args.all:
        stocks = [s for s in stocks if _needs_fetch(s[0], conn)]

    conn.close()

    if args.limit:
        stocks = stocks[: args.limit]

    total = len(stocks)
    _stats["total"] = total
    print(f"待处理: {total} 只  并发: {args.workers} 线程")

    t0 = time.perf_counter()
    done = 0

    with ThreadPoolExecutor(max_workers=args.workers) as pool:
        futures = {
            pool.submit(_fetch_one, s[0], s[1], s[2]): s for s in stocks
        }
        for fut in as_completed(futures):
            done += 1
            try:
                r = fut.result()
                if r["fin"] > 0 or r["ind"] > 0:
                    _inc("ok")
                    if done % 100 == 0 or r["fin"] > 0:
                        elapsed = time.perf_counter() - t0
                        rate = done / elapsed
                        eta = (total - done) / rate if rate > 0 else 0
                        _log(
                            f"[{done}/{total}] {r['code']} fin={r['fin']} ind={r['ind']}"
                            f" src={'+'.join(r['source']) or '-'}"
                            f"  速度={rate:.1f}只/s  剩余≈{eta/60:.1f}min"
                        )
                else:
                    _inc("skip")
            except Exception as e:
                _inc("fail")
                s = futures[fut]
                _log(f"[{done}/{total}] ERROR {s[1]}: {e}")

    elapsed = time.perf_counter() - t0
    print(f"\n完成  耗时={elapsed/60:.1f}min  ok={_stats['ok']}  skip={_stats['skip']}  fail={_stats['fail']}")

    # 补算 stock_v5_metrics
    print("\n补算 stock_v5_metrics...")
    try:
        from services.quality_metrics_calc import compute_all_v5_metrics
        r = compute_all_v5_metrics()
        print(f"stock_v5_metrics: {r}")
    except Exception as e:
        print(f"stock_v5_metrics 错误: {e}")

    # V5 重算
    print("\n重算 V5 综合分...")
    try:
        conn2 = sqlite3.connect(DB_PATH)
        ids = [r[0] for r in conn2.execute("SELECT id FROM stocks WHERE is_active=1").fetchall()]
        conn2.close()
        from services.v5_scorer import compute_all_v5_scores
        r2 = compute_all_v5_scores(stock_ids=ids)
        print(f"V5 重算: {r2}")
    except Exception as e:
        print(f"V5 重算错误: {e}")


if __name__ == "__main__":
    main()
