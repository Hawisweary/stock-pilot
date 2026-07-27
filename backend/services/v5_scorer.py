"""V5 十维档位打分 — 加权求和、短板惩罚、一票否决 → composite_v5。"""
from __future__ import annotations

import json
import sqlite3
import time
from typing import Any, Callable, TypeVar

import config
from config import latest_trading_date
from database import write_lock

T = TypeVar("T")


def _db_connect() -> sqlite3.Connection:
    conn = sqlite3.connect(config.DB_PATH, timeout=120)
    conn.execute("PRAGMA busy_timeout=30000")
    return conn


def _retry_locked(fn: Callable[[], T], *, attempts: int = 15) -> T:
    last: Exception | None = None
    for i in range(attempts):
        try:
            return fn()
        except sqlite3.OperationalError as e:
            last = e
            if "locked" not in str(e).lower() or i == attempts - 1:
                raise
            time.sleep(min(2.0, 0.2 * (i + 1)))
    if last:
        raise last
    raise RuntimeError("retry exhausted")

V5_WEIGHTS: dict[str, float] = {
    "fundamental": 0.20,
    "quality": 0.15,
    "industry": 0.10,
    "capital": 0.15,
    "valuation": 0.10,
    "technical": 0.10,
    "market_env": 0.10,
    "policy": 0.05,
    "news": 0.03,
    "mood": 0.02,
}

V5_LABELS: dict[str, str] = {
    "fundamental": "基本面",
    "quality": "质量因子",
    "industry": "行业景气",
    "capital": "资金面",
    "valuation": "估值",
    "technical": "技术面",
    "market_env": "大盘环境",
    "policy": "政策面",
    "news": "新闻面",
    "mood": "情绪面",
}

SHORTBOARD_PENALTY_CAP = 30.0
_PENALTY_MINUS1 = (3.0, 5.0, 8.0)
_PENALTY_MINUS2 = (10.0, 15.0)

from services.event_classifier import event_intensity


def tier_to_pct(tier: int | None) -> float:
    if tier is None:
        return 50.0
    return float((int(tier) + 2) * 25)


def pct_to_tier(score: float | None) -> int:
    if score is None:
        return 0
    s = float(score)
    if s >= 75:
        return 2
    if s >= 60:
        return 1
    if s >= 40:
        return 0
    if s >= 25:
        return -1
    return -2


def _clamp_tier(t: int) -> int:
    return max(-2, min(2, int(t)))


def _weighted_base(tiers: dict[str, int | None], weights: dict[str, float] | None = None) -> float:
    weights = weights or V5_WEIGHTS
    total = 0.0
    w_sum = 0.0
    for dim, w in weights.items():
        tier = tiers.get(dim)
        if tier is None:
            continue
        total += tier_to_pct(tier) * w
        w_sum += w
    if w_sum <= 0:
        # C2 fix: 所有维度缺失时返回 50（中性）而非污染分；调用方通过 missing_dims 判断是否写入。
        return 50.0
    return total / w_sum


def _shortboard_penalty(tiers: dict[str, int | None]) -> float:
    """累进短板惩罚：-1 依次 3/5/8，-2 依次 10/15，总扣封顶 30。"""
    n_minus1 = n_minus2 = 0
    penalty = 0.0
    for tier in tiers.values():
        if tier is None:
            continue
        t = int(tier)
        if t == -1:
            penalty += _PENALTY_MINUS1[min(n_minus1, len(_PENALTY_MINUS1) - 1)]
            n_minus1 += 1
        elif t == -2:
            penalty += _PENALTY_MINUS2[min(n_minus2, len(_PENALTY_MINUS2) - 1)]
            n_minus2 += 1
    return min(SHORTBOARD_PENALTY_CAP, penalty)


def _score_metadata(tiers: dict[str, int | None], weights: dict[str, float] | None = None) -> dict[str, Any]:
    weights = weights or V5_WEIGHTS
    missing = [d for d in weights if tiers.get(d) is None]
    available = [d for d in weights if tiers.get(d) is not None]
    w_sum = sum(weights[d] for d in available)
    effective = {
        d: round(weights[d] / w_sum, 4) if w_sum > 0 else 0.0 for d in available
    }
    return {
        "missing_dims": missing,
        "dims_available": f"{len(available)}/{len(weights)}",
        "effective_weights": effective,
    }


def _regime_weights(
    conn: sqlite3.Connection,
    calc_date: str | None = None,
) -> dict[str, float]:
    """根据市场状态返回动态权重；无数据或表不存在时返回基线权重。"""
    as_of = calc_date or latest_trading_date()
    try:
        row = conn.execute(
            """SELECT regime FROM market_regime_daily
               WHERE trade_date<=? ORDER BY trade_date DESC LIMIT 1""",
            (as_of,),
        ).fetchone()
    except sqlite3.OperationalError:
        return dict(V5_WEIGHTS)
    if not row:
        return dict(V5_WEIGHTS)
    regime = str(row[0])
    deltas = config.V5_REGIME_WEIGHT_DELTAS.get(regime, {})
    if not deltas:
        return dict(V5_WEIGHTS)
    weights = {d: V5_WEIGHTS[d] + deltas.get(d, 0.0) for d in V5_WEIGHTS}
    # 归一化到 1.0
    total = sum(weights.values())
    if total > 0 and abs(total - 1.0) > 1e-6:
        weights = {d: w / total for d, w in weights.items()}
    return weights


