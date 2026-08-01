"""统一选股 — 回测与模拟盘共用 Top N 逻辑。"""
from __future__ import annotations

import math
import sqlite3
from typing import List, Optional

import config
from config import DB_PATH
from services.strategy_registry import (
    effective_v5_strategy,
    get_meta,
    is_factor_id,
    normalize_strategy_id,
)
from services.strategies.sector_rotation_select import (
    sector_rotation_day_scores,
    select_sector_rotation,
)
from services.strategies.turtle import turtle_score
from services.score_sql import per_stock_latest_join, per_stock_latest_quality_join
from services.strategy_types import SelectedStock
from services.v5_score_query import fetch_latest_top_n, resolve_score_spec


def _current_regime(conn: sqlite3.Connection) -> str:
    try:
        row = conn.execute(
            """SELECT regime FROM market_regime_daily
               ORDER BY trade_date DESC LIMIT 1"""
        ).fetchone()
        return str(row[0]) if row else "oscillation"
    except sqlite3.OperationalError:
        return "oscillation"


def _row_to_selected(r: sqlite3.Row | dict) -> SelectedStock:
    d = dict(r)
    return SelectedStock(
        stock_id=int(d["stock_id"]),
        code=str(d["code"]),
        name=str(d.get("name") or ""),
        score=float(d["score"]),
    )


def _select_v5(
    conn: sqlite3.Connection,
    strategy: str,
    min_score: float,
    top_n: int,
) -> List[SelectedStock]:
    spec = resolve_score_spec(effective_v5_strategy(strategy))
    if not spec:
        return []
    rows = fetch_latest_top_n(conn, spec, min_score, top_n)
    return [_row_to_selected(r) for r in rows]


def _momentum_score_from_quotes(rows: list) -> Optional[float]:
    """rows: DESC 序 (trade_date, close, volume)。"""
    if len(rows) < 12:
        return None
    rows = list(reversed(rows))
    closes = [float(r[1]) for r in rows if r[1]]
    volumes = [float(r[2] or 0) for r in rows]
    if len(closes) < 12:
        return None
    rets = [(closes[i] / closes[i - 1]) - 1 for i in range(1, len(closes))]
    momentum = sum(rets[-10:]) if rets else 0
    mom20 = sum(rets[-20:]) if len(rets) >= 20 else momentum
    # 动量崩溃惩罚：10日动量转负且近期加速下行
    if mom20 < 0 and momentum < mom20:
        return None
    avg_r = sum(rets) / len(rets) if rets else 0
    vol = math.sqrt(sum((x - avg_r) ** 2 for x in rets) / len(rets)) if rets else 0
    low_vol = 1 / (vol + 0.001) if vol > 0 else 100
    if len(volumes) > 5 and sum(volumes) > 0:
        vols_avg = sum(volumes) / len(volumes)
        vol_stab = 1 / (abs(volumes[-1] / vols_avg - 1) + 0.1)
    else:
        vol_stab = 0
    return round(momentum * 0.4 * 100 + low_vol * 0.35 * 0.01 + vol_stab * 0.25 * 50 + 50, 2)


def momentum_crash_reason(rows: list) -> str | None:
    """检测单票动量崩溃：5日跌幅超阈值且10日动量转负。"""
    if len(rows) < 12:
        return None
    rows = list(reversed(rows))
    closes = [float(r[1]) for r in rows if r[1]]
    if len(closes) < 6:
        return None
    rets = [(closes[i] / closes[i - 1]) - 1 for i in range(1, len(closes))]
    ret5 = (closes[-1] / closes[-6] - 1) * 100 if len(closes) >= 6 else 0
    mom10 = sum(rets[-10:]) if len(rets) >= 10 else sum(rets)
    if ret5 <= config.MOMENTUM_CRASH_5D_PCT and mom10 < 0:
        return "momentum_crash"
    return None


def _select_momentum(
    conn: sqlite3.Connection,
    min_score: float,
    top_n: int,
    lookback: int = 20,
) -> List[SelectedStock]:
    regime = _current_regime(conn)
    eff_min = min_score
    if regime in config.MOMENTUM_BEAR_REGIMES:
        eff_min = min_score + config.MOMENTUM_BEAR_MIN_SCORE_BONUS
    stocks = conn.execute(
        "SELECT id, code, name FROM stocks WHERE is_active=1"
    ).fetchall()
    scored: list[SelectedStock] = []
    need = lookback + 5
    for sid, code, name in stocks:
        qrows = conn.execute(
            """SELECT trade_date, close, volume FROM stock_daily_quotes
               WHERE stock_id=? AND close IS NOT NULL
               ORDER BY trade_date DESC LIMIT ?""",
            (sid, need),
        ).fetchall()
        sc = _momentum_score_from_quotes(qrows)
        if sc is not None and sc >= eff_min:
            scored.append(SelectedStock(int(sid), code, name or "", sc))
    scored.sort(key=lambda x: -x.score)
    return scored[:top_n]


