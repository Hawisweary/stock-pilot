"""回填 stock_eps_forecast 的 revision_3m_pct 并重建 industry_eps_revision_daily。

方案 A：把 lookback 从 90 天缩短到 30 天，用现有 2 个月数据算出 revision。
"""
from __future__ import annotations

import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import config
from services.eastmoney_forecast_sync import (
    _past_eps_fy2,
    _revision_pct,
    sync_industry_eps_revision,
)


def backfill() -> dict:
    conn = sqlite3.connect(config.DB_PATH)
    conn.row_factory = sqlite3.Row

    rows = conn.execute(
        "SELECT id, stock_id, as_of_date, eps_fy2 FROM stock_eps_forecast WHERE eps_fy2 IS NOT NULL"
    ).fetchall()

    updated = 0
    for row in rows:
        past = _past_eps_fy2(conn, row["stock_id"], row["as_of_date"], days=30)
        rev = _revision_pct(row["eps_fy2"], past)
        if rev is not None:
            conn.execute(
                "UPDATE stock_eps_forecast SET revision_3m_pct=? WHERE id=?",
                (rev, row["id"]),
            )
            updated += 1
    conn.commit()

    dates = [r[0] for r in conn.execute(
        "SELECT DISTINCT as_of_date FROM stock_eps_forecast ORDER BY as_of_date"
    ).fetchall()]

    industry_total = 0
    for d in dates:
        r = sync_industry_eps_revision(trade_date=d)
        industry_total += r.get("industries", 0)

    conn.close()
    return {
        "rows_updated": updated,
        "total_forecast_rows": len(rows),
        "industry_dates": len(dates),
        "industry_rows": industry_total,
    }


if __name__ == "__main__":
    stats = backfill()
    print(stats)
