"""用 Tushare Pro 的"全市场批量"接口补齐行情/财务/估值 —— 按天/按季度循环，而不是按股票循环

跟 tushare_backfill_fundamentals.py（逐股票，5292只跑几小时）不同，这里用的是
daily/daily_basic/adj_factor/income_vip/balancesheet_vip/cashflow_vip/fina_indicator_vip
这些"全市场批量"接口 —— 一次调用返回全市场 5500+ 只股票当天/当季度的数据，
调用次数从"股票数"降到"天数/季度数"，快一个数量级。

用法:
    python3 scripts/tushare_bulk_backfill.py                       # 默认：近30个交易日行情+资金流(近10日) + 近3年财务 + 最新估值
    python3 scripts/tushare_bulk_backfill.py --quotes-days 250      # 行情回溯天数
    python3 scripts/tushare_bulk_backfill.py --skip-quotes          # 只补财务+估值+资金流
    python3 scripts/tushare_bulk_backfill.py --skip-financials       # 只补行情+估值+资金流
    python3 scripts/tushare_bulk_backfill.py --skip-fund-flow        # 跳过资金流
    python3 scripts/tushare_bulk_backfill.py --years 3               # 财务回溯年数（默认3）
"""
from __future__ import annotations

import argparse
import sqlite3
import sys
import time
from datetime import date, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
import config
from services.tushare_adapter import (
    _pro,
    fetch_market_adj_factor,
    fetch_market_daily,
    fetch_market_daily_basic,
    fetch_market_financials_vip,
    fetch_sector_fund_flow,
    code_to_ts_code,
)


def _trading_days(start: str, end: str) -> list[str]:
    pro = _pro()
    df = pro.trade_cal(exchange="SSE", start_date=start, end_date=end, is_open="1")
    return sorted(df["cal_date"].astype(str).tolist())


def _quarter_ends(years: int) -> list[str]:
    """近 N 年的季度末列表（YYYYMMDD），最新一期用当前日期兜底（未发布的季报批量接口会跳过）。"""
    today = date.today()
    ends = []
    y = today.year - years
    for yy in range(y, today.year + 1):
        for md in ("0331", "0630", "0930", "1231"):
            d = f"{yy}{md}"
            if d <= today.strftime("%Y%m%d"):
                ends.append(d)
    return ends


def _load_code_map(conn: sqlite3.Connection) -> tuple[dict[str, int], dict[str, str]]:
    """返回 (ts_code -> stock_id, ts_code -> code) 映射。"""
    rows = conn.execute(
        "SELECT id, code, COALESCE(market,'A') AS market FROM stocks WHERE is_active=1"
    ).fetchall()
    ts_to_id: dict[str, int] = {}
    ts_to_code: dict[str, str] = {}
    for stock_id, code, market in rows:
        ts_code = code_to_ts_code(code, market)
        ts_to_id[ts_code] = stock_id
        ts_to_code[ts_code] = code
    return ts_to_id, ts_to_code


