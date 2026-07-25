"""ML 分 horizon 特征选配 — H5 资金/情绪, H20 趋势/基本面, H60 估值/质量。"""
from __future__ import annotations

import math
import sqlite3
from collections import defaultdict
from dataclasses import dataclass, field
from typing import Any, Optional

from services.ml_impute import ImputeTable, WINSOR_BOUNDS, is_valid, winsorize

# 特征名列表（训练向量顺序）
H5_FEATURES = [
    "pv_corr_5",           # 5日价量相关
    "reversal_5",          # 5日反转（-累计收益）
    "turnover_mean_5",     # 5日换手均值
    "turnover_std_5",      # 5日换手波动
    "main_net_5d",         # 主力净流入5日累计
    "miss_main_net_5d",    # 主力流缺失指示
    "vol_5",               # 5日波动率
    "rsi_14",              # RSI 超买超卖
    "amihud_5",            # Amihud 非流动性
    "mom_5_rank",          # 5日涨幅横截面 rank
    "turnover_extreme",    # 换手率极端分位（距50%）
]

H20_FEATURES = [
    "mom_20_skip5",        # 20日动量剔除近5日
    "ma20_slope",          # MA20 斜率
    "ma20_dev",            # 相对 MA20 乖离
    "vol_20",              # 20日波动
    "turnover_mean_20",    # 20日换手均值
    "turnover_chg_20",     # 换手变化（5d/20d - 1）
    "pv_corr_20",          # 20日价量相关
    "macd_hist",           # MACD 柱
    "margin_chg_20",       # 融资余额20日变化%
    "revenue_yoy_q",       # 营收同比（季）
    "cfo_np",              # 经营现金流/净利润
    "eps_revision_3m",     # 分析师 EPS 3M 修正（作1M代理）
    "pe_ttm",              # PE TTM（估值）
    "amp_std_20",          # 20日振幅标准差
    "rs_20_rank",          # 20日相对强度 rank
    "industry_eps_rev",    # 行业 EPS 上修占比
    "illiq_20",            # 20日 Amihud 非流动性
    "miss_revenue_yoy_q",
    "miss_cfo_np",
    "miss_pe_ttm",
    "miss_eps_revision_3m",
    "miss_industry_eps_rev",
    "miss_margin_chg_20",
]

H60_FEATURES = [
    "pe_ttm",              # PE
    "pb",                  # PB
    "dividend_yield",      # 股息率
    "roe_proxy",           # ROE 代理（quality tier）
    "revenue_yoy_q",       # 营收增速
    "cfo_np",              # 现金流质量
    "debt_ratio",          # 资产负债率
    "eps_revision_3m",     # EPS 3M 修正
    "industry_eps_rev",    # 行业景气
    "mom_12m_skip1m",      # 12月动量剔除近1月
    "beta_60",             # 与池内等权市场60日相关
    "vol_60",              # 60日波动
    "turnover_mean_60",    # 60日换手均值
    "macro_pmi",           # PMI
    "macro_bond_10y",      # 10Y 国债
    "macro_usd_cnh",       # 汇率
    "miss_pe_ttm",
    "miss_pb",
    "miss_revenue_yoy_q",
    "miss_cfo_np",
    "miss_debt_ratio",
    "miss_eps_revision_3m",
]

# v3 实验:行业中性(cross-sectional 行业内分位)替换 raw 基本面 + 砍掉 miss flag。
# 假设:原始 PE/ROE/营收增速的噪声很多来自行业混淆,行业内分位能去掉这层。
H20_V3_FEATURES = [
    # 技术核心(沿用 v2)
    "mom_20_skip5", "ma20_slope", "ma20_dev", "vol_20",
    "turnover_mean_20", "turnover_chg_20", "pv_corr_20", "macd_hist",
    "margin_chg_20", "amp_std_20", "rs_20_rank", "illiq_20",
    # 行业景气 / 分析师修正(沿用)
    "industry_eps_rev", "eps_revision_3m",
    # v3 核心:基本面行业内分位(替换 raw revenue_yoy_q / cfo_np,新增 quality)
    "ind_rank_revenue_yoy_q", "ind_rank_cfo_np", "ind_rank_quality",
    # 估值 raw 保留(valuation 仅有最新快照,无法做无未来偏差的历史分位)
    "pe_ttm",
]

