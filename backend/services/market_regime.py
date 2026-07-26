"""市场状态分类（趋势/震荡/高波动）。

基于沪深300日K线，计算 RSI、20日波动率、20/60日收益、价格与均线位置，
输出每日 regime 并写入 market_regime_daily，供 V5 动态权重调整。
"""
from __future__ import annotations

import math
import sqlite3
from datetime import date, timedelta
from typing import Any, Optional

import config
from services.market_index import fetch_index_kline


REGIME_ORDER = [
    "high_volatility",
    "strong_trend_up",
    "strong_trend_down",
    "weak_trend_up",
    "weak_trend_down",
    "oscillation",
]


def _is_valid(v: Any) -> bool:
    if v is None:
        return False
    try:
        f = float(v)
        return math.isfinite(f)
    except (TypeError, ValueError):
        return False


def _rsi(closes: list[float], window: int = 14) -> float:
    if len(closes) < window + 1:
        return 50.0
    gains = []
    losses = []
    for i in range(1, window + 1):
        diff = closes[-window - 1 + i] - closes[-window - 2 + i]
        if diff > 0:
            gains.append(diff)
            losses.append(0.0)
        else:
            gains.append(0.0)
            losses.append(-diff)
    avg_gain = sum(gains) / window
    avg_loss = sum(losses) / window
    if avg_loss < 1e-12:
        return 100.0 if avg_gain > 0 else 50.0
    rs = avg_gain / avg_loss
    return 100.0 - 100.0 / (1.0 + rs)


def _volatility(closes: list[float], window: int = 20) -> float:
    if len(closes) < window + 1:
        return 0.0
    rets = [
        closes[i] / closes[i - 1] - 1
        for i in range(-window, 0)
        if closes[i - 1] > 0
    ]
    if len(rets) < window:
        return 0.0
    mean = sum(rets) / len(rets)
    var = sum((r - mean) ** 2 for r in rets) / len(rets)
    # 年化波动率（交易日 252）
    return math.sqrt(var) * math.sqrt(252)


def _ma(closes: list[float], window: int) -> float:
    if len(closes) < window:
        return sum(closes) / len(closes) if closes else 0.0
    return sum(closes[-window:]) / window


def _pct_ret(closes: list[float], lag: int) -> float:
    if len(closes) <= lag or closes[-lag - 1] <= 0:
        return 0.0
    return closes[-1] / closes[-lag - 1] - 1


def _adx(highs: list[float], lows: list[float], closes: list[float], window: int = 14) -> float:
    """简化版 ADX（趋势强度）。"""
    if len(closes) < window * 2 + 1:
        return 25.0
    trs = []
    plus_dms = []
    minus_dms = []
    for i in range(-window, 0):
        h, l, c = highs[i], lows[i], closes[i]
        prev_c = closes[i - 1]
        tr = max(h - l, abs(h - prev_c), abs(l - prev_c))
        plus_dm = max(h - highs[i - 1], 0)
        minus_dm = max(lows[i - 1] - l, 0)
        trs.append(tr)
        plus_dms.append(plus_dm)
        minus_dms.append(minus_dm)
    atr = sum(trs) / len(trs) if trs else 1e-9
    plus_di = 100 * sum(plus_dms) / (atr * window) if atr > 0 else 0
    minus_di = 100 * sum(minus_dms) / (atr * window) if atr > 0 else 0
    dx = abs(plus_di - minus_di) / (plus_di + minus_di + 1e-9) * 100
    return dx