def _capital_tier_from_flow(main_net_5d: float | None) -> int:
    if main_net_5d is None:
        return 0
    if main_net_5d > 5e7:
        return 2
    if main_net_5d > 0:
        return 1
    if main_net_5d < -5e7:
        return -2
    if main_net_5d < 0:
        return -1
    return 0


def _news_tier_from_events(events: list[dict]) -> int:
    total = sum(event_intensity(e.get("event_type") or "") for e in events)
    return _clamp_tier(max(-2, min(2, total)))


def _table_exists(conn: sqlite3.Connection, name: str) -> bool:
    return (
        conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",
            (name,),
        ).fetchone()
        is not None
    )


def _latest_dimension_pct(
    conn: sqlite3.Connection,
    stock_id: int,
    table: str,
    *,
    score_col: str = "composite_score",
    order_col: str = "date",
) -> float | None:
    if not _table_exists(conn, table):
        return None
    row = conn.execute(
        f"""SELECT {score_col} FROM {table}
            WHERE stock_id=? ORDER BY {order_col} DESC LIMIT 1""",
        (stock_id,),
    ).fetchone()
    if not row or row[0] is None:
        return None
    return float(row[0])


def _resolve_technical_tier(
    conn: sqlite3.Connection,
    stock_id: int,
    cs: sqlite3.Row | None,
) -> int | None:
    """技术面五档：优先规则引擎缓存，否则离散映射 comprehensive 分。"""
    row = None
    if _table_exists(conn, "tech_analysis_cache"):
        row = conn.execute(
            """SELECT full_result FROM tech_analysis_cache
               WHERE stock_id=? ORDER BY created_at DESC LIMIT 1""",
            (stock_id,),
        ).fetchone()
    if row and row[0]:
        try:
            fr = json.loads(row[0])
            if fr.get("final_technical_tier") is not None:
                return int(fr["final_technical_tier"])
        except Exception:
            pass
    if cs and cs["technical_score"] is not None:
        from services.technical_rule_engine import tier_from_pct_score

        tier = tier_from_pct_score(float(cs["technical_score"]))
        if tier is not None:
            return tier
        return pct_to_tier(float(cs["technical_score"]))
    tech_pct = _latest_dimension_pct(
        conn,
        stock_id,
        "tech_analysis_cache",
        score_col="score",
        order_col="created_at",
    )
    if tech_pct is not None:
        from services.technical_rule_engine import tier_from_pct_score

        tier = tier_from_pct_score(tech_pct)
        return tier if tier is not None else pct_to_tier(tech_pct)
    return None


def _resolve_industry_key(industry_sw2: str | None, industry_sw: str | None) -> str:
    from services.industry_normalize import normalize_industry

    if industry_sw2 and str(industry_sw2).strip():
        return normalize_industry(industry_sw2) or str(industry_sw2).strip()
    if industry_sw and str(industry_sw).strip():
        return normalize_industry(industry_sw) or str(industry_sw).strip()
    return ""


def _sector_momentum_tier(conn: sqlite3.Connection, industry_key: str) -> int | None:
    """板块当日涨跌幅在全市场行业中的百分位 → 五档（EPS 缺失时替代）。"""
    if not _table_exists(conn, "sector_fund_flow_daily"):
        return None
    latest = conn.execute(
        "SELECT MAX(trade_date) AS d FROM sector_fund_flow_daily"
    ).fetchone()
    if not latest or not latest[0]:
        return None
    rows = conn.execute(
        """SELECT sector_name, change_pct FROM sector_fund_flow_daily
           WHERE trade_date=? AND change_pct IS NOT NULL""",
        (latest[0],),
    ).fetchall()
    if len(rows) < 5:
        return None
    sector_chg: float | None = None
    for name, chg in rows:
        if industry_key in str(name) or str(name) in industry_key:
            sector_chg = float(chg)
            break
    if sector_chg is None:
        return None
    vals = [float(r[1]) for r in rows]
    pct = sum(1 for v in vals if v <= sector_chg) / len(vals) * 100
    if pct >= 80:
        return 2
    if pct >= 60:
        return 1
    if pct >= 40:
        return 0
    if pct >= 20:
        return -1
    return -2


def _industry_tier(
    conn: sqlite3.Connection, industry_key: str
) -> tuple[int | None, str]:
    if not industry_key:
        return None, "missing"
    eps = conn.execute(
        """SELECT tier FROM industry_eps_revision_daily
           WHERE industry_sw2=? ORDER BY trade_date DESC LIMIT 1""",
        (industry_key,),
    ).fetchone()
    eps_tier = int(eps[0]) if eps and eps[0] is not None else None
    if eps_tier is None:
        eps_tier = _sector_momentum_tier(conn, industry_key)
        eps_src = "sector_momentum" if eps_tier is not None else "missing"
    else:
        eps_src = "eps_revision"

    sector = conn.execute(
        """SELECT rs_csi300_20d, net_inflow_pct FROM sector_fund_flow_daily
           WHERE sector_name=? OR sector_name LIKE ?
           ORDER BY trade_date DESC LIMIT 1""",
        (industry_key, f"%{industry_key}%"),
    ).fetchone()
    flow_tier: int | None = None
    if sector:
        rs = sector[0]
        if rs is not None:
            if rs >= 5:
                flow_tier = 2
            elif rs >= 2:
                flow_tier = 1
            elif rs <= -5:
                flow_tier = -2
            elif rs <= -2:
                flow_tier = -1
            else:
                flow_tier = 0
    parts = [t for t in (eps_tier, flow_tier) if t is not None]
    if not parts:
        return None, eps_src
    return _clamp_tier(round(sum(parts) / len(parts))), eps_src