def _select_reversal(
    conn: sqlite3.Connection,
    min_score: float,
    top_n: int,
) -> List[SelectedStock]:
    latest = conn.execute(
        "SELECT MAX(date) FROM factor_values WHERE factor_id='F020'",
    ).fetchone()
    if not latest or not latest[0]:
        return []
    dt = latest[0]
    join_cs = per_stock_latest_join("cs")
    rows = conn.execute(
        f"""
        SELECT DISTINCT s.id AS stock_id, s.code, s.name, fv.value AS score
        FROM factor_values fv
        JOIN stocks s ON fv.stock_id = s.id
        {join_cs}
        WHERE fv.factor_id='F020' AND fv.date=? AND s.is_active=1
          AND fv.value IS NOT NULL AND fv.value >= ?
          AND COALESCE(cs.quality_score, 0) >= 40
          AND COALESCE(cs.veto_status, '') != 'exclude'
        ORDER BY fv.value DESC LIMIT ?
        """,
        (dt, min_score, top_n),
    ).fetchall()
    return [_row_to_selected(r) for r in rows]


def _select_dividend_defensive(
    conn: sqlite3.Connection,
    min_score: float,
    top_n: int,
) -> List[SelectedStock]:
    join_cs = per_stock_latest_quality_join("cs")
    rows = conn.execute(
        f"""
        SELECT s.id AS stock_id, s.code, s.name,
               cs.quality_score, cs.val_score, vs.dividend_yield
        FROM stocks s
        {join_cs}
        LEFT JOIN (
            SELECT stock_id,
                   COALESCE(dividend_yield, dividend_yield_ttm) AS dividend_yield
            FROM valuation_snapshots v1
            WHERE COALESCE(v1.dividend_yield, v1.dividend_yield_ttm) IS NOT NULL
              AND v1.as_of_date = (
                SELECT MAX(v2.as_of_date) FROM valuation_snapshots v2
                WHERE v2.stock_id = v1.stock_id
                  AND COALESCE(v2.dividend_yield, v2.dividend_yield_ttm) IS NOT NULL
              )
        ) vs ON vs.stock_id = s.id
        WHERE s.is_active=1
          AND COALESCE(cs.quality_score, 0) >= 50
          AND COALESCE(vs.dividend_yield, 0) >= 2.0
          AND COALESCE(cs.veto_status, '') != 'exclude'
        """,
    ).fetchall()
    scored: list[SelectedStock] = []
    for r in rows:
        q = float(r["quality_score"] or 0)
        v = float(r["val_score"] or 0)
        dy = float(r["dividend_yield"] or 0)
        score = round(0.4 * q + 0.3 * v + 0.3 * min(dy * 10, 100), 2)
        if score >= min_score:
            scored.append(
                SelectedStock(int(r["stock_id"]), str(r["code"]), str(r["name"] or ""), score)
            )
    scored.sort(key=lambda x: -x.score)
    return scored[:top_n]


def _resolve_factor_id(strategy: str, combination_id: int | None) -> Optional[str]:
    key = normalize_strategy_id(strategy)
    meta = get_meta(key)
    if meta and meta.factor_id:
        return meta.factor_id
    if is_factor_id(key):
        return key.upper()
    if key == "factor_combination" or combination_id:
        if not combination_id:
            return None
        from services.factor_combinations import get_combination

        combo = get_combination(combination_id)
        if not combo:
            return None
        return combo.get("output_factor_id")
    return None


def _select_factor(
    conn: sqlite3.Connection,
    factor_id: str,
    min_score: float,
    top_n: int,
) -> List[SelectedStock]:
    latest = conn.execute(
        "SELECT MAX(date) FROM factor_values WHERE factor_id=?",
        (factor_id,),
    ).fetchone()
    if not latest or not latest[0]:
        return []
    dt = latest[0]
    rows = conn.execute(
        """SELECT s.id AS stock_id, s.code, s.name, fv.value AS score
           FROM factor_values fv
           JOIN stocks s ON fv.stock_id = s.id
           WHERE fv.factor_id=? AND fv.date=? AND s.is_active=1
             AND fv.value IS NOT NULL AND fv.value >= ?
           ORDER BY fv.value DESC LIMIT ?""",
        (factor_id, dt, min_score, top_n),
    ).fetchall()
    return [_row_to_selected(r) for r in rows]


