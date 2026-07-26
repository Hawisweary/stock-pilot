"""Phase 1 数据质量 / 异常检测。

规则引擎 + 统计阈值，输出 data_quality_alerts 表。
设计原则：
- 可解释优先：每个异常都带 flag 名和阈值。
- 不误杀：A 股涨跌停 / 复牌等真实场景只做标记，不轻易 critical。
- 无未来偏差：只使用 trade_date 及之前数据。
"""
from __future__ import annotations

import json
import math
import sqlite3
from collections import defaultdict
from datetime import date, timedelta
from typing import Any, Optional

import config

SEVERITY_THRESHOLDS = {
    "info": 0,
    "warning": 35,
    "critical": 70,
}

MAX_SCORE = 100.0

# 各规则权重
_RULE_WEIGHTS: dict[str, int] = {
    "price_spike": 30,           # 涨跌幅异常
    "price_gap": 25,             # 日内跳空
    "volume_burst": 25,          # 成交量爆量
    "turnover_burst": 20,        # 换手率爆量
    "volume_price_divergence": 20,  # 量价背离
    "pe_extreme": 30,            # PE 极值
    "pe_jump": 25,               # PE 突变
    "pb_extreme": 25,            # PB 极值
    "fund_flow_divergence": 25,  # 资金流向与价格反向
    "fundamental_jump": 25,      # 基本面字段跳变
}