HORIZON_FEATURE_NAMES: dict[int, list[str]] = {
    5: H5_FEATURES,
    20: H20_FEATURES,
    60: H60_FEATURES,
}


def feature_names_for(horizon: int, variant: str = "v2") -> list[str]:
    if variant == "v3" and horizon == 20:
        return list(H20_V3_FEATURES)
    return list(HORIZON_FEATURE_NAMES.get(horizon, H20_FEATURES))


def _sf(v: Any, default: float = 0.0) -> float:
    if v is None:
        return default
    try:
        f = float(v)
        return f if math.isfinite(f) else default
    except (TypeError, ValueError):
        return default


def _pct_ret(closes: list[float], lag: int) -> float:
    """closes 按日期升序，末元素为当前。"""
    if len(closes) <= lag or closes[-lag - 1] <= 0:
        return 0.0
    return closes[-1] / closes[-lag - 1] - 1


def _volatility(closes: list[float], window: int) -> float:
    if len(closes) <= window:
        return 0.0
    start = len(closes) - window
    rets = [
        closes[j] / closes[j - 1] - 1
        for j in range(start + 1, len(closes))
        if closes[j - 1] > 0
    ]
    if not rets:
        return 0.0
    return math.sqrt(sum(r * r for r in rets) / len(rets))


def _rolling_corr(xs: list[float], ys: list[float], window: int) -> float:
    if len(xs) < window or len(ys) < window:
        return 0.0
    x, y = xs[-window:], ys[-window:]
    mx, my = sum(x) / window, sum(y) / window
    num = sum((x[i] - mx) * (y[i] - my) for i in range(window))
    den_x = math.sqrt(sum((v - mx) ** 2 for v in x))
    den_y = math.sqrt(sum((v - my) ** 2 for v in y))
    if den_x < 1e-12 or den_y < 1e-12:
        return 0.0
    return num / (den_x * den_y)


def _rsi(closes: list[float], period: int = 14) -> float:
    if len(closes) < period + 1:
        return 50.0
    gains, losses = [], []
    for j in range(len(closes) - period, len(closes)):
        d = closes[j] - closes[j - 1]
        gains.append(max(d, 0))
        losses.append(max(-d, 0))
    ag = sum(gains) / period
    al = sum(losses) / period
    if al <= 0:
        return 100.0
    return 100 - 100 / (1 + ag / al)


def _ema_series(vals: list[float], span: int) -> list[float]:
    if not vals:
        return []
    alpha = 2 / (span + 1)
    out = [vals[0]]
    for v in vals[1:]:
        out.append(alpha * v + (1 - alpha) * out[-1])
    return out


def _macd_hist(closes: list[float]) -> float:
    if len(closes) < 35:
        return 0.0
    tail = closes[-35:]
    e12 = _ema_series(tail, 12)
    e26 = _ema_series(tail, 26)
    macd_line = [a - b for a, b in zip(e12, e26)]
    sig = _ema_series(macd_line, 9)
    return macd_line[-1] - sig[-1]


def _amihud(closes: list[float], amounts: list[float], window: int = 5) -> float:
    n = min(window, len(closes) - 1, len(amounts))
    if n < 2:
        return 0.0
    vals = []
    for j in range(len(closes) - n, len(closes)):
        if closes[j - 1] <= 0:
            continue
        ret = abs(closes[j] / closes[j - 1] - 1)
        amt = amounts[j] or 0
        if amt > 0:
            vals.append(ret / amt * 1e8)
    return sum(vals) / len(vals) if vals else 0.0


def _amp_std(highs: list[float], lows: list[float], closes: list[float], w: int) -> float:
    n = min(w, len(highs), len(lows), len(closes))
    if n < 5:
        return 0.0
    h, l, c = highs[-n:], lows[-n:], closes[-n:]
    amps = [(h[i] - l[i]) / c[i] for i in range(n) if c[i] > 0]
    if len(amps) < 3:
        return 0.0
    m = sum(amps) / len(amps)
    return math.sqrt(sum((a - m) ** 2 for a in amps) / len(amps))


