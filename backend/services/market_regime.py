"""市场状态分类（趋势/震荡/高波动/流动性枯竭）。

Phase A：指数 K 线 + 全市场广度（涨跌家数、成交额、行业轮动、相关性）。
Phase B：两层规则分类，输出 regime + regime_label，供 V5 动态权重与前端展示。
"""
from __future__ import annotations

import math
import sqlite3
from datetime import date, timedelta
from typing import Any, Optional

import config
from services.market_index import fetch_index_kline

REGIME_ORDER = [
    "liquidity_drought",
    "high_volatility",
    "strong_trend_up",
    "strong_trend_down",
    "weak_trend_up",
    "weak_trend_down",
    "oscillation",
]

REGIME_LABELS: dict[str, str] = {
    "strong_trend_up": "趋势上涨",
    "weak_trend_up": "趋势上涨",
    "strong_trend_down": "趋势下跌",
    "weak_trend_down": "趋势下跌",
    "oscillation": "震荡",
    "high_volatility": "高波动",
    "liquidity_drought": "流动性枯竭",
}

CORR_SAMPLE_SIZE = 120


def regime_label(regime: str) -> str:
    return REGIME_LABELS.get(regime, regime)


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
    if len(closes) < window * 2 + 1:
        return 25.0
    trs = []
    plus_dms = []
    minus_dms = []
    for i in range(-window, 0):
        h, l = highs[i], lows[i]
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
    return abs(plus_di - minus_di) / (plus_di + minus_di + 1e-9) * 100


def _pearson(x: list[float], y: list[float]) -> float:
    n = len(x)
    if n < 3 or len(y) != n:
        return 0.0
    mx = sum(x) / n
    my = sum(y) / n
    num = sum((x[i] - mx) * (y[i] - my) for i in range(n))
    den = math.sqrt(sum((x[i] - mx) ** 2 for i in range(n)) * sum((y[i] - my) ** 2 for i in range(n)))
    return num / den if den > 1e-12 else 0.0


def _spearman(x: list[float], y: list[float]) -> float:
    n = len(x)
    if n < 3 or len(y) != n:
        return 0.0

    def _rank(values: list[float]) -> list[float]:
        order = sorted(range(n), key=lambda i: values[i])
        ranks = [0.0] * n
        for ri, idx in enumerate(order):
            ranks[idx] = ri + 1.0
        return ranks

    return _pearson(_rank(x), _rank(y))


def _ma20_slope(closes: list[float]) -> float:
    if len(closes) < 25:
        return 0.0
    ma_now = _ma(closes, 20)
    ma_prev = sum(closes[-25:-5]) / 20 if len(closes) >= 25 else ma_now
    if abs(ma_prev) < 1e-12:
        return 0.0
    return ma_now / ma_prev - 1


def _trade_dates(conn: sqlite3.Connection, trade_date: str, limit: int) -> list[str]:
    rows = conn.execute(
        """SELECT DISTINCT trade_date FROM stock_daily_quotes
           WHERE trade_date <= ? AND close IS NOT NULL
           ORDER BY trade_date DESC LIMIT ?""",
        (trade_date, limit),
    ).fetchall()
    return [r[0] for r in rows]


def _compute_ad_ratio(conn: sqlite3.Connection, trade_date: str) -> Optional[float]:
    prev = conn.execute(
        "SELECT MAX(trade_date) FROM stock_daily_quotes WHERE trade_date < ? AND close IS NOT NULL",
        (trade_date,),
    ).fetchone()
    if not prev or not prev[0]:
        return None
    row = conn.execute(
        """SELECT
             SUM(CASE WHEN q0.close > q1.close THEN 1 ELSE 0 END),
             SUM(CASE WHEN q0.close < q1.close THEN 1 ELSE 0 END)
           FROM stock_daily_quotes q0
           JOIN stock_daily_quotes q1 ON q0.stock_id = q1.stock_id AND q1.trade_date = ?
           WHERE q0.trade_date = ? AND q0.close IS NOT NULL AND q1.close IS NOT NULL AND q1.close > 0""",
        (prev[0], trade_date),
    ).fetchone()
    if not row or row[0] is None:
        return None
    adv, dec = int(row[0] or 0), int(row[1] or 0)
    total = adv + dec
    if total <= 0:
        return None
    return adv / total