def _industry_tier_candidates(
    conn: sqlite3.Connection,
    industry_sw2: str | None,
    industry_sw: str | None,
) -> tuple[int | None, str | None, str]:
    """先二级行业、再一级行业、再原文，逐级尝试匹配板块/EPS 数据。"""
    from services.industry_normalize import normalize_industry

    keys: list[str] = []
    for raw in (industry_sw2, industry_sw):
        if not raw or not str(raw).strip():
            continue
        for candidate in (normalize_industry(raw, conn), str(raw).strip()):
            if candidate and candidate not in keys:
                keys.append(candidate)
    for key in keys:
        tier, src = _industry_tier(conn, key)
        if tier is not None:
            return tier, key, src
    if industry_sw2 and str(industry_sw2).strip() not in ("", "-", "—"):
        mom = _sector_momentum_tier(conn, str(industry_sw2).strip())
        if mom is not None:
            return mom, str(industry_sw2).strip(), "sector_momentum"
        return 0, str(industry_sw2).strip(), "neutral_fallback"
    return None, None, "missing"


def _index_technical_tier() -> int:
    try:
        from services.market_index import fetch_index_kline

        k = fetch_index_kline("sh000300", days=60, with_technical=True)
        bars = k.get("kline") or []
        tech = k.get("technical") or []
        if len(bars) < 20:
            return 0
        c0 = bars[-20].get("close")
        c1 = bars[-1].get("close")
        if not c0 or not c1 or c0 <= 0:
            return 0
        ret = (float(c1) - float(c0)) / float(c0) * 100
        if ret >= 8:
            base = 2
        elif ret >= 3:
            base = 1
        elif ret <= -8:
            base = -2
        elif ret <= -3:
            base = -1
        else:
            base = 0
        if tech:
            last = tech[-1]
            rsi = last.get("rsi")
            if rsi is not None:
                if rsi >= 75 and base > 0:
                    base = min(base, 1)
                if rsi <= 25 and base < 0:
                    base = max(base, -1)
        return base
    except Exception:
        return 0


def _rates_weekly_tier(rows: list) -> int:
    """10Y 国债 + 人民币汇率周变化（各 50%）→ 宏观高频代理档位。"""
    if len(rows) < 2:
        return 0
    score = 0
    bond_now, bond_prev = rows[0][2], rows[min(4, len(rows) - 1)][2]
    if bond_now is not None and bond_prev is not None:
        chg = float(bond_now) - float(bond_prev)
        if chg <= -0.05:
            score += 1
        elif chg >= 0.10:
            score -= 1
    fx_now, fx_prev = rows[0][3], rows[min(4, len(rows) - 1)][3]
    if fx_now is not None and fx_prev is not None and fx_prev != 0:
        fx_chg_pct = (float(fx_now) - float(fx_prev)) / abs(float(fx_prev)) * 100
        if fx_chg_pct <= -0.3:
            score += 1
        elif fx_chg_pct >= 0.5:
            score -= 1
    return _clamp_tier(score)


def _macro_tier(conn: sqlite3.Connection) -> int:
    cols = {r[1] for r in conn.execute("PRAGMA table_info(macro_indicators)")}
    if "bond_yield_10y" in cols and "usd_cnh" in cols:
        rows = conn.execute(
            """SELECT pmi_manufacturing, social_financing_yoy, bond_yield_10y, usd_cnh
               FROM macro_indicators ORDER BY date DESC LIMIT 5"""
        ).fetchall()
    else:
        rows = conn.execute(
            """SELECT pmi_manufacturing, social_financing_yoy, NULL, NULL
               FROM macro_indicators ORDER BY date DESC LIMIT 3"""
        ).fetchall()
    if not rows:
        return 0
    pmi = rows[0][0]
    score = 0
    if pmi is not None:
        if pmi >= 52:
            score += 2
        elif pmi >= 50:
            score += 1
        elif pmi < 48:
            score -= 2
        elif pmi < 50:
            score -= 1
    if len(rows) >= 2:
        y0, y1 = rows[0][1], rows[1][1]
        if y0 is not None and y1 is not None and y0 < y1 - 0.3:
            score -= 1
    slow = _clamp_tier(round(score / 2))
    fast = _rates_weekly_tier(rows)
    return _clamp_tier(round(slow * 0.8 + fast * 0.2))