class AnomalyDetector:
    def __init__(
        self,
        conn: sqlite3.Connection,
        trade_date: Optional[str] = None,
        lookback: int = 60,
    ):
        self.conn = conn
        self.trade_date = trade_date or self._latest_trading_date()
        self.lookback = lookback
        # 多取一些历史用于计算均值/标准差
        self.start_date = (
            date.fromisoformat(self.trade_date) - timedelta(days=lookback * 2)
        ).isoformat()
        self.industries: dict[int, str] = {}
        self._quotes: dict[int, list[tuple[str, float, float, float, float, float, float]]] = {}
        self._metrics: dict[tuple[int, str], dict[str, Any]] = {}
        self._valuation: dict[tuple[int, str], dict[str, Any]] = {}
        self._fund_flow: dict[tuple[int, str], float] = {}
        self._industry_returns: dict[str, list[float]] = defaultdict(list)
        self._market_return: float = 0.0

    # ── 数据加载 ───────────────────────────────────────────

    def _latest_trading_date(self) -> str:
        row = self.conn.execute(
            "SELECT MAX(trade_date) FROM stock_daily_quotes WHERE close IS NOT NULL"
        ).fetchone()
        return row[0] if row and row[0] else date.today().isoformat()

    def _load_industries(self) -> dict[int, str]:
        industries: dict[int, str] = {}
        for sid, ind in self.conn.execute(
            "SELECT id, COALESCE(industry_sw2, industry_sw, '') FROM stocks WHERE is_active=1"
        ):
            industries[int(sid)] = str(ind or "")
        return industries

    def _load_all(self) -> None:
        self.industries = self._load_industries()
        self._load_quotes()
        self._load_metrics()
        self._load_valuation()
        self._load_fund_flow()
        self._compute_industry_returns()

    def _load_quotes(self) -> None:
        by_sid: dict[int, list] = defaultdict(list)
        for row in self.conn.execute(
            """SELECT stock_id, trade_date, close, volume, high, low, turnover, amount
               FROM stock_daily_quotes
               WHERE trade_date >= ? AND trade_date <= ? AND close IS NOT NULL
               ORDER BY stock_id, trade_date""",
            (self.start_date, self.trade_date),
        ):
            sid = int(row[0])
            by_sid[sid].append((
                row[1],  # date
                float(row[2]),  # close
                float(row[3] or 0),  # volume
                float(row[4] or 0),  # high
                float(row[5] or 0),  # low
                float(row[6] or 0),  # turnover
                float(row[7] or 0),  # amount
            ))
        self._quotes = dict(by_sid)

    def _load_metrics(self) -> None:
        for row in self.conn.execute(
            """SELECT stock_id, calc_date, revenue_yoy_q, cfo_np, debt_ratio, quality_tier
               FROM stock_v5_metrics
               WHERE calc_date >= ? AND calc_date <= ?""",
            (self.start_date, self.trade_date),
        ):
            self._metrics[(int(row[0]), row[1])] = {
                "revenue_yoy_q": row[2],
                "cfo_np": row[3],
                "debt_ratio": row[4],
                "quality_tier": row[5],
            }

    def _load_valuation(self) -> None:
        for row in self.conn.execute(
            """SELECT stock_id, as_of_date, pe_ttm, pb, dividend_yield
               FROM valuation_snapshots
               WHERE as_of_date >= ? AND as_of_date <= ?""",
            (self.start_date, self.trade_date),
        ):
            self._valuation[(int(row[0]), row[1])] = {
                "pe_ttm": row[2],
                "pb": row[3],
                "dividend_yield": row[4],
            }

    def _load_fund_flow(self) -> None:
        for row in self.conn.execute(
            """SELECT stock_id, trade_date, main_net_5d
               FROM stock_fund_flow_daily
               WHERE trade_date >= ? AND trade_date <= ? AND main_net_5d IS NOT NULL""",
            (self.start_date, self.trade_date),
        ):
            self._fund_flow[(int(row[0]), row[1])] = float(row[2])

    def _compute_industry_returns(self) -> None:
        """计算各行业 trade_date 当日收益率列表，用于横向对比。"""
        returns_by_ind: dict[str, list[float]] = defaultdict(list)
        market_rets: list[float] = []
        for sid, hist in self._quotes.items():
            if len(hist) < 2:
                continue
            today = hist[-1]
            yesterday = hist[-2]
            if yesterday[1] <= 0:
                continue
            ret = today[1] / yesterday[1] - 1
            ind = self.industries.get(sid, "")
            returns_by_ind[ind].append(ret)
            market_rets.append(ret)
        self._industry_returns = dict(returns_by_ind)
        self._market_return = (sum(market_rets) / len(market_rets)) if market_rets else 0.0

    def _active_stocks(self) -> list[int]:
        return list(self.industries.keys())

    # ── 工具函数 ───────────────────────────────────────────

    def _hist(self, stock_id: int) -> list[tuple[str, float, float, float, float, float, float]]:
        return self._quotes.get(stock_id, [])

    def _today(self, stock_id: int) -> Optional[tuple[str, float, float, float, float, float, float]]:
        hist = self._hist(stock_id)
        if not hist:
            return None
        last = hist[-1]
        return last if last[0] == self.trade_date else None

    def _ma(self, values: list[float], n: int) -> float:
        if len(values) < n:
            return sum(values) / len(values) if values else 0.0
        return sum(values[-n:]) / n

    def _std(self, values: list[float], n: int) -> float:
        if len(values) < n:
            return 0.0
        window = values[-n:]
        m = sum(window) / len(window)
        return math.sqrt(sum((x - m) ** 2 for x in window) / len(window))

    def _is_valid(self, v: Any) -> bool:
        if v is None:
            return False
        try:
            f = float(v)
            return math.isfinite(f)
        except (TypeError, ValueError):
            return False

    def _severity(self, score: float) -> str:
        if score >= SEVERITY_THRESHOLDS["critical"]:
            return "critical"
        if score >= SEVERITY_THRESHOLDS["warning"]:
            return "warning"
        return "info"

    def _add_flag(self, flags: list[str], name: str) -> int:
        flags.append(name)
        return _RULE_WEIGHTS.get(name, 0)

    # ── 异常规则 ───────────────────────────────────────────

    def _check_price_spike(self, stock_id: int, flags: list[str]) -> int:
        hist = self._hist(stock_id)
        if len(hist) < 2:
            return 0
        today = hist[-1]
        yesterday = hist[-2]
        if yesterday[1] <= 0:
            return 0
        ret = today[1] / yesterday[1] - 1
        abs_ret = abs(ret)

        # 绝对涨跌幅阈值
        if abs_ret >= 0.15:
            return self._add_flag(flags, "price_spike")

        # 相对行业 3σ
        ind = self.industries.get(stock_id, "")
        ind_rets = self._industry_returns.get(ind, [])
        if len(ind_rets) >= 10:
            ind_mean = sum(ind_rets) / len(ind_rets)
            ind_std = math.sqrt(
                sum((r - ind_mean) ** 2 for r in ind_rets) / len(ind_rets)
            )
            if ind_std > 1e-6 and abs(ret - ind_mean) > 3 * ind_std:
                return self._add_flag(flags, "price_spike")
        return 0

    def _check_price_gap(self, stock_id: int, flags: list[str]) -> int:
        today = self._today(stock_id)
        if today is None or today[1] <= 0:
            return 0
        high, low, close = today[3], today[4], today[1]
        if close <= 0:
            return 0
        gap = (high - low) / close
        if gap >= 0.15:
            return self._add_flag(flags, "price_gap")
        return 0

    def _check_volume_burst(self, stock_id: int, flags: list[str]) -> int:
        hist = self._hist(stock_id)
        if len(hist) < 21:
            return 0
        today = hist[-1]
        volumes = [h[2] for h in hist[:-1]]
        ma20 = self._ma(volumes, 20)
        if ma20 <= 0 or today[2] <= 0:
            return 0
        if today[2] / ma20 >= 10:
            return self._add_flag(flags, "volume_burst")
        return 0

    def _check_turnover_burst(self, stock_id: int, flags: list[str]) -> int:
        hist = self._hist(stock_id)
        if len(hist) < 21:
            return 0
        today = hist[-1]
        turnovers = [h[5] for h in hist[:-1] if h[5] > 0]
        if len(turnovers) < 20:
            return 0
        ma20 = self._ma(turnovers, 20)
        if ma20 <= 0 or today[5] <= 0:
            return 0
        if today[5] / ma20 >= 5:
            return self._add_flag(flags, "turnover_burst")
        return 0

    def _check_volume_price_divergence(self, stock_id: int, flags: list[str]) -> int:
        hist = self._hist(stock_id)
        if len(hist) < 6:
            return 0
        today = hist[-1]
        amounts = [h[6] for h in hist[-6:-1] if h[6] > 0]
        if len(amounts) < 5:
            return 0
        ma5 = sum(amounts) / len(amounts)
        if today[1] > hist[-2][1] and today[6] > 0 and ma5 > 0:
            if today[6] / ma5 <= 0.5:
                return self._add_flag(flags, "volume_price_divergence")
        return 0

    def _check_pe_anomaly(self, stock_id: int, flags: list[str]) -> int:
        val = self._valuation.get((stock_id, self.trade_date), {})
        pe = val.get("pe_ttm")
        if not self._is_valid(pe):
            return 0
        pe = float(pe)
        score = 0
        if pe < -500 or pe > 500:
            score += self._add_flag(flags, "pe_extreme")

        # PE 突变：比较最近可用的前一天
        prev_pe = None
        for offset in range(1, 10):
            d = (date.fromisoformat(self.trade_date) - timedelta(days=offset)).isoformat()
            pv = self._valuation.get((stock_id, d), {}).get("pe_ttm")
            if self._is_valid(pv):
                prev_pe = float(pv)
                break
        if prev_pe is not None and abs(prev_pe) > 1e-6:
            if abs(pe - prev_pe) / abs(prev_pe) >= 0.5:
                score += self._add_flag(flags, "pe_jump")
        return score

    def _check_pb_anomaly(self, stock_id: int, flags: list[str]) -> int:
        val = self._valuation.get((stock_id, self.trade_date), {})
        pb = val.get("pb")
        if not self._is_valid(pb):
            return 0
        pb = float(pb)
        if pb < 0 or pb > 30:
            return self._add_flag(flags, "pb_extreme")
        return 0

    def _check_fund_flow_divergence(self, stock_id: int, flags: list[str]) -> int:
        hist = self._hist(stock_id)
        if len(hist) < 2:
            return 0
        today = hist[-1]
        yesterday = hist[-2]
        if yesterday[1] <= 0:
            return 0
        ret = today[1] / yesterday[1] - 1
        net5 = self._fund_flow.get((stock_id, self.trade_date))
        amount = today[6]
        if not self._is_valid(net5) or amount <= 0:
            return 0
        net_pct = float(net5) / amount
        # 主力资金大幅净流入但股价大跌
        if net_pct >= 0.05 and ret <= -0.03:
            return self._add_flag(flags, "fund_flow_divergence")
        # 主力资金大幅净流出但股价大涨
        if net_pct <= -0.05 and ret >= 0.03:
            return self._add_flag(flags, "fund_flow_divergence")
        return 0

    def _check_fundamental_jump(self, stock_id: int, flags: list[str]) -> int:
        today = self._metrics.get((stock_id, self.trade_date))
        if not today:
            return 0

        # 找上一个可用报告期（同一股往前 30 天内）
        prev = None
        for offset in range(1, 30):
            d = (date.fromisoformat(self.trade_date) - timedelta(days=offset)).isoformat()
            p = self._metrics.get((stock_id, d))
            if p:
                prev = p
                break
        if not prev:
            return 0

        score = 0
        for field, threshold in [("revenue_yoy_q", 0.5), ("cfo_np", 2.0), ("debt_ratio", 20.0)]:
            cur = today.get(field)
            pre = prev.get(field)
            if not self._is_valid(cur) or not self._is_valid(pre):
                continue
            cur_f, pre_f = float(cur), float(pre)
            if abs(pre_f) < 1e-6:
                if abs(cur_f) > threshold:
                    score += self._add_flag(flags, "fundamental_jump")
            else:
                if abs(cur_f - pre_f) / abs(pre_f) > threshold:
                    score += self._add_flag(flags, "fundamental_jump")
        return score

    # ── 主入口 ───────────────────────────────────────────

    def detect(self) -> list[dict]:
        self._load_all()
        results = []
        for stock_id in self._active_stocks():
            flags: list[str] = []
            score = 0
            score += self._check_price_spike(stock_id, flags)
            score += self._check_price_gap(stock_id, flags)
            score += self._check_volume_burst(stock_id, flags)
            score += self._check_turnover_burst(stock_id, flags)
            score += self._check_volume_price_divergence(stock_id, flags)
            score += self._check_pe_anomaly(stock_id, flags)
            score += self._check_pb_anomaly(stock_id, flags)
            score += self._check_fund_flow_divergence(stock_id, flags)
            score += self._check_fundamental_jump(stock_id, flags)

            if score > 0:
                results.append({
                    "stock_id": stock_id,
                    "trade_date": self.trade_date,
                    "anomaly_score": min(MAX_SCORE, score),
                    "flags": json.dumps(flags, ensure_ascii=False),
                    "severity": self._severity(score),
                })
        return results