def backfill_quotes(conn: sqlite3.Connection, ts_to_id: dict[str, int], days: int) -> dict:
    end = date.today().strftime("%Y%m%d")
    start = (date.today() - timedelta(days=int(days * 1.6) + 10)).strftime("%Y%m%d")  # 多留缓冲跳过非交易日
    trading_days = _trading_days(start, end)[-days:]
    print(f"行情回补: {len(trading_days)} 个交易日 ({trading_days[0]}~{trading_days[-1]})")

    # 用最新交易日的复权因子作为前复权基准（当前价格不变，历史价格按比例缩放）
    latest_factor = fetch_market_adj_factor(trading_days[-1])

    total_rows = 0
    for i, d in enumerate(trading_days):
        daily = fetch_market_daily(d)
        factor_today = fetch_market_adj_factor(d)
        basic_today = fetch_market_daily_basic(d)
        rows = []
        for ts_code, q in daily.items():
            stock_id = ts_to_id.get(ts_code)
            if not stock_id:
                continue
            f_today = factor_today.get(ts_code)
            f_latest = latest_factor.get(ts_code)
            close = q["close"]
            adj_close = (
                round(close * f_today / f_latest, 4)
                if (close is not None and f_today and f_latest)
                else close
            )
            turnover = basic_today.get(ts_code, {}).get("turnover_rate")
            trade_date_fmt = f"{d[:4]}-{d[4:6]}-{d[6:8]}"
            rows.append((
                stock_id, trade_date_fmt, q["open"], q["high"], q["low"],
                close, adj_close, q["volume"], q["amount"], q["change_pct"], turnover,
            ))
        if rows:
            conn.executemany(
                """INSERT OR REPLACE INTO stock_daily_quotes
                   (stock_id, trade_date, open, high, low, close, adj_close, volume, amount, change_pct, turnover)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
                rows,
            )
            conn.commit()
            total_rows += len(rows)
        print(f"  [{i+1}/{len(trading_days)}] {d}: {len(rows)} 只")
    return {"trading_days": len(trading_days), "rows": total_rows}


def backfill_financials(conn: sqlite3.Connection, ts_to_id: dict[str, int], years: int) -> dict:
    periods = _quarter_ends(years)
    print(f"财务回补: {len(periods)} 个报告期 ({periods[0]}~{periods[-1]})")

    total_fin = 0
    total_ind = 0
    for i, period in enumerate(periods):
        data = fetch_market_financials_vip(period)
        period_end = f"{period[:4]}-{period[4:6]}-{period[6:8]}"
        month = period[4:6]
        report_type = "annual" if month == "12" else "quarterly"

        fin_rows = []
        ind_rows = []
        for ts_code, d in data.items():
            stock_id = ts_to_id.get(ts_code)
            if not stock_id:
                continue
            report_date = d.get("report_date") or period_end
            fin_rows.append((
                stock_id, report_date, period_end, report_type,
                d.get("revenue"), d.get("net_profit"), d.get("net_profit_parent"),
                d.get("operating_profit"), d.get("total_assets"), d.get("total_liabilities"),
                d.get("total_equity"), d.get("current_assets"), d.get("current_liabilities"),
                d.get("operating_cf"), d.get("investing_cf"), d.get("financing_cf"),
                d.get("operating_revenue"), d.get("eps"), d.get("accounts_receivable"),
                d.get("rd_exp"), d.get("money_cap"), d.get("inventories"),
                d.get("goodwill"), d.get("fix_assets"),
            ))
            if d.get("roe") is not None or d.get("roa") is not None:
                ind_rows.append((
                    stock_id, period_end, d.get("roe"), d.get("roa"),
                    d.get("gross_margin"), d.get("net_margin"),
                    d.get("debt_to_equity"), d.get("current_ratio"),
                ))

        if fin_rows:
            conn.executemany(
                """INSERT OR REPLACE INTO financial_reports
                   (stock_id, report_date, period_end_date, report_type, revenue, net_profit,
                    net_profit_parent, operating_profit, total_assets, total_liabilities,
                    total_equity, current_assets, current_liabilities, operating_cf,
                    investing_cf, financing_cf, operating_revenue, eps, accounts_receivable,
                    rd_exp, money_cap, inventories, goodwill, fix_assets)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                fin_rows,
            )
        if ind_rows:
            conn.executemany(
                """INSERT OR REPLACE INTO financial_indicators
                   (stock_id, calc_date, roe, roa, gross_margin, net_margin, debt_to_equity, current_ratio)
                   VALUES (?,?,?,?,?,?,?,?)""",
                ind_rows,
            )
        conn.commit()
        total_fin += len(fin_rows)
        total_ind += len(ind_rows)
        print(f"  [{i+1}/{len(periods)}] {period}: fin={len(fin_rows)} indicators={len(ind_rows)}")
    return {"periods": len(periods), "fin_rows": total_fin, "indicator_rows": total_ind}


