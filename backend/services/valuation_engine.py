"""估值面评分 — 行业内分位 + winsorize + 周期行业偏 PB。"""
from __future__ import annotations

import json
import sqlite3
from collections import defaultdict
from datetime import date

from config import DB_PATH

# 强周期行业：估值以 PB 分位为主（申万二级名 / 关键词）
CYCLE_PB_INDUSTRIES: frozenset[str] = frozenset({
    "煤炭开采", "煤炭", "工业金属", "有色金属", "钢铁", "普钢", "特钢",
    "建筑材料", "水泥", "玻璃玻纤", "航运", "航运港口", "港口", "养殖业",
    "生猪养殖", "石油开采", "油气开采", "油服工程", "化学纤维", "涤纶",
})
CYCLE_PB_KEYWORDS: tuple[str, ...] = (
    "煤炭", "钢铁", "有色", "养殖", "航运", "石油", "水泥", "玻璃", "化工",
)


def _is_cycle_industry(industry_sw2: str | None, industry_sw: str | None) -> bool:
    for raw in (industry_sw2, industry_sw):
        if not raw or not str(raw).strip():
            continue
        name = str(raw).strip()
        if name in CYCLE_PB_INDUSTRIES:
            return True
        if any(kw in name for kw in CYCLE_PB_KEYWORDS):
            return True
    return False


def _industry_blend_weights(sample_n: int) -> tuple[float, float]:
    """返回 (行业内权重, 全市场权重)。"""
    if sample_n >= 20:
        return 0.9, 0.1
    if sample_n >= 10:
        return 0.8, 0.2
    if sample_n >= 5:
        return 0.6, 0.4
    return 0.0, 1.0


def _winsorize(values: list[float]) -> list[float]:
    if len(values) < 10:
        return list(values)
    sorted_v = sorted(values)
    lo = sorted_v[int(0.01 * (len(sorted_v) - 1))]
    hi = sorted_v[int(0.99 * (len(sorted_v) - 1))]
    return [max(lo, min(hi, v)) for v in values]


def _percentile_low_is_good(val: float, population: list[float]) -> float:
    """值越低分越高（越便宜）→ 0–100。"""
    if not population:
        return 50.0
    rank = sum(1 for v in population if v > val) / len(population)
    return min(100.0, max(0.0, round(rank * 100, 1)))


def _industry_key(row: sqlite3.Row) -> str:
    sw2 = (row["industry_sw2"] or "").strip() if "industry_sw2" in row.keys() else ""
    sw = (row["industry_sw"] or "").strip()
    return sw2 or sw or "_unknown"