def _stock_beta_vs_csi300(conn: sqlite3.Connection, stock_id: int, *, days: int = 60) -> float:
    """个股相对沪深300的 60 日 Beta；数据不足返回 1.0。"""
    stock_rows = conn.execute(
        """SELECT trade_date, close FROM stock_daily_quotes
           WHERE stock_id=? AND close IS NOT NULL
           ORDER BY trade_date DESC LIMIT ?""",
        (stock_id, days + 1),
    ).fetchall()
    if len(stock_rows) < 21:
        return 1.0
    stock_rows = list(reversed(stock_rows))
    dates = [r[0] for r in stock_rows]
    s_closes = [float(r[1]) for r in stock_rows]
    try:
        from services.market_index import fetch_index_kline

        k = fetch_index_kline("sh000300", days=days + 10, with_technical=False)
        bars = {b["date"]: float(b["close"]) for b in (k.get("kline") or []) if b.get("close")}
    except Exception:
        return 1.0
    aligned_s, aligned_i = [], []
    for i in range(1, len(dates)):
        d0, d1 = dates[i - 1], dates[i]
        if d0 not in bars or d1 not in bars:
            continue
        i0, i1 = bars[d0], bars[d1]
        if i0 <= 0 or s_closes[i - 1] <= 0:
            continue
        aligned_s.append((s_closes[i] - s_closes[i - 1]) / s_closes[i - 1])
        aligned_i.append((i1 - i0) / i0)
    if len(aligned_s) < 15:
        return 1.0
    mean_s = sum(aligned_s) / len(aligned_s)
    mean_i = sum(aligned_i) / len(aligned_i)
    cov = sum((aligned_s[j] - mean_s) * (aligned_i[j] - mean_i) for j in range(len(aligned_s)))
    var_i = sum((aligned_i[j] - mean_i) ** 2 for j in range(len(aligned_i)))
    if var_i <= 0:
        return 1.0
    beta = cov / var_i
    return max(0.5, min(1.5, beta))


def get_market_env_tier(conn: sqlite3.Connection | None = None) -> int:
    own = conn is None
    if own:
        conn = sqlite3.connect(config.DB_PATH)
    try:
        tech = _index_technical_tier()
        macro = _macro_tier(conn)
        combined = tech * 0.6 + macro * 0.4
        return _clamp_tier(round(combined))
    finally:
        if own:
            conn.close()


def get_market_env_tier_for_stock(
    conn: sqlite3.Connection,
    stock_id: int,
    *,
    base_tier: int | None = None,
) -> tuple[int, float]:
    """全市场大盘档 × (0.6 + 0.4×β) 个性化调节。"""
    base = base_tier if base_tier is not None else get_market_env_tier(conn)
    beta = _stock_beta_vs_csi300(conn, stock_id)
    adjusted = base * (0.6 + 0.4 * beta)
    return _clamp_tier(round(adjusted)), beta


def _macro_cold_veto(conn: sqlite3.Connection) -> bool:
    rows = conn.execute(
        """SELECT pmi_manufacturing, social_financing_yoy
           FROM macro_indicators ORDER BY date DESC LIMIT 3"""
    ).fetchall()
    if len(rows) < 2:
        return False
    pmi = rows[0][0]
    if pmi is None or pmi >= 48:
        return False
    yoys = [r[1] for r in rows[:3] if r[1] is not None]
    if len(yoys) < 2:
        return False
    return all(yoys[i] < yoys[i + 1] - 0.3 for i in range(len(yoys) - 1))


def _apply_veto_discounts(
    composite: float,
    *,
    quality_minus2: bool = False,
    market_minus2: bool = False,
    macro_cold: bool = False,
    data_quality: bool = False,
) -> float:
    """财务/宏观/数据质量类否决改为折扣，保留极端行情机会。
    A-1 修复：quality_minus2 去掉固定 -10，改为纯乘数（配置 V5_QUALITY_DISCOUNT_MULT）。
    DQ-1：数据质量异常分 >=50 时折扣。
    短板惩罚层已独立扣分，不需再减常数。
    """
    result = composite
    if quality_minus2:
        result = result * config.V5_QUALITY_DISCOUNT_MULT
    if market_minus2:
        result = result * 0.7
    if macro_cold:
        result = result * 0.8
    if data_quality:
        result = result * config.V5_DATA_QUALITY_DISCOUNT_MULT
    return max(0.0, result)


def check_veto(
    stock_id: int,
    tiers: dict[str, int | None],
    *,
    conn: sqlite3.Connection | None = None,
    calc_date: str | None = None,
) -> tuple[str, list[str], dict[str, bool]]:
    """返回 (veto_status, reasons, discount_flags)。status: ok | discount | exclude"""
    own = conn is None
    if own:
        conn = sqlite3.connect(config.DB_PATH)
    reasons: list[str] = []
    discounts: dict[str, bool] = {
        "quality_minus2": False,
        "market_minus2": False,
        "macro_cold": False,
        "data_quality": False,
    }
    try:
        from services.risk_scanner import has_veto_risk

        if has_veto_risk(stock_id):
            reasons.append("风险标记(立案/非标/连跌停/ST)")
            return "exclude", reasons, discounts

        # DQ-1：数据质量异常检查
        as_of = calc_date or latest_trading_date()
        dq_row = conn.execute(
            """SELECT anomaly_score, flags, severity FROM data_quality_alerts
               WHERE stock_id=? AND trade_date<=?
               ORDER BY trade_date DESC LIMIT 1""",
            (stock_id, as_of),
        ).fetchone()
        if dq_row:
            dq_score = float(dq_row[0]) if dq_row[0] is not None else 0.0
            if dq_score >= config.V5_DATA_QUALITY_EXCLUDE_THRESHOLD:
                reasons.append(f"数据质量异常分数={dq_score:.0f}>=阈值(排除)")
                return "exclude", reasons, discounts
            if dq_score >= 50:
                reasons.append(f"数据质量异常分数={dq_score:.0f}(折扣×{config.V5_DATA_QUALITY_DISCOUNT_MULT})")
                discounts["data_quality"] = True

        q = tiers.get("quality")
        if q is not None and int(q) <= -2:
            reasons.append(f"质量因子=-2(折扣×{config.V5_QUALITY_DISCOUNT_MULT})")
            discounts["quality_minus2"] = True

        me = tiers.get("market_env")
        if me is not None and int(me) <= -2:
            reasons.append("大盘环境=-2(折扣×0.7)")
            discounts["market_minus2"] = True

        if _macro_cold_veto(conn):
            reasons.append("宏观极冷(PMI<48且社融连降,折扣×0.8)")
            discounts["macro_cold"] = True

        if any(discounts.values()):
            return "discount", reasons, discounts

        if any(t is not None and int(t) <= -2 for t in tiers.values()):
            reasons.append("存在-2档位维度")
        return "ok", reasons, discounts
    finally:
        if own:
            conn.close()