def _turtle_live_score(
    conn: sqlite3.Connection,
    stock_id: int,
    entry: int = 20,
) -> Optional[float]:
    rows = conn.execute(
        """SELECT trade_date, close, high, low FROM stock_daily_quotes
           WHERE stock_id=? AND close IS NOT NULL
           ORDER BY trade_date DESC LIMIT ?""",
        (stock_id, entry + 2),
    ).fetchall()
    if len(rows) < entry + 1:
        return None
    rows = list(reversed(rows))
    dates = [str(r[0]) for r in rows]
    series = {
        str(r[0]): {
            "close": float(r[1]),
            "high": float(r[2] or r[1]),
            "low": float(r[3] or r[1]),
        }
        for r in rows
    }
    return turtle_score(series, dates, len(dates) - 1, entry=entry)


def _select_turtle(
    conn: sqlite3.Connection,
    min_score: float,
    top_n: int,
    lookback: int = 20,
) -> List[SelectedStock]:
    entry = max(10, lookback)
    stocks = conn.execute(
        "SELECT id, code, name FROM stocks WHERE is_active=1"
    ).fetchall()
    scored: list[SelectedStock] = []
    for sid, code, name in stocks:
        sc = _turtle_live_score(conn, int(sid), entry=entry)
        if sc is not None and sc >= min_score:
            scored.append(SelectedStock(int(sid), code, name or "", sc))
    scored.sort(key=lambda x: -x.score)
    return scored[:top_n]


def select_top_n(
    conn: sqlite3.Connection | None = None,
    *,
    strategy: str = "composite",
    top_n: int = 5,
    min_score: float = 50.0,
    combination_id: int | None = None,
    lookback: int = 20,
    sector_window: int = 5,
    per_sector: int = 2,
) -> tuple[List[SelectedStock], str | None]:
    """
    返回 (选股列表, error)。
    error 非空时列表为空。
    """
    external = conn is not None
    if conn is None:
        conn = sqlite3.connect(DB_PATH)
        conn.row_factory = sqlite3.Row

    key = normalize_strategy_id(strategy)
    meta = get_meta(key)
    if not meta:
        if not external:
            conn.close()
        return [], f"未知策略: {strategy}"

    if meta.requires_combination_id and not combination_id:
        if not external:
            conn.close()
        return [], "factor_combination 需 combination_id"

    try:
        if meta.kind == "v5":
            rows = _select_v5(conn, key, min_score, top_n)
        elif meta.kind == "momentum":
            rows = _select_momentum(conn, min_score, top_n, lookback=lookback)
        elif meta.kind == "defensive":
            rows = _select_dividend_defensive(conn, min_score, top_n)
        elif meta.id == "reversal":
            rows = _select_reversal(conn, min_score, top_n)
        elif meta.kind in ("factor", "combo"):
            fid = _resolve_factor_id(key, combination_id)
            if not fid:
                if not external:
                    conn.close()
                return [], "合成方案不存在或未 materialize"
            rows = _select_factor(conn, fid, min_score, top_n)
        elif meta.kind == "turtle":
            rows = _select_turtle(conn, min_score, top_n, lookback=lookback)
        elif meta.kind == "sector":
            rows, _, _, err = select_sector_rotation(
                conn,
                top_n=top_n,
                window_days=sector_window,
                per_sector=per_sector,
                min_score=min_score,
            )
            if err and not rows:
                if not external:
                    conn.close()
                return [], err
        else:
            rows = []
    finally:
        if not external:
            conn.close()

    strategy_key = meta.id
    if meta.kind == "combo" and combination_id:
        strategy_key = f"combo#{combination_id}"

    if not rows:
        return [], f"策略 {strategy_key} 无符合条件的股票"

    return rows, None


DIVIDEND_DEFENSIVE_DY_POOL = 10
DIVIDEND_DEFENSIVE_VOL_WINDOW = 60


def nearest_score_snap(score_snap: dict, code: str, dt: str) -> Optional[float]:
    """回测：取不晚于 dt 的最近因子/V5 分。"""
    if code not in score_snap:
        return None
    best = None
    for sd in sorted(score_snap[code].keys()):
        if sd <= dt:
            best = sd
    if best is None and score_snap[code]:
        best = sorted(score_snap[code].keys())[0]
    return score_snap[code].get(best) if best else None


