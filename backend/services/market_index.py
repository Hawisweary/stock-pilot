"""大盘指数技术摘要 — 供技术面 LLM / 规则引擎参考市场环境"""
from __future__ import annotations

import time
from datetime import datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

_CN_TZ = ZoneInfo("Asia/Shanghai")

# (Ashare 代码, 展示名)
DEFAULT_INDICES: tuple[tuple[str, str], ...] = (
    ("sh000001", "上证指数"),
    ("sz399001", "深证成指"),
    ("sh000300", "沪深300"),
    ("sh000906", "中证800"),
    ("sz399006", "创业板指"),
)

# 前端/API 别名 → Ashare 代码
_INDEX_CODE_ALIASES: dict[str, str] = {
    "sh000001": "sh000001",
    "000001": "sh000001",
    "上证指数": "sh000001",
    "sz399001": "sz399001",
    "399001": "sz399001",
    "深证成指": "sz399001",
    "深证指数": "sz399001",
    "深成指": "sz399001",
    "sh000300": "sh000300",
    "000300": "sh000300",
    "沪深300": "sh000300",
    "sh000906": "sh000906",
    "000906": "sh000906",
    "中证800": "sh000906",
    "csi800": "sh000906",
    "sz399006": "sz399006",
    "399006": "sz399006",
    "创业板指": "sz399006",
    "创业板": "sz399006",
}

_INDEX_NAMES: dict[str, str] = {c: n for c, n in DEFAULT_INDICES}

_cache: dict[str, Any] = {"ts": 0.0, "data": None, "stale": False}
_kline_cache: dict[str, Any] = {"ts": 0.0, "entries": {}}
_STALE_CACHE_MAX_SEC = 1800
_realtime_cache: dict[str, Any] = {"ts": 0.0, "data": None}


def _index_cache_mode(last_bar: str | None) -> str:
    """intraday=日历日新于日 K；closed=日 K 已对齐库内最新交易日。"""
    exp = _expected_trade_date()
    cal = _calendar_today()
    if last_bar and exp and last_bar >= exp and last_bar < cal:
        return "intraday"
    if last_bar and exp and last_bar >= exp:
        return "closed"
    return "intraday"


def _closed_ttl(fallback: float) -> float:
    """收盘后缓存到下次开盘；拿不到市场状态时退回固定 TTL。"""
    try:
        from services.trade_pricing import seconds_until_next_open

        ttl = seconds_until_next_open()
        return ttl if ttl > 0 else fallback
    except Exception:
        return fallback


def _snapshot_cache_ttl(snapshot: dict[str, dict[str, Any]] | None) -> float:
    from services.market_data_cache import (
        TTL_INDEX_SNAPSHOT_CLOSED_SEC,
        TTL_INDEX_SNAPSHOT_INTRADAY_SEC,
    )

    mode = _index_cache_mode(_snapshot_trade_date(snapshot))
    return (
        TTL_INDEX_SNAPSHOT_INTRADAY_SEC
        if mode == "intraday"
        else _closed_ttl(TTL_INDEX_SNAPSHOT_CLOSED_SEC)
    )


def _kline_cache_ttl(last_bar: str | None, period: str) -> float:
    from services.market_data_cache import (
        TTL_INDEX_KLINE_CLOSED_SEC,
        TTL_INDEX_KLINE_INTRADAY_SEC,
        TTL_INDEX_KLINE_WEEKLY_SEC,
    )

    if period == "weekly":
        return TTL_INDEX_KLINE_WEEKLY_SEC
    mode = _index_cache_mode(last_bar)
    return (
        TTL_INDEX_KLINE_INTRADAY_SEC
        if mode == "intraday"
        else _closed_ttl(TTL_INDEX_KLINE_CLOSED_SEC)
    )


def _snapshot_trade_date(snapshot: dict[str, dict[str, Any]] | None) -> str | None:
    if not snapshot:
        return None
    dates: list[str] = []
    for block in snapshot.values():
        d = block.get("daily") or {}
        if d.get("trade_date"):
            dates.append(str(d["trade_date"]))
    return max(dates) if dates else None


