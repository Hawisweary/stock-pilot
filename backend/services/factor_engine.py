"""
因子评分引擎 v2.0 — 5因子 × 池内/行业内百分位排名 × 权重可配
Quality / Growth / Value / Fundamental Momentum / Risk
"""
from __future__ import annotations

import sqlite3
import json
import math
from datetime import datetime
from typing import Optional

from config import FACTOR_BENCHMARK_DEFAULT
from database import write_lock

FACTOR_META = {
    "quality": {"label": "盈利能力", "weight_default": 0.30},
    "growth": {"label": "成长性", "weight_default": 0.25},
    "value": {"label": "估值", "weight_default": 0.20},
    "momentum": {"label": "基本面动量", "weight_default": 0.10},
    "risk": {"label": "安全性", "weight_default": 0.15},
}

MIN_INDUSTRY_PEERS = 3


def _is_positive_number(val) -> bool:
    if val is None:
        return False
    if isinstance(val, float) and math.isnan(val):
        return False
    try:
        return float(val) > 0
    except (TypeError, ValueError):
        return False


class FactorEngine:
    """5因子标准化评分引擎"""

    def __init__(self, conn: sqlite3.Connection, benchmark_mode: str | None = None):
        self.conn = conn
        self.benchmark_mode = (benchmark_mode or FACTOR_BENCHMARK_DEFAULT).lower()
        if self.benchmark_mode not in ("industry", "watchlist"):
            self.benchmark_mode = "industry"

    def calculate_incremental(
        self, stock_ids: list[int], *, sync_comprehensive: bool = True
    ) -> list[dict]:
        """增量基本面评分 — 复用当日全市场百分位基准缓存。"""
        from services.factor_percentile_cache import get_universe_metrics

        calc_date = datetime.now().strftime("%Y-%m-%d")
        cached = get_universe_metrics(calc_date, self.conn)
        all_metrics = cached["metrics"]
        stocks_info = cached["stocks_info"]
        weights = self._load_weights()
        results = []

        for sid in stock_ids:
            try:
                m = all_metrics.get(sid, {})
                if not m:
                    m = self._attach_valuation(sid, {})
                    fin_rows = self.conn.execute(
                        """SELECT stock_id, period_end_date, revenue, net_profit, eps, operating_cf
                           FROM financial_reports
                           WHERE stock_id=? AND report_type='annual'
                           ORDER BY period_end_date DESC LIMIT 4""",
                        (sid,),
                    ).fetchall()
                    m["_financials"] = [dict(r) for r in fin_rows]
                    all_metrics[sid] = m

                peer_metrics = self._peer_metrics(sid, all_metrics, stocks_info)
                benchmark_label, benchmark_count = self._benchmark_meta(sid, stocks_info, peer_metrics)

                scores, detail = {}, {}
                detail["quality"], scores["quality"] = self._calc_quality(m, peer_metrics)
                detail["growth"], scores["growth"] = self._calc_growth(m, peer_metrics)
                detail["value"], scores["value"] = self._calc_value(m, peer_metrics)
                detail["momentum"], scores["momentum"] = self._calc_fundamental_momentum(m, peer_metrics)
                detail["risk"], scores["risk"] = self._calc_risk(m, peer_metrics)

                composite = sum(
                    scores[k] * weights.get(k, FACTOR_META[k]["weight_default"]) for k in FACTOR_META
                )
                detail["_weights"] = weights
                detail["_industry"] = stocks_info.get(sid, {}).get("industry", "")
                detail["_industry_sw"] = stocks_info.get(sid, {}).get("industry_sw", "")
                detail["_benchmark_mode"] = self.benchmark_mode
                detail["_benchmark_label"] = benchmark_label
                detail["_benchmark_peer_count"] = benchmark_count
                detail["_insights"] = self._generate_insights(scores, detail)

                with write_lock:
                    self.conn.execute(
                        """INSERT OR REPLACE INTO factor_scores
                           (stock_id, calc_date, profitability_score, growth_score,
                            safety_score, value_score, momentum_score, composite_score, score_detail_json)
                           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                        (
                            sid,
                            calc_date,
                            scores["quality"],
                            scores["growth"],
                            scores["risk"],
                            scores["value"],
                            scores["momentum"],
                            round(composite, 2),
                            json.dumps(detail, ensure_ascii=False),
                        ),
                    )
                    self.conn.commit()
                if sync_comprehensive:
                    try:
                        from services.comprehensive_store import sync_factor_fundamental

                        sync_factor_fundamental(sid, round(composite, 2), calc_date)
                    except Exception as e:
                        print(f"[FactorEngine] comprehensive sync sid={sid}: {e}")
                results.append(
                    {
                        "stock_id": sid,
                        "calc_date": calc_date,
                        "composite_score": round(composite, 2),
                        "benchmark_mode": self.benchmark_mode,
                        "benchmark_peer_count": benchmark_count,
                    }
                )
            except Exception as e:
                print(f"[FactorEngine] incremental sid={sid} failed: {e}")
                results.append({"stock_id": sid, "error": str(e)})
        return results

    def calculate_all(self, stock_ids: list[int], *, sync_comprehensive: bool = True) -> list[dict]:
        weights = self._load_weights()
        calc_date = datetime.now().strftime("%Y-%m-%d")
        universe_ids = self._active_stock_ids()
        all_metrics = self._get_all_metrics(universe_ids)
        stocks_info = self._load_stocks_info(universe_ids)
        results = []

        for sid in stock_ids:
            try:
                m = all_metrics.get(sid, {})
                peer_metrics = self._peer_metrics(sid, all_metrics, stocks_info)
                benchmark_label, benchmark_count = self._benchmark_meta(sid, stocks_info, peer_metrics)

                scores, detail = {}, {}
                detail["quality"], scores["quality"] = self._calc_quality(m, peer_metrics)
                detail["growth"], scores["growth"] = self._calc_growth(m, peer_metrics)
                detail["value"], scores["value"] = self._calc_value(m, peer_metrics)
                detail["momentum"], scores["momentum"] = self._calc_fundamental_momentum(m, peer_metrics)
                detail["risk"], scores["risk"] = self._calc_risk(m, peer_metrics)

                composite = sum(
                    scores[k] * weights.get(k, FACTOR_META[k]["weight_default"]) for k in FACTOR_META
                )
                detail["_weights"] = weights
                detail["_industry"] = stocks_info.get(sid, {}).get("industry", "")
                detail["_industry_sw"] = stocks_info.get(sid, {}).get("industry_sw", "")
                detail["_benchmark_mode"] = self.benchmark_mode
                detail["_benchmark_label"] = benchmark_label
                detail["_benchmark_peer_count"] = benchmark_count
                detail["_insights"] = self._generate_insights(scores, detail)

                with write_lock:
                    self.conn.execute(
                        """INSERT OR REPLACE INTO factor_scores
                           (stock_id, calc_date, profitability_score, growth_score,
                            safety_score, value_score, momentum_score, composite_score, score_detail_json)
                           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                        (
                            sid,
                            calc_date,
                            scores["quality"],
                            scores["growth"],
                            scores["risk"],
                            scores["value"],
                            scores["momentum"],
                            round(composite, 2),
                            json.dumps(detail, ensure_ascii=False),
                        ),
                    )
                    self.conn.commit()
                if sync_comprehensive:
                    try:
                        from services.comprehensive_store import sync_factor_fundamental

                        sync_factor_fundamental(sid, round(composite, 2), calc_date)
                    except Exception as e:
                        print(f"[FactorEngine] comprehensive sync sid={sid}: {e}")
                results.append(
                    {
                        "stock_id": sid,
                        "calc_date": calc_date,
                        **{f"{k}_score": v for k, v in scores.items()},
                        "composite_score": round(composite, 2),
                        "benchmark_mode": self.benchmark_mode,
                        "benchmark_peer_count": benchmark_count,
                    }
                )
            except Exception as e:
                print(f"[FactorEngine] sid={sid} failed: {e}")
                results.append({"stock_id": sid, "error": str(e)})
        return results

    def _active_stock_ids(self) -> list[int]:
        rows = self.conn.execute("SELECT id FROM stocks WHERE is_active=1").fetchall()
        return [r["id"] for r in rows]

    def _load_weights(self) -> dict:
        try:
            row = self.conn.execute("SELECT * FROM factor_weights WHERE id=1").fetchone()
        except sqlite3.OperationalError:
            row = None
        if row:
            r = dict(row) if hasattr(row, 'keys') else dict(zip(
                ['id','weight_quality','weight_growth','weight_value','weight_momentum','weight_safety',
                 'created_at','updated_at'], row or []
            ))
            return {k: r.get(f"weight_{k}", FACTOR_META[k]["weight_default"]) for k in FACTOR_META}
        return {k: v["weight_default"] for k, v in FACTOR_META.items()}

    def _load_stocks_info(self, stock_ids: list[int]) -> dict:
        if not stock_ids:
            return {}
        placeholders = ",".join(["?"] * len(stock_ids))
        rows = self.conn.execute(
            f"SELECT id, industry, industry_sw FROM stocks WHERE id IN ({placeholders})",
            tuple(stock_ids),
        ).fetchall()
        return {r["id"]: dict(r) for r in rows}

    def _peer_metrics(self, sid: int, all_metrics: dict, stocks_info: dict) -> dict:
        if self.benchmark_mode == "watchlist":
            return all_metrics

        info = stocks_info.get(sid, {})
        sw = (info.get("industry_sw") or "").strip()
        ind = (info.get("industry") or "").strip()

        peer_ids = set()
        for pid, pinfo in stocks_info.items():
            psw = (pinfo.get("industry_sw") or "").strip()
            pind = (pinfo.get("industry") or "").strip()
            if sw and psw == sw:
                peer_ids.add(pid)
            elif not sw and ind and pind == ind:
                peer_ids.add(pid)

        if len(peer_ids) < MIN_INDUSTRY_PEERS:
            return all_metrics
        return {k: v for k, v in all_metrics.items() if k in peer_ids}

    def _benchmark_meta(self, sid: int, stocks_info: dict, peer_metrics: dict) -> tuple[str, int]:
        n = len(peer_metrics)
        if self.benchmark_mode == "watchlist":
            return "自选股池", n
        sw = (stocks_info.get(sid, {}).get("industry_sw") or "").strip()
        if sw and n >= MIN_INDUSTRY_PEERS:
            return f"申万一级·{sw}", n
        ind = (stocks_info.get(sid, {}).get("industry") or "").strip()
        if ind:
            return f"行业·{ind}", n
        return "全市场跟踪池", n

    def _attach_valuation(self, sid: int, m: dict) -> dict:
        try:
            row = self.conn.execute(
                """SELECT pe_ttm, pb, dividend_yield, market_cap, as_of_date, peg_ratio, ps_ratio
                   FROM valuation_snapshots WHERE stock_id=?
                   ORDER BY as_of_date DESC LIMIT 1""",
                (sid,),
            ).fetchone()
        except sqlite3.OperationalError:
            row = None
        if row:
            m = {**m, **{k: row[k] for k in ("pe_ttm", "pb", "dividend_yield", "market_cap", "peg_ratio", "ps_ratio")}}
            m["_valuation_as_of"] = row["as_of_date"]
        return m

    def _get_all_metrics(self, stock_ids: list[int]) -> dict:
        if not stock_ids:
            return {}

        optional_cols = [
            "operating_margin",
            "fcf_margin",
            "roic",
            "rev_growth_3y",
            "eps_growth_3y",
            "earnings_volatility",
            "beta",
        ]
        ind_cols = self._indicator_columns()
        select_cols = [
            "roe",
            "gross_margin",
            "net_margin",
            "pe_ttm",
            "pb",
            "dividend_yield",
            "debt_to_equity",
            "current_ratio",
        ]
        for c in optional_cols:
            if c in ind_cols:
                select_cols.append(c)

        placeholders = ",".join(["?"] * len(stock_ids))
        cols_sql = ", ".join(f"fi.{c}" for c in select_cols)
        ind_rows = self.conn.execute(
            f"""SELECT fi.stock_id, {cols_sql}
                FROM financial_indicators fi
                INNER JOIN (
                    SELECT stock_id, MAX(calc_date) AS md FROM financial_indicators
                    WHERE stock_id IN ({placeholders}) GROUP BY stock_id
                ) t ON fi.stock_id = t.stock_id AND fi.calc_date = t.md""",
            tuple(stock_ids),
        ).fetchall()

        val_rows = self.conn.execute(
            f"""SELECT vs.stock_id, vs.pe_ttm, vs.pb, vs.dividend_yield, vs.market_cap,
                       vs.as_of_date, vs.peg_ratio, vs.ps_ratio
                FROM valuation_snapshots vs
                INNER JOIN (
                    SELECT stock_id, MAX(as_of_date) AS md FROM valuation_snapshots
                    WHERE stock_id IN ({placeholders}) GROUP BY stock_id
                ) t ON vs.stock_id = t.stock_id AND vs.as_of_date = t.md""",
            tuple(stock_ids),
        ).fetchall()
        val_map = {r["stock_id"]: dict(r) for r in val_rows}

        fin_rows = self.conn.execute(
            f"""SELECT stock_id, period_end_date, revenue, net_profit, eps, operating_cf
                FROM financial_reports
                WHERE stock_id IN ({placeholders}) AND report_type='annual'
                ORDER BY stock_id, period_end_date DESC""",
            tuple(stock_ids),
        ).fetchall()
        fin_map: dict[int, list] = {}
        for r in fin_rows:
            sid = r["stock_id"]
            if sid not in fin_map:
                fin_map[sid] = []
            if len(fin_map[sid]) < 4:
                fin_map[sid].append(dict(r))

        metrics = {}
        for row in ind_rows:
            sid = row["stock_id"]
            m = {c: row[c] for c in select_cols}
            v = val_map.get(sid)
            if v:
                m.update({k: v[k] for k in ("pe_ttm", "pb", "dividend_yield", "market_cap", "peg_ratio", "ps_ratio")})
                m["_valuation_as_of"] = v.get("as_of_date")
            m["_financials"] = fin_map.get(sid, [])
            metrics[sid] = m

        for sid in stock_ids:
            if sid not in metrics:
                m = {}
                v = val_map.get(sid)
                if v:
                    m.update({k: v[k] for k in ("pe_ttm", "pb", "dividend_yield", "market_cap", "peg_ratio", "ps_ratio")})
                    m["_valuation_as_of"] = v.get("as_of_date")
                m["_financials"] = fin_map.get(sid, [])
                metrics[sid] = m
        return metrics

    def _indicator_columns(self) -> set[str]:
        rows = self.conn.execute("PRAGMA table_info(financial_indicators)").fetchall()
        return {r[1] for r in rows}

    def _calc_quality(self, m: dict, peers: dict) -> tuple[dict, float]:
        sub = {"roe": 0.40, "gm": 0.25, "nm": 0.25, "roic": 0.10}
        if m.get("gross_margin") is None and m.get("roe"):
            sub = {"roe": 0.50, "gm": 0.00, "nm": 0.35, "roic": 0.15}
        vals = {
            "roe": self._pct(m.get("roe"), peers, "roe"),
            "gm": self._pct(m.get("gross_margin"), peers, "gross_margin"),
            "nm": self._pct(m.get("net_margin"), peers, "net_margin"),
            "roic": self._pct(m.get("roic"), peers, "roic"),
        }
        score = sum((vals[k] or 50) * w for k, w in sub.items())
        return (
            {"sub_scores": {f"{k}_score": round(vals[k] or 50, 1) for k in sub}, "raw": {k: m.get(k) for k in sub}},
            min(100, round(score, 1)),
        )

    def _calc_growth(self, m: dict, peers: dict) -> tuple[dict, float]:
        fin = m.get("_financials", [])
        rev_cagr = self._cagr([f.get("revenue") for f in fin])
        profit_cagr = self._cagr([f.get("net_profit") for f in fin])
        eps_vals = [f.get("eps") for f in fin if f.get("eps")]
        eps_g = (
            (eps_vals[0] - eps_vals[-1]) / abs(eps_vals[-1])
            if len(eps_vals) >= 2 and eps_vals[-1]
            else None
        )
        all_rev, all_profit, all_eps = [], [], []
        for sm in peers.values():
            f = sm.get("_financials", [])
            r = self._cagr([x.get("revenue") for x in f])
            p = self._cagr([x.get("net_profit") for x in f])
            e = [x.get("eps") for x in f if x.get("eps")]
            if r is not None:
                all_rev.append(r)
            if p is not None:
                all_profit.append(p)
            if len(e) >= 2 and e[-1]:
                all_eps.append((e[0] - e[-1]) / abs(e[-1]))
        score = (self._pct(rev_cagr, all_rev) or 50) * 0.40 + (self._pct(profit_cagr, all_profit) or 50) * 0.40 + (
            self._pct(eps_g, all_eps) or 50
        ) * 0.20
        return (
            {"revenue_cagr_3y": rev_cagr, "profit_cagr_3y": profit_cagr, "eps_growth": eps_g},
            min(100, round(score, 1)),
        )

    def _calc_value(self, m: dict, peers: dict) -> tuple[dict, float]:
        # 估值因子 v3: 5个子因子，行业百分位比较
        pe = self._pct_inv(m.get("pe_ttm"), [v.get("pe_ttm") for v in peers.values() if _is_positive_number(v.get("pe_ttm"))])
        pb = self._pct_inv(m.get("pb"), [v.get("pb") for v in peers.values() if _is_positive_number(v.get("pb"))])
        dy = self._pct(m.get("dividend_yield"), [v.get("dividend_yield") for v in peers.values()])
        peg = self._pct_inv(m.get("peg_ratio"), [v.get("peg_ratio") for v in peers.values() if _is_positive_number(v.get("peg_ratio"))])
        ps = self._pct_inv(m.get("ps_ratio"), [v.get("ps_ratio") for v in peers.values() if _is_positive_number(v.get("ps_ratio"))])
        # 权重: PE 30%, PB 25%, DY 15%, PEG 20%, PS 10%
        score = (pe or 50) * 0.30 + (pb or 50) * 0.25 + (dy or 50) * 0.15 + (peg or 50) * 0.20 + (ps or 50) * 0.10
        return (
            {
                "pe_ttm": m.get("pe_ttm"),
                "pb": m.get("pb"),
                "dy": m.get("dividend_yield"),
                "peg_ratio": m.get("peg_ratio"),
                "ps_ratio": m.get("ps_ratio"),
                "as_of": m.get("_valuation_as_of"),
            },
            min(100, round(score, 1)),
        )

    def _calc_fundamental_momentum(self, m: dict, peers: dict) -> tuple[dict, float]:
        """基本面动量：营收/净利/EPS同比改善 + ROE 趋势（不用股价）"""
        fin = m.get("_financials", [])
        rev_yoy = self._yoy_growth([f.get("revenue") for f in fin])
        profit_yoy = self._yoy_growth([f.get("net_profit") for f in fin])
        eps_vals = [f.get("eps") for f in fin if f.get("eps") is not None]
        eps_yoy = self._yoy_growth(eps_vals)
        roe_now = m.get("roe")
        roe_prev = None
        if len(fin) >= 2:
            roe_prev = m.get("_roe_prev")

        all_rev, all_profit, all_eps, all_roe_delta = [], [], [], []
        for sm in peers.values():
            pf = sm.get("_financials", [])
            r = self._yoy_growth([x.get("revenue") for x in pf])
            p = self._yoy_growth([x.get("net_profit") for x in pf])
            ev = [x.get("eps") for x in pf if x.get("eps") is not None]
            e = self._yoy_growth(ev)
            if r is not None:
                all_rev.append(r)
            if p is not None:
                all_profit.append(p)
            if e is not None:
                all_eps.append(e)
            if sm.get("roe") is not None:
                all_roe_delta.append(sm.get("roe"))

        roe_delta = (roe_now - roe_prev) if roe_now is not None and roe_prev is not None else None
        parts = [
            (self._pct(rev_yoy, all_rev), 0.35),
            (self._pct(profit_yoy, all_profit), 0.35),
            (self._pct(eps_yoy, all_eps), 0.20),
            (self._pct(roe_delta, all_roe_delta), 0.10),
        ]
        score, w_sum = 0.0, 0.0
        for p, w in parts:
            if p is not None:
                score += p * w
                w_sum += w
        final = score / w_sum if w_sum > 0 else 50.0
        return (
            {
                "revenue_yoy": rev_yoy,
                "profit_yoy": profit_yoy,
                "eps_yoy": eps_yoy,
                "roe": roe_now,
                "type": "fundamental",
            },
            min(100, round(final, 1)),
        )

    def _calc_risk(self, m: dict, peers: dict) -> tuple[dict, float]:
        # V3: 安全性因子 - 使用流动比率、资产负债率、利息保障倍数（替换ROE）
        cr_raw = m.get("current_ratio")
        de_raw = m.get("debt_to_equity")
        ic_raw = m.get("interest_coverage_ratio")  # 利息保障倍数 = EBIT / 利息费用
        if cr_raw is not None and de_raw is not None and ic_raw is not None:
            # 1. 流动比率 (0-10)
            if cr_raw > 2: cr_s = 10
            elif cr_raw > 1: cr_s = 5
            else: cr_s = 0
            # 2. 资产负债率 (0-10, 权重1.5): debt_to_equity→asset_liability_ratio
            alr = de_raw / (1 + de_raw) * 100  # 转换成%
            if alr < 40: de_s = 10
            elif alr < 60: de_s = 6
            elif alr < 80: de_s = 3
            else: de_s = 0
            # 3. 利息保障倍数 (0-10)
            if ic_raw > 5: ic_s = 10
            elif ic_raw > 2: ic_s = 5
            elif ic_raw > 1: ic_s = 2
            else: ic_s = 0
            score = (cr_s + de_s * 1.5 + ic_s) / 3.5 * 10
            return (
                {"debt_to_equity": de_raw, "current_ratio": cr_raw, "interest_coverage_ratio": ic_raw},
                min(100, round(score, 1)),
            )
        # 退化：百分位法（若缺少利息保障倍数，使用ROE作为临时替代）
        de = self._pct_inv(m.get("debt_to_equity"), [v.get("debt_to_equity") for v in peers.values()])
        cr = self._pct(m.get("current_ratio"), [v.get("current_ratio") for v in peers.values()])
        roe = self._pct(m.get("roe"), [v.get("roe") for v in peers.values()])
        score = (de or 50) * 0.50 + (cr or 50) * 0.25 + (roe or 50) * 0.25
        return (
            {"debt_to_equity": m.get("debt_to_equity"), "current_ratio": m.get("current_ratio"), "roe": m.get("roe")},
            min(100, round(score, 1)),
        )

    def _yoy_growth(self, values: list) -> Optional[float]:
        from services.data_processor import compute_yoy_meta

        clean = [v for v in values if v is not None]
        if len(clean) < 2:
            return None
        return compute_yoy_meta(clean[0], clean[1])["yoy_decimal"]

    def _pct(self, value, all_values, key=None) -> Optional[float]:
        if value is None:
            return None
        try:
            value = float(value)
        except (TypeError, ValueError):
            return None
        if isinstance(value, float) and math.isnan(value):
            return None
        if key and isinstance(all_values, dict):
            all_values = [v.get(key) for v in all_values.values() if v.get(key) is not None]
        clean = [v for v in all_values if v is not None and not (isinstance(v, float) and math.isnan(v))]
        if len(clean) < 2:
            return 50.0
        rank = sum(1 for v in clean if v <= value) / len(clean) * 100
        return round(rank, 1)

    def _pct_inv(self, value, all_values, key=None) -> Optional[float]:
        p = self._pct(value, all_values, key)
        return 100 - p if p is not None else None

    def _cagr(self, values: list) -> Optional[float]:
        clean = [v for v in values if v is not None and v > 0]
        if len(clean) < 2:
            return None
        return (clean[0] / clean[-1]) ** (1 / (len(clean) - 1)) - 1

    def _generate_insights(self, scores: dict, detail: dict) -> list[dict]:
        insights = []
        m = detail.get("quality", {})
        roe = (m.get("raw") or {}).get("roe")
        if _is_positive_number(roe) and float(roe) > 20:
            insights.append(
                {"type": "quality", "fact": f"ROE={roe:.1f}%", "signal": "strong", "context": "资本回报优异"}
            )
        g = detail.get("growth", {})
        cagr = g.get("revenue_cagr_3y")
        if cagr is not None and cagr > 0.15:
            insights.append(
                {"type": "growth", "fact": f"3年营收CAGR={cagr*100:.0f}%", "signal": "strong", "context": "成长性突出"}
            )
        mom = detail.get("momentum", {})
        ry = mom.get("revenue_yoy")
        if ry is not None and ry > 0.1:
            insights.append(
                {"type": "momentum", "fact": f"营收同比+{ry*100:.0f}%", "signal": "strong", "context": "基本面动量改善"}
            )
        elif ry is not None and ry < -0.05:
            insights.append(
                {"type": "momentum", "fact": f"营收同比{ry*100:.0f}%", "signal": "weak", "context": "营收承压"}
            )
        pe = detail.get("value", {}).get("pe_ttm")
        if pe and pe > 80:
            insights.append({"type": "value", "fact": f"PE_TTM={pe:.0f}", "signal": "expensive", "context": "估值偏高"})
        return insights
