"""ML 训练用行情面板加载。"""
from __future__ import annotations

import sqlite3
from collections import defaultdict


def load_quote_panel(db_path: str) -> tuple[dict[str, list], dict[str, int], list[str]]:
    conn = sqlite3.connect(db_path)
    dates = [
        r[0]
        for r in conn.execute(
            "SELECT DISTINCT trade_date FROM stock_daily_quotes ORDER BY trade_date"
        ).fetchall()
    ]
    by_code: dict[str, list] = defaultdict(list)
    code_to_id: dict[str, int] = {}
    for r in conn.execute(
        """SELECT s.id, s.code, q.trade_date,
                  COALESCE(q.adj_close, q.close), q.volume,
                  COALESCE(q.high, q.close), COALESCE(q.low, q.close),
                  COALESCE(q.turnover, 0), COALESCE(q.amount, 0)
           FROM stock_daily_quotes q JOIN stocks s ON q.stock_id=s.id
           WHERE s.is_active=1 AND COALESCE(q.adj_close, q.close) IS NOT NULL
           ORDER BY s.code, q.trade_date"""
    ).fetchall():
        code_to_id[r[1]] = int(r[0])
        by_code[r[1]].append(
            (r[2], float(r[3]), float(r[4] or 0), float(r[5]), float(r[6]), float(r[7]), float(r[8]))
        )
    conn.close()
    return by_code, code_to_id, dates