def _calendar_today() -> str:
    return datetime.now(_CN_TZ).date().strftime("%Y-%m-%d")


def _expected_trade_date() -> str | None:
    try:
        from config import latest_trading_date

        return latest_trading_date()
    except Exception:
        return None


def fetch_index_realtime_quotes(
    ash_codes: list[str] | None = None,
    *,
    force: bool = False,
) -> dict[str, dict[str, Any]]:
    """
    腾讯财经指数简要行情（s_ 前缀），盘中为实时点位，收盘后为当日收盘。
    返回: {ash_code: {price, change_amt, change_pct, volume, amount}}
    """
    from services.market_data_cache import TTL_INDEX_REALTIME_SEC

    global _realtime_cache
    now = time.time()
    if (
        not force
        and _realtime_cache.get("data")
        and now - _realtime_cache.get("ts", 0) < TTL_INDEX_REALTIME_SEC
    ):
        return _realtime_cache["data"]

    codes = ash_codes or [c for c, _ in DEFAULT_INDICES]
    prefixed = [f"s_{c}" for c in codes]
    try:
        from services.http_client import get as http_get

        url = "https://qt.gtimg.cn/q=" + ",".join(prefixed)
        r = http_get(url, headers={"User-Agent": "Mozilla/5.0"}, timeout=5)
        r.encoding = "gbk"
        body = r.text
    except Exception:
        stale = _realtime_cache.get("data")
        return stale if stale else {}

    out: dict[str, dict[str, Any]] = {}
    for line in body.strip().split(";"):
        if not line.strip() or "=" not in line or '"' not in line:
            continue
        key = line.split("=")[0].split("_")[-1]
        if not key.startswith(("sh", "sz")):
            continue
        vals = line.split('"')[1].split("~")
        if len(vals) < 6:
            continue
        out[key] = {
            "price": _safe_num(vals[3]),
            "change_amt": _safe_num(vals[4]),
            "change_pct": _safe_num(vals[5]),
            "volume": _safe_num(vals[6]) if len(vals) > 6 else None,
            "amount": _safe_num(vals[7]) if len(vals) > 7 else None,
        }
    if out:
        _realtime_cache = {"ts": now, "data": out}
    return out


def _snapshot_covers_latest(snapshot: dict[str, dict[str, Any]] | None) -> bool:
    """指数 K 线日期应不早于库内个股最新交易日。"""
    snap_dt = _snapshot_trade_date(snapshot)
    exp = _expected_trade_date()
    if not snap_dt or not exp:
        return True
    return snap_dt >= exp


def clear_market_index_cache() -> None:
    global _cache, _realtime_cache
    _cache = {"ts": 0.0, "data": None, "stale": False}
    _kline_cache["entries"] = {}
    _kline_cache["ts"] = 0.0
    _realtime_cache = {"ts": 0.0, "data": None}


def warm_market_index_cache() -> dict[str, Any]:
    """行情同步后预热指数缓存，避免页面读到过期内存数据。"""
    return fetch_market_index_snapshot(force=True)


def resolve_index_code(code: str) -> tuple[str, str] | None:
    """解析指数代码，返回 (ashare_code, display_name)。"""
    key = (code or "").strip()
    if not key:
        return None
    ash = _INDEX_CODE_ALIASES.get(key) or _INDEX_CODE_ALIASES.get(key.lower())
    if not ash and key.startswith(("sh", "sz")):
        ash = key.lower()
    if not ash:
        return None
    return ash, _INDEX_NAMES.get(ash, key)


# ashare 代码 -> Tushare ts_code（仅覆盖 DEFAULT_INDICES 这几个大盘指数）
_ASH_TO_TS_CODE: dict[str, str] = {
    "sh000001": "000001.SH",
    "sz399001": "399001.SZ",
    "sh000300": "000300.SH",
    "sh000906": "000906.SH",
    "sz399006": "399006.SZ",
}


