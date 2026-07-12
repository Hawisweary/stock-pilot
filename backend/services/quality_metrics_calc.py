"""V5 Quality V2 + 基本面单季增速 — 基于 financial_reports 自算。"""
from __future__ import annotations

import sqlite3
from collections import defaultdict
from datetime import date

from config import DB_PATH, latest_trading_date
from services.data_processor import compute_yoy_meta, is_quarterly_report_type


def _clamp_tier(t: int) -> int:
    return max(-2, min(2, int(t)))


def _median_tier(*tiers: int | None) -> int | None:
    vals = [int(t) for t in tiers if t is not None]
    if not vals:
        return None
    vals.sort()
    mid = vals[len(vals) // 2] if len(vals) % 2 else round(
        (vals[len(vals) // 2 - 1] + vals[len(vals) // 2]) / 2
    )
    return _clamp_tier(int(mid))


def _tier_from_roe_percentile(roe: float | None, peer_roes: list[float]) -> int | None:
    if roe is None or not peer_roes:
        return None
    pct = sum(1 for v in peer_roes if v <= roe) / len(peer_roes) * 100
    if pct >= 80:
        return 2
    if pct >= 60:
        return 1
    if pct >= 40:
        return 0
    if pct >= 20:
        return -1
    return -2


def _tier_from_yoy(rev_yoy: float | None, profit_yoy: float | None) -> int | None:
    if rev_yoy is None and profit_yoy is None:
        return None
    vals = [v for v in (rev_yoy, profit_yoy) if v is not None]
    mid = sum(vals) / len(vals)
    if mid >= 30:
        return 2
    if mid >= 15:
        return 1
    if mid >= 0:
        return 0
    if mid >= -10:
        return -1
    return -2


def _quality_inputs_from_row(row: dict) -> tuple[float | None, float | None]:
    """从单条财报行计算质量因子输入；应计 = (净利润-经营现金流)/营收。"""
    np = row.get("net_profit")
    cfo = row.get("operating_cf")
    revenue = row.get("revenue")
    cfo_np = (cfo / np) if cfo is not None and np not in (None, 0) else None
    accrual = (
        ((np - cfo) / revenue)
        if np is not None and cfo is not None and revenue not in (None, 0)
        else None
    )
    return cfo_np, accrual


def _best_quality_row(series: list[dict], all_rows: list[dict]) -> dict | None:
    """最新季经营现金流缺失时，回退到最近一期可算质量指标的财报。"""
    seen: set[tuple[str, str]] = set()
    candidates: list[dict] = []
    for r in series + all_rows:
        key = (r.get("period_end_date") or "", r.get("report_type") or "")
        if key in seen:
            continue
        seen.add(key)
        candidates.append(r)
    candidates.sort(key=lambda r: r.get("period_end_date", ""), reverse=True)
    for row in candidates:
        cfo_np, accrual = _quality_inputs_from_row(row)
        if cfo_np is not None or accrual is not None:
            return row
    return None


def _quality_tier(cfo_np: float | None, accrual: float | None) -> int | None:
    if cfo_np is None and accrual is None:
        return None
    score = 0
    if cfo_np is not None:
        if cfo_np > 1.5:
            score += 2
        elif cfo_np > 1.0:
            score += 1
        elif cfo_np > 0.7:
            score += 0
        elif cfo_np > 0.3:
            score -= 1
        else:
            score -= 2
    if accrual is not None and accrual > 0.10:
        score -= 1
    return _clamp_tier(round(score / 2))


def _single_quarter_value(cumulative: float | None, prev_cumulative: float | None) -> float | None:
    if cumulative is None:
        return None
    if prev_cumulative is None:
        return cumulative
    return cumulative - prev_cumulative


def _yoy_pct(current: float | None, prior: float | None) -> float | None:
    meta = compute_yoy_meta(current, prior)
    return meta["yoy_pct"] if meta["yoy_reliable"] else None


def _row_dict(row: sqlite3.Row) -> dict:
    return dict(row)


def _select_quarterly_series(rows: list[dict]) -> list[dict]:
    """优先 q1/q2/q3；剔除与 annual 重复的 12-31 quarterly 伪季报。"""
    by_date: dict[str, list[dict]] = defaultdict(list)
    for r in rows:
        by_date[r["period_end_date"]].append(r)

    selected: list[dict] = []
    for pe in sorted(by_date.keys(), reverse=True):
        group = by_date[pe]
        types = {g["report_type"] for g in group}
        pick: dict | None = None
        for pref in ("q1", "q2", "q3", "quarterly"):
            candidates = [g for g in group if g["report_type"] == pref]
            if not candidates:
                continue
            if pref == "quarterly":
                if pe[5:7] == "12" and "annual" in types:
                    continue
                if not is_quarterly_report_type("quarterly", pe):
                    continue
            pick = candidates[0]
            break
        if pick:
            selected.append(pick)
    return selected


def _find_row(series: list[dict], period_end_date: str, report_type: str | None = None) -> dict | None:
    for r in series:
        if r["period_end_date"] != period_end_date:
            continue
        if report_type is None or r["report_type"] == report_type:
            return r
    for r in series:
        if r["period_end_date"] == period_end_date:
            return r
    return None


def _previous_calendar_quarter(period_end_date: str) -> str:
    year = int(period_end_date[:4])
    month = period_end_date[5:7]
    if month == "03":
        return f"{year - 1}-12-31"
    if month == "06":
        return f"{year}-03-31"
    if month == "09":
        return f"{year}-06-30"
    return f"{year}-09-30"


def _find_prev_cumulative_quarter(row: dict, series: list[dict]) -> dict | None:
    pe = row["period_end_date"]
    rt = row["report_type"]
    year = int(pe[:4])
    month = pe[5:7]
    if rt == "q1" or month == "03":
        return None
    if rt == "q2" or month == "06":
        return _find_row(series, f"{year}-03-31", "q1")
    if rt == "q3" or month == "09":
        return _find_row(series, f"{year}-06-30", "q2")
    if month == "12":
        q3 = _find_row(series, f"{year}-09-30", "q3")
        if q3:
            return q3
        annual = next(
            (
                r
                for r in series
                if r["period_end_date"] == pe and r["report_type"] == "annual"
            ),
            None,
        )
        if annual and q3 is None:
            return _find_row(series, f"{year}-09-30")
    return None


def _metric_single_quarter(row: dict, series: list[dict], field: str) -> float | None:
    prev_q = _find_prev_cumulative_quarter(row, series)
    cum = row.get(field)
    if prev_q is None:
        return cum
    return _single_quarter_value(cum, prev_q.get(field))


def _prior_year_row(row: dict, series: list[dict]) -> dict | None:
    year = int(row["period_end_date"][:4]) - 1
    py_pe = f"{year}-{row['period_end_date'][5:]}"
    return _find_row(series, py_pe, row["report_type"])


def _annual_yoy_from_rows(rows: list[dict]) -> tuple[float | None, float | None, float | None]:
    annual = [r for r in rows if r["report_type"] == "annual"]
    annual.sort(key=lambda r: r["period_end_date"], reverse=True)
    if len(annual) < 2:
        return None, None, None
    cur, prev = annual[0], annual[1]
    rev_yoy = _yoy_pct(cur.get("revenue"), prev.get("revenue"))
    profit_yoy = _yoy_pct(cur.get("net_profit"), prev.get("net_profit"))
    cfo_yoy = _yoy_pct(cur.get("operating_cf"), prev.get("operating_cf"))
    return rev_yoy, profit_yoy, cfo_yoy


def _compute_growth_metrics(series: list[dict], all_rows: list[dict]) -> dict:
    rev_yoy_q = profit_yoy_q = growth_qoq_delta = cfo_yoy = None
    if not series:
        rev_yoy_q, profit_yoy_q, cfo_yoy = _annual_yoy_from_rows(all_rows)
        return {
            "rev_yoy_q": rev_yoy_q,
            "profit_yoy_q": profit_yoy_q,
            "growth_qoq_delta": growth_qoq_delta,
            "cfo_yoy": cfo_yoy,
            "latest": all_rows[0] if all_rows else None,
        }

    latest = series[0]
    py = _prior_year_row(latest, series)
    if py:
        cur_rev = _metric_single_quarter(latest, series, "revenue")
        py_rev = _metric_single_quarter(py, series, "revenue")
        cur_np = _metric_single_quarter(latest, series, "net_profit")
        py_np = _metric_single_quarter(py, series, "net_profit")
        cur_cfo = _metric_single_quarter(latest, series, "operating_cf")
        py_cfo = _metric_single_quarter(py, series, "operating_cf")
        rev_yoy_q = _yoy_pct(cur_rev, py_rev)
        profit_yoy_q = _yoy_pct(cur_np, py_np)
        cfo_yoy = _yoy_pct(cur_cfo, py_cfo)

        prev_q_pe = _previous_calendar_quarter(latest["period_end_date"])
        prev_q_row = _find_row(series, prev_q_pe)
        if prev_q_row:
            prev_py = _prior_year_row(prev_q_row, series)
            if prev_py:
                prev_rev_yoy = _yoy_pct(
                    _metric_single_quarter(prev_q_row, series, "revenue"),
                    _metric_single_quarter(prev_py, series, "revenue"),
                )
                if rev_yoy_q is not None and prev_rev_yoy is not None:
                    growth_qoq_delta = round(
                        (rev_yoy_q - prev_rev_yoy) / (abs(prev_rev_yoy) + 0.01),
                        4,
                    )
    else:
        rev_yoy_q, profit_yoy_q, cfo_yoy = _annual_yoy_from_rows(all_rows)

    return {
        "rev_yoy_q": rev_yoy_q,
        "profit_yoy_q": profit_yoy_q,
        "growth_qoq_delta": growth_qoq_delta,
        "cfo_yoy": cfo_yoy,
        "latest": latest,
    }


def compute_stock_v5_metrics(stock_id: int, calc_date: str | None = None) -> dict | None:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    try:
        raw_rows = conn.execute(
            """SELECT period_end_date, revenue, net_profit, operating_cf, total_assets,
                      accounts_receivable, report_type
               FROM financial_reports
               WHERE stock_id=?
               ORDER BY period_end_date DESC LIMIT 24""",
            (stock_id,),
        ).fetchall()
        ind = conn.execute(
            """SELECT debt_to_equity, roe FROM financial_indicators
               WHERE stock_id=? ORDER BY calc_date DESC LIMIT 1""",
            (stock_id,),
        ).fetchone()
        stock = conn.execute(
            "SELECT industry_sw, industry_sw2 FROM stocks WHERE id=?", (stock_id,)
        ).fetchone()
        industry_key = (stock["industry_sw2"] or stock["industry_sw"] or "") if stock else ""
        peer_roes: list[float] = []
        if industry_key:
            peer_rows = conn.execute(
                """SELECT fi.roe FROM financial_indicators fi
                   JOIN stocks s ON s.id=fi.stock_id
                   WHERE s.is_active=1 AND fi.roe IS NOT NULL
                     AND (s.industry_sw2=? OR s.industry_sw=?)
                     AND fi.calc_date=(
                         SELECT MAX(fi2.calc_date) FROM financial_indicators fi2
                         WHERE fi2.stock_id=s.id
                     )""",
                (industry_key, industry_key),
            ).fetchall()
            peer_roes = [float(r[0]) for r in peer_rows if r[0] is not None]
    finally:
        conn.close()

    if not raw_rows:
        return None

    all_rows = [_row_dict(r) for r in raw_rows]
    series = _select_quarterly_series(all_rows)
    growth = _compute_growth_metrics(series, all_rows)
    latest = growth["latest"]
    if not latest:
        return None

    quality_row = _best_quality_row(series, all_rows) or latest
    cfo_np, accrual = _quality_inputs_from_row(quality_row)

    debt_ratio = ind["debt_to_equity"] if ind else None
    debt_vs_ind = None
    if debt_ratio is not None and stock and stock["industry_sw"]:
        debt_vs_ind = _industry_debt_delta(stock["industry_sw"], debt_ratio)

    calc = calc_date or latest_trading_date() or date.today().strftime("%Y-%m-%d")
    rev_yoy_q = growth["rev_yoy_q"]
    profit_yoy_q = growth["profit_yoy_q"]
    growth_only = _tier_from_yoy(rev_yoy_q, profit_yoy_q)
    roe_val = float(ind["roe"]) if ind and ind["roe"] is not None else None
    roe_tier = _tier_from_roe_percentile(roe_val, peer_roes)
    growth_tier = _median_tier(growth_only, roe_tier) if roe_tier is not None else growth_only
    return {
        "stock_id": stock_id,
        "calc_date": calc,
        "revenue_yoy_q": rev_yoy_q,
        "profit_yoy_q": profit_yoy_q,
        "growth_qoq_delta": growth["growth_qoq_delta"],
        "cfo_np": round(cfo_np, 4) if cfo_np is not None else None,
        "accrual_ratio": round(accrual, 4) if accrual is not None else None,
        "cfo_yoy": growth["cfo_yoy"],
        "debt_ratio": debt_ratio,
        "debt_vs_industry": debt_vs_ind,
        "quality_tier": _quality_tier(cfo_np, accrual),
        "growth_tier": growth_tier,
        "source": "computed",
    }


def _industry_debt_delta(industry_sw: str, debt_ratio: float) -> float | None:
    conn = sqlite3.connect(DB_PATH)
    try:
        row = conn.execute(
            """SELECT AVG(fi.debt_to_equity) AS avg_debt
               FROM financial_indicators fi
               JOIN stocks s ON s.id=fi.stock_id
               WHERE s.industry_sw=? AND s.is_active=1 AND fi.debt_to_equity IS NOT NULL""",
            (industry_sw,),
        ).fetchone()
    finally:
        conn.close()
    if not row or row[0] is None:
        return None
    return round(debt_ratio - float(row[0]), 4)


def compute_all_v5_metrics(stock_ids: list[int] | None = None) -> dict:
    conn = sqlite3.connect(DB_PATH)
    try:
        if stock_ids:
            ph = ",".join("?" * len(stock_ids))
            ids = [
                r[0]
                for r in conn.execute(
                    f"SELECT id FROM stocks WHERE id IN ({ph}) AND is_active=1",
                    stock_ids,
                ).fetchall()
            ]
        else:
            ids = [
                r[0]
                for r in conn.execute(
                    "SELECT id FROM stocks WHERE is_active=1 ORDER BY id"
                ).fetchall()
            ]
    finally:
        conn.close()

    written = 0
    skipped = 0
    conn = sqlite3.connect(DB_PATH)
    try:
        for sid in ids:
            m = compute_stock_v5_metrics(sid)
            if not m:
                skipped += 1
                continue
            conn.execute(
                """INSERT OR REPLACE INTO stock_v5_metrics
                (stock_id, calc_date, revenue_yoy_q, profit_yoy_q, growth_qoq_delta,
                 cfo_np, accrual_ratio, cfo_yoy, debt_ratio, debt_vs_industry,
                 quality_tier, growth_tier, source)
                VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (
                    m["stock_id"],
                    m["calc_date"],
                    m.get("revenue_yoy_q"),
                    m.get("profit_yoy_q"),
                    m.get("growth_qoq_delta"),
                    m.get("cfo_np"),
                    m.get("accrual_ratio"),
                    m.get("cfo_yoy"),
                    m.get("debt_ratio"),
                    m.get("debt_vs_industry"),
                    m.get("quality_tier"),
                    m.get("growth_tier"),
                    m.get("source"),
                ),
            )
            written += 1
        conn.commit()
    finally:
        conn.close()

    return {"computed": written, "skipped": skipped, "total": len(ids)}


def get_stock_v5_metrics(stock_id: int) -> dict | None:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    try:
        row = conn.execute(
            "SELECT * FROM stock_v5_metrics WHERE stock_id=? ORDER BY calc_date DESC LIMIT 1",
            (stock_id,),
        ).fetchone()
    finally:
        conn.close()
    return dict(row) if row else None
