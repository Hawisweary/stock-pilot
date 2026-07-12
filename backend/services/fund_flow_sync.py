"""个股主力资金流同步 — Tushare Pro 官方数据为主（一次批量覆盖全市场），东财兜底。"""
from __future__ import annotations

import sqlite3
import time

from config import DB_PATH, latest_trading_date
from services.margin_fetcher import fetch_main_net_5d_map, fetch_margin_data


def _tushare_fund_flow_map(as_of: str | None = None) -> dict[str, dict]:
    """当日全市场资金流（一次调用），{ts_code: {main_net_inflow, super_large_inflow}}。"""
    try:
        from services.tushare_adapter import fetch_market_fund_flow, latest_trading_date as ts_latest_trading_date

        trade_date = ts_latest_trading_date(as_of or time.strftime("%Y%m%d"))
        if not trade_date:
            return {}
        return fetch_market_fund_flow(trade_date)
    except Exception:
        return {}


def sync_stock_fund_flow(
    stock_ids: list[int] | None = None,
    *,
    limit: int = 80,
    sleep_ms: int = 200,
) -> dict:
    """拉取并持久化个股主力净流入序列。Tushare 批量数据优先，东财逐股兜底。"""
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    stocks = []
    try:
        if stock_ids:
            ph = ",".join("?" * len(stock_ids))
            stocks = conn.execute(
                f"SELECT id, code, COALESCE(market,'A') AS market FROM stocks WHERE id IN ({ph}) AND is_active=1",
                stock_ids,
            ).fetchall()
        else:
            stocks = conn.execute(
                "SELECT id, code, COALESCE(market,'A') AS market FROM stocks WHERE is_active=1 ORDER BY id LIMIT ?",
                (limit,),
            ).fetchall()

        synced = 0
        tushare_hits = 0
        errors: list[str] = []
        codes = [row["code"] for row in stocks]

        from services.tushare_adapter import code_to_ts_code

        ts_map = _tushare_fund_flow_map()
        today_str = latest_trading_date()
        net5_map = fetch_main_net_5d_map(set(codes)) if len(ts_map) < len(codes) else {}

        for row in stocks:
            sid, code, market = int(row["id"]), row["code"], row["market"]
            try:
                ts_data = ts_map.get(code_to_ts_code(code, market))
                if ts_data is not None:
                    conn.execute(
                        """INSERT OR REPLACE INTO stock_fund_flow_daily
                        (stock_id, trade_date, main_net_inflow, super_large_inflow, source)
                        VALUES (?,?,?,?,?)""",
                        (
                            sid,
                            today_str,
                            ts_data.get("main_net_inflow"),
                            ts_data.get("super_large_inflow"),
                            "tushare",
                        ),
                    )
                    recent = conn.execute(
                        """SELECT main_net_inflow FROM stock_fund_flow_daily
                           WHERE stock_id=? ORDER BY trade_date DESC LIMIT 5""",
                        (sid,),
                    ).fetchall()
                    if recent:
                        net5 = sum(r[0] or 0 for r in recent)
                        conn.execute(
                            "UPDATE stock_fund_flow_daily SET main_net_5d=? WHERE stock_id=? AND trade_date=?",
                            (net5, sid, today_str),
                        )
                    tushare_hits += 1
                    synced += 1
                    continue

                # Tushare 未覆盖（新股/退市整理期等）时兜底：东财
                rows = fetch_margin_data(code)
                if not rows and code not in net5_map:
                    continue
                source = "eastmoney"
                for item in (rows or [])[-60:]:
                    conn.execute(
                        """INSERT OR REPLACE INTO stock_fund_flow_daily
                        (stock_id, trade_date, main_net_inflow, super_large_inflow, source)
                        VALUES (?,?,?,?,?)""",
                        (
                            sid,
                            item["date"],
                            item.get("main_net_inflow"),
                            item.get("super_large_inflow"),
                            source,
                        ),
                    )
                # 5 日主力：优先 clist 汇总，否则库内近 5 日滚动
                latest_date = rows[-1]["date"] if rows else latest_trading_date()
                net5 = net5_map.get(code)
                if net5 is None:
                    recent = conn.execute(
                        """SELECT trade_date, main_net_inflow FROM stock_fund_flow_daily
                           WHERE stock_id=? ORDER BY trade_date DESC LIMIT 5""",
                        (sid,),
                    ).fetchall()
                    if recent:
                        net5 = sum(r[1] or 0 for r in recent)
                        latest_date = recent[0][0]
                if net5 is not None:
                    if not rows:
                        conn.execute(
                            """INSERT OR REPLACE INTO stock_fund_flow_daily
                            (stock_id, trade_date, main_net_inflow, super_large_inflow,
                             main_net_5d, source)
                            VALUES (?,?,?,?,?,?)""",
                            (sid, latest_date, None, None, net5, source),
                        )
                    else:
                        conn.execute(
                            "UPDATE stock_fund_flow_daily SET main_net_5d=? WHERE stock_id=? AND trade_date=?",
                            (net5, sid, latest_date),
                        )
                synced += 1
            except Exception as e:
                errors.append(f"{code}:{e}")
            if sleep_ms > 0:
                time.sleep(sleep_ms / 1000)
        conn.commit()
    finally:
        conn.close()

    return {
        "synced": synced,
        "total": len(stocks),
        "errors": errors[:10],
        "source": "tushare",
        "tushare_hits": tushare_hits,
    }


def get_stock_fund_flow(stock_id: int, days: int = 20) -> dict:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    try:
        rows = conn.execute(
            """SELECT trade_date, main_net_inflow, super_large_inflow, main_net_5d
               FROM stock_fund_flow_daily WHERE stock_id=?
               ORDER BY trade_date DESC LIMIT ?""",
            (stock_id, days),
        ).fetchall()
    finally:
        conn.close()
    latest = dict(rows[0]) if rows else None
    return {
        "stock_id": stock_id,
        "latest": latest,
        "history": [dict(r) for r in rows],
        "as_of": latest_trading_date(),
    }