def _compute_amount_metrics(conn: sqlite3.Connection, trade_date: str) -> tuple[Optional[float], Optional[float]]:
    rows = conn.execute(
        """SELECT trade_date, SUM(COALESCE(amount, 0)) AS total
           FROM stock_daily_quotes
           WHERE trade_date <= ? AND close IS NOT NULL
           GROUP BY trade_date
           ORDER BY trade_date DESC LIMIT 21""",
        (trade_date,),
    ).fetchall()
    if len(rows) < 6:
        return None, None
    totals = [float(r[1] or 0) for r in rows if float(r[1] or 0) > 0]
    if len(totals) < 6:
        return None, None
    today = totals[0]
    hist = totals[1:21]
    if not hist:
        return None, None
    ma20 = sum(hist) / len(hist)
    amount_ratio_20 = today / ma20 if ma20 > 0 else None
    recent5 = totals[:5][::-1]
    if len(recent5) >= 2:
        xs = list(range(len(recent5)))
        mx = sum(xs) / len(xs)
        my = sum(recent5) / len(recent5)
        num = sum((xs[i] - mx) * (recent5[i] - my) for i in range(len(recent5)))
        den = sum((xs[i] - mx) ** 2 for i in range(len(recent5)))
        amount_slope_5 = num / den if den > 1e-12 else 0.0
    else:
        amount_slope_5 = 0.0
    return amount_ratio_20, amount_slope_5


def _industry_returns(
    conn: sqlite3.Connection,
    end_date: str,
    start_date: str,
) -> dict[str, float]:
    rows = conn.execute(
        """SELECT s.industry_sw, q0.close AS c0, q1.close AS c1
           FROM stocks s
           JOIN stock_daily_quotes q0 ON q0.stock_id = s.id AND q0.trade_date = ?
           JOIN stock_daily_quotes q1 ON q1.stock_id = s.id AND q1.trade_date = ?
           WHERE s.is_active = 1 AND s.industry_sw IS NOT NULL AND s.industry_sw != ''
             AND q0.close IS NOT NULL AND q1.close IS NOT NULL AND q1.close > 0""",
        (end_date, start_date),
    ).fetchall()
    by_ind: dict[str, list[float]] = {}
    for ind, c0, c1 in rows:
        if not ind:
            continue
        by_ind.setdefault(str(ind), []).append(float(c0) / float(c1) - 1)
    return {ind: sum(v) / len(v) for ind, v in by_ind.items() if v}


def _compute_rotation_speed(conn: sqlite3.Connection, trade_date: str) -> Optional[float]:
    dates = _trade_dates(conn, trade_date, 11)
    if len(dates) < 6:
        return None
    recent = _industry_returns(conn, dates[0], dates[5])
    prior = _industry_returns(conn, dates[5], dates[min(10, len(dates) - 1)])
    common = sorted(set(recent.keys()) & set(prior.keys()))
    if len(common) < 5:
        return None
    x = [recent[i] for i in common]
    y = [prior[i] for i in common]
    corr = _spearman(x, y)
    return max(0.0, min(1.0, 1.0 - corr))


def _compute_avg_corr(conn: sqlite3.Connection, trade_date: str) -> Optional[float]:
    dates = _trade_dates(conn, trade_date, 21)
    if len(dates) < 21:
        return None
    date_set = set(dates)
    rows = conn.execute(
        """SELECT stock_id, trade_date, close
           FROM stock_daily_quotes
           WHERE trade_date IN ({}) AND close IS NOT NULL AND close > 0
           ORDER BY stock_id, trade_date""".format(",".join("?" * len(dates))),
        tuple(dates),
    ).fetchall()
    by_sid: dict[int, dict[str, float]] = {}
    for sid, td, close in rows:
        by_sid.setdefault(int(sid), {})[td] = float(close)

    candidates = [
        sid for sid, px in by_sid.items()
        if len(px) >= 21 and dates[0] in px and dates[-1] in px
    ]
    if len(candidates) < 20:
        return None
    # 按最近一日成交额抽样，降低计算量
    amt_rows = conn.execute(
        """SELECT stock_id, amount FROM stock_daily_quotes
           WHERE trade_date = ? AND amount IS NOT NULL
           ORDER BY amount DESC LIMIT ?""",
        (dates[0], CORR_SAMPLE_SIZE * 2),
    ).fetchall()
    sample_ids = [int(r[0]) for r in amt_rows if int(r[0]) in by_sid][:CORR_SAMPLE_SIZE]
    if len(sample_ids) < 20:
        sample_ids = candidates[:CORR_SAMPLE_SIZE]

    ordered_dates = sorted(date_set)
    series: list[list[float]] = []
    for sid in sample_ids:
        px = by_sid[sid]
        closes = [px[d] for d in ordered_dates if d in px]
        if len(closes) < 21:
            continue
        rets = [closes[i] / closes[i - 1] - 1 for i in range(1, len(closes)) if closes[i - 1] > 0]
        if len(rets) >= 20:
            series.append(rets[-20:])
    if len(series) < 10:
        return None

    corrs: list[float] = []
    n = len(series)
    for i in range(n):
        for j in range(i + 1, n):
            c = _pearson(series[i], series[j])
            if math.isfinite(c):
                corrs.append(c)
    return sum(corrs) / len(corrs) if corrs else None


