"""Polars 向量化回测因子预计算 — v4 QADataBridge 热点路径"""
from __future__ import annotations

import sqlite3
from typing import Dict, List, Optional

from config import DB_PATH


def polars_backtest_available() -> bool:
    try:
        import polars  # noqa: F401

        return True
    except ImportError:
        return False


def precompute_price_factors(
    start_str: str,
    end_str: str,
    lookback: int = 20,
) -> tuple[list[str], Dict[str, Dict[str, float]]]:
    """全市场逐日动量因子矩阵 {code: {date: score}}。"""
    import polars as pl

    conn = sqlite3.connect(DB_PATH)
    rows = conn.execute(
        """SELECT s.code, q.trade_date, q.close, q.volume
           FROM stock_daily_quotes q
           JOIN stocks s ON q.stock_id = s.id
           WHERE s.is_active = 1 AND q.trade_date BETWEEN ? AND ?
             AND q.close IS NOT NULL
           ORDER BY s.code, q.trade_date""",
        (start_str, end_str),
    ).fetchall()
    conn.close()

    if not rows:
        return [], {}

    df = pl.DataFrame(
        {
            "code": [r[0] for r in rows],
            "date": [r[1] for r in rows],
            "close": [float(r[2]) for r in rows],
            "volume": [float(r[3] or 0) for r in rows],
        }
    ).sort(["code", "date"])

    df = df.with_columns(
        pl.col("close").pct_change().over("code").alias("ret"),
    )
    lb = max(lookback, 5)
    df = df.with_columns(
        pl.col("ret").rolling_sum(lb).over("code").alias("momentum"),
        pl.col("ret").rolling_std(lb).over("code").alias("vol"),
        (
            pl.col("volume").rolling_mean(5).over("code")
            / (pl.col("volume").rolling_mean(lb).over("code") + 1e-9)
        ).alias("vol_ratio"),
    )

    df = df.with_columns(
        (
            pl.col("momentum").fill_null(0) * 40
            + (1 / (pl.col("vol").fill_null(0.01) + 0.001)) * 0.35
            + pl.col("vol_ratio").fill_null(1) * 12.5
            + 50
        ).alias("factor_score"),
    )

    dates = df["date"].unique().sort().to_list()
    out: Dict[str, Dict[str, float]] = {}
    for row in df.filter(pl.col("factor_score").is_not_null()).iter_rows(named=True):
        code = row["code"]
        out.setdefault(code, {})[row["date"]] = round(float(row["factor_score"]), 4)
    return dates, out


def run_momentum_backtest_polars(
    days: int = 90,
    top_n: int = 5,
    lookback: int = 20,
) -> Optional[dict]:
    """Polars 加速的价格因子回测；失败返回 None 供 Python 路径 fallback。"""
    if not polars_backtest_available():
        return None
    from datetime import date, timedelta

    from services.price_backtest import calc_metrics, compute_benchmark

    end_date = date.today()
    start_date = end_date - timedelta(days=max(days + lookback + 60, 365))
    start_str, end_str = start_date.strftime("%Y-%m-%d"), end_date.strftime("%Y-%m-%d")

    dates, factor_snap = precompute_price_factors(start_str, end_str, lookback=lookback)
    if len(dates) < lookback + 10:
        return None

    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    quotes: dict = {}
    for r in conn.execute(
        """SELECT s.code, q.trade_date, q.close, q.volume
           FROM stock_daily_quotes q JOIN stocks s ON q.stock_id = s.id
           WHERE s.is_active = 1 AND q.trade_date BETWEEN ? AND ?""",
        (start_str, end_str),
    ).fetchall():
        quotes.setdefault(r["code"], {})[r["trade_date"]] = {
            "close": float(r["close"]),
            "volume": float(r["volume"] or 0),
        }
    name_map = {
        r["code"]: r["name"]
        for r in conn.execute("SELECT code, name FROM stocks WHERE is_active=1").fetchall()
    }
    conn.close()

    cutoff_idx = max(0, len(dates) - 1 - int(days * 0.65))
    dates = dates[cutoff_idx:]
    benchmark = compute_benchmark(quotes, dates)

    cash = 100000.0
    holdings: dict = {}
    daily_records: list = []
    trades: list = []
    rebalance_interval = min(5, max(1, len(dates) // 20))

    for di, dt in enumerate(dates):
        available = {c: quotes[c][dt] for c in quotes if dt in quotes[c]}
        if di % rebalance_interval == 0 and di >= lookback:
            scores = {
                c: factor_snap[c][dt]
                for c in available
                if c in factor_snap and dt in factor_snap[c]
            }
            ranked = sorted(scores.items(), key=lambda x: -x[1])
            selected = [c for c, _ in ranked[:top_n]]

            for code in list(holdings):
                if code not in selected:
                    cash += holdings[code] * available[code]["close"]
                    trades.append(
                        {
                            "date": dt,
                            "code": code,
                            "name": name_map.get(code, ""),
                            "action": "SELL",
                            "price": round(available[code]["close"], 2),
                            "shares": holdings[code],
                        }
                    )
                    del holdings[code]

            if selected and cash > 0:
                per = cash / len(selected)
                for code in selected:
                    price = available[code]["close"]
                    target_shares = max(0, int(per / price / 100) * 100)
                    diff = target_shares - holdings.get(code, 0)
                    if diff:
                        cash -= diff * price
                        holdings[code] = holdings.get(code, 0) + diff
                        trades.append(
                            {
                                "date": dt,
                                "code": code,
                                "name": name_map.get(code, ""),
                                "action": "BUY" if diff > 0 else "SELL",
                                "price": round(price, 2),
                                "shares": abs(diff),
                            }
                        )

        hold_val = sum(
            holdings.get(c, 0) * available.get(c, {}).get("close", 0) for c in holdings
        )
        daily_records.append(
            {
                "date": dt,
                "value": round(cash + hold_val, 2),
                "cash": round(cash, 2),
                "holdings": len(holdings),
            }
        )

    result = calc_metrics(
        daily_records, dates, trades, days, top_n, lookback, "equal", rebalance_interval, benchmark
    )
    result["engine"] = "polars_vector"
    return result
