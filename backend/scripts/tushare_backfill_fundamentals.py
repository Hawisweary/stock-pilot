"""用 Tushare Pro 批量补齐/替换：复权行情、财务三表、财务指标、估值快照

覆盖此前 4 套拼凑管道：
- stock_daily_quotes（原腾讯+yfinance拼凑）-> daily + adj_factor 官方前复权
- financial_reports（原adata/eastmoney/mootdx三路，缺operating_cf）-> income+balancesheet+cashflow
- financial_indicators（原adata算)-> fina_indicator 官方口径
- valuation_snapshots（原腾讯实时拼凑）-> daily_basic

用法:
    python3 scripts/tushare_backfill_fundamentals.py                  # 全量5292只
    python3 scripts/tushare_backfill_fundamentals.py --limit 50        # 测试用
    python3 scripts/tushare_backfill_fundamentals.py --workers 4        # 并发（默认4，Tushare有分钟频控，共享节流锁）
    python3 scripts/tushare_backfill_fundamentals.py --skip-quotes      # 只补财务+估值，跳过行情
    python3 scripts/tushare_backfill_fundamentals.py --start-date 20230101
"""
from __future__ import annotations

import argparse
import sqlite3
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date
from pathlib import Path
from threading import Lock

sys.path.insert(0, str(Path(__file__).parent.parent))
import config
from services.tushare_adapter import (
    code_to_ts_code,
    fetch_daily_adjusted,
    fetch_daily_basic,
    fetch_fina_indicator,
    fetch_financial_reports,
    latest_trading_date,
)

DB_PATH = config.DB_PATH
_print_lock = Lock()
_counter_lock = Lock()
_stats = {"quotes_ok": 0, "fin_ok": 0, "indicator_ok": 0, "valuation_ok": 0, "fail": 0, "total": 0}


def _log(msg: str) -> None:
    with _print_lock:
        print(msg, flush=True)


def _inc(key: str) -> None:
    with _counter_lock:
        _stats[key] += 1