# Bar: (date, close, volume, high, low, turnover, amount)
QuoteBar = tuple[str, float, float, float, float, float, float]


@dataclass
class MlFeatureContext:
    """预加载慢变/外部特征，按 (stock_id, date) 查询。"""

    stock_industry: dict[int, str] = field(default_factory=dict)
    fund_flow_5d: dict[tuple[int, str], float] = field(default_factory=dict)
    margin_by_stock: dict[int, list[tuple[str, float]]] = field(default_factory=dict)
    v5_metrics: dict[tuple[int, str], dict] = field(default_factory=dict)
    eps_forecast: dict[tuple[int, str], dict] = field(default_factory=dict)
    industry_eps: dict[tuple[str, str], float] = field(default_factory=dict)
    valuation: dict[int, dict] = field(default_factory=dict)
    macro: dict[str, dict] = field(default_factory=dict)
    macro_dates: list[str] = field(default_factory=list)
    impute: ImputeTable = field(default_factory=ImputeTable)
    market_ret: dict[str, float] = field(default_factory=dict)

    @classmethod
    def load(cls, conn: sqlite3.Connection, dates: list[str]) -> MlFeatureContext:
        ctx = cls()
        date_set = set(dates)
        impute = ImputeTable()

        def _safe(sql: str):
            try:
                return conn.execute(sql).fetchall()
            except sqlite3.OperationalError:
                return []

        for sid, ind in _safe(
            "SELECT id, COALESCE(industry_sw2, industry_sw, '') FROM stocks WHERE is_active=1"
        ):
            ctx.stock_industry[int(sid)] = str(ind or "")

        for sid, dt, net5 in _safe(
            """SELECT stock_id, trade_date, main_net_5d FROM stock_fund_flow_daily
               WHERE main_net_5d IS NOT NULL"""
        ):
            if dt in date_set:
                sid_i = int(sid)
                ctx.fund_flow_5d[(sid_i, dt)] = _sf(net5)
                ind = ctx.stock_industry.get(sid_i, "")
                impute.add("main_net_5d", ind, _sf(net5))

        by_sid: dict[int, list] = defaultdict(list)
        for sid, dt, bal in _safe(
            """SELECT stock_id, date, margin_balance FROM eastmoney_margin
               WHERE margin_balance IS NOT NULL ORDER BY stock_id, date"""
        ):
            by_sid[int(sid)].append((dt, _sf(bal)))
        ctx.margin_by_stock = dict(by_sid)

        for row in _safe(
            """SELECT stock_id, calc_date, revenue_yoy_q, cfo_np, debt_ratio, quality_tier
               FROM stock_v5_metrics"""
        ):
            sid, dt = int(row[0]), row[1]
            if dt in date_set:
                ind = ctx.stock_industry.get(sid, "")
                vals = {
                    "revenue_yoy_q": _sf(row[2]),
                    "cfo_np": _sf(row[3]),
                    "debt_ratio": _sf(row[4]),
                    "quality_tier": _sf(row[5], 2),
                }
                ctx.v5_metrics[(sid, dt)] = vals
                for f in ("revenue_yoy_q", "cfo_np", "debt_ratio"):
                    impute.add(f, ind, vals[f])
                if is_valid(vals["quality_tier"]):
                    impute.add("quality_tier", ind, vals["quality_tier"])

        for row in _safe(
            """SELECT stock_id, as_of_date, revision_3m_pct FROM stock_eps_forecast
               WHERE revision_3m_pct IS NOT NULL"""
        ):
            sid, dt = int(row[0]), row[1]
            if dt in date_set:
                rev = _sf(row[2])
                ctx.eps_forecast[(sid, dt)] = {"revision_3m_pct": rev}
                ind = ctx.stock_industry.get(sid, "")
                impute.add("eps_revision_3m", ind, rev)

        for ind, dt, rev in _safe(
            """SELECT industry_sw2, trade_date, revision_3m_pct
               FROM industry_eps_revision_daily WHERE revision_3m_pct IS NOT NULL"""
        ):
            if dt in date_set:
                ctx.industry_eps[(str(ind), dt)] = _sf(rev)
                impute.add("industry_eps_rev", str(ind), _sf(rev))

        for row in _safe(
            """SELECT stock_id, pe_ttm, pb, dividend_yield FROM valuation_snapshots
               WHERE stock_id IN (SELECT id FROM stocks WHERE is_active=1)
               ORDER BY as_of_date DESC"""
        ):
            sid = int(row[0])
            if sid not in ctx.valuation:
                ind = ctx.stock_industry.get(sid, "")
                val = {
                    "pe_ttm": _sf(row[1]),
                    "pb": _sf(row[2]),
                    "dividend_yield": _sf(row[3]),
                }
                ctx.valuation[sid] = val
                impute.add("pe_ttm", ind, val["pe_ttm"])
                impute.add("pb", ind, val["pb"])
                impute.add("dividend_yield", ind, val["dividend_yield"])

        for row in _safe(
            """SELECT date, pmi_manufacturing, bond_yield_10y, usd_cnh FROM macro_indicators ORDER BY date"""
        ):
            ctx.macro[str(row[0])] = {
                "pmi": _sf(row[1], 50),
                "bond_10y": _sf(row[2]),
                "usd_cnh": _sf(row[3]),
            }
        ctx.macro_dates = sorted(ctx.macro.keys())
        impute.finalize()
        ctx.impute = impute

        return ctx

    def _margin_chg(self, stock_id: int, as_of: str, lag: int) -> Optional[float]:
        hist = self.margin_by_stock.get(stock_id, [])
        if not hist:
            return None
        cur = next((b for d, b in hist if d <= as_of), None)
        if cur is None or cur <= 0:
            return None
        prior_dates = [d for d, _ in hist if d <= as_of]
        if len(prior_dates) <= lag:
            return None
        prior_dt = prior_dates[-lag - 1] if len(prior_dates) > lag else prior_dates[0]
        prior = next((b for d, b in hist if d == prior_dt), cur)
        if prior <= 0:
            return None
        return (cur / prior - 1) * 100

    def _macro_at(self, as_of: str) -> dict[str, float]:
        if not self.macro_dates:
            return {"pmi": 50.0, "bond_10y": 0.0, "usd_cnh": 0.0}
        idx = next((i for i, d in enumerate(self.macro_dates) if d > as_of), len(self.macro_dates))
        dt = self.macro_dates[max(0, idx - 1)]
        m = self.macro.get(dt, {})
        return {
            "pmi": m.get("pmi", 50.0),
            "bond_10y": m.get("bond_10y", 0.0),
            "usd_cnh": m.get("usd_cnh", 0.0),
        }

    def _fill_field(
        self,
        stock_id: int,
        field: str,
        raw: Any,
        *,
        miss_key: str,
        out: dict[str, float],
    ) -> None:
        ind = self.stock_industry.get(stock_id, "")
        if is_valid(raw):
            lo, hi = WINSOR_BOUNDS.get(field, (-1e9, 1e9))
            out[field] = winsorize(float(raw), lo, hi)
            out[miss_key] = 0.0
        else:
            out[field] = self.impute.lookup(field, ind)
            out[miss_key] = 1.0

    def _aux(self, stock_id: int, as_of: str) -> dict[str, float]:
        v5 = self.v5_metrics.get((stock_id, as_of), {})
        eps = self.eps_forecast.get((stock_id, as_of), {})
        val = self.valuation.get(stock_id, {})
        ind = self.stock_industry.get(stock_id, "")
        macro = self._macro_at(as_of)
        out: dict[str, float] = {}

        self._fill_field(stock_id, "revenue_yoy_q", v5.get("revenue_yoy_q"), miss_key="miss_revenue_yoy_q", out=out)
        self._fill_field(stock_id, "cfo_np", v5.get("cfo_np"), miss_key="miss_cfo_np", out=out)
        self._fill_field(stock_id, "debt_ratio", v5.get("debt_ratio"), miss_key="miss_debt_ratio", out=out)
        out["roe_proxy"] = (
            v5.get("quality_tier", 2.0) * 10
            if v5 and is_valid(v5.get("quality_tier"))
            else self.impute.lookup("quality_tier", ind) * 10
        )
        self._fill_field(stock_id, "eps_revision_3m", eps.get("revision_3m_pct"), miss_key="miss_eps_revision_3m", out=out)
        self._fill_field(
            stock_id,
            "industry_eps_rev",
            self.industry_eps.get((ind, as_of)),
            miss_key="miss_industry_eps_rev",
            out=out,
        )
        self._fill_field(stock_id, "pe_ttm", val.get("pe_ttm"), miss_key="miss_pe_ttm", out=out)
        self._fill_field(stock_id, "pb", val.get("pb"), miss_key="miss_pb", out=out)
        self._fill_field(stock_id, "dividend_yield", val.get("dividend_yield"), miss_key="miss_dividend_yield", out=out)
        self._fill_field(
            stock_id,
            "main_net_5d",
            self.fund_flow_5d.get((stock_id, as_of)),
            miss_key="miss_main_net_5d",
            out=out,
        )
        mchg = self._margin_chg(stock_id, as_of, 20)
        self._fill_field(stock_id, "margin_chg_20", mchg, miss_key="miss_margin_chg_20", out=out)

        for mk, mv in (
            ("macro_pmi", macro["pmi"]),
            ("macro_bond_10y", macro["bond_10y"]),
            ("macro_usd_cnh", macro["usd_cnh"]),
        ):
            field = mk.replace("macro_", "")
            if field == "pmi":
                out[mk] = float(mv) if is_valid(mv) else 50.0
            elif is_valid(mv):
                lo, hi = WINSOR_BOUNDS.get(f"macro_{field}", (-1e9, 1e9))
                out[mk] = winsorize(float(mv), lo, hi)
            else:
                out[mk] = 0.0
        return out


