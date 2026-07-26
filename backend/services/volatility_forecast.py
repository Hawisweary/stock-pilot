"""个股波动率 / 流动性预测。

基于日 K 计算历史已实现波动率与流动性指标，并用 EWMA 预测未来 20 日波动率。
结果写入 volatility_forecast_daily 表，供仓位管理、止损宽度、选股过滤使用。

设计原则：
- 无未来偏差：只使用 trade_date 及之前数据。
- 解释性强：保留 realized_vol_20、avg_turnover_20、amihud_illiq_20 等原始指标。
- 幂等：sync 按 trade_date 先删除后写入。
"""
from __future__ import annotations

import math
import sqlite3
from datetime import date, timedelta
from typing import Any, Optional

import config

EWMA_LAMBDA = 0.94
MIN_HISTORY_DAYS = 21


def _is_valid(v: Any) -> bool:
    if v is None:
        return False
    try:
        f = float(v)
        return math.isfinite(f) and f > 0
    except (TypeError, ValueError):
        return False


def _ewma_variance(series: list[float]) -> float:
    """EWMA 方差，从最近向最久推进。"""
    var = 0.0
    for r in series:
        var = EWMA_LAMBDA * var + (1 - EWMA_LAMBDA) * (r ** 2)
    return var


def _compute_forecast(close_history: list[float]) -> dict[str, Any]:
    """从收盘价序列计算波动率与流动性指标。"""
    if len(close_history) < MIN_HISTORY_DAYS:
        return {}

    log_returns = []
    for i in range(1, len(close_history)):
        if close_history[i - 1] > 0 and close_history[i] > 0:
            log_returns.append(math.log(close_history[i] / close_history[i - 1]))

    if len(log_returns) < 20:
        return {}

    realized_vol_20 = math.sqrt(
        sum((r - sum(log_returns[-20:]) / 20) ** 2 for r in log_returns[-20:]) / 20
    ) if len(log_returns) >= 20 else 0.0

    realized_vol_60 = math.sqrt(
        sum((r - sum(log_returns[-60:]) / len(log_returns[-60:])) ** 2 for r in log_returns[-60:])
        / len(log_returns[-60:])
    ) if len(log_returns) >= 60 else realized_vol_20

    ewma_var = _ewma_variance(list(reversed(log_returns[-60:])))
    forecast_vol_20 = math.sqrt(ewma_var) if ewma_var > 0 else realized_vol_20

    return {
        "realized_vol_20": realized_vol_20,
        "realized_vol_60": realized_vol_60,
        "forecast_vol_20": forecast_vol_20,
        "forecast_horizon": 20,
        "forecast_method": "ewma",
    }