def _process_one(
    stock_id: int,
    code: str,
    market: str,
    start_date: str,
    end_date: str,
    valuation_trade_date: str,
    skip_quotes: bool,
) -> dict:
    ts_code = code_to_ts_code(code, market)
    result = {"code": code, "quotes": 0, "fin": 0, "indicators": 0, "valuation": False, "error": None}
    conn = sqlite3.connect(DB_PATH, timeout=60)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=60000")
    try:
        # 1. 复权行情
        if not skip_quotes:
            rows = fetch_daily_adjusted(ts_code, start_date, end_date)
            if rows:
                # 口径A(#60):OHLC 全部存前复权 qfq,close=adj_close,与腾讯行/live 路径一致
                conn.executemany(
                    """INSERT OR REPLACE INTO stock_daily_quotes
                       (stock_id, trade_date, open, high, low, close, adj_close, volume, amount, change_pct)
                       VALUES (?,?,?,?,?,?,?,?,?,?)""",
                    [
                        (stock_id, r["trade_date"], r["adj_open"], r["adj_high"], r["adj_low"],
                         r["adj_close"], r["adj_close"], r["volume"], r["amount"], r["change_pct"])
                        for r in rows
                    ],
                )
                result["quotes"] = len(rows)

        # 2. 财务三表
        fin_rows = fetch_financial_reports(ts_code, start_date, end_date)
        if fin_rows:
            conn.executemany(
                """INSERT OR REPLACE INTO financial_reports
                   (stock_id, report_date, period_end_date, report_type, revenue, net_profit,
                    net_profit_parent, operating_profit, total_assets, total_liabilities,
                    total_equity, current_assets, current_liabilities, operating_cf,
                    investing_cf, financing_cf)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                [
                    (stock_id, r["report_date"], r["period_end_date"], r["report_type"],
                     r.get("revenue"), r.get("net_profit"), r.get("net_profit_parent"),
                     r.get("operating_profit"), r.get("total_assets"), r.get("total_liabilities"),
                     r.get("total_equity"), r.get("current_assets"), r.get("current_liabilities"),
                     r.get("operating_cf"), r.get("investing_cf"), r.get("financing_cf"))
                    for r in fin_rows
                ],
            )
            result["fin"] = len(fin_rows)

        # 3. 财务指标
        ind_rows = fetch_fina_indicator(ts_code, start_date, end_date)
        if ind_rows:
            conn.executemany(
                """INSERT OR REPLACE INTO financial_indicators
                   (stock_id, calc_date, roe, roa, gross_margin, net_margin, debt_to_equity, current_ratio)
                   VALUES (?,?,?,?,?,?,?,?)""",
                [
                    (stock_id, r["calc_date"], r.get("roe"), r.get("roa"),
                     r.get("gross_margin"), r.get("net_margin"),
                     r.get("debt_to_equity"), r.get("current_ratio"))
                    for r in ind_rows
                ],
            )
            result["indicators"] = len(ind_rows)

        # 4. 估值快照（当前最新交易日）
        val = fetch_daily_basic(ts_code, valuation_trade_date)
        if val:
            today_str = date.today().strftime("%Y-%m-%d")
            conn.execute(
                """INSERT OR REPLACE INTO valuation_snapshots
                   (stock_id, as_of_date, pe_ttm, pb, market_cap, dividend_yield, source, ps_ratio)
                   VALUES (?,?,?,?,?,?,?,?)""",
                (stock_id, today_str, val.get("pe_ttm"), val.get("pb"),
                 val.get("market_cap"), val.get("dividend_yield"), "tushare", val.get("ps_ttm")),
            )
            result["valuation"] = True

        conn.commit()
    except Exception as e:
        result["error"] = str(e)[:200]
    finally:
        conn.close()
    return result


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--workers", type=int, default=4, help="并发线程数（默认4，共享节流锁不会超频）")
    parser.add_argument("--limit", type=int, default=0, help="最多处理N只（0=全部，测试用）")
    parser.add_argument("--start-date", default="20230101", help="起始日期 YYYYMMDD（默认20230101，覆盖近3年）")
    parser.add_argument("--skip-quotes", action="store_true", help="跳过行情，只补财务+估值")
    parser.add_argument("--market", default="", help="只处理指定市场 A/SH/SZ/BJ")
    args = parser.parse_args()

    end_date = date.today().strftime("%Y%m%d")
    valuation_trade_date = latest_trading_date(end_date) or end_date
    print(f"估值快照使用最近交易日: {valuation_trade_date}")

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
    conn.close()

    if args.limit:
        stocks = stocks[: args.limit]

    total = len(stocks)
    _stats["total"] = total
    print(f"待处理: {total} 只  并发: {args.workers} 线程  区间: {args.start_date}~{end_date}")

    t0 = time.perf_counter()
    done = 0

    with ThreadPoolExecutor(max_workers=args.workers) as pool:
        futures = {
            pool.submit(_process_one, s[0], s[1], s[2], args.start_date, end_date, valuation_trade_date, args.skip_quotes): s
            for s in stocks
        }
        for fut in as_completed(futures):
            done += 1
            s = futures[fut]
            try:
                r = fut.result()
                if r["error"]:
                    _inc("fail")
                    _log(f"[{done}/{total}] ERROR {r['code']}: {r['error']}")
                else:
                    if r["quotes"]:
                        _inc("quotes_ok")
                    if r["fin"]:
                        _inc("fin_ok")
                    if r["indicators"]:
                        _inc("indicator_ok")
                    if r["valuation"]:
                        _inc("valuation_ok")
            except Exception as e:
                _inc("fail")
                _log(f"[{done}/{total}] ERROR {s[1]}: {e}")

            if done % 50 == 0 or done == total:
                elapsed = time.perf_counter() - t0
                rate = done / elapsed if elapsed > 0 else 0
                eta = (total - done) / rate if rate > 0 else 0
                _log(
                    f"[{done}/{total}] quotes={_stats['quotes_ok']} fin={_stats['fin_ok']}"
                    f" indicators={_stats['indicator_ok']} valuation={_stats['valuation_ok']}"
                    f" fail={_stats['fail']}  速度={rate:.2f}只/s  剩余≈{eta/60:.1f}min"
                )

    elapsed = time.perf_counter() - t0
    print(f"\n完成  耗时={elapsed/60:.1f}min")
    print(
        f"quotes={_stats['quotes_ok']}  fin={_stats['fin_ok']}"
        f"  indicators={_stats['indicator_ok']}  valuation={_stats['valuation_ok']}"
        f"  fail={_stats['fail']}"
    )


if __name__ == "__main__":
    main()