def nearest_dividend_yield(dividend_snap: dict[str, dict[str, float]], code: str, dt: str) -> Optional[float]:
    """回测：取不晚于 dt 的最近股息率快照。"""
    if code not in dividend_snap:
        return None
    best = None
    for sd in sorted(dividend_snap[code].keys()):
        if sd <= dt:
            best = sd
    if best is None and dividend_snap[code]:
        best = sorted(dividend_snap[code].keys())[0]
    dy = dividend_snap[code].get(best) if best else None
    return float(dy) if dy is not None and dy > 0 else None


def load_dividend_yield_snap(
    conn: sqlite3.Connection,
    start_str: str,
    end_str: str,
) -> dict[str, dict[str, float]]:
    """code -> as_of_date -> dividend_yield（用于红利防御回测）。"""
    snap: dict[str, dict[str, float]] = {}
    rows = conn.execute(
        """SELECT s.code, vs.as_of_date,
                  COALESCE(vs.dividend_yield, vs.dividend_yield_ttm) AS dy
           FROM valuation_snapshots vs
           JOIN stocks s ON s.id = vs.stock_id AND s.is_active = 1
           WHERE vs.as_of_date BETWEEN ? AND ?
             AND COALESCE(vs.dividend_yield, vs.dividend_yield_ttm) IS NOT NULL
             AND COALESCE(vs.dividend_yield, vs.dividend_yield_ttm) > 0""",
        (start_str, end_str),
    ).fetchall()
    for code, as_of, dy in rows:
        if not code or dy is None:
            continue
        snap.setdefault(str(code), {})[str(as_of)] = float(dy)
    return snap