def compute_valuation_scores(
    *,
    sync_comprehensive: bool = True,
    score_date: str | None = None,
) -> dict:
    """为活跃股票计算估值分并写入 valuation_scores。"""
    today = score_date or date.today().strftime("%Y-%m-%d")
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row

    conn.execute(
        """CREATE TABLE IF NOT EXISTS valuation_scores (
        id INTEGER PRIMARY KEY AUTOINCREMENT, stock_id INTEGER, date TEXT,
        pe_score REAL, pb_score REAL, composite_score REAL,
        div_score REAL, breakdown_json TEXT,
        UNIQUE(stock_id, date))"""
    )
    conn.commit()

    rows = conn.execute(
        """SELECT vs.stock_id, vs.pe_ttm, vs.pb, vs.dividend_yield,
                  s.industry_sw, s.industry_sw2, s.code
           FROM valuation_snapshots vs
           JOIN stocks s ON vs.stock_id=s.id
           WHERE vs.as_of_date=(
               SELECT MAX(v2.as_of_date) FROM valuation_snapshots v2
               WHERE v2.stock_id=vs.stock_id
           ) AND s.is_active=1"""
    ).fetchall()

    if not rows:
        conn.close()
        return {"error": "无估值快照数据"}

    _PE_CAP = 1000.0  # C3 fix: clip 极端 PE，防止 10000+ PE 拉偏全市场百分位
    pe_pos = [min(float(r["pe_ttm"]), _PE_CAP) for r in rows if r["pe_ttm"] and float(r["pe_ttm"]) > 0]
    pb_pos = [float(r["pb"]) for r in rows if r["pb"] and float(r["pb"]) > 0]
    pe_w = _winsorize(pe_pos)
    pb_w = _winsorize(pb_pos)

    by_ind_pe: dict[str, list[float]] = defaultdict(list)
    by_ind_pb: dict[str, list[float]] = defaultdict(list)
    stock_pe: dict[int, float | None] = {}
    stock_pb: dict[int, float | None] = {}
    stock_meta: dict[int, sqlite3.Row] = {}

    for r in rows:
        sid = int(r["stock_id"])
        stock_meta[sid] = r
        # C3 fix: EPS≤0 (pe_ttm≤0) 视为亏损股，不参与 PE 百分位；PE>1000 clip 避免极端值污染
        pe_raw_val = float(r["pe_ttm"]) if r["pe_ttm"] else None
        pe = min(pe_raw_val, _PE_CAP) if (pe_raw_val is not None and pe_raw_val > 0) else None
        pb = float(r["pb"]) if r["pb"] and float(r["pb"]) > 0 else None
        stock_pe[sid] = pe
        stock_pb[sid] = pb
        ind = _industry_key(r)
        if pe is not None:
            by_ind_pe[ind].append(pe)
        if pb is not None:
            by_ind_pb[ind].append(pb)

    ind_pe_w = {k: _winsorize(v) for k, v in by_ind_pe.items()}
    ind_pb_w = {k: _winsorize(v) for k, v in by_ind_pb.items()}

    count = 0
    for sid, r in stock_meta.items():
        pe = stock_pe[sid]
        pb = stock_pb[sid]
        ind = _industry_key(r)
        cycle = _is_cycle_industry(
            r["industry_sw2"] if "industry_sw2" in r.keys() else None,
            r["industry_sw"],
        )

        pe_raw = r["pe_ttm"]
        pb_raw = r["pb"]
        distressed = (
            (pe_raw is None or float(pe_raw) <= 0)
            and (pb_raw is None or float(pb_raw) <= 0)
        )
        if distressed:
            comp = 0.0
            breakdown = {
                "pe": pe_raw,
                "pb": pb_raw,
                "distressed": True,
                "industry": ind,
                "cycle_pb": cycle,
            }
        else:
            mkt_pe_score = _percentile_low_is_good(pe, pe_w) if pe is not None else None
            mkt_pb_score = _percentile_low_is_good(pb, pb_w) if pb is not None else None

            ind_n = max(len(by_ind_pe.get(ind, [])), len(by_ind_pb.get(ind, [])))
            w_ind, w_mkt = _industry_blend_weights(ind_n)

            ind_pe_score = (
                _percentile_low_is_good(pe, ind_pe_w[ind])
                if pe is not None and ind in ind_pe_w
                else None
            )
            ind_pb_score = (
                _percentile_low_is_good(pb, ind_pb_w[ind])
                if pb is not None and ind in ind_pb_w
                else None
            )

            def _blend(ind_s: float | None, mkt_s: float | None) -> float | None:
                if ind_s is None and mkt_s is None:
                    return None
                if ind_s is None:
                    return mkt_s
                if mkt_s is None:
                    return ind_s
                return round(ind_s * w_ind + mkt_s * w_mkt, 1)

            pe_score = _blend(ind_pe_score, mkt_pe_score)
            pb_score = _blend(ind_pb_score, mkt_pb_score)

            if cycle:
                if pb_score is not None:
                    comp = pb_score
                elif pe_score is not None:
                    comp = pe_score
                else:
                    comp = None  # C2 fix: 缺 PE/PB 时不填默认分
            else:
                parts = [s for s in (pe_score, pb_score) if s is not None]
                comp = round(sum(parts) / len(parts), 1) if parts else None  # C2 fix

            breakdown = {
                "pe": round(float(pe_raw), 2) if pe_raw else None,
                "pb": round(float(pb_raw), 2) if pb_raw else None,
                "pe_score": pe_score,
                "pb_score": pb_score,
                "industry": ind,
                "industry_sample_n": ind_n,
                "industry_weight": w_ind,
                "market_weight": w_mkt,
                "cycle_pb": cycle,
                "low_sample": ind_n < 5,
            }

        pe_col = breakdown.get("pe_score") if not distressed else 0.0
        pb_col = breakdown.get("pb_score") if not distressed else 0.0
        conn.execute(
            """INSERT OR REPLACE INTO valuation_scores
            (stock_id, date, pe_score, pb_score, composite_score, breakdown_json)
            VALUES (?,?,?,?,?,?)""",
            (
                sid,
                today,
                pe_col,
                pb_col,
                comp,
                json.dumps(breakdown, ensure_ascii=False),
            ),
        )
        count += 1

    conn.commit()
    conn.close()

    if sync_comprehensive:
        from services.comprehensive_store import upsert_dimension_scores_to_latest_rows

        batch: dict[int, dict[str, float]] = {}
        sync_conn = sqlite3.connect(DB_PATH)
        sync_conn.row_factory = sqlite3.Row
        try:
            for sid in stock_meta:
                val_row = sync_conn.execute(
                    "SELECT composite_score FROM valuation_scores WHERE stock_id=? AND date=?",
                    (sid, today),
                ).fetchone()
                if val_row and val_row["composite_score"] is not None:
                    batch[sid] = {"val_score": float(val_row["composite_score"])}
        finally:
            sync_conn.close()
        if batch:
            upsert_dimension_scores_to_latest_rows(batch)

    return {"date": today, "computed": count}