def compute_stock_v5_tiers(
    stock_id: int,
    *,
    conn: sqlite3.Connection | None = None,
    market_env_tier: int | None = None,
    calc_date: str | None = None,
) -> dict[str, Any]:
    own = conn is None
    if own:
        conn = sqlite3.connect(config.DB_PATH)
    conn.row_factory = sqlite3.Row
    try:
        stock = conn.execute(
            "SELECT id, code, industry_sw2, industry_sw FROM stocks WHERE id=?",
            (stock_id,),
        ).fetchone()
        if not stock:
            return {}

        tier_sources: dict[str, str] = {}
        if market_env_tier is not None:
            me = market_env_tier
            stock_beta = 1.0
        else:
            me, stock_beta = get_market_env_tier_for_stock(conn, stock_id)

        mrow = conn.execute(
            """SELECT growth_tier, quality_tier, growth_qoq_delta
               FROM stock_v5_metrics WHERE stock_id=?
               ORDER BY calc_date DESC LIMIT 1""",
            (stock_id,),
        ).fetchone()

        if mrow and mrow["growth_tier"] is not None:
            growth_tier: int | None = int(mrow["growth_tier"])
            tier_sources["fundamental"] = "v5_metrics"
            if mrow["growth_qoq_delta"] is not None:
                delta = float(mrow["growth_qoq_delta"])
                if delta > 0.5:
                    growth_tier = _clamp_tier((growth_tier or 0) + 1)
                elif delta < -0.5:
                    growth_tier = _clamp_tier((growth_tier or 0) - 1)
        else:
            growth_tier = None
            tier_sources["fundamental"] = "missing"

        if mrow and mrow["quality_tier"] is not None:
            quality_tier: int | None = int(mrow["quality_tier"])
            tier_sources["quality"] = "v5_metrics"
        else:
            quality_tier = None
            tier_sources["quality"] = "missing"

        from services.capital_tier_v5 import compute_capital_tier_v5

        cap_v5 = compute_capital_tier_v5(
            stock_id, conn, code=str(stock["code"])
        )
        capital_tier: int | None = cap_v5.get("tier")
        if capital_tier is not None:
            subs = cap_v5.get("sub_tiers") or {}
            parts = [k for k, t in subs.items() if t is not None]
            tier_sources["capital"] = (
                f"{cap_v5.get('source')}({'+'.join(parts)})"
                if parts
                else str(cap_v5.get("source"))
            )
        else:
            cap_pct = _latest_dimension_pct(conn, stock_id, "capital_scores")
            if cap_pct is not None:
                capital_tier = pct_to_tier(cap_pct)
                tier_sources["capital"] = "capital_scores"
            else:
                capital_tier = None
                tier_sources["capital"] = "missing"

        cs = conn.execute(
            """SELECT val_score, technical_score FROM comprehensive_scores
               WHERE stock_id=? ORDER BY calc_date DESC LIMIT 1""",
            (stock_id,),
        ).fetchone()
        if cs and cs["val_score"] is not None:
            val_tier: int | None = pct_to_tier(float(cs["val_score"]))
            tier_sources["valuation"] = "comprehensive"
        else:
            val_pct = _latest_dimension_pct(conn, stock_id, "valuation_scores")
            if val_pct is not None:
                val_tier = pct_to_tier(val_pct)
                tier_sources["valuation"] = "valuation_scores"
            else:
                val_tier = None
                tier_sources["valuation"] = "missing"

        tech_tier: int | None = _resolve_technical_tier(conn, stock_id, cs)
        if tech_tier is not None:
            row = conn.execute(
                """SELECT full_result FROM tech_analysis_cache
                   WHERE stock_id=? ORDER BY created_at DESC LIMIT 1""",
                (stock_id,),
            ).fetchone()
            src = "comprehensive"
            if row and row[0]:
                try:
                    fr = json.loads(row[0])
                    src = str(fr.get("engine") or "tech_cache")
                except Exception:
                    src = "tech_cache"
            tier_sources["technical"] = src
        else:
            tier_sources["technical"] = "missing"

        from services.policy_event_sync import get_policy_score_v5_for_stock
        from services.event_classifier import get_stock_events

        pol = get_policy_score_v5_for_stock(stock_id) or {}
        pol_events = pol.get("events") or []
        if pol_events:
            policy_tier: int | None = int(pol.get("tier") or 0)
            tier_sources["policy"] = "policy_events"
        else:
            pol_pct = _latest_dimension_pct(conn, stock_id, "policy_scores")
            if pol_pct is not None:
                policy_tier = pct_to_tier(pol_pct)
                tier_sources["policy"] = "policy_scores"
            else:
                policy_tier = None
                tier_sources["policy"] = "missing"
                try:
                    from services.policy_scorer import compute_policy_score

                    ps = compute_policy_score(
                        stock_id, str(stock["code"]), use_llm=False
                    )
                    if ps.get("composite_score") is not None:
                        policy_tier = pct_to_tier(float(ps["composite_score"]))
                        tier_sources["policy"] = "policy_keywords"
                except Exception:
                    pass

        events = get_stock_events(stock_id, limit=15, include_fundamental=False)
        if events:
            news_tier: int | None = _news_tier_from_events(events)
            tier_sources["news"] = "events"
        else:
            news_tier = None
            tier_sources["news"] = "missing"

        mood_row = conn.execute(
            """SELECT mood_tier FROM stock_mood_v5_daily
               WHERE stock_id=? ORDER BY calc_date DESC LIMIT 1""",
            (stock_id,),
        ).fetchone()
        if mood_row and mood_row[0] is not None:
            mood_tier: int | None = int(mood_row[0])
            tier_sources["mood"] = "mood_v5"
        else:
            mood_pct = _latest_dimension_pct(conn, stock_id, "sentiment_scores")
            if mood_pct is not None:
                mood_tier = pct_to_tier(mood_pct)
                tier_sources["mood"] = "sentiment_scores"
            else:
                mood_tier = None
                tier_sources["mood"] = "missing"

        industry_tier, _matched_ind_key, ind_src = _industry_tier_candidates(
            conn, stock["industry_sw2"], stock["industry_sw"]
        )
        tier_sources["industry"] = ind_src if industry_tier is not None else "missing"
        tier_sources["market_env"] = f"index_macro_beta({stock_beta:.2f})"

        tiers: dict[str, int | None] = {
            "fundamental": growth_tier,
            "quality": quality_tier,
            "industry": industry_tier,
            "capital": capital_tier,
            "valuation": val_tier,
            "technical": tech_tier,
            "market_env": me,
            "policy": policy_tier,
            "news": news_tier,
            "mood": mood_tier,
        }

        weights = _regime_weights(conn, calc_date)
        base = _weighted_base(tiers, weights)
        penalty = _shortboard_penalty(tiers)
        raw_composite = max(0.0, base - penalty)

        veto_status, veto_reasons, discount_flags = check_veto(
            stock_id, tiers, conn=conn, calc_date=calc_date
        )
        composite_v5 = raw_composite
        if veto_status == "exclude":
            composite_v5 = min(composite_v5, 25.0)
        elif veto_status == "discount":
            composite_v5 = _apply_veto_discounts(
                composite_v5,
                quality_minus2=discount_flags.get("quality_minus2", False),
                market_minus2=discount_flags.get("market_minus2", False),
                macro_cold=discount_flags.get("macro_cold", False),
                data_quality=discount_flags.get("data_quality", False),
            )

        dim_scores = {
            dim: round(tier_to_pct(tiers[dim]), 1) if tiers[dim] is not None else None
            for dim in tiers
        }
        meta = _score_metadata(tiers, weights)
        try:
            regime = conn.execute(
                """SELECT regime FROM market_regime_daily
                   WHERE trade_date<=? ORDER BY trade_date DESC LIMIT 1""",
                (calc_date or latest_trading_date(),),
            ).fetchone()
            market_regime = regime[0] if regime else "oscillation"
        except sqlite3.OperationalError:
            market_regime = "oscillation"

        as_of = latest_trading_date()
        return {
            "stock_id": stock_id,
            "calc_date": as_of,
            "tiers": tiers,
            "dim_scores": dim_scores,
            "labels": V5_LABELS,
            "base_score": round(base, 2),
            "shortboard_penalty": round(penalty, 2),
            "composite_v5": round(composite_v5, 2),
            "veto_status": veto_status,
            "veto_reasons": veto_reasons,
            "quality_score": dim_scores["quality"],
            "industry_score": dim_scores["industry"],
            "capital_score": dim_scores["capital"],
            "val_score": dim_scores["valuation"],
            "technical_score": dim_scores["technical"],
            "policy_score": dim_scores["policy"],
            "market_env_score": dim_scores["market_env"],
            "tier_sources": tier_sources,
            "missing_dims": meta["missing_dims"],
            "dims_available": meta["dims_available"],
            "effective_weights": meta["effective_weights"],
            "market_regime": market_regime,
            "market_beta": round(stock_beta, 3),
            "capital_breakdown": cap_v5,
            "discount_flags": discount_flags,
        }
    finally:
        if own:
            conn.close()