def _fetch_tushare_index_kline(
    ash_code: str,
    *,
    frequency: str,
    count: int,
    end_date: str | None = None,
):
    """Ashare(新浪+腾讯) 失败/空数据时的兜底 —— Tushare 官方指数日线，更稳定。"""
    ts_code = _ASH_TO_TS_CODE.get(ash_code)
    if not ts_code:
        return None
    import pandas as pd
    from services.tushare_adapter import _pro

    pro = _pro()
    if end_date:
        end_dt = datetime.strptime(end_date[:10], "%Y-%m-%d").replace(tzinfo=_CN_TZ)
        end = end_dt.strftime("%Y%m%d")
    else:
        end_dt = datetime.now(_CN_TZ)
        end = end_dt.strftime("%Y%m%d")
    # 多留缓冲天数以覆盖非交易日/周线聚合需要的原始日数据
    lookback_days = int(count * (7 if frequency == "1w" else 1.6)) + 20
    start = (end_dt - timedelta(days=lookback_days)).strftime("%Y%m%d")
    df = pro.index_daily(ts_code=ts_code, start_date=start, end_date=end)
    if df is None or df.empty:
        return None
    df = df.sort_values("trade_date")
    df["trade_date"] = pd.to_datetime(df["trade_date"], format="%Y%m%d")
    out = df.set_index("trade_date")[["open", "high", "low", "close", "vol"]].rename(columns={"vol": "volume"})
    if frequency == "1w":
        out = out.resample("W").agg({
            "open": "first", "high": "max", "low": "min", "close": "last", "volume": "sum",
        }).dropna(subset=["close"])
    return out.tail(count)


def _safe_num(v: Any) -> float | None:
    if v is None:
        return None
    try:
        f = float(v)
        if f != f:  # NaN
            return None
        return round(f, 4)
    except (TypeError, ValueError):
        return None


def fetch_index_kline(
    code: str,
    *,
    period: str = "daily",
    days: int = 250,
    with_technical: bool = True,
    force: bool = False,
    end_date: str | None = None,
) -> dict[str, Any]:
    """
    拉取单只指数 K 线（日/周）及可选技术指标。
    period: daily | weekly
    end_date: 历史截断日 YYYY-MM-DD（回填 regime 用）
    """
    resolved = resolve_index_code(code)
    if not resolved:
        return {"error": f"未知指数代码: {code}", "kline": [], "technical": []}

    ash_code, name = resolved
    period = "weekly" if period in ("weekly", "week", "1w") else "daily"
    max_days = 800 if end_date else 500
    days = max(20, min(int(days), max_days))
    freq = "1w" if period == "weekly" else "1d"

    cache_key = f"{ash_code}:{period}:{days}:{with_technical}:{end_date or ''}"
    now = time.time()
    entries = _kline_cache.setdefault("entries", {})
    hit = entries.get(cache_key)
    if force:
        entries.pop(cache_key, None)
    elif hit:
        cached = hit["data"]
        last = (cached.get("kline") or [])[-1]["date"] if cached.get("kline") else None
        ttl = _kline_cache_ttl(last, period)
        if now - hit.get("ts", 0) < ttl:
            exp = _expected_trade_date()
            if last and exp and last >= exp:
                return cached

    df = None
    try:
        df = _fetch_tushare_index_kline(ash_code, frequency=freq, count=days, end_date=end_date)
    except Exception:
        df = None

    if (df is None or df.empty) and not end_date:
        # Tushare 无数据/异常时兜底：Ashare（新浪+腾讯）
        from services.Ashare import get_price

        try:
            df = get_price(ash_code, frequency=freq, count=days)
        except Exception as e:
            return {
                "code": ash_code,
                "name": name,
                "period": period,
                "error": str(e)[:120],
                "kline": [],
                "technical": [],
            }

    if df is None or df.empty:
        err = "暂无行情数据"
        if end_date:
            err = f"暂无 {end_date} 之前指数数据"
        return {
            "code": ash_code,
            "name": name,
            "period": period,
            "error": err,
            "kline": [],
            "technical": [],
        }

    import numpy as np

    opens = np.array(df["open"].values, dtype=float)
    highs = np.array(df["high"].values, dtype=float)
    lows = np.array(df["low"].values, dtype=float)
    closes = np.array(df["close"].values, dtype=float)
    vols = (
        np.array(df["volume"].values, dtype=float)
        if "volume" in df.columns
        else np.zeros(len(closes))
    )
    dates = df.index.strftime("%Y-%m-%d").tolist()

    kline: list[dict[str, Any]] = []
    technical: list[dict[str, Any]] = []
    macd_dif = macd_dea = macd_bar = None
    k = d = j = None
    rsi = upper = mid = lower = atr = None

    if with_technical and len(closes) >= 5:
        from services.MyTT import MACD, KDJ, RSI, BOLL, ATR

        macd_dif, macd_dea, macd_bar = MACD(closes)
        k, d, j = KDJ(closes, highs, lows)
        rsi = RSI(closes, 14)
        upper, mid, lower = BOLL(closes)
        atr = ATR(closes, highs, lows, 14)

    for i in range(len(dates)):
        kline.append({
            "date": dates[i],
            "open": _safe_num(opens[i]),
            "high": _safe_num(highs[i]),
            "low": _safe_num(lows[i]),
            "close": _safe_num(closes[i]),
            "volume": _safe_num(vols[i]),
        })
        if with_technical and macd_dif is not None:
            technical.append({
                "date": dates[i],
                "macd_dif": _safe_num(macd_dif[i]),
                "macd_dea": _safe_num(macd_dea[i]),
                "macd_bar": _safe_num(macd_bar[i]),
                "kdj_k": _safe_num(k[i]),
                "kdj_d": _safe_num(d[i]),
                "kdj_j": _safe_num(j[i]),
                "rsi14": _safe_num(rsi[i]),
                "boll_upper": _safe_num(upper[i]),
                "boll_mid": _safe_num(mid[i]),
                "boll_lower": _safe_num(lower[i]),
                "atr14": _safe_num(atr[i]),
            })

    last_bar = kline[-1]["date"] if kline else None
    result = {
        "code": ash_code,
        "name": name,
        "period": period,
        "count": len(kline),
        "kline": kline,
        "technical": technical,
        "updated_at": int(now),
        "as_of_trade_date": last_bar,
        "cache_mode": _index_cache_mode(last_bar),
    }
    entries[cache_key] = {"ts": now, "data": result}
    _kline_cache["ts"] = now
    return result


