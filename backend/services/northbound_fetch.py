"""北向资金拉取 — 多数据源与历史回退"""
from __future__ import annotations

import sqlite3
from typing import Any

from config import DB_PATH

# 亿元 → 元（与前端 formatMoney 一致）
_YI_TO_YUAN = 100_000_000


def _yi_to_yuan(v: float | None) -> float:
    if v is None:
        return 0.0
    try:
        f = float(v)
        if f != f:
            return 0.0
        return round(f * _YI_TO_YUAN, 2)
    except (TypeError, ValueError):
        return 0.0


def _from_summary_em() -> dict[str, Any] | None:
    """东方财富当日汇总（交易时段可能有数，披露调整后常为 0）"""
    import akshare as ak

    df = ak.stock_hsgt_fund_flow_summary_em()
    if df is None or df.empty:
        return None

    north = df[df["资金方向"] == "北向"]
    if north.empty:
        return None

    date = str(north.iloc[0]["交易日"])[:10]
    sh_row = north[north["板块"] == "沪股通"]
    sz_row = north[north["板块"] == "深股通"]
    sh_yi = float(sh_row.iloc[0]["成交净买额"]) if not sh_row.empty else 0.0
    sz_yi = float(sz_row.iloc[0]["成交净买额"]) if not sz_row.empty else 0.0
    net_yi = sh_yi + sz_yi

    if net_yi == 0.0 and sh_yi == 0.0 and sz_yi == 0.0:
        return None

    return {
        "date": date,
        "net_inflow": _yi_to_yuan(net_yi),
        "sh_inflow": _yi_to_yuan(sh_yi),
        "sz_inflow": _yi_to_yuan(sz_yi),
        "source": "eastmoney_summary",
        "data_status": "live",
        "note": "",
    }


def _from_hist_em() -> dict[str, Any] | None:
    """AKShare 历史序列中最近一条有效净买额（亿元）"""
    import akshare as ak

    def _last_yi(symbol: str) -> tuple[str, float] | None:
        df = ak.stock_hsgt_hist_em(symbol=symbol)
        if df is None or df.empty or "当日成交净买额" not in df.columns:
            return None
        sub = df[df["当日成交净买额"].notna()]
        if sub.empty:
            return None
        row = sub.iloc[-1]
        d = str(row["日期"])[:10]
        return d, float(row["当日成交净买额"])

    sh = _last_yi("沪股通")
    sz = _last_yi("深股通")
    total = _last_yi("北向资金")
    if not sh and not sz and not total:
        return None

    date = (sh or sz or total)[0]
    sh_yi = sh[1] if sh else 0.0
    sz_yi = sz[1] if sz else 0.0
    net_yi = total[1] if total else (sh_yi + sz_yi)

    return {
        "date": date,
        "net_inflow": _yi_to_yuan(net_yi),
        "sh_inflow": _yi_to_yuan(sh_yi),
        "sz_inflow": _yi_to_yuan(sz_yi),
        "source": "akshare_hist",
        "data_status": "stale",
        "note": (
            "交易所自 2024-08-19 起不再披露北向资金实时净买额，"
            f"当前展示最近有效交易日（{date}）历史数据，仅供环境参考。"
        ),
    }


def _from_db() -> dict[str, Any] | None:
    try:
        conn = sqlite3.connect(DB_PATH)
        conn.row_factory = sqlite3.Row
        row = conn.execute(
            "SELECT date, net_flow FROM north_flow_daily WHERE net_flow IS NOT NULL "
            "ORDER BY date DESC LIMIT 1"
        ).fetchone()
        conn.close()
        if not row or row["net_flow"] is None:
            return None
        net = float(row["net_flow"])
        # DB 可能存元或亿：大于 1e6 视为已是元
        if abs(net) < 1e6:
            net = _yi_to_yuan(net)
        return {
            "date": str(row["date"])[:10],
            "net_inflow": net,
            "sh_inflow": 0.0,
            "sz_inflow": 0.0,
            "source": "local_db",
            "data_status": "stale",
            "note": f"来自本地缓存（{row['date']}），沪深拆分不可用。",
        }
    except Exception:
        return None


def fetch_northbound() -> dict[str, Any]:
    """
    北向资金快照。优先当日汇总；若为 0 则回退最近有效历史。
    金额单位：元（与前端展示一致）。
    """
    for loader in (_from_summary_em, _from_hist_em, _from_db):
        try:
            hit = loader()
            if hit:
                hit.setdefault("cumulative", 0.0)
                hit.setdefault("error", "OK")
                return hit
        except Exception:
            continue

    return {
        "date": "",
        "net_inflow": 0.0,
        "cumulative": 0.0,
        "sh_inflow": 0.0,
        "sz_inflow": 0.0,
        "source": "none",
        "data_status": "unavailable",
        "note": "暂无北向资金数据（披露规则调整后，公开渠道可能长期为 0）",
        "error": "NO_DATA",
    }