def write_alerts(conn: sqlite3.Connection, alerts: list[dict]) -> int:
    """写入 data_quality_alerts；INSERT OR REPLACE 保证幂等。"""
    if not alerts:
        return 0
    conn.executemany(
        """INSERT OR REPLACE INTO data_quality_alerts
           (stock_id, trade_date, anomaly_score, flags, severity, isolation_score)
           VALUES (?, ?, ?, ?, ?, NULL)""",
        [
            (a["stock_id"], a["trade_date"], a["anomaly_score"], a["flags"], a["severity"])
            for a in alerts
        ],
    )
    conn.commit()
    return len(alerts)


def detect_and_write(
    conn: sqlite3.Connection,
    trade_date: Optional[str] = None,
    lookback: int = 60,
) -> dict:
    """一站式：检测 + 写入 + 返回摘要。"""
    detector = AnomalyDetector(conn, trade_date=trade_date, lookback=lookback)
    alerts = detector.detect()
    written = write_alerts(conn, alerts)
    summary = {
        "trade_date": detector.trade_date,
        "total_alerts": written,
        "critical": sum(1 for a in alerts if a["severity"] == "critical"),
        "warning": sum(1 for a in alerts if a["severity"] == "warning"),
        "info": sum(1 for a in alerts if a["severity"] == "info"),
    }
    return summary


