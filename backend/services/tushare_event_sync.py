"""Tushare 事件类 / 筹码类 / 融资融券数据同步服务。

供 scripts/tushare_sync_event_data.py 和 v5_data_sync 调用。
"""
from __future__ import annotations

import sqlite3
import time
from datetime import date, timedelta
from typing import Any

import config
from services.tushare_adapter import (
    code_to_ts_code,
    fetch_broker_recommend,
    fetch_cyq_perf,
    fetch_holder_trade,
    fetch_margin_detail,
    fetch_pledge_detail,
    fetch_pledge_stat,
    fetch_repurchase,
    fetch_share_float,
    fetch_stk_holdernumber,
)


def _code_map(conn: sqlite3.Connection) -> dict[str, int]:
    rows = conn.execute("SELECT id, code FROM stocks").fetchall()
    return {code: sid for sid, code in rows}


def _active_stocks(conn: sqlite3.Connection, stock_ids: list[int] | None) -> list[tuple[int, str, str]]:
    if stock_ids:
        ph = ",".join("?" * len(stock_ids))
        rows = conn.execute(
            f"SELECT id, code, market FROM stocks WHERE id IN ({ph}) AND is_active=1",
            stock_ids,
        ).fetchall()
    else:
        rows = conn.execute(
            "SELECT id, code, market FROM stocks WHERE is_active=1 ORDER BY id"
        ).fetchall()
    return [(int(r[0]), r[1], r[2] or "") for r in rows]


def _strip_suffix(ts_code: str) -> str:
    return ts_code.split(".")[0]


def _insert_or_replace_many(conn: sqlite3.Connection, sql: str, rows: list[tuple]) -> None:
    if not rows:
        return
    conn.executemany(sql, rows)
    conn.commit()


# ── 事件类：按公告日期区间批量拉取 ─────────────────────────────────────────


def sync_pledge_detail(conn: sqlite3.Connection, code_map: dict[str, int], start: str, end: str) -> dict:
    data = fetch_pledge_detail(ts_code=None, start_date=start, end_date=end)
    rows: list[tuple] = []
    for r in data:
        code = _strip_suffix(r["ts_code"])
        stock_id = code_map.get(code)
        if stock_id is None:
            continue
        rows.append((
            stock_id, r["ann_date"], r["holder_name"], r["pledge_amount"], r["start_date"],
            r["end_date"], r["is_release"], r["release_date"], r["pledgor"],
            r["holding_amount"], r["pledged_amount"], r["p_total_ratio"], r["h_total_ratio"],
            r["is_buyback"],
        ))
    _insert_or_replace_many(conn, """
        INSERT OR REPLACE INTO stock_pledge_detail
        (stock_id, ann_date, holder_name, pledge_amount, start_date, end_date, is_release,
         release_date, pledgor, holding_amount, pledged_amount, p_total_ratio, h_total_ratio, is_buyback)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, rows)
    return {"rows": len(rows)}


def sync_share_float(conn: sqlite3.Connection, code_map: dict[str, int], start: str, end: str) -> dict:
    data = fetch_share_float(ts_code=None, start_date=start, end_date=end)
    rows: list[tuple] = []
    for r in data:
        code = _strip_suffix(r["ts_code"])
        stock_id = code_map.get(code)
        if stock_id is None:
            continue
        rows.append((
            stock_id, r["ann_date"], r["float_date"], r["float_share"], r["float_ratio"],
            r["holder_name"], r["share_type"],
        ))
    _insert_or_replace_many(conn, """
        INSERT OR REPLACE INTO stock_share_float
        (stock_id, ann_date, float_date, float_share, float_ratio, holder_name, share_type)
        VALUES (?, ?, ?, ?, ?, ?, ?)
    """, rows)
    return {"rows": len(rows)}


def sync_repurchase(conn: sqlite3.Connection, code_map: dict[str, int], start: str, end: str) -> dict:
    data = fetch_repurchase(start_date=start, end_date=end)
    rows: list[tuple] = []
    for r in data:
        code = _strip_suffix(r["ts_code"])
        stock_id = code_map.get(code)
        if stock_id is None:
            continue
        rows.append((
            stock_id, r["ann_date"], r["end_date"], r["proc"], r["exp_date"], r["vol"],
            r["amount"], r["high_limit"], r["low_limit"],
        ))
    _insert_or_replace_many(conn, """
        INSERT OR REPLACE INTO stock_repurchase
        (stock_id, ann_date, end_date, proc, exp_date, vol, amount, high_limit, low_limit)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, rows)
    return {"rows": len(rows)}