def persist_v5_score(
    stock_id: int, result: dict[str, Any], calc_date: str, *, quick: bool = False
) -> None:
    breakdown = {
        "tiers": result.get("tiers"),
        "dim_scores": result.get("dim_scores"),
        "tier_sources": result.get("tier_sources"),
        "base_score": result.get("base_score"),
        "shortboard_penalty": result.get("shortboard_penalty"),
        "veto_reasons": result.get("veto_reasons"),
        "missing_dims": result.get("missing_dims"),
        "dims_available": result.get("dims_available"),
        "effective_weights": result.get("effective_weights"),
        "market_regime": result.get("market_regime"),
        "market_beta": result.get("market_beta"),
        "capital_breakdown": result.get("capital_breakdown"),
    }
    cols = {
        "quality_score": result.get("quality_score"),
        "industry_score": result.get("industry_score"),
        "capital_score": result.get("capital_score"),
        "val_score": result.get("val_score"),
        "technical_score": result.get("technical_score"),
        "policy_score": result.get("policy_score"),
        "market_env_score": result.get("market_env_score"),
        "composite_v5": result.get("composite_v5"),
        "veto_status": result.get("veto_status", "ok"),
        "v5_breakdown_json": json.dumps(breakdown, ensure_ascii=False),
    }

    def _do() -> None:
        # quick 模式（API 实时路径）：短 busy_timeout 快速失败，不阻塞响应
        conn = _db_connect()
        if quick:
            conn.execute("PRAGMA busy_timeout=2000")
        try:
            row = conn.execute(
                "SELECT 1 FROM comprehensive_scores WHERE stock_id=? AND calc_date=?",
                (stock_id, calc_date),
            ).fetchone()
            if row:
                conn.execute(
                    """UPDATE comprehensive_scores SET
                       quality_score=?, industry_score=?, capital_score=?,
                       val_score=?, technical_score=?, policy_score=?,
                       market_env_score=?, composite_v5=?, veto_status=?, v5_breakdown_json=?
                       WHERE stock_id=? AND calc_date=?""",
                    (
                        cols["quality_score"],
                        cols["industry_score"],
                        cols["capital_score"],
                        cols["val_score"],
                        cols["technical_score"],
                        cols["policy_score"],
                        cols["market_env_score"],
                        cols["composite_v5"],
                        cols["veto_status"],
                        cols["v5_breakdown_json"],
                        stock_id,
                        calc_date,
                    ),
                )
            else:
                conn.execute(
                    """INSERT INTO comprehensive_scores
                    (stock_id, calc_date, quality_score, industry_score, capital_score,
                     val_score, technical_score, policy_score, market_env_score,
                     composite_v5, veto_status, v5_breakdown_json)
                    VALUES (?,?,?,?,?,?,?,?,?,?,?,?)""",
                    (
                        stock_id,
                        calc_date,
                        cols["quality_score"],
                        cols["industry_score"],
                        cols["capital_score"],
                        cols["val_score"],
                        cols["technical_score"],
                        cols["policy_score"],
                        cols["market_env_score"],
                        cols["composite_v5"],
                        cols["veto_status"],
                        cols["v5_breakdown_json"],
                    ),
                )
            conn.commit()
        finally:
            conn.close()

    with write_lock:
        _retry_locked(_do, attempts=1 if quick else 15)