def fetch_market_index_snapshot(
    *,
    count_daily: int = 120,
    count_weekly: int = 60,
    indices: tuple[tuple[str, str], ...] | None = None,
    force: bool = False,
) -> dict[str, dict[str, Any]]:
    """
    拉取多只指数日/周最新指标摘要。
    返回: { "上证指数": { "code", "daily": {...}, "weekly": {...}, "change_5d_pct", "change_20d_pct" }, ... }
    """
    global _cache
    now = time.time()
    if force:
        clear_market_index_cache()
    elif (
        _cache["data"] is not None
        and now - _cache["ts"] < _snapshot_cache_ttl(_cache["data"])
        and _snapshot_covers_latest(_cache["data"])
        and not _cache.get("stale")
    ):
        return _cache["data"]

    from services.Ashare import get_price
    from services.tech_ai import compute_technical_indicators

    out: dict[str, dict[str, Any]] = {}
    fetch_errors: list[str] = []
    for ash_code, label in indices or DEFAULT_INDICES:
        try:
            df_d = get_price(ash_code, frequency="1d", count=count_daily)
            if df_d is None or df_d.empty or len(df_d) < 5:
                continue
            daily = compute_technical_indicators(
                df_d["close"].values,
                df_d["high"].values,
                df_d["low"].values,
                df_d["volume"].values if "volume" in df_d.columns else None,
            )
            closes = df_d["close"].values.astype(float)
            daily["close"] = round(float(closes[-1]), 2)
            daily["trade_date"] = df_d.index[-1].strftime("%Y-%m-%d")
            if len(closes) >= 2:
                daily["change_1d_pct"] = round((closes[-1] / closes[-2] - 1) * 100, 2)
            if len(closes) >= 6:
                daily["change_5d_pct"] = round((closes[-1] / closes[-6] - 1) * 100, 2)
            if len(closes) >= 21:
                daily["change_20d_pct"] = round((closes[-1] / closes[-21] - 1) * 100, 2)

            weekly: dict[str, Any] = {}
            df_w = get_price(ash_code, frequency="1w", count=count_weekly)
            if df_w is not None and not df_w.empty:
                weekly = compute_technical_indicators(
                    df_w["close"].values,
                    df_w["high"].values,
                    df_w["low"].values,
                )
                weekly["close"] = round(float(df_w["close"].values[-1]), 2)

            out[label] = {
                "code": ash_code,
                "daily": daily,
                "weekly": weekly,
            }
        except Exception as e:
            fetch_errors.append(f"{label}:{str(e)[:60]}")
            continue

    if out:
        _cache["ts"] = now
        _cache["data"] = out
        _cache["stale"] = False
        return out

    # 腾讯/新浪均不可用时，仅在短时间内回退过期缓存
    stale = _cache.get("data")
    if stale and now - _cache.get("ts", 0) < _STALE_CACHE_MAX_SEC:
        _cache["stale"] = True
        return stale

    if fetch_errors:
        raise RuntimeError("; ".join(fetch_errors[:3]))
    return out