def compute_market_features(conn: sqlite3.Connection, trade_date: str) -> dict[str, Any]:
    """Phase A：全市场广度特征（仅使用 trade_date 及之前数据）。"""
    ad_ratio = _compute_ad_ratio(conn, trade_date)
    amount_ratio_20, amount_slope_5 = _compute_amount_metrics(conn, trade_date)
    rotation_speed = _compute_rotation_speed(conn, trade_date)
    avg_corr_20 = _compute_avg_corr(conn, trade_date)

    liquidity_score = None
    if amount_ratio_20 is not None:
        liquidity_score = max(0.0, min(1.0, amount_ratio_20))

    return {
        "ad_ratio": ad_ratio,
        "amount_ratio_20": amount_ratio_20,
        "amount_slope_5": amount_slope_5,
        "rotation_speed": rotation_speed,
        "avg_corr_20": avg_corr_20,
        "liquidity_score": liquidity_score,
    }


def _classify_index_only(
    rsi: float,
    vol: float,
    adx: float,
    ret20: float,
    ret60: float,
    price_vs_ma20: float,
    price_vs_ma60: float,
    ma20_slope: float,
) -> str:
    """无全市场特征时的指数规则（兼容旧逻辑）。"""
    if vol > 0.30 or (adx > 50 and abs(ret20) < 0.03):
        return "high_volatility"
    if rsi > 55 and ret20 > 0.03 and ret60 > 0.05 and price_vs_ma20 > 0 and price_vs_ma60 > 0:
        return "strong_trend_up"
    if rsi < 45 and ret20 < -0.03 and ret60 < -0.05 and price_vs_ma20 < 0 and price_vs_ma60 < 0:
        return "strong_trend_down"
    if rsi > 50 and ret20 > 0 and price_vs_ma20 > 0 and ma20_slope > 0:
        return "weak_trend_up"
    if rsi < 50 and ret20 < 0 and price_vs_ma20 < 0 and ma20_slope < 0:
        return "weak_trend_down"
    return "oscillation"


def _classify_with_features(
    rsi: float,
    vol: float,
    adx: float,
    ret20: float,
    ret60: float,
    price_vs_ma20: float,
    price_vs_ma60: float,
    ma20_slope: float,
    features: dict[str, Any],
) -> str:
    """Phase B：两层规则（流动性 → 波动 → 趋势）。"""
    ad_ratio = features.get("ad_ratio")
    amount_ratio_20 = features.get("amount_ratio_20")
    avg_corr_20 = features.get("avg_corr_20")
    rotation_speed = features.get("rotation_speed")

    # 第一层：流动性枯竭
    if amount_ratio_20 is not None and amount_ratio_20 < 0.60 and vol < 0.28:
        return "liquidity_drought"
    if amount_ratio_20 is not None and amount_ratio_20 < 0.55:
        return "liquidity_drought"

    # 第二层：高波动 / 系统性风险
    if vol > 0.30:
        return "high_volatility"
    if avg_corr_20 is not None and avg_corr_20 > 0.65:
        return "high_volatility"
    if adx > 50 and abs(ret20) < 0.03:
        return "high_volatility"

    # 第三层：趋势（需广度确认）
    breadth_up = ad_ratio is None or ad_ratio > 0.55
    breadth_down = ad_ratio is None or ad_ratio < 0.45
    fast_rotation = rotation_speed is not None and rotation_speed > 0.70

    if ret20 > 0.03 and ma20_slope > 0 and breadth_up and not fast_rotation:
        if ret60 > 0.05 and price_vs_ma60 > 0 and rsi > 55:
            return "strong_trend_up"
        return "weak_trend_up"

    if ret20 < -0.03 and ma20_slope < 0 and breadth_down and not fast_rotation:
        if ret60 < -0.05 and price_vs_ma60 < 0 and rsi < 45:
            return "strong_trend_down"
        return "weak_trend_down"

    # 指数涨但广度差 → 震荡（假突破）
    if ret20 > 0.02 and ad_ratio is not None and ad_ratio < 0.50:
        return "oscillation"
    if ret20 < -0.02 and ad_ratio is not None and ad_ratio > 0.50:
        return "oscillation"

    return "oscillation"