def _latest_comprehensive_calc_date(
    conn: sqlite3.Connection, stock_id: int, *, fallback: str | None = None
) -> str:
    row = conn.execute(
        """SELECT calc_date FROM comprehensive_scores
           WHERE stock_id=? ORDER BY calc_date DESC LIMIT 1""",
        (stock_id,),
    ).fetchone()
    if row and row[0]:
        return str(row[0])
    return fallback or latest_trading_date() or ""


def _format_v5_response(result: dict[str, Any]) -> dict[str, Any]:
    from services.market_regime import describe_regime_weight_deltas, get_regime_guidance, regime_label

    regime = str(result.get("market_regime") or "oscillation")
    guidance = get_regime_guidance(regime)
    weight_note = describe_regime_weight_deltas(regime)
    return {
        **result,
        "market_regime_label": regime_label(regime),
        "weight_note": weight_note,
        "regime_guidance": guidance,
        "labels": V5_LABELS,
        "breakdown": {
            "tiers": result.get("tiers"),
            "dim_scores": result.get("dim_scores"),
            "tier_sources": result.get("tier_sources"),
            "base_score": result.get("base_score"),
            "shortboard_penalty": result.get("shortboard_penalty"),
            "veto_reasons": result.get("veto_reasons"),
            "missing_dims": result.get("missing_dims"),
            "dims_available": result.get("dims_available"),
            "effective_weights": result.get("effective_weights"),
            "market_beta": result.get("market_beta"),
            "capital_breakdown": result.get("capital_breakdown"),
            "market_regime": regime,
            "market_regime_label": regime_label(regime),
            "weight_note": weight_note,
            "regime_guidance": guidance,
        },
    }


