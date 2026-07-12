"""
趋势变化检测 v2.0 — 15+ 规则引擎
Quant Layer: 发现异常 → AI Layer: 解释原因
"""
def detect_changes_rules(periods: list[dict]) -> list[dict]:
    alerts = []
    if len(periods) < 2: return alerts

    rev_yoy = _safe_list(periods[:8], "revenue_yoy")
    profit_yoy = _safe_list(periods[:8], "profit_yoy")
    margin_qoq = _safe_list(periods[:8], "margin_qoq")
    revenues = _safe_list(periods, "revenue")
    profits = _safe_list(periods, "net_profit")
    ocf = _safe_list(periods, "operating_cf")
    assets = _safe_list(periods, "total_assets")
    liabilities = _safe_list(periods, "total_liabilities")
    equity = _safe_list(periods, "total_equity")

    # ── REVENUE (3 rules) ──
    _revenue_rules(alerts, rev_yoy, revenues)
    # ── PROFIT (3 rules) ──
    _profit_rules(alerts, profit_yoy, profits)
    # ── MARGIN (2 rules) ──
    _margin_rules(alerts, margin_qoq, revenues, profits)
    # ── CASH FLOW (3 rules) ──
    _cashflow_rules(alerts, ocf, profits)
    # ── BALANCE SHEET (3 rules) ──
    _balance_rules(alerts, liabilities, assets, equity)
    # ── EARNING QUALITY (2 rules) ──
    _quality_rules(alerts, ocf, profits, revenues)

    return alerts[:6]


# ═══════ sub-rules ═══════

def _revenue_rules(alerts, yoy, rev):
    if len(yoy) >= 2 and all(c is not None and c < 0 for c in yoy[:2]):
        alerts.append({"type": "warning", "title": "营收同比连续下滑",
                       "detail": f"近2期营收YoY: {yoy[0]:.1f}%, {yoy[1]:.1f}%", "severity": "high"})
    elif len(yoy) >= 2 and all(c and c > 0 for c in yoy[:2]):
        alerts.append({"type": "positive", "title": "营收同比持续增长",
                       "detail": f"近2期营收YoY: {yoy[0]:.1f}%, {yoy[1]:.1f}%", "severity": "low"})
    if len(yoy) >= 4 and all(c is not None for c in yoy[:4]) \
       and yoy[0] < yoy[1] < yoy[2] < yoy[3]:
        alerts.append({"type": "warning", "title": "营收增速连续放缓",
                       "detail": f"近4期YoY递减: {yoy[3]:.1f}%→{yoy[0]:.1f}%", "severity": "medium"})


def _profit_rules(alerts, yoy, profits):
    if len(yoy) >= 2 and all(c is not None and c < 0 for c in yoy[:2]):
        alerts.append({"type": "warning", "title": "利润同比连续下滑",
                       "detail": f"近2期净利润YoY: {yoy[0]:.1f}%, {yoy[1]:.1f}%", "severity": "high"})
    if len(profits) >= 3 and all(p and p > 0 for p in profits[:3]):
        if profits[0] < profits[1] < profits[2]:
            alerts.append({"type": "positive", "title": "净利润逐季增长",
                           "detail": "近3期净利润持续上升", "severity": "low"})
    if len(profits) >= 2 and profits[0] and profits[1] and profits[0] > 0 and profits[1] < 0:
        alerts.append({"type": "warning", "title": "利润由盈转亏",
                       "detail": "最新一期净利润转负", "severity": "high"})


def _margin_rules(alerts, qoq, revenues, profits):
    if len(qoq) >= 3 and all(c is not None and c < 0 for c in qoq[:3]):
        alerts.append({"type": "warning", "title": "毛利率连续压缩",
                       "detail": f"近3期环比分别下降: {'/'.join(f'{m:.1f}%' for m in qoq[:3])}", "severity": "high"})
    if len(revenues) >= 4 and len(profits) >= 4 and all(v and v > 0 for v in revenues[:4]+profits[:4]):
        margins = [(profits[i]/revenues[i]*100) for i in range(min(4, len(profits)))]
        if len(margins) >= 3 and margins[0] < margins[1] < margins[2]:
            alerts.append({"type": "positive", "title": "净利率持续改善",
                           "detail": f"净利率: {margins[2]:.1f}%→{margins[0]:.1f}%", "severity": "low"})


