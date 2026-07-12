"""季度异动检测引擎 — QoQ 边际变化 + 异常标记"""
from dataclasses import dataclass, field


@dataclass
class QuarterlyAlert:
    quarter: str
    type: str           # revenue_surge / profit_drop / margin_erosion / cash_warning / growth_stall
    severity: str       # red / amber / green
    title: str
    detail: str


def detect_quarterly_alerts(reports: list[dict], is_financial: bool = False) -> list[QuarterlyAlert]:
    """
    reports: 按 period_end_date ASC 排序的季报列表
    is_financial: 银行/保险/券商等金融股 — 跳过现金流告警
    """
    if len(reports) < 3:
        return []

    alerts = []
    q_reports = []
    for r in reports:
        rt = (r.get("report_type") or "").strip().lower()
        if rt in ("q1", "q2", "q3", "annual"):
            q_reports.append(r)
    if len(q_reports) < 3:
        return []

    # 只检测最近 4 季，不去翻历史
    recent = q_reports[-5:]
    for i in range(1, len(recent)):
        cur = recent[i]
        prev = recent[i - 1]
        q_label = cur.get("period_end_date", "?")[:10]

        rev_cur = cur.get("revenue") or 0
        rev_prev = prev.get("revenue") or 0
        np_cur = cur.get("net_profit_parent") or 0
        np_prev = prev.get("net_profit_parent") or 0

        # ── 1. 营收 QoQ ──
        if rev_prev > 0:
            rev_qoq = (rev_cur - rev_prev) / rev_prev
            if rev_qoq > 0.3:
                alerts.append(QuarterlyAlert(q_label, "revenue_surge", "green",
                    "营收高增长", f"QoQ {rev_qoq:.0%}"))
            elif rev_qoq < -0.2:
                alerts.append(QuarterlyAlert(q_label, "revenue_surge", "red",
                    "营收大幅下滑", f"QoQ {rev_qoq:.0%}"))

        # ── 2. 利润 QoQ ──
        if abs(np_prev) > 0:
            np_qoq = (np_cur - np_prev) / abs(np_prev)
            if np_qoq > 0.5 and np_cur > 0:
                alerts.append(QuarterlyAlert(q_label, "profit_surge", "green",
                    "利润大幅增长", f"QoQ {np_qoq:.0%}"))
            elif np_qoq < -0.3 and np_cur > 0:
                alerts.append(QuarterlyAlert(q_label, "profit_drop", "red",
                    "利润大幅下降", f"QoQ {np_qoq:.0%}"))

        # ── 3. 毛利率 ──
        gm_cur = cur.get("gross_profit") / rev_cur if rev_cur and cur.get("gross_profit") else 0
        gm_prev = prev.get("gross_profit") / rev_prev if rev_prev and prev.get("gross_profit") else 0
        if gm_cur and gm_prev and gm_cur < gm_prev - 0.03:
            alerts.append(QuarterlyAlert(q_label, "margin_erosion", "amber",
                "毛利率下降", f"{gm_prev:.1%} → {gm_cur:.1%}"))

        # ── 4. 经营现金流 (跳过金融股) ──
        if not is_financial:
            ocf = cur.get("operating_cf") or 0
            if ocf < 0 and np_cur > 0:
                alerts.append(QuarterlyAlert(q_label, "cash_warning", "amber",
                    "经营现金流为负", f"净利润正但OCF负({ocf/1e8:.1f}亿)"))

    return alerts