def compute_all_v5_scores(
    stock_ids: list[int] | None = None,
    *,
    calc_date: str | None = None,
) -> dict:
    as_of = calc_date or latest_trading_date()
    conn = sqlite3.connect(config.DB_PATH)
    try:
        if stock_ids:
            ph = ",".join("?" * len(stock_ids))
            ids = [
                int(r[0])
                for r in conn.execute(
                    f"SELECT id FROM stocks WHERE id IN ({ph}) AND is_active=1",
                    stock_ids,
                ).fetchall()
            ]
        else:
            ids = [
                int(r[0])
                for r in conn.execute("SELECT id FROM stocks WHERE is_active=1").fetchall()
            ]
    finally:
        conn.close()

    computed = 0
    veto_exclude = 0
    # M1：收集 tiers + discount_flags 供 profile 批量计算
    _tiers_cache: dict[int, dict] = {}
    _discount_flags_cache: dict[int, dict] = {}
    for sid in ids:
        conn = _db_connect()
        try:
            r = compute_stock_v5_tiers(sid, conn=conn, calc_date=as_of)
            if not r:
                continue
            # C2 fix: 若所有 10 个维度都缺失，跳过写入避免产生全空综合分
            if len(r.get("missing_dims", [])) >= len(V5_WEIGHTS):
                continue
            from services.comprehensive_store import resolve_calc_date

            stock_date = resolve_calc_date(conn, sid) or calc_date or as_of
        finally:
            conn.close()
        persist_v5_score(sid, r, stock_date)
        computed += 1
        if r.get("veto_status") == "exclude":
            veto_exclude += 1
        # 缓存用于 profile 计算
        _tiers_cache[sid] = r.get("tiers", {})
        _discount_flags_cache[sid] = r.get("discount_flags", {})

    # PC-1.0 P0-2: 记录分数变动
    try:
        from services.score_alert import record_score_changes
        conn = _db_connect()
        try:
            changes = record_score_changes(conn, stock_ids=ids or None)
        finally:
            conn.close()
    except Exception as _exc:
        changes = []
        import logging
        logging.getLogger(__name__).warning("score_alert failed: %s", _exc)

    # M1：批量写入 profile 衍生分（隔离写 stock_score_profiles，不碰 comprehensive_scores）
    profile_written = 0
    try:
        profile_written = compute_and_persist_profiles(
            ids, _tiers_cache, _discount_flags_cache, as_of
        )
    except Exception as _exc:
        import logging
        logging.getLogger(__name__).warning("profile compute failed: %s", _exc)

    return {
        "calc_date": as_of,
        "computed": computed,
        "veto_exclude": veto_exclude,
        "alerts": len(changes),
        "profile_written": profile_written,
    }


# ─────────────────────────────────────────────────────────────────────────────
# M1：Profile 衍生分计算 + 落库（与 comprehensive_scores 完全隔离）
# ─────────────────────────────────────────────────────────────────────────────

def _ensure_profile_table(conn: sqlite3.Connection) -> None:
    """建 stock_score_profiles 表（migration v36）。"""
    conn.execute("""
        CREATE TABLE IF NOT EXISTS stock_score_profiles (
          stock_id  INTEGER NOT NULL,
          calc_date TEXT    NOT NULL,
          profile   TEXT    NOT NULL CHECK(profile IN ('momentum','dividend')),
          score     REAL    NOT NULL,
          breakdown_json TEXT,
          PRIMARY KEY (stock_id, calc_date, profile)
        )
    """)
    conn.commit()


def _compute_profile_score(
    tiers: dict[str, int | None],
    profile: str,
    quality_minus2: bool = False,
) -> float:
    """用 profile 权重重新加权同一套 tiers，返回衍生分（0-100）。"""
    weights = config.V5_PROFILE_WEIGHTS.get(profile)
    if not weights:
        raise ValueError(f"未知 profile: {profile}")

    total = w_sum = 0.0
    for dim, w in weights.items():
        tier = tiers.get(dim)
        if tier is None:
            continue
        total += tier_to_pct(tier) * w
        w_sum += w
    if w_sum <= 0:
        return 50.0

    base = total / w_sum
    penalty = _shortboard_penalty(tiers)
    raw = max(0.0, base - penalty)

    # quality_minus2 折扣
    if quality_minus2:
        mult = (
            config.V5_MOMENTUM_QUALITY_DISCOUNT_MULT
            if profile == "momentum"
            else config.V5_QUALITY_DISCOUNT_MULT
        )
        raw = raw * mult

    return round(raw, 2)


def compute_and_persist_profiles(
    stock_ids: list[int],
    tiers_cache: dict[int, dict],  # stock_id → tiers dict（来自 compute_all_v5_scores 收集）
    discount_flags_cache: dict[int, dict],
    calc_date: str,
) -> int:
    """批量计算并写入 momentum/dividend profile 分。返回写入行数。"""
    if not tiers_cache:
        return 0
    conn = _db_connect()
    _ensure_profile_table(conn)
    written = 0
    for sid in stock_ids:
        tiers = tiers_cache.get(sid)
        if not tiers:
            continue
        flags = discount_flags_cache.get(sid, {})
        q2 = flags.get("quality_minus2", False)
        rows = []
        for profile in ("momentum", "dividend"):
            try:
                score = _compute_profile_score(tiers, profile, quality_minus2=q2)
                rows.append((sid, calc_date, profile, score, None))
            except Exception:
                pass
        if rows:
            conn.executemany(
                """INSERT OR REPLACE INTO stock_score_profiles
                   (stock_id, calc_date, profile, score, breakdown_json)
                   VALUES (?, ?, ?, ?, ?)""",
                rows,
            )
            written += len(rows)
    conn.commit()
    conn.close()
    return written


def get_stock_v5_score(stock_id: int) -> dict | None:
    """实时计算 V5 并写入该股最新 comprehensive_scores 行。"""
    result = compute_stock_v5_tiers(stock_id)
    if not result:
        return None
    conn = _db_connect()
    try:
        from services.comprehensive_store import resolve_calc_date

        calc_date = resolve_calc_date(conn, stock_id) or result.get("calc_date")
    finally:
        conn.close()
    if calc_date:
        # 持久化只是缓存优化：后台批任务持写锁时降级为不落库，仍返回已算出的分数
        try:
            persist_v5_score(stock_id, result, calc_date, quick=True)
        except sqlite3.OperationalError as e:
            if "locked" not in str(e).lower():
                raise
            import logging

            logging.getLogger(__name__).warning(
                "V5分数持久化跳过(数据库忙) stock_id=%s", stock_id
            )
    return _format_v5_response(result)