def sync_holder_trade(conn: sqlite3.Connection, code_map: dict[str, int], start: str, end: str) -> dict:
    data = fetch_holder_trade(ts_code=None, start_date=start, end_date=end)
    rows: list[tuple] = []
    for r in data:
        code = _strip_suffix(r["ts_code"])
        stock_id = code_map.get(code)
        if stock_id is None:
            continue
        rows.append((
            stock_id, r["ann_date"], r["holder_name"], r["holder_type"], r["in_de"],
            r["change_vol"], r["change_ratio"], r["after_share"], r["after_ratio"],
            r["avg_price"], r["total_share"], r["begin_date"], r["close_date"],
        ))
    _insert_or_replace_many(conn, """
        INSERT OR REPLACE INTO stock_holder_trade
        (stock_id, ann_date, holder_name, holder_type, in_de, change_vol, change_ratio,
         after_share, after_ratio, avg_price, total_share, begin_date, close_date)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, rows)
    return {"rows": len(rows)}


# ── 逐股拉取（有频次限制，需控制速度）─────────────────────────────────────────


def sync_pledge_stat(conn: sqlite3.Connection, stocks: list[tuple[int, str, str]], end: str) -> dict:
    """股权质押统计。建议每周或每月跑一次，日常不跑。"""
    rows: list[tuple] = []
    errors: list[str] = []
    for i, (stock_id, code, market) in enumerate(stocks, 1):
        ts_code = code_to_ts_code(code, market if market in ("BJ",) else None)
        try:
            data = fetch_pledge_stat(ts_code=ts_code, end_date=end)
            for r in data:
                rows.append((
                    stock_id, r["end_date"], r["pledge_count"], r["unrest_pledge"],
                    r["rest_pledge"], r["total_share"], r["pledge_ratio"],
                ))
        except Exception as e:
            errors.append(f"{code}:{e}")
            if i % 100 == 0:
                time.sleep(1)
        if i % 200 == 0:
            _insert_or_replace_many(conn, """
                INSERT OR REPLACE INTO stock_pledge_stat
                (stock_id, end_date, pledge_count, unrest_pledge, rest_pledge, total_share, pledge_ratio)
                VALUES (?, ?, ?, ?, ?, ?, ?)
            """, rows)
            rows = []
    _insert_or_replace_many(conn, """
        INSERT OR REPLACE INTO stock_pledge_stat
        (stock_id, end_date, pledge_count, unrest_pledge, rest_pledge, total_share, pledge_ratio)
        VALUES (?, ?, ?, ?, ?, ?, ?)
    """, rows)
    return {"rows": len(rows), "errors": errors[:5]}


def sync_holdernumber(conn: sqlite3.Connection, stocks: list[tuple[int, str, str]], start: str, end: str) -> dict:
    """股东户数。逐股，Tushare 有较高频次限制，适配器 0.35s 节流已够用。"""
    rows: list[tuple] = []
    errors: list[str] = []
    for i, (stock_id, code, market) in enumerate(stocks, 1):
        ts_code = code_to_ts_code(code, market if market in ("BJ",) else None)
        try:
            data = fetch_stk_holdernumber(ts_code=ts_code, start_date=start, end_date=end)
            for r in data:
                rows.append((stock_id, r["ann_date"], r["end_date"], r["holder_num"]))
        except Exception as e:
            errors.append(f"{code}:{e}")
        if i % 200 == 0:
            _insert_or_replace_many(conn, """
                INSERT OR REPLACE INTO stock_holdernumber
                (stock_id, ann_date, end_date, holder_num)
                VALUES (?, ?, ?, ?)
            """, rows)
            rows = []
    _insert_or_replace_many(conn, """
        INSERT OR REPLACE INTO stock_holdernumber
        (stock_id, ann_date, end_date, holder_num)
        VALUES (?, ?, ?, ?)
    """, rows)
    return {"rows": len(rows), "errors": errors[:5]}


def sync_cyq_perf(conn: sqlite3.Connection, stocks: list[tuple[int, str, str]], start: str, end: str) -> dict:
    """每日筹码及胜率。逐股，5000 积分限制 200 次/分钟，0.35s 节流安全。"""
    rows: list[tuple] = []
    errors: list[str] = []
    for i, (stock_id, code, market) in enumerate(stocks, 1):
        ts_code = code_to_ts_code(code, market if market in ("BJ",) else None)
        try:
            data = fetch_cyq_perf(ts_code=ts_code, start_date=start, end_date=end)
            for r in data:
                rows.append((
                    stock_id, r["trade_date"], r["his_low"], r["his_high"], r["cost_5pct"],
                    r["cost_15pct"], r["cost_50pct"], r["cost_85pct"], r["cost_95pct"],
                    r["weight_avg"], r["winner_rate"],
                ))
        except Exception as e:
            errors.append(f"{code}:{e}")
        if i % 100 == 0:
            _insert_or_replace_many(conn, """
                INSERT OR REPLACE INTO stock_cyq_perf
                (stock_id, trade_date, his_low, his_high, cost_5pct, cost_15pct, cost_50pct,
                 cost_85pct, cost_95pct, weight_avg, winner_rate)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, rows)
            rows = []
    _insert_or_replace_many(conn, """
        INSERT OR REPLACE INTO stock_cyq_perf
        (stock_id, trade_date, his_low, his_high, cost_5pct, cost_15pct, cost_50pct,
         cost_85pct, cost_95pct, weight_avg, winner_rate)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, rows)
    return {"rows": len(rows), "errors": errors[:5]}


# ── 融资融券：按交易日全市场拉取 ────────────────────────────────────────────


def sync_margin_detail(conn: sqlite3.Connection, code_map: dict[str, int], start: str, end: str) -> dict:
    from services.tushare_adapter import _pro, _throttle

    pro = _pro()
    _throttle()
    df = pro.trade_cal(exchange="SSE", start_date=start, end_date=end, is_open="1")
    if df is None or df.empty:
        return {"rows": 0, "trading_days": 0}
    trading_days = sorted(df["cal_date"].astype(str).tolist())

    total_rows = 0
    for i, d in enumerate(trading_days, 1):
        data = fetch_margin_detail(trade_date=d)
        trade_date_fmt = f"{d[:4]}-{d[4:6]}-{d[6:8]}"
        rows = []
        for ts_code, v in data.items():
            code = _strip_suffix(ts_code)
            stock_id = code_map.get(code)
            if stock_id is None:
                continue
            rows.append((
                stock_id, trade_date_fmt, v["rzye"], v["rqye"], v["rzmre"], v["rqyl"],
                v["rzche"], v["rqchl"], v["rqmcl"], v["rzrqye"],
            ))
        if rows:
            _insert_or_replace_many(conn, """
                INSERT OR REPLACE INTO tushare_margin_detail
                (stock_id, trade_date, rzye, rqye, rzmre, rqyl, rzche, rqchl, rqmcl, rzrqye)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, rows)
            total_rows += len(rows)
    return {"rows": total_rows, "trading_days": len(trading_days)}