def classify_regime(kline: list[dict[str, Any]]) -> dict[str, Any]:
    """基于指数 K 线列表（日期升序）分类市场状态。"""
    if not kline or len(kline) < 65:
        return {"regime": "oscillation", "rsi_14": 50.0, "volatility_20": 0.0, "adx": 25.0}

    closes = [float(b["close"]) for b in kline if _is_valid(b.get("close"))]
    highs = [float(b["high"]) for b in kline if _is_valid(b.get("high"))]
    lows = [float(b["low"]) for b in kline if _is_valid(b.get("low"))]
    if len(closes) < 65:
        return {"regime": "oscillation", "rsi_14": 50.0, "volatility_20": 0.0, "adx": 25.0}

    rsi = _rsi(closes, 14)
    vol = _volatility(closes, 20)
    adx = _adx(highs, lows, closes, 14)
    ret20 = _pct_ret(closes, 20)
    ret60 = _pct_ret(closes, 60)
    ma20 = _ma(closes, 20)
    ma60 = _ma(closes, 60)
    price = closes[-1]
    price_vs_ma20 = (price / ma20 - 1) if ma20 > 0 else 0.0
    price_vs_ma60 = (price / ma60 - 1) if ma60 > 0 else 0.0

    # 高波动优先：年化波动率 > 30% 或 ADX 高但价格横盘
    if vol > 0.30 or (adx > 50 and abs(ret20) < 0.03):
        regime = "high_volatility"
    elif rsi > 55 and ret20 > 0.03 and ret60 > 0.05 and price_vs_ma20 > 0 and price_vs_ma60 > 0:
        regime = "strong_trend_up"
    elif rsi < 45 and ret20 < -0.03 and ret60 < -0.05 and price_vs_ma20 < 0 and price_vs_ma60 < 0:
        regime = "strong_trend_down"
    elif rsi > 50 and ret20 > 0 and price_vs_ma20 > 0:
        regime = "weak_trend_up"
    elif rsi < 50 and ret20 < 0 and price_vs_ma20 < 0:
        regime = "weak_trend_down"
    else:
        regime = "oscillation"

    return {
        "regime": regime,
        "rsi_14": rsi,
        "volatility_20": vol,
        "adx": adx,
        "return_20d": ret20,
        "return_60d": ret60,
        "price_vs_ma20": price_vs_ma20,
        "price_vs_ma60": price_vs_ma60,
    }


def detect_regime(
    trade_date: Optional[str] = None,
    index_code: str = "sh000300",
) -> dict[str, Any]:
    """拉取指数 K 线并分类。trade_date 仅用于校验最后一条日期。"""
    data = fetch_index_kline(index_code, period="daily", days=120, with_technical=False)
    kline = data.get("kline") or []
    if not kline:
        return {"regime": "oscillation", "error": data.get("error", "无指数数据")}

    result = classify_regime(kline)
    last_date = kline[-1].get("date") if kline else None
    if trade_date and last_date and last_date != trade_date:
        # 如果 trade_date 不是最新交易日，尝试截断到该日期
        truncated = [b for b in kline if b.get("date") <= trade_date]
        if len(truncated) >= 65:
            result = classify_regime(truncated)
            last_date = truncated[-1].get("date")
    result["trade_date"] = last_date
    result["index_code"] = index_code
    return result


def get_regime_for_date(
    conn: sqlite3.Connection,
    trade_date: Optional[str] = None,
) -> dict[str, Any]:
    """读取某日期市场状态；不传日期取最新。"""
    if trade_date is None:
        row = conn.execute(
            "SELECT * FROM market_regime_daily ORDER BY trade_date DESC LIMIT 1"
        ).fetchone()
    else:
        row = conn.execute(
            "SELECT * FROM market_regime_daily WHERE trade_date=? LIMIT 1", (trade_date,)
        ).fetchone()
    if not row:
        return {"regime": "oscillation"}
    return dict(row)


def sync_regime(
    conn: sqlite3.Connection,
    trade_date: Optional[str] = None,
    index_code: str = "sh000300",
) -> dict[str, Any]:
    """计算并写入 market_regime_daily，返回结果。"""
    result = detect_regime(trade_date=trade_date, index_code=index_code)
    if "error" in result:
        return result

    dt = result.get("trade_date")
    if not dt:
        return result

    conn.execute(
        """INSERT OR REPLACE INTO market_regime_daily
           (trade_date, index_code, regime, rsi_14, volatility_20, adx,
            return_20d, return_60d, price_vs_ma20, price_vs_ma60, updated_at)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, datetime('now'))""",
        (
            dt,
            index_code,
            result["regime"],
            result["rsi_14"],
            result["volatility_20"],
            result["adx"],
            result["return_20d"],
            result["return_60d"],
            result["price_vs_ma20"],
            result["price_vs_ma60"],
        ),
    )
    conn.commit()
    return result