def classify_regime(
    kline: list[dict[str, Any]],
    features: Optional[dict[str, Any]] = None,
) -> dict[str, Any]:
    """基于指数 K 线 + 可选全市场特征分类市场状态。"""
    empty = {
        "regime": "oscillation",
        "regime_label": regime_label("oscillation"),
        "rsi_14": 50.0,
        "volatility_20": 0.0,
        "adx": 25.0,
        "return_20d": 0.0,
        "return_60d": 0.0,
        "price_vs_ma20": 0.0,
        "price_vs_ma60": 0.0,
        "ma20_slope": 0.0,
    }
    if not kline or len(kline) < 65:
        return {**empty, **(features or {})}

    closes = [float(b["close"]) for b in kline if _is_valid(b.get("close"))]
    highs = [float(b["high"]) for b in kline if _is_valid(b.get("high"))]
    lows = [float(b["low"]) for b in kline if _is_valid(b.get("low"))]
    if len(closes) < 65:
        return {**empty, **(features or {})}

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
    ma20_slope = _ma20_slope(closes)

    use_features = features is not None and features.get("ad_ratio") is not None
    if use_features:
        regime = _classify_with_features(
            rsi, vol, adx, ret20, ret60, price_vs_ma20, price_vs_ma60, ma20_slope, features
        )
    else:
        regime = _classify_index_only(
            rsi, vol, adx, ret20, ret60, price_vs_ma20, price_vs_ma60, ma20_slope
        )

    out = {
        "regime": regime,
        "regime_label": regime_label(regime),
        "rsi_14": rsi,
        "volatility_20": vol,
        "adx": adx,
        "return_20d": ret20,
        "return_60d": ret60,
        "price_vs_ma20": price_vs_ma20,
        "price_vs_ma60": price_vs_ma60,
        "ma20_slope": ma20_slope,
    }
    if features:
        out.update({k: v for k, v in features.items() if k not in out})
    return out


def detect_regime(
    trade_date: Optional[str] = None,
    index_code: str = "sh000300",
    conn: Optional[sqlite3.Connection] = None,
) -> dict[str, Any]:
    """拉取指数 K 线并分类。trade_date 仅用于校验最后一条日期。"""
    data = fetch_index_kline(index_code, period="daily", days=120, with_technical=False)
    kline = data.get("kline") or []
    if not kline:
        return {"regime": "oscillation", "regime_label": regime_label("oscillation"), "error": data.get("error", "无指数数据")}

    last_date = kline[-1].get("date") if kline else None
    if trade_date and last_date and last_date != trade_date:
        truncated = [b for b in kline if b.get("date") <= trade_date]
        if len(truncated) >= 65:
            kline = truncated
            last_date = truncated[-1].get("date")

    features = None
    own_conn = None
    if last_date:
        try:
            if conn is None:
                own_conn = sqlite3.connect(config.DB_PATH, timeout=30)
                conn = own_conn
            features = compute_market_features(conn, last_date)
        except Exception:
            features = None
        finally:
            if own_conn is not None:
                own_conn.close()

    result = classify_regime(kline, features=features)
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
        return {"regime": "oscillation", "regime_label": regime_label("oscillation")}
    if hasattr(row, "keys"):
        out = dict(row)
    else:
        cols = [d[0] for d in conn.execute("PRAGMA table_info(market_regime_daily)")]
        out = dict(zip(cols, row))
    if not out.get("regime_label"):
        out["regime_label"] = regime_label(str(out.get("regime", "oscillation")))
    return out


def sync_regime(
    conn: sqlite3.Connection,
    trade_date: Optional[str] = None,
    index_code: str = "sh000300",
) -> dict[str, Any]:
    """计算并写入 market_regime_daily，返回结果。"""
    result = detect_regime(trade_date=trade_date, index_code=index_code, conn=conn)
    if "error" in result:
        return result

    dt = result.get("trade_date")
    if not dt:
        return result

    conn.execute(
        """INSERT OR REPLACE INTO market_regime_daily
           (trade_date, index_code, regime, regime_label, rsi_14, volatility_20, adx,
            return_20d, return_60d, price_vs_ma20, price_vs_ma60, ma20_slope,
            ad_ratio, amount_ratio_20, amount_slope_5, rotation_speed, avg_corr_20,
            liquidity_score, updated_at)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, datetime('now'))""",
        (
            dt,
            index_code,
            result["regime"],
            result.get("regime_label") or regime_label(result["regime"]),
            result["rsi_14"],
            result["volatility_20"],
            result["adx"],
            result["return_20d"],
            result["return_60d"],
            result["price_vs_ma20"],
            result["price_vs_ma60"],
            result.get("ma20_slope"),
            result.get("ad_ratio"),
            result.get("amount_ratio_20"),
            result.get("amount_slope_5"),
            result.get("rotation_speed"),
            result.get("avg_corr_20"),
            result.get("liquidity_score"),
        ),
    )
    conn.commit()
    return result