def _slice_bars(bars: list[QuoteBar], i: int) -> dict[str, list[float]]:
    seg = bars[: i + 1]
    return {
        "closes": [b[1] for b in seg],
        "vols": [b[2] for b in seg],
        "highs": [b[3] for b in seg],
        "lows": [b[4] for b in seg],
        "turnovers": [_sf(b[5]) for b in seg],
        "amounts": [_sf(b[6]) for b in seg],
    }


def compute_base_features(
    bars: list[QuoteBar],
    i: int,
    horizon: int,
    stock_id: int,
    ctx: MlFeatureContext,
) -> dict[str, float]:
    """计算单样本特征（不含横截面 rank）。"""
    s = _slice_bars(bars, i)
    c, v, h, l, t, a = s["closes"], s["vols"], s["highs"], s["lows"], s["turnovers"], s["amounts"]
    dt = bars[i][0]
    aux = ctx._aux(stock_id, dt)
    out: dict[str, float] = {}

    if horizon == 5:
        out["pv_corr_5"] = _rolling_corr(c, v, 5)
        out["reversal_5"] = -_pct_ret(c, 5) * 100
        t5 = [x for x in t[-5:] if x > 0]
        out["turnover_mean_5"] = sum(t5) / len(t5) if t5 else 0.0
        if len(t5) >= 2:
            m = out["turnover_mean_5"]
            out["turnover_std_5"] = math.sqrt(sum((x - m) ** 2 for x in t5) / len(t5))
        else:
            out["turnover_std_5"] = 0.0
        out["main_net_5d"] = aux["main_net_5d"]
        out["miss_main_net_5d"] = aux["miss_main_net_5d"]
        out["vol_5"] = _volatility(c, 5) * 100
        out["rsi_14"] = _rsi(c, 14)
        out["amihud_5"] = _amihud(c, a, 5)
        out["mom_5"] = _pct_ret(c, 5) * 100
        out["turnover_mean_5_raw"] = out["turnover_mean_5"]

    elif horizon == 20:
        mom20 = _pct_ret(c, 20)
        mom5 = _pct_ret(c, 5)
        out["mom_20_skip5"] = (mom20 - mom5) * 100
        c20 = c[-20:] if len(c) >= 20 else c
        ma20 = sum(c20) / len(c20)
        c25 = c[-25:] if len(c) >= 25 else c
        ma20_prev = sum(c25[:20]) / min(20, len(c25))
        out["ma20_slope"] = (ma20 - ma20_prev) / ma20_prev * 100 if ma20_prev else 0.0
        out["ma20_dev"] = (c[-1] / ma20 - 1) * 100 if ma20 else 0.0
        out["vol_20"] = _volatility(c, 20) * 100
        t20 = [x for x in t[-20:] if x > 0]
        t5 = [x for x in t[-5:] if x > 0]
        out["turnover_mean_20"] = sum(t20) / len(t20) if t20 else 0.0
        m5 = sum(t5) / len(t5) if t5 else 0.0
        out["turnover_chg_20"] = (m5 / out["turnover_mean_20"] - 1) * 100 if out["turnover_mean_20"] else 0.0
        out["pv_corr_20"] = _rolling_corr(c, v, 20)
        out["macd_hist"] = _macd_hist(c)
        out["margin_chg_20"] = aux["margin_chg_20"]
        out["miss_margin_chg_20"] = aux["miss_margin_chg_20"]
        out["revenue_yoy_q"] = aux["revenue_yoy_q"]
        out["miss_revenue_yoy_q"] = aux["miss_revenue_yoy_q"]
        out["cfo_np"] = aux["cfo_np"]
        out["miss_cfo_np"] = aux["miss_cfo_np"]
        out["eps_revision_3m"] = aux["eps_revision_3m"]
        out["miss_eps_revision_3m"] = aux["miss_eps_revision_3m"]
        out["pe_ttm"] = aux["pe_ttm"]
        out["miss_pe_ttm"] = aux["miss_pe_ttm"]
        out["amp_std_20"] = _amp_std(h, l, c, 20) * 100
        out["mom_20"] = _pct_ret(c, 20) * 100
        out["industry_eps_rev"] = aux["industry_eps_rev"]
        out["miss_industry_eps_rev"] = aux["miss_industry_eps_rev"]
        out["illiq_20"] = _amihud(c, a, 20)
        # v3 行业中性用:携带行业 + 原始 quality_tier(revenue_yoy_q/cfo_np 已在 out 上)
        out["_industry"] = ctx.stock_industry.get(stock_id, "")
        _qt = ctx.v5_metrics.get((stock_id, dt), {}).get("quality_tier")
        out["_quality_tier"] = _sf(_qt) if is_valid(_qt) else float("nan")

    elif horizon == 60:
        out["pe_ttm"] = aux["pe_ttm"]
        out["miss_pe_ttm"] = aux["miss_pe_ttm"]
        out["pb"] = aux["pb"]
        out["miss_pb"] = aux["miss_pb"]
        out["dividend_yield"] = aux["dividend_yield"]
        out["revenue_yoy_q"] = aux["revenue_yoy_q"]
        out["miss_revenue_yoy_q"] = aux["miss_revenue_yoy_q"]
        out["cfo_np"] = aux["cfo_np"]
        out["miss_cfo_np"] = aux["miss_cfo_np"]
        out["debt_ratio"] = aux["debt_ratio"]
        out["miss_debt_ratio"] = aux["miss_debt_ratio"]
        out["eps_revision_3m"] = aux["eps_revision_3m"]
        out["miss_eps_revision_3m"] = aux["miss_eps_revision_3m"]
        out["industry_eps_rev"] = aux["industry_eps_rev"]
        out["roe_proxy"] = aux["roe_proxy"]
        mom12 = _pct_ret(c, min(250, len(c) - 1))
        mom1 = _pct_ret(c, min(20, len(c) - 1))
        out["mom_12m_skip1m"] = (mom12 - mom1) * 100
        out["vol_60"] = _volatility(c, 60) * 100
        t60 = [x for x in t[-60:] if x > 0]
        out["turnover_mean_60"] = sum(t60) / len(t60) if t60 else 0.0
        out["macro_pmi"] = aux["macro_pmi"]
        out["macro_bond_10y"] = aux["macro_bond_10y"]
        out["macro_usd_cnh"] = aux["macro_usd_cnh"]
        rets60 = []
        start = max(1, len(c) - 60)
        for j in range(start, len(c)):
            if c[j - 1] > 0:
                rets60.append(c[j] / c[j - 1] - 1)
        out["_rets60"] = rets60

    return out