def get_alerts_for_stock(
    conn: sqlite3.Connection,
    stock_id: int,
    limit: int = 30,
) -> list[dict]:
    rows = conn.execute(
        """SELECT trade_date, anomaly_score, flags, severity, created_at
           FROM data_quality_alerts
           WHERE stock_id=?
           ORDER BY trade_date DESC LIMIT ?""",
        (stock_id, limit),
    ).fetchall()
    return [
        {
            "trade_date": r[0],
            "anomaly_score": r[1],
            "flags": json.loads(r[2]) if r[2] else [],
            "severity": r[3],
            "created_at": r[4],
        }
        for r in rows
    ]


def get_summary_for_date(
    conn: sqlite3.Connection,
    trade_date: Optional[str] = None,
) -> dict:
    if trade_date is None:
        row = conn.execute(
            "SELECT MAX(trade_date) FROM data_quality_alerts"
        ).fetchone()
        trade_date = row[0] if row and row[0] else date.today().isoformat()
    total = conn.execute(
        "SELECT COUNT(*) FROM data_quality_alerts WHERE trade_date=?",
        (trade_date,),
    ).fetchone()[0]
    by_severity = {}
    for sev, cnt in conn.execute(
        "SELECT severity, COUNT(*) FROM data_quality_alerts WHERE trade_date=? GROUP BY severity",
        (trade_date,),
    ):
        by_severity[sev] = cnt
    top = [
        {
            "stock_id": r[0],
            "anomaly_score": r[1],
            "severity": r[2],
            "flags": json.loads(r[3]) if r[3] else [],
        }
        for r in conn.execute(
            """SELECT stock_id, anomaly_score, severity, flags
               FROM data_quality_alerts
               WHERE trade_date=?
               ORDER BY anomaly_score DESC LIMIT 20""",
            (trade_date,),
        ).fetchall()
    ]
    return {
        "trade_date": trade_date,
        "total_alerts": total,
        "by_severity": by_severity,
        "top_alerts": top,
    }