def format_market_index_text(snapshot: dict[str, dict[str, Any]] | None) -> str:
    """拼入 LLM user prompt 的大盘段落。"""
    if not snapshot:
        return "（大盘指数数据暂不可用）"

    def fmt(v: Any) -> str:
        if v is None:
            return "N/A"
        return f"{v:.2f}" if isinstance(v, float) else str(v)

    lines: list[str] = []
    for label, block in snapshot.items():
        d = block.get("daily") or {}
        w = block.get("weekly") or {}
        extra = ""
        if d.get("change_5d_pct") is not None:
            extra = f" | 5日涨跌{d['change_5d_pct']:+.2f}%"
        if d.get("change_20d_pct") is not None:
            extra += f" | 20日涨跌{d['change_20d_pct']:+.2f}%"
        lines.append(
            f"【{label}】收盘 {fmt(d.get('close'))}{extra}\n"
            f"  日线: MA5={fmt(d.get('ma5'))} MA20={fmt(d.get('ma20'))} "
            f"MACD柱={fmt(d.get('macd_bar'))} RSI={fmt(d.get('rsi14'))} "
            f"BOLL上/中/下={fmt(d.get('boll_upper'))}/{fmt(d.get('boll_mid'))}/{fmt(d.get('boll_lower'))}\n"
            f"  周线: 收盘={fmt(w.get('close'))} RSI={fmt(w.get('rsi14'))} "
            f"MACD柱={fmt(w.get('macd_bar'))}"
        )
    return "\n".join(lines)


def _index_signal(daily: dict[str, Any]) -> str:
    """单指数短线环境标签（与 tech_ai 规则口径接近）。"""
    ch5 = daily.get("change_5d_pct")
    rsi = daily.get("rsi14")
    ma5, ma20 = daily.get("ma5"), daily.get("ma20")
    if ch5 is not None and ch5 > 1.5 and (rsi or 0) >= 52:
        return "偏多"
    if ch5 is not None and ch5 < -1.5 and (rsi or 50) <= 48:
        return "偏空"
    if ma5 and ma20:
        if ma5 > ma20 * 1.002:
            return "偏多"
        if ma5 < ma20 * 0.998:
            return "偏空"
    return "震荡"