def _industry_percentile(rows: list[dict], raw_key: str, out_key: str) -> None:
    """就地写入行业内分位 [0,1]:同行业中 <= 本值的占比;缺失(NaN)/无行业→0.5 中性。"""
    groups: dict[str, list[int]] = defaultdict(list)
    for idx, r in enumerate(rows):
        v = r.get(raw_key)
        if v is not None and isinstance(v, (int, float)) and math.isfinite(v):
            groups[str(r.get("_industry", ""))].append(idx)
    ranks = {}
    for _ind, idxs in groups.items():
        vals = [rows[j][raw_key] for j in idxs]
        n = len(vals)
        for j in idxs:
            vj = rows[j][raw_key]
            ranks[j] = (sum(1 for x in vals if x <= vj) / n) if n else 0.5
    for idx, r in enumerate(rows):
        r[out_key] = ranks.get(idx, 0.5)


def apply_cross_section_ranks(
    rows: list[dict[str, float]],
    horizon: int,
    variant: str = "v2",
) -> None:
    """就地补充横截面 rank 特征。"""
    if not rows:
        return
    if variant == "v3" and horizon == 20:
        _industry_percentile(rows, "revenue_yoy_q", "ind_rank_revenue_yoy_q")
        _industry_percentile(rows, "cfo_np", "ind_rank_cfo_np")
        _industry_percentile(rows, "_quality_tier", "ind_rank_quality")
    if horizon == 5:
        moms = [r.get("mom_5", 0.0) for r in rows]
        turns = [r.get("turnover_mean_5_raw", 0.0) for r in rows]
        n = len(rows)
        for i, r in enumerate(rows):
            r["mom_5_rank"] = sum(1 for m in moms if m <= moms[i]) / n
            pct = sum(1 for t in turns if t <= turns[i]) / n
            r["turnover_extreme"] = abs(pct - 0.5) * 2
            r.pop("mom_5", None)
            r.pop("turnover_mean_5_raw", None)
    elif horizon == 20:
        moms = [r.get("mom_20", 0.0) for r in rows]
        n = len(rows)
        for i, r in enumerate(rows):
            r["rs_20_rank"] = sum(1 for m in moms if m <= moms[i]) / n
            r.pop("mom_20", None)
    elif horizon == 60:
        n_days = max(len(r.get("_rets60", [])) for r in rows) if rows else 0
        mkt: list[float] = []
        for d in range(n_days):
            vals = [r["_rets60"][d] for r in rows if d < len(r.get("_rets60", []))]
            mkt.append(sum(vals) / len(vals) if vals else 0.0)
        m_mean = sum(mkt) / len(mkt) if mkt else 0.0
        m_var = sum((x - m_mean) ** 2 for x in mkt) / max(len(mkt), 1)
        for r in rows:
            sr = r.get("_rets60", [])
            L = min(len(sr), len(mkt))
            if L >= 10 and m_var > 1e-12:
                s_mean = sum(sr[:L]) / L
                cov = sum((sr[k] - s_mean) * (mkt[k] - m_mean) for k in range(L)) / L
                r["beta_60"] = cov / m_var
            else:
                r["beta_60"] = 0.0
            r.pop("_rets60", None)


def vectorize(feat: dict[str, float], horizon: int, variant: str = "v2") -> list[float]:
    names = feature_names_for(horizon, variant)
    return [_sf(feat.get(n)) for n in names]


def feature_spec_summary() -> dict[int, dict]:
    """文档用：各 horizon 特征清单。"""
    return {
        5: {"count": len(H5_FEATURES), "features": H5_FEATURES, "theme": "资金/情绪/短期反转"},
        20: {"count": len(H20_FEATURES), "features": H20_FEATURES, "theme": "趋势/动量/基本面"},
        60: {"count": len(H60_FEATURES), "features": H60_FEATURES, "theme": "估值/质量/宏观"},
    }
