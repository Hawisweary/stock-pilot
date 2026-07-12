"""Alpha 因子 v1 — 盈余惊喜 / 三方资金共振 / 行业中性估值。

三个都是独立信号，暂不并入 V5 综合分，先作为单独因子展示/回测验证效果。
"""
from __future__ import annotations

import sqlite3
from datetime import date

import config

# ---------------------------------------------------------------------------
# 1. 盈余惊喜（Earnings Surprise，v1：仅年报，实际EPS vs 年初一致预期EPS）
# ---------------------------------------------------------------------------


def _surprise_tier(pct: float) -> int:
    if pct > 20:
        return 2
    if pct > 5:
        return 1
    if pct >= -5:
        return 0
    if pct >= -20:
        return -1
    return -2


def compute_earnings_surprise(conn: sqlite3.Connection) -> int:
    """年报实际EPS（earnings_express.diluted_eps）vs 年初一致预期EPS（stock_eps_forecast.eps_fy1）。

    仅覆盖：1) 有业绩快报的年报期，2) 该股票在报告期年份有一致预期 eps_fy1 快照。
    覆盖率天然受限于两张源表本身的覆盖率（业绩快报纯自愿披露，一致预期只同步过一次）。
    """
    rows = conn.execute(
        """SELECT e.stock_id, e.period_end_date, e.ann_date, e.diluted_eps,
                  f.eps_fy1, f.as_of_date
           FROM earnings_express e
           JOIN stock_eps_forecast f
             ON f.stock_id = e.stock_id
            AND f.eps_fy1_year = CAST(substr(e.period_end_date, 1, 4) AS INTEGER)
           WHERE e.period_end_date LIKE '%-12-31'
             AND e.diluted_eps IS NOT NULL
             AND f.eps_fy1 IS NOT NULL AND f.eps_fy1 != 0""",
    ).fetchall()

    written = 0
    for stock_id, period_end, ann_date, actual_eps, consensus_eps, consensus_date in rows:
        surprise_pct = round((actual_eps - consensus_eps) / abs(consensus_eps) * 100, 2)
        conn.execute(
            """INSERT OR REPLACE INTO earnings_surprise_factor
               (stock_id, period_end_date, actual_source, actual_growth, guided_growth,
                guided_ann_date, actual_ann_date, surprise_pct, tier)
               VALUES (?,?,?,?,?,?,?,?,?)""",
            (
                stock_id, period_end, "express_vs_consensus", actual_eps, consensus_eps,
                consensus_date, ann_date, surprise_pct, _surprise_tier(surprise_pct),
            ),
        )
        written += 1
    conn.commit()
    return written


# ---------------------------------------------------------------------------
# 2. 三方资金共振（L2大单 + 龙虎榜 + 沪深股通十大成交，同日同向净买入）
# ---------------------------------------------------------------------------


def compute_capital_resonance(conn: sqlite3.Connection, trade_date: str) -> int:
    """某交易日的三方资金共振。trade_date 格式 YYYY-MM-DD。

    三路数据里，北向(hsgt_top10)只覆盖当日成交额前十的个股，天然是全市场里的极少数，
    所以这个因子设计上就是稀疏的、罕见的强确认信号，不是日常可用的全市场因子。
    """
    l2 = dict(conn.execute(
        "SELECT stock_id, net_mf_amount FROM stock_moneyflow_l2_daily WHERE trade_date=?",
        (trade_date,),
    ).fetchall())
    lhb = dict(conn.execute(
        "SELECT stock_id, net_buy FROM lhb_daily WHERE trade_date=?",
        (trade_date,),
    ).fetchall())
    hsgt = dict(conn.execute(
        "SELECT stock_id, SUM(net_amount) FROM hsgt_top10_daily WHERE trade_date=? GROUP BY stock_id",
        (trade_date,),
    ).fetchall())

    stock_ids = set(l2) | set(lhb) | set(hsgt)
    rows = []
    for sid in stock_ids:
        l2_v = l2.get(sid)
        lhb_v = lhb.get(sid)
        hsgt_v = hsgt.get(sid)
        count = sum(1 for v in (l2_v, lhb_v, hsgt_v) if v is not None and v > 0)
        if count < 2:
            # count=1（通常是全市场近半数股票都满足的 L2 单路净流入）不算"共振"，
            # 只有≥2路资金同时同向才是设计要捕捉的稀疏高确信信号。
            continue
        tier = {3: 2, 2: 1}.get(count, 0)
        rows.append((sid, trade_date, l2_v, lhb_v, hsgt_v, count, tier))

    if rows:
        conn.executemany(
            """INSERT OR REPLACE INTO capital_resonance_daily
               (stock_id, trade_date, l2_net_amount, lhb_net_buy, hsgt_net_amount,
                resonance_count, tier)
               VALUES (?,?,?,?,?,?,?)""",
            rows,
        )
        conn.commit()
    return len(rows)


# ---------------------------------------------------------------------------
# 3. 行业中性估值（复用已有的个股-行业内 PE/PB 百分位，作为独立因子读出）
# ---------------------------------------------------------------------------


def get_industry_neutral_valuation(conn: sqlite3.Connection, stock_id: int) -> dict | None:
    """直接复用 valuation_scores.breakdown_json 里已经算好的行业内 PE/PB 百分位。

    这个"行业中性"是指已经在同行业内做过横截面比较（而不是跨行业裸比PE），
    不是额外再去减一个"行业PE历史分位"——现有数据没有足够长的行业PE历史序列
    支撑一个可靠的时间序列分位数，所以 v1 用横截面版本。
    """
    import json

    row = conn.execute(
        """SELECT pe_score, pb_score, breakdown_json FROM valuation_scores
           WHERE stock_id=? ORDER BY date DESC LIMIT 1""",
        (stock_id,),
    ).fetchone()
    if not row:
        return None
    pe_pct, pb_pct, breakdown_json = row
    bd = {}
    try:
        bd = json.loads(breakdown_json or "{}")
    except Exception:
        pass
    if pe_pct is None and pb_pct is None:
        return None
    combined = round(
        sum(v for v in (pe_pct, pb_pct) if v is not None)
        / sum(1 for v in (pe_pct, pb_pct) if v is not None),
        1,
    )
    # pe_pct/pb_pct 是"便宜度"百分位（越高越便宜），转成 -2..2 档位
    tier = _clamp_tier((combined - 50) / 25)
    return {
        "pe_pct_cheap": pe_pct,
        "pb_pct_cheap": pb_pct,
        "combined_cheap_pct": combined,
        "industry": bd.get("industry"),
        "tier": tier,
    }


def _clamp_tier(t: float) -> int:
    return int(max(-2, min(2, round(t))))