def backfill_fund_flow(conn: sqlite3.Connection, ts_to_id: dict[str, int], days: int) -> dict:
    from services.tushare_adapter import fetch_market_fund_flow

    end = date.today().strftime("%Y%m%d")
    start = (date.today() - timedelta(days=int(days * 1.6) + 10)).strftime("%Y%m%d")
    trading_days = _trading_days(start, end)[-days:]
    print(f"资金流回补: {len(trading_days)} 个交易日 ({trading_days[0]}~{trading_days[-1]})")

    total_rows = 0
    for i, d in enumerate(trading_days):
        data = fetch_market_fund_flow(d)
        trade_date_fmt = f"{d[:4]}-{d[4:6]}-{d[6:8]}"
        rows = []
        for ts_code, v in data.items():
            stock_id = ts_to_id.get(ts_code)
            if not stock_id:
                continue
            rows.append((
                stock_id, trade_date_fmt, v.get("main_net_inflow"),
                v.get("super_large_inflow"), "tushare",
            ))
        if rows:
            conn.executemany(
                """INSERT OR REPLACE INTO stock_fund_flow_daily
                   (stock_id, trade_date, main_net_inflow, super_large_inflow, source)
                   VALUES (?,?,?,?,?)""",
                rows,
            )
            conn.commit()
            total_rows += len(rows)
        print(f"  [{i+1}/{len(trading_days)}] {d}: {len(rows)} 只")

    # 滚动算 main_net_5d（近5个交易日主力净流入汇总）—— capital_tier_v5 读的是这个
    # 聚合字段，不是单日 main_net_inflow，之前批量写入漏了这一步导致 capital_score
    # 覆盖率长期只有个位数百分比。
    stock_ids = list(ts_to_id.values())
    if stock_ids:
        ph = ",".join("?" * len(stock_ids))
        conn.execute(
            f"""
            WITH ranked AS (
                SELECT rowid AS rid,
                       SUM(main_net_inflow) OVER (
                           PARTITION BY stock_id ORDER BY trade_date
                           ROWS BETWEEN 4 PRECEDING AND CURRENT ROW
                       ) AS net5
                FROM stock_fund_flow_daily
                WHERE stock_id IN ({ph})
            )
            UPDATE stock_fund_flow_daily
            SET main_net_5d = (SELECT net5 FROM ranked WHERE ranked.rid = stock_fund_flow_daily.rowid)
            WHERE rowid IN (SELECT rid FROM ranked)
            """,
            stock_ids,
        )
        conn.commit()
    print("main_net_5d 滚动汇总已更新")

    return {"trading_days": len(trading_days), "rows": total_rows}


def backfill_sector_fund_flow(conn: sqlite3.Connection) -> dict:
    """申万一级行业板块资金流 + 20日相对强弱（替代长期停滞的东财板块资金流同步）。"""
    from services.tushare_adapter import latest_trading_date

    trade_date = latest_trading_date(date.today().strftime("%Y%m%d"))
    print(f"行业板块资金流: 最近交易日 {trade_date}")
    boards = fetch_sector_fund_flow(trade_date)
    trade_date_fmt = f"{trade_date[:4]}-{trade_date[4:6]}-{trade_date[6:8]}"

    rows = [
        (b["sector_code"], b["sector_name"], trade_date_fmt, b["net_inflow"],
         b["net_inflow_pct"], b["change_pct"], b["rs_csi300_20d"], "tushare")
        for b in boards
    ]
    if rows:
        conn.executemany(
            """INSERT OR REPLACE INTO sector_fund_flow_daily
               (sector_code, sector_name, trade_date, net_inflow, net_inflow_pct,
                change_pct, rs_csi300_20d, source)
               VALUES (?,?,?,?,?,?,?,?)""",
            rows,
        )
        conn.commit()
    print(f"  {len(rows)} 个行业板块")
    return {"trade_date": trade_date_fmt, "rows": len(rows)}


def backfill_suspend(conn: sqlite3.Connection, ts_to_id: dict[str, int], days: int) -> dict:
    """用 Tushare 官方停牌名单校准 is_suspended（原本只靠成交量=0 启发式判断）。"""
    from services.tushare_adapter import fetch_market_suspend

    end = date.today().strftime("%Y%m%d")
    start = (date.today() - timedelta(days=int(days * 1.6) + 10)).strftime("%Y%m%d")
    trading_days = _trading_days(start, end)[-days:]
    print(f"停牌校准: {len(trading_days)} 个交易日 ({trading_days[0]}~{trading_days[-1]})")

    id_to_code = {v: k for k, v in ts_to_id.items()}  # 仅用于日志，非必须
    total_marked = 0
    for i, d in enumerate(trading_days):
        suspended_ts_codes = fetch_market_suspend(d)
        trade_date_fmt = f"{d[:4]}-{d[4:6]}-{d[6:8]}"
        stock_ids = [ts_to_id[ts] for ts in suspended_ts_codes if ts in ts_to_id]
        if stock_ids:
            conn.executemany(
                """UPDATE stock_daily_quotes SET is_suspended=1
                   WHERE stock_id=? AND trade_date=?""",
                [(sid, trade_date_fmt) for sid in stock_ids],
            )
            conn.commit()
            total_marked += len(stock_ids)
        print(f"  [{i+1}/{len(trading_days)}] {d}: {len(stock_ids)} 只停牌")
    return {"trading_days": len(trading_days), "marked": total_marked}