def _aggregate_environment(indices: list[dict[str, Any]]) -> tuple[str, str]:
    """综合多只指数 5 日涨跌与 RSI，给出大盘环境。"""
    if not indices:
        return "震荡", "指数数据暂不可用"
    ch5_vals = [i["change_5d_pct"] for i in indices if i.get("change_5d_pct") is not None]
    rsi_vals = [i["rsi14"] for i in indices if i.get("rsi14") is not None]
    avg5 = sum(ch5_vals) / len(ch5_vals) if ch5_vals else 0.0
    avg_rsi = sum(rsi_vals) / len(rsi_vals) if rsi_vals else 50.0
    bullish = sum(1 for i in indices if i.get("signal") == "偏多")
    bearish = sum(1 for i in indices if i.get("signal") == "偏空")

    if avg5 > 1.5 or bullish >= 2:
        env = "偏多"
    elif avg5 < -1.5 or bearish >= 2:
        env = "偏空"
    else:
        env = "震荡"

    parts = [f"5日均涨跌 {avg5:+.2f}%"]
    if rsi_vals:
        parts.append(f"均RSI {avg_rsi:.0f}")
    parts.append(f"{bullish}涨/{bearish}跌/{len(indices) - bullish - bearish}震")
    return env, " · ".join(parts)


def snapshot_to_api_payload(snapshot: dict[str, dict[str, Any]] | None) -> dict[str, Any]:
    """供前端展示的结构化大盘指数数据。"""
    calendar_today = _calendar_today()
    if not snapshot:
        return {
            "updated_at": int(time.time()),
            "calendar_date": calendar_today,
            "environment": "震荡",
            "environment_comment": "指数数据暂不可用",
            "indices": [],
            "available": False,
        }

    realtime = fetch_index_realtime_quotes()
    indices: list[dict[str, Any]] = []
    has_realtime = False
    for label, block in snapshot.items():
        d = block.get("daily") or {}
        w = block.get("weekly") or {}
        signal = _index_signal(d)
        ash = block.get("code", "")
        rt = realtime.get(ash) or {}
        last = rt.get("price")
        if last is not None:
            has_realtime = True
        ch1 = rt.get("change_pct")
        if ch1 is None:
            ch1 = d.get("change_1d_pct")
        indices.append({
            "name": label,
            "code": ash,
            "close": d.get("close"),
            "last": last,
            "change_1d_pct": ch1,
            "change_pct_today": rt.get("change_pct"),
            "change_amt_today": rt.get("change_amt"),
            "change_5d_pct": d.get("change_5d_pct"),
            "change_20d_pct": d.get("change_20d_pct"),
            "rsi14": d.get("rsi14"),
            "macd_bar": d.get("macd_bar"),
            "ma5": d.get("ma5"),
            "ma20": d.get("ma20"),
            "weekly_rsi14": w.get("rsi14"),
            "signal": signal,
        })

    env, comment = _aggregate_environment(indices)
    trade_dates: list[str] = []
    for block in snapshot.values():
        d = block.get("daily") or {}
        if d.get("trade_date"):
            trade_dates.append(str(d["trade_date"]))
    as_of = max(trade_dates) if trade_dates else None
    exp = _expected_trade_date()
    behind_db = bool(as_of and exp and as_of < exp)
    behind_calendar = bool(as_of and as_of < calendar_today)
    quote_mode = "realtime" if has_realtime and behind_calendar else "daily"
    stale = behind_db or (behind_calendar and not has_realtime)

    extra = ""
    if behind_db:
        extra = f" · 指数日线滞后(截至{as_of})"
    elif behind_calendar and has_realtime:
        extra = f" · 技术指标基于{as_of}收盘，点位为实时"
    elif behind_calendar:
        extra = f" · 日线尚未含今日(截至{as_of})"

    tech_cache_mode = _index_cache_mode(as_of)
    return {
        "updated_at": int(time.time()),
        "calendar_date": calendar_today,
        "as_of_trade_date": as_of,
        "expected_trade_date": exp,
        "quote_mode": quote_mode,
        "tech_cache_mode": tech_cache_mode,
        "stale": stale,
        "environment": env,
        "environment_comment": f"{comment}{extra}",
        "indices": indices,
        "available": len(indices) > 0,
    }


def market_hash_part(snapshot: dict[str, dict[str, Any]] | None) -> str:
    """供 tech 缓存 hash 使用。"""
    if not snapshot:
        return ""
    parts: list[str] = []
    for label in sorted(snapshot.keys()):
        d = (snapshot[label].get("daily") or {})
        parts.append(
            f"{label}:{d.get('close','')}|{d.get('rsi14','')}|{d.get('macd_bar','')}"
        )
    return "|".join(parts)