def realized_volatility(
    quotes: dict,
    dates: list[str],
    di: int,
    code: str,
    window: int = DIVIDEND_DEFENSIVE_VOL_WINDOW,
) -> Optional[float]:
    """年化已实现波动率（仅用到 di 及之前数据）。"""
    series = quotes.get(code) or {}
    window_dates = dates[max(0, di - window + 1) : di + 1]
    closes = [float(series[d]["close"]) for d in window_dates if d in series and series[d].get("close")]
    if len(closes) < max(20, window // 2):
        return None
    rets = [(closes[i] / closes[i - 1]) - 1 for i in range(1, len(closes)) if closes[i - 1] > 0]
    if len(rets) < 10:
        return None
    mean = sum(rets) / len(rets)
    var = sum((r - mean) ** 2 for r in rets) / len(rets)
    return math.sqrt(var) * math.sqrt(252)


def dividend_defensive_day_scores(
    *,
    available: dict[str, float],
    quotes: dict,
    dates: list[str],
    di: int,
    dt: str,
    dividend_snap: dict[str, dict[str, float]],
    top_n: int = 5,
    dy_pool: int = DIVIDEND_DEFENSIVE_DY_POOL,
    vol_window: int = DIVIDEND_DEFENSIVE_VOL_WINDOW,
    min_score: float = 50.0,
) -> dict[str, float]:
    """股息率 Top N → 低波动 Top top_n（与回测/矩阵归因一致）。"""
    pool: list[tuple[str, float]] = []
    for code in available:
        dy = nearest_dividend_yield(dividend_snap, code, dt)
        if dy is not None:
            pool.append((code, dy))
    pool.sort(key=lambda x: -x[1])
    top_dy = pool[:dy_pool]

    vol_ranked: list[tuple[str, float, float]] = []
    for code, dy in top_dy:
        vol = realized_volatility(quotes, dates, di, code, vol_window)
        if vol is not None:
            vol_ranked.append((code, vol, dy))
    vol_ranked.sort(key=lambda x: x[1])

    out: dict[str, float] = {}
    for i, (code, vol, dy) in enumerate(vol_ranked[:top_n]):
        # 低波优先；股息率作 tie-break
        score = round(100 - i * 3 + min(dy, 8) * 0.5, 2)
        if score >= min_score:
            out[code] = score
    return out


def momentum_backtest_score(
    series: dict,
    dates: list[str],
    idx: int,
    lookback: int,
) -> Optional[float]:
    """回测动量分 — 与模拟盘 _momentum_score_from_quotes 同公式。"""
    window = dates[max(0, idx - lookback) : idx + 1]
    closes = [float(series[d]["close"]) for d in window if d in series and series[d].get("close")]
    volumes = [float(series[d].get("volume") or 0) for d in window if d in series]
    if len(closes) < lookback * 0.6:
        return None
    rets = [(closes[i] / closes[i - 1]) - 1 for i in range(1, len(closes))]
    momentum = sum(rets[-10:]) if rets else 0
    avg_r = sum(rets) / len(rets) if rets else 0
    vol = math.sqrt(sum((x - avg_r) ** 2 for x in rets) / len(rets)) if rets else 0
    low_vol = 1 / (vol + 0.001) if vol > 0 else 100
    if len(volumes) > 5 and sum(volumes) > 0:
        vols_avg = sum(volumes) / len(volumes)
        vol_stab = 1 / (abs(volumes[-1] / vols_avg - 1) + 0.1)
    else:
        vol_stab = 0
    return round(momentum * 0.4 * 100 + low_vol * 0.35 * 0.01 + vol_stab * 0.25 * 50 + 50, 2)


def dual_ma_backtest_score(
    series: dict,
    dates: list[str],
    idx: int,
) -> Optional[float]:
    """回测双均线 F031 得分 — 与 ohlcv_technical_factors._ma_crossover_filtered 对齐。"""
    if idx < 19:
        return None
    window = dates[max(0, idx - 29) : idx + 1]
    closes: list[float] = []
    highs: list[float] = []
    lows: list[float] = []
    for d in reversed(window):
        bar = series.get(d)
        if not bar:
            return None
        c = float(bar.get("close") or 0)
        if c <= 0:
            return None
        closes.append(c)
        highs.append(float(bar.get("high") or c))
        lows.append(float(bar.get("low") or c))
    if len(closes) < 20:
        return None
    from services.ohlcv_technical_factors import _ma_crossover_filtered

    sc = _ma_crossover_filtered({"closes": closes, "highs": highs, "lows": lows})
    if sc is None:
        return None
    return float(sc)


def compute_backtest_day_scores(
    *,
    strategy: str,
    quotes: dict,
    dates: list[str],
    di: int,
    dt: str,
    available: dict[str, float],
    score_snap: dict | None = None,
    industry_map: dict[str, str] | None = None,
    lookback: int = 20,
    min_score: float = 50.0,
    sector_window: int = 5,
    use_factor_scores: bool = False,
    dividend_snap: dict | None = None,
    top_n: int = 5,
) -> dict[str, float]:
    """回测单日选股得分 — 与 select_top_n 逻辑对齐。"""
    key = normalize_strategy_id(strategy)
    meta = get_meta(key)
    if not meta:
        return {}

    if meta.kind == "sector":
        return sector_rotation_day_scores(
            quotes,
            dates,
            di,
            industry_map or {},
            window=sector_window,
            min_score=min_score,
        )

    if meta.kind == "momentum":
        out: dict[str, float] = {}
        for code in available:
            sc = momentum_backtest_score(quotes.get(code, {}), dates, di, lookback)
            if sc is not None and sc >= min_score:
                out[code] = sc
        return out

    if key == "dual_ma":
        out = {}
        for code in available:
            sc = dual_ma_backtest_score(quotes.get(code, {}), dates, di)
            if sc is not None and sc >= min_score:
                out[code] = sc
        return out

    if meta.kind == "turtle":
        entry = max(10, lookback)
        out = {}
        for code in available:
            sc = turtle_score(quotes.get(code, {}), dates, di, entry=entry)
            if sc is not None and sc >= min_score:
                out[code] = sc
        return out

    if meta.kind == "defensive" and dividend_snap is not None:
        return dividend_defensive_day_scores(
            available=available,
            quotes=quotes,
            dates=dates,
            di=di,
            dt=dt,
            dividend_snap=dividend_snap,
            top_n=top_n,
            min_score=min_score,
        )

    if use_factor_scores and score_snap:
        out = {}
        for code in available:
            sc = nearest_score_snap(score_snap, code, dt)
            if sc is not None and sc >= min_score:
                out[code] = sc
        return out

    return {}


def select_top_n_dicts(**kwargs) -> tuple[list[dict], str | None]:
    rows, err = select_top_n(**kwargs)
    return [r.to_dict() for r in rows], err


def select_sector_rebalance(
    conn: sqlite3.Connection | None = None,
    *,
    top_n: int = 10,
    min_score: float = 0.0,
    sector_window: int = 5,
    per_sector: int = 2,
) -> tuple[list[dict], list[str], list[str], str | None]:
    """行业轮动调仓：买入列表 + 应卖代码 + 减仓行业。"""
    external = conn is not None
    if conn is None:
        conn = sqlite3.connect(DB_PATH)
        conn.row_factory = sqlite3.Row
    try:
        rows, sell_codes, reduce_inds, err = select_sector_rotation(
            conn,
            top_n=top_n,
            window_days=sector_window,
            per_sector=per_sector,
            min_score=min_score,
        )
        if err and not rows:
            return [], sell_codes, reduce_inds, err
        return [r.to_dict() for r in rows], sell_codes, reduce_inds, None
    finally:
        if not external:
            conn.close()
