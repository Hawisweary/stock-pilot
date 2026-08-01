"""海龟交易 — 唐奇安通道突破评分与 ATR 止损/出场。"""
from __future__ import annotations

from typing import Optional


def turtle_atr(
    series: dict,
    dates: list[str],
    idx: int,
    period: int = 20,
) -> Optional[float]:
    """平均真实波幅（ATR）。"""
    if idx < 1 or idx >= len(dates):
        return None
    trs: list[float] = []
    start = max(1, idx - period + 1)
    for i in range(start, idx + 1):
        dt = dates[i]
        if dt not in series:
            continue
        bar = series[dt]
        high = float(bar.get("high") or bar.get("close") or 0)
        low = float(bar.get("low") or bar.get("close") or 0)
        prev_close = float(series.get(dates[i - 1], {}).get("close") or 0)
        if high <= 0 or low <= 0:
            continue
        tr = max(high - low, abs(high - prev_close), abs(low - prev_close))
        trs.append(tr)
    if len(trs) < max(3, period // 2):
        return None
    return sum(trs) / len(trs)


def turtle_should_exit(
    series: dict,
    dates: list[str],
    idx: int,
    *,
    exit_period: int = 10,
    stop_price: Optional[float] = None,
    stop_only: bool = False,
) -> bool:
    """
    出场：收盘价跌破 2N 止损价；stop_only=True 时仅检查止损（不做通道出场）。
    """
    if idx < 1 or idx >= len(dates):
        return False
    dt = dates[idx]
    if dt not in series:
        return False
    close = float(series[dt].get("close") or 0)
    if close <= 0:
        return False
    if stop_price is not None and close < stop_price:
        return True
    if stop_only:
        return False
    if idx < exit_period:
        return False
    prior = dates[idx - exit_period : idx]
    lows: list[float] = []
    for d in prior:
        if d not in series:
            continue
        lo = float(series[d].get("low") or series[d].get("close") or 0)
        if lo > 0:
            lows.append(lo)
    if len(lows) < exit_period * 0.6:
        return False
    return close < min(lows)


def turtle_score(
    series: dict,
    dates: list[str],
    idx: int,
    entry: int = 20,
) -> Optional[float]:
    """
    使用前 entry 日（不含当日）的最高价通道。
    收盘价突破通道上轨 → 高分；通道内按相对位置 0–100。
    """
    if idx < entry or idx >= len(dates):
        return None
    dt = dates[idx]
    if dt not in series:
        return None
    cur = series[dt]
    close = float(cur.get("close") or 0)
    if close <= 0:
        return None

    prior = dates[idx - entry : idx]
    highs: list[float] = []
    lows: list[float] = []
    for d in prior:
        if d not in series:
            continue
        bar = series[d]
        h = float(bar.get("high") or bar.get("close") or 0)
        lo = float(bar.get("low") or bar.get("close") or 0)
        if h > 0:
            highs.append(h)
        if lo > 0:
            lows.append(lo)
    if len(highs) < entry * 0.6:
        return None

    channel_high = max(highs)
    channel_low = min(lows) if lows else channel_high * 0.9
    if close >= channel_high:
        return 100.0
    span = channel_high - channel_low
    if span <= 0:
        return 50.0
    return round(max(0.0, min(100.0, (close - channel_low) / span * 100)), 2)