def _cashflow_rules(alerts, ocf, profits):
    if len(ocf) >= 3 and all(c is not None and c < 0 for c in ocf[:3]):
        alerts.append({"type": "warning", "title": "经营现金流持续为负",
                       "detail": "连续3期经营现金流为负，流动性风险", "severity": "high"})
    if len(ocf) >= 3 and len(profits) >= 3 and all(p and p > 0 for p in profits[:3]):
        ocf_sum = sum(c for c in ocf[:3] if c)
        profit_sum = sum(p for p in profits[:3] if p)
        if profit_sum > 0 and ocf_sum / profit_sum < 0.5:
            alerts.append({"type": "warning", "title": "利润含金量不足",
                           "detail": f"近3期经营现金流/净利润={ocf_sum/profit_sum:.2f}<0.5", "severity": "medium"})
    if len(ocf) >= 2 and all(c is not None for c in ocf[:2]) and ocf[0] < 0 and ocf[1] > 0:
        alerts.append({"type": "warning", "title": "经营现金流转负",
                       "detail": "最新一期经营现金流转为负值", "severity": "medium"})


def _balance_rules(alerts, liab, assets, equity):
    if len(assets) >= 3 and len(liab) >= 3 and all(a and a > 0 for a in assets[:3]):
        d_ratios = [liab[i]/assets[i]*100 for i in range(min(3, len(liab))) if liab[i] and assets[i]]
        if len(d_ratios) >= 3 and d_ratios[0] > d_ratios[1] > d_ratios[2]:
            alerts.append({"type": "warning", "title": "负债率持续上升",
                           "detail": f"负债率: {d_ratios[2]:.1f}%→{d_ratios[0]:.1f}%", "severity": "medium"})
    if len(equity) >= 3 and all(e and e != 0 for e in equity[:3]):
        if equity[0] < equity[1] < equity[2]:
            alerts.append({"type": "positive", "title": "净资产持续增长",
                           "detail": f"近3期净资产稳步上升", "severity": "low"})
    if len(assets) >= 3 and len(liab) >= 3:
        if all(a and l and l/a > 0.7 for a,l in zip(assets[:3], liab[:3])):
            alerts.append({"type": "warning", "title": "高杠杆运行",
                           "detail": "近3期资产负债率持续>70%", "severity": "medium"})


def _quality_rules(alerts, ocf, profits, revenues):
    if len(ocf) >= 2 and len(revenues) >= 2 and all(r and r>0 for r in revenues[:2]):
        ocf_growth = [(ocf[i]-ocf[i+1])/abs(ocf[i+1])*100 for i in range(min(1, len(ocf)-1)) if ocf[i+1] and ocf[i+1]!=0]
        rev_growth = [(revenues[i]-revenues[i+1])/abs(revenues[i+1])*100 for i in range(min(1, len(revenues)-1)) if revenues[i+1] and revenues[i+1]!=0]
        if ocf_growth and rev_growth and ocf_growth[0] < 0 and rev_growth[0] > 0:
            alerts.append({"type": "warning", "title": "增收不增现",
                           "detail": "营收增长但经营现金流下降", "severity": "medium"})
    if len(profits) >= 5:
        profit_vol = _volatility(profits[:5])
        if profit_vol > 0.5:
            alerts.append({"type": "warning", "title": "盈利波动较大",
                           "detail": f"近5期净利润变异系数={profit_vol:.2f}", "severity": "medium"})


def _safe_list(periods, key) -> list:
    return [p.get(key) for p in periods if p.get(key) is not None]


def _volatility(values) -> float:
    clean = [v for v in values if v and v != 0]
    if len(clean) < 2: return 0
    avg = sum(clean) / len(clean)
    std = (sum((v-avg)**2 for v in clean) / len(clean)) ** 0.5
    return std / abs(avg) if avg else 0