def sync_forecast(
    conn: sqlite3.Connection,
    trade_date: Optional[str] = None,
    lookback: int = 120,
) -> dict[str, Any]:
    """计算并写入指定交易日的波动率 / 流动性预测。"""
    if trade_date is None:
        row = conn.execute(
            "SELECT MAX(trade_date) FROM stock_daily_quotes WHERE close IS NOT NULL"
        ).fetchone()
        trade_date = row[0] if row and row[0] else date.today().isoformat()

    start_date = (date.fromisoformat(trade_date) - timedelta(days=lookback * 2)).isoformat()

    # 加载收盘价、成交量、成交额
    quotes: dict[int, list[tuple[str, float, float, float]]] = {}
    for row in conn.execute(
        """SELECT stock_id, trade_date, close, volume, amount
           FROM stock_daily_quotes
           WHERE trade_date >= ? AND trade_date <= ? AND close IS NOT NULL AND close > 0
           ORDER BY stock_id, trade_date""",
        (start_date, trade_date),
    ):
        sid = int(row[0])
        if sid not in quotes:
            quotes[sid] = []
        quotes[sid].append((row[1], float(row[2]), float(row[3] or 0), float(row[4] or 0)))

    # 获取活跃股票列表
    active_stocks = {int(row[0]) for row in conn.execute("SELECT id FROM stocks WHERE is_active=1")}

    records = []
    for sid in active_stocks:
        hist = quotes.get(sid, [])
        if len(hist) < MIN_HISTORY_DAYS:
            continue

        closes = [h[1] for h in hist]
        forecast = _compute_forecast(closes)
        if not forecast:
            continue

        recent = hist[-20:]
        turnovers = [h[2] for h in recent if h[2] > 0]
        amounts = [h[3] for h in recent if h[3] > 0]

        avg_turnover_20 = sum(turnovers) / len(turnovers) if turnovers else 0.0
        avg_amount_20 = sum(amounts) / len(amounts) if amounts else 0.0

        # Amihud illiquidity = |return| / amount，取 20 日均值并乘以 1e8 便于阅读
        amihud_samples = []
        for i in range(1, len(recent)):
            if recent[i - 1][1] > 0 and recent[i][3] > 0:
                ret = abs(math.log(recent[i][1] / recent[i - 1][1]))
                amihud_samples.append(ret / recent[i][3])
        amihud_illiq_20 = (sum(amihud_samples) / len(amihud_samples) * 1e8) if amihud_samples else 0.0

        records.append({
            "stock_id": sid,
            "trade_date": trade_date,
            "realized_vol_20": forecast["realized_vol_20"],
            "realized_vol_60": forecast["realized_vol_60"],
            "avg_turnover_20": avg_turnover_20,
            "avg_amount_20": avg_amount_20,
            "amihud_illiq_20": amihud_illiq_20,
            "forecast_vol_20": forecast["forecast_vol_20"],
            "forecast_horizon": forecast["forecast_horizon"],
            "forecast_method": forecast["forecast_method"],
        })

    # 幂等：先删除后写入
    conn.execute("DELETE FROM volatility_forecast_daily WHERE trade_date=?", (trade_date,))
    conn.executemany(
        """INSERT INTO volatility_forecast_daily
           (stock_id, trade_date, realized_vol_20, realized_vol_60, avg_turnover_20,
            avg_amount_20, amihud_illiq_20, forecast_vol_20, forecast_horizon, forecast_method)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        [
            (
                r["stock_id"], r["trade_date"], r["realized_vol_20"], r["realized_vol_60"],
                r["avg_turnover_20"], r["avg_amount_20"], r["amihud_illiq_20"],
                r["forecast_vol_20"], r["forecast_horizon"], r["forecast_method"],
            )
            for r in records
        ],
    )
    conn.commit()

    return {
        "trade_date": trade_date,
        "records": len(records),
        "avg_realized_vol_20": sum(r["realized_vol_20"] for r in records) / len(records) if records else 0.0,
        "avg_forecast_vol_20": sum(r["forecast_vol_20"] for r in records) / len(records) if records else 0.0,
    }


def get_forecast_for_stock(
    conn: sqlite3.Connection,
    stock_id: int,
    limit: int = 30,
) -> list[dict[str, Any]]:
    rows = conn.execute(
        """SELECT trade_date, realized_vol_20, realized_vol_60, avg_turnover_20,
                  avg_amount_20, amihud_illiq_20, forecast_vol_20, forecast_horizon, forecast_method
           FROM volatility_forecast_daily
           WHERE stock_id=?
           ORDER BY trade_date DESC LIMIT ?""",
        (stock_id, limit),
    ).fetchall()
    return [
        {
            "trade_date": r[0],
            "realized_vol_20": r[1],
            "realized_vol_60": r[2],
            "avg_turnover_20": r[3],
            "avg_amount_20": r[4],
            "amihud_illiq_20": r[5],
            "forecast_vol_20": r[6],
            "forecast_horizon": r[7],
            "forecast_method": r[8],
        }
        for r in rows
    ]


def get_summary_for_date(
    conn: sqlite3.Connection,
    trade_date: Optional[str] = None,
) -> dict[str, Any]:
    if trade_date is None:
        row = conn.execute("SELECT MAX(trade_date) FROM volatility_forecast_daily").fetchone()
        trade_date = row[0] if row and row[0] else date.today().isoformat()

    total = conn.execute(
        "SELECT COUNT(*) FROM volatility_forecast_daily WHERE trade_date=?",
        (trade_date,),
    ).fetchone()[0]

    stats = conn.execute(
        """SELECT AVG(realized_vol_20), AVG(forecast_vol_20), AVG(avg_turnover_20),
                  AVG(avg_amount_20), AVG(amihud_illiq_20)
           FROM volatility_forecast_daily WHERE trade_date=?""",
        (trade_date,),
    ).fetchone()

    top_vol = [
        {
            "stock_id": r[0],
            "code": r[4] or "",
            "name": r[5] or "",
            "realized_vol_20": r[1],
            "forecast_vol_20": r[2],
            "avg_turnover_20": r[3],
        }
        for r in conn.execute(
            """SELECT v.stock_id, v.realized_vol_20, v.forecast_vol_20, v.avg_turnover_20,
                      s.code, s.name
               FROM volatility_forecast_daily v
               LEFT JOIN stocks s ON s.id = v.stock_id
               WHERE v.trade_date=?
               ORDER BY v.forecast_vol_20 DESC LIMIT 20""",
            (trade_date,),
        ).fetchall()
    ]

    return {
        "trade_date": trade_date,
        "total_records": total,
        "avg_realized_vol_20": stats[0] or 0.0,
        "avg_forecast_vol_20": stats[1] or 0.0,
        "avg_turnover_20": stats[2] or 0.0,
        "avg_amount_20": stats[3] or 0.0,
        "avg_amihud_illiq_20": stats[4] or 0.0,
        "top_volatility": top_vol,
    }