def backfill_valuation(conn: sqlite3.Connection, ts_to_id: dict[str, int]) -> dict:
    from services.tushare_adapter import latest_trading_date

    trade_date = latest_trading_date(date.today().strftime("%Y%m%d"))
    print(f"估值快照: 最近交易日 {trade_date}")
    data = fetch_market_daily_basic(trade_date)
    today_str = date.today().strftime("%Y-%m-%d")

    rows = []
    for ts_code, v in data.items():
        stock_id = ts_to_id.get(ts_code)
        if not stock_id:
            continue
        rows.append((
            stock_id, today_str, v.get("pe_ttm"), v.get("pb"),
            v.get("market_cap"), v.get("dividend_yield"), "tushare", v.get("ps_ttm"),
            v.get("pe"), v.get("turnover_rate"), v.get("turnover_rate_f"),
            v.get("volume_ratio"), v.get("dividend_yield_ttm"),
            v.get("total_share"), v.get("float_share"), v.get("free_share"),
            v.get("limit_status"),
        ))
    if rows:
        conn.executemany(
            """INSERT OR REPLACE INTO valuation_snapshots
               (stock_id, as_of_date, pe_ttm, pb, market_cap, dividend_yield, source, ps_ratio,
                pe, turnover_rate, turnover_rate_f, volume_ratio, dividend_yield_ttm,
                total_share, float_share, free_share, limit_status)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            rows,
        )
        conn.commit()
    return {"rows": len(rows)}


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--quotes-days", type=int, default=30, help="行情回溯交易日数（默认30）")
    parser.add_argument("--years", type=int, default=3, help="财务回溯年数（默认3）")
    parser.add_argument("--skip-quotes", action="store_true")
    parser.add_argument("--skip-financials", action="store_true")
    parser.add_argument("--skip-valuation", action="store_true")
    parser.add_argument("--skip-fund-flow", action="store_true")
    parser.add_argument("--fund-flow-days", type=int, default=10, help="资金流回溯交易日数（默认10）")
    parser.add_argument("--skip-suspend", action="store_true")
    parser.add_argument("--suspend-days", type=int, default=30, help="停牌校准回溯交易日数（默认30，跟quotes-days对齐）")
    parser.add_argument("--skip-sector-flow", action="store_true")
    args = parser.parse_args()

    conn = sqlite3.connect(config.DB_PATH, timeout=60)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=60000")
    ts_to_id, _ = _load_code_map(conn)
    print(f"本地活跃股票: {len(ts_to_id)} 只\n")

    t0 = time.perf_counter()
    summary = {}

    if not args.skip_quotes:
        summary["quotes"] = backfill_quotes(conn, ts_to_id, args.quotes_days)
        print()
    if not args.skip_financials:
        summary["financials"] = backfill_financials(conn, ts_to_id, args.years)
        print()
    if not args.skip_valuation:
        summary["valuation"] = backfill_valuation(conn, ts_to_id)
        print()
    if not args.skip_fund_flow:
        summary["fund_flow"] = backfill_fund_flow(conn, ts_to_id, args.fund_flow_days)
        print()
    if not args.skip_suspend:
        summary["suspend"] = backfill_suspend(conn, ts_to_id, args.suspend_days)
        print()
    if not args.skip_sector_flow:
        summary["sector_fund_flow"] = backfill_sector_fund_flow(conn)
        print()

    conn.close()
    elapsed = time.perf_counter() - t0
    print(f"全部完成，耗时 {elapsed/60:.1f}min")
    print(summary)


if __name__ == "__main__":
    main()
