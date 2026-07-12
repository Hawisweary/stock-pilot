"""跟踪池日线行情增量同步 — 市场行情页刷新时调用"""
from __future__ import annotations

import sqlite3

from config import DB_PATH, QUOTE_SYNC_MAX_BARS
from services.data_fetcher import DataFetcher


def sync_active_stock_quotes(*, max_bars: int = QUOTE_SYNC_MAX_BARS) -> dict:
    """拉取活跃股票最近行情（腾讯 OHLCV + 东财/akshare 成交额换手 + 融资余额）。"""
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    rows = conn.execute(
        "SELECT id, code, market FROM stocks WHERE is_active=1 ORDER BY id"
    ).fetchall()

    fetcher = DataFetcher(conn)
    ok = err = 0
    errors: list[str] = []
    for r in rows:
        try:
            n = fetcher._fetch_daily_quotes(int(r["id"]), r["code"], max_bars=max_bars)
            if n > 0:
                ok += 1
            else:
                err += 1
        except Exception as e:
            err += 1
            if len(errors) < 5:
                errors.append(f"{r['code']}: {e}")

    latest = conn.execute(
        "SELECT MAX(trade_date) FROM stock_daily_quotes WHERE close IS NOT NULL"
    ).fetchone()[0]
    conn.close()

    margin_result: dict = {}
    try:
        from services.margin_balance_sync import sync_margin_balance

        margin_result = sync_margin_balance()
    except Exception as e:
        margin_result = {"error": str(e)}

    fund_flow_result: dict = {}
    try:
        from services.fund_flow_sync import sync_stock_fund_flow

        fund_flow_result = sync_stock_fund_flow()
    except Exception as e:
        fund_flow_result = {"error": str(e)}

    index_result: dict = {}
    try:
        from services.market_index import _snapshot_trade_date, warm_market_index_cache

        snap = warm_market_index_cache()
        index_result = {
            "as_of_trade_date": _snapshot_trade_date(snap),
            "indices": len(snap or {}),
        }
    except Exception as e:
        index_result = {"error": str(e)}

    lhb_result: dict = {}
    try:
        from services.lhb_sync import sync_lhb_latest_market_day

        lhb_result = sync_lhb_latest_market_day(lookback=5)
    except Exception as e:
        lhb_result = {"error": str(e)}

    try:
        from services.market_data_cache import invalidate_market_page_caches
        from services.sector_rotation import clear_sector_rotation_cache

        invalidate_market_page_caches()
        clear_sector_rotation_cache()
    except Exception:
        pass

    return {
        "stocks": len(rows),
        "synced": ok,
        "failed": err,
        "latest_trade_date": latest,
        "errors": errors,
        "margin": margin_result,
        "market_indices": index_result,
        "lhb_market": lhb_result,
        "fund_flow": fund_flow_result,
    }