# ── 券商金股：按月（需 6000 积分）────────────────────────────────────────────


def sync_broker_recommend(conn: sqlite3.Connection, code_map: dict[str, int], month: str) -> dict:
    data = fetch_broker_recommend(month=month)
    rows: list[tuple] = []
    for r in data:
        code = _strip_suffix(r["ts_code"])
        stock_id = code_map.get(code)
        if stock_id is None:
            continue
        rows.append((r["month"], r["broker"], stock_id, r["name"]))
    _insert_or_replace_many(conn, """
        INSERT OR REPLACE INTO broker_recommend_monthly
        (month, broker, stock_id, name)
        VALUES (?, ?, ?, ?)
    """, rows)
    return {"rows": len(rows)}


# ── 统一入口 ───────────────────────────────────────────────────────────────


def sync_tushare_event_data(
    conn: sqlite3.Connection | None = None,
    stock_ids: list[int] | None = None,
    days: int = 10,
    start_date: str | None = None,
    end_date: str | None = None,
    full_backfill: bool = False,
    skip_pledge: bool = False,
    skip_pledge_stat: bool = False,
    skip_share_float: bool = False,
    skip_repurchase: bool = False,
    skip_holder_trade: bool = False,
    skip_holdernumber: bool = False,
    skip_cyq_perf: bool = False,
    skip_margin: bool = False,
    skip_per_stock: bool = True,  # 默认跳过慢速逐股接口
    broker_recommend: bool = False,
    broker_month: str | None = None,
) -> dict[str, Any]:
    """供 V5 sync 编排调用的统一入口。默认只跑事件类 + 融资融券，不跑逐股。"""
    close_conn = conn is None
    if conn is None:
        conn = sqlite3.connect(config.DB_PATH)

    if end_date is None:
        end = date.today().strftime("%Y%m%d")
    else:
        end = end_date

    if start_date is None:
        if full_backfill:
            start = "20200101"
        else:
            start = (date.today() - timedelta(days=int(days * 1.5) + 5)).strftime("%Y%m%d")
    else:
        start = start_date

    code_map = _code_map(conn)
    stocks = _active_stocks(conn, stock_ids)
    result: dict[str, Any] = {"range": f"{start}~{end}", "active_stocks": len(stocks)}

    if not skip_pledge:
        result["pledge_detail"] = sync_pledge_detail(conn, code_map, start, end)
    if not skip_pledge_stat and not skip_per_stock:
        result["pledge_stat"] = sync_pledge_stat(conn, stocks, end)
    if not skip_share_float:
        result["share_float"] = sync_share_float(conn, code_map, start, end)
    if not skip_repurchase:
        result["repurchase"] = sync_repurchase(conn, code_map, start, end)
    if not skip_holder_trade:
        result["holder_trade"] = sync_holder_trade(conn, code_map, start, end)
    if not skip_holdernumber and not skip_per_stock:
        result["holdernumber"] = sync_holdernumber(conn, stocks, start, end)
    if not skip_cyq_perf and not skip_per_stock:
        result["cyq_perf"] = sync_cyq_perf(conn, stocks, start, end)
    if not skip_margin:
        result["margin_detail"] = sync_margin_detail(conn, code_map, start, end)
    if broker_recommend:
        if broker_month is None:
            last_month = date.today().replace(day=1) - timedelta(days=1)
            broker_month = last_month.strftime("%Y%m")
        result["broker_recommend"] = sync_broker_recommend(conn, code_map, broker_month)

    if close_conn:
        conn.close()
    return result
