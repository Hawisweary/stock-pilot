"""Tushare Pro 数据适配器 — 行业分类 / 复权行情 / 财务三表 / 财务指标 / 估值快照

替代原先分散在 adata/eastmoney/mootdx/yfinance 的拼凑管道：
- 行业分类: index_classify + index_member_all（标准申万一级，31次调用覆盖全市场）
- 复权行情: pro_bar(adj='qfq')（官方前复权，不用自己算除权除息）
- 财务三表: income / balancesheet / cashflow（含此前缺失的经营性现金流 operating_cf）
- 财务指标: fina_indicator（ROE/ROA/毛利率等，官方口径）
- 估值快照: daily_basic（PE/PB/市值/换手率）
"""
from __future__ import annotations

import threading
import time
from typing import Any

import config

_pro_lock = threading.Lock()
_pro_client = None

# Tushare 标准接口约 200次/分钟的经验频控（5000积分不解除分钟频控，只解除总量上限）
_RATE_LOCK = threading.Lock()
_last_call_ts = 0.0
_MIN_INTERVAL_SEC = 0.35  # ~170次/分钟，留余量避免触发限流


def _throttle() -> None:
    global _last_call_ts
    with _RATE_LOCK:
        now = time.perf_counter()
        wait = _MIN_INTERVAL_SEC - (now - _last_call_ts)
        if wait > 0:
            time.sleep(wait)
        _last_call_ts = time.perf_counter()


def _pro():
    global _pro_client
    if _pro_client is None:
        with _pro_lock:
            if _pro_client is None:
                import tushare as ts

                if not config.TUSHARE_TOKEN:
                    raise RuntimeError("TUSHARE_TOKEN 未配置，请在 .env 里设置")
                ts.set_token(config.TUSHARE_TOKEN)
                _pro_client = ts.pro_api()
    return _pro_client


def code_to_ts_code(code: str, market: str | None = None) -> str:
    """A股代码转 Tushare ts_code 格式（600000 -> 600000.SH）。

    北交所（8/4 开头老三板 + 92/93 开头2023起新股统一编号）-> .BJ，
    与 services/tencent_adapter.py::_get_exchange() 同样的判断逻辑。
    """
    if market == "BJ" or code.startswith(("8", "4", "92", "93")):
        return f"{code}.BJ"
    if code.startswith("6"):
        return f"{code}.SH"
    return f"{code}.SZ"


def latest_trading_date(as_of: str) -> str | None:
    """给定日期（YYYYMMDD）往前找最近一个实际交易日，用于 daily_basic 这类只认交易日的接口。"""
    pro = _pro()
    _throttle()
    df = pro.trade_cal(exchange="SSE", end_date=as_of, is_open="1")
    if df is None or df.empty:
        return None
    return str(df.sort_values("cal_date").iloc[-1]["cal_date"])


def fetch_trade_calendar(start_date: str, end_date: str) -> list[tuple[str, int]]:
    """SSE 交易日历（含法定节假日 + 调休补班），一次调用覆盖整个区间。

    返回 [(cal_date 'YYYY-MM-DD', is_open), ...]，供本地缓存表使用，
    避免每次判断交易日都要拿周末近似（无法识别春节/国庆等长假）。
    """
    pro = _pro()
    _throttle()
    df = pro.trade_cal(exchange="SSE", start_date=start_date, end_date=end_date)
    if df is None or df.empty:
        return []
    return [
        (f"{d[:4]}-{d[4:6]}-{d[6:8]}", int(o))
        for d, o in zip(df["cal_date"].astype(str), df["is_open"])
    ]


def fetch_industry_map() -> dict[str, dict[str, str]]:
    """全市场申万一级/二级/三级行业分类。返回 {code: {l1, l2, l3}}，31 次调用覆盖全市场。

    index_member_all() 返回的行本身就带 l2_name/l3_name，之前只取了 l1_name，
    这次一并取出，不需要额外调用。
    """
    pro = _pro()
    _throttle()
    l1_df = pro.index_classify(level="L1", src="SW2021")

    result: dict[str, dict[str, str]] = {}
    for _, row in l1_df.iterrows():
        l1_code = row["index_code"]
        l1_name = row["industry_name"]
        _throttle()
        members = pro.index_member_all(l1_code=l1_code)
        for _, m in members.iterrows():
            ts_code = m["ts_code"]
            if m.get("out_date"):
                continue  # 已调出该行业
            code = ts_code.split(".")[0]
            result[code] = {
                "l1": l1_name,
                "l2": m.get("l2_name") or "",
                "l3": m.get("l3_name") or "",
            }
    return result


def fetch_daily_adjusted(ts_code: str, start_date: str, end_date: str) -> list[dict[str, Any]]:
    """原始日线 + 复权因子，分别保留 raw OHLC 和算好的前复权 adj_close。

    库里 stock_daily_quotes 表把 close（原始收盘价）和 adj_close（前复权）分开存，
    所以这里不用 pro_bar(adj='qfq')（它会直接改写 OHLC），而是自己用标准前复权
    公式 adj_close = close * (factor / 最新factor) 计算，保证与库内既有数据口径一致。
    """
    pro = _pro()
    _throttle()
    daily = pro.daily(ts_code=ts_code, start_date=start_date, end_date=end_date)
    if daily is None or daily.empty:
        return []
    _throttle()
    factor_df = pro.adj_factor(ts_code=ts_code, start_date=start_date, end_date=end_date)
    factor_map = {
        str(r["trade_date"]): float(r["adj_factor"])
        for _, r in factor_df.iterrows()
    } if factor_df is not None and not factor_df.empty else {}
    latest_factor = max(factor_map.values()) if factor_map else 1.0

    rows = []
    for _, r in daily.iterrows():
        d = str(r["trade_date"])
        o, h, l, close = _f(r.get("open")), _f(r.get("high")), _f(r.get("low")), _f(r.get("close"))
        factor = factor_map.get(d)
        # 前复权系数 = factor / 最新factor;OHLC 同乘,保证 K 线内部一致
        k = (factor / latest_factor) if factor else None
        def _adj(v):
            return round(v * k, 4) if (v is not None and k) else v
        rows.append({
            "trade_date": f"{d[:4]}-{d[4:6]}-{d[6:8]}",
            "open": o,
            "high": h,
            "low": l,
            "close": close,
            "adj_open": _adj(o),
            "adj_high": _adj(h),
            "adj_low": _adj(l),
            "adj_close": _adj(close),
            "volume": _f(r.get("vol")) * 100 if r.get("vol") is not None else None,  # 手->股
            "amount": _f(r.get("amount")) * 1000 if r.get("amount") is not None else None,  # 千元->元
            "change_pct": _f(r.get("pct_chg")),
        })
    return rows


def fetch_financial_reports(ts_code: str, start_date: str, end_date: str) -> list[dict[str, Any]]:
    """财务三表合并（利润表 + 资产负债表 + 现金流量表），按 end_date 对齐。"""
    pro = _pro()

    _throttle()
    income = pro.income(
        ts_code=ts_code, start_date=start_date, end_date=end_date,
        fields="ts_code,end_date,ann_date,report_type,revenue,operate_profit,n_income,n_income_attr_p",
    )
    _throttle()
    balance = pro.balancesheet(
        ts_code=ts_code, start_date=start_date, end_date=end_date,
        fields="ts_code,end_date,total_assets,total_liab,total_hldr_eqy_exc_min_int,total_cur_assets,total_cur_liab",
    )
    _throttle()
    cashflow = pro.cashflow(
        ts_code=ts_code, start_date=start_date, end_date=end_date,
        fields="ts_code,end_date,n_cashflow_act,n_cashflow_inv_act,n_cash_flows_fnc_act",
    )

    by_date: dict[str, dict[str, Any]] = {}
    for _, r in income.iterrows():
        d = str(r["end_date"])
        by_date.setdefault(d, {})["revenue"] = _f(r.get("revenue"))
        by_date[d]["operating_profit"] = _f(r.get("operate_profit"))
        by_date[d]["net_profit"] = _f(r.get("n_income"))
        by_date[d]["net_profit_parent"] = _f(r.get("n_income_attr_p"))
        by_date[d]["report_date"] = _fmt_date(r.get("ann_date")) or _fmt_date(d)
    for _, r in balance.iterrows():
        d = str(r["end_date"])
        by_date.setdefault(d, {})["total_assets"] = _f(r.get("total_assets"))
        by_date[d]["total_liabilities"] = _f(r.get("total_liab"))
        by_date[d]["total_equity"] = _f(r.get("total_hldr_eqy_exc_min_int"))
        by_date[d]["current_assets"] = _f(r.get("total_cur_assets"))
        by_date[d]["current_liabilities"] = _f(r.get("total_cur_liab"))
    for _, r in cashflow.iterrows():
        d = str(r["end_date"])
        by_date.setdefault(d, {})["operating_cf"] = _f(r.get("n_cashflow_act"))
        by_date[d]["investing_cf"] = _f(r.get("n_cashflow_inv_act"))
        by_date[d]["financing_cf"] = _f(r.get("n_cash_flows_fnc_act"))

    rows = []
    for d, fields in sorted(by_date.items()):
        period_end = _fmt_date(d)
        month = d[4:6] if len(d) >= 6 else "12"
        rows.append({
            "period_end_date": period_end,
            "report_date": fields.get("report_date") or period_end,
            "report_type": "annual" if month == "12" else "quarterly",
            **{k: v for k, v in fields.items() if k not in ("report_date",)},
        })
    return rows


def fetch_fina_indicator(ts_code: str, start_date: str, end_date: str) -> list[dict[str, Any]]:
    """财务指标（ROE/ROA/毛利率/净利率/资产负债率）。"""
    pro = _pro()
    _throttle()
    df = pro.fina_indicator(
        ts_code=ts_code, start_date=start_date, end_date=end_date,
        fields="ts_code,end_date,roe,roa,grossprofit_margin,netprofit_margin,debt_to_assets,current_ratio",
    )
    rows = []
    for _, r in df.iterrows():
        rows.append({
            "calc_date": _fmt_date(r["end_date"]),
            "roe": _f(r.get("roe")),
            "roa": _f(r.get("roa")),
            "gross_margin": _f(r.get("grossprofit_margin")),
            "net_margin": _f(r.get("netprofit_margin")),
            "debt_to_equity": _f(r.get("debt_to_assets")),
            "current_ratio": _f(r.get("current_ratio")),
        })
    return rows


_DAILY_BASIC_FIELDS = (
    "ts_code,trade_date,pe,pe_ttm,pb,ps,ps_ttm,total_mv,circ_mv,"
    "turnover_rate,turnover_rate_f,volume_ratio,dv_ratio,dv_ttm,"
    "total_share,float_share,free_share,limit_status"
)


def _parse_daily_basic_row(r: Any) -> dict[str, Any]:
    limit_status = r.get("limit_status")
    return {
        "pe": _f(r.get("pe")),
        "pe_ttm": _f(r.get("pe_ttm")),
        "pb": _f(r.get("pb")),
        "ps": _f(r.get("ps")),
        "ps_ttm": _f(r.get("ps_ttm")),
        "market_cap": _f(r.get("total_mv")) / 10000 if r.get("total_mv") is not None else None,  # 万元->亿元
        "circ_market_cap": _f(r.get("circ_mv")) / 10000 if r.get("circ_mv") is not None else None,
        "turnover_rate": _f(r.get("turnover_rate")),
        "turnover_rate_f": _f(r.get("turnover_rate_f")),
        "volume_ratio": _f(r.get("volume_ratio")),
        "dividend_yield": _f(r.get("dv_ratio")),
        "dividend_yield_ttm": _f(r.get("dv_ttm")),
        "total_share": _f(r.get("total_share")),
        "float_share": _f(r.get("float_share")),
        "free_share": _f(r.get("free_share")),
        "limit_status": int(limit_status) if limit_status is not None else None,
    }


def fetch_daily_basic(ts_code: str, trade_date: str) -> dict[str, Any] | None:
    """估值快照（PE/PB/市值/换手率/量比/股本等），trade_date 格式 YYYYMMDD。"""
    pro = _pro()
    _throttle()
    df = pro.daily_basic(ts_code=ts_code, trade_date=trade_date, fields=_DAILY_BASIC_FIELDS)
    if df is None or df.empty:
        return None
    return _parse_daily_basic_row(df.iloc[0])


def fetch_market_adj_factor(trade_date: str) -> dict[str, float]:
    """某交易日全市场复权因子。返回 {ts_code: factor}。"""
    pro = _pro()
    _throttle()
    df = pro.adj_factor(trade_date=trade_date)
    if df is None or df.empty:
        return {}
    return {r["ts_code"]: float(r["adj_factor"]) for _, r in df.iterrows()}


def fetch_market_daily(trade_date: str) -> dict[str, dict[str, Any]]:
    """某交易日全市场原始日线。返回 {ts_code: {open,high,low,close,volume,amount,change_pct}}。"""
    pro = _pro()
    _throttle()
    df = pro.daily(trade_date=trade_date)
    if df is None or df.empty:
        return {}
    result = {}
    for _, r in df.iterrows():
        result[r["ts_code"]] = {
            "open": _f(r.get("open")),
            "high": _f(r.get("high")),
            "low": _f(r.get("low")),
            "close": _f(r.get("close")),
            "volume": _f(r.get("vol")) * 100 if r.get("vol") is not None else None,
            "amount": _f(r.get("amount")) * 1000 if r.get("amount") is not None else None,
            "change_pct": _f(r.get("pct_chg")),
        }
    return result


def fetch_market_daily_basic(trade_date: str) -> dict[str, dict[str, Any]]:
    """某交易日全市场估值快照（PE/PB/市值/换手率/量比/股本/涨跌停状态等）。返回 {ts_code: {...}}。"""
    pro = _pro()
    _throttle()
    df = pro.daily_basic(trade_date=trade_date, fields=_DAILY_BASIC_FIELDS)
    if df is None or df.empty:
        return {}
    return {r["ts_code"]: _parse_daily_basic_row(r) for _, r in df.iterrows()}


_sw_l1_cache: dict[str, Any] = {"ts": 0.0, "codes": None}


def _sw_l1_index_codes() -> dict[str, str]:
    """申万一级指数代码 -> 名称，缓存1小时（一级行业列表几乎不变）。"""
    now = time.time()
    if _sw_l1_cache["codes"] and now - _sw_l1_cache["ts"] < 3600:
        return _sw_l1_cache["codes"]
    pro = _pro()
    _throttle()
    df = pro.index_classify(level="L1", src="SW2021")
    codes = {row["index_code"]: row["industry_name"] for _, row in df.iterrows()}
    _sw_l1_cache["codes"] = codes
    _sw_l1_cache["ts"] = now
    return codes


def fetch_sw_l1_boards(trade_date: str) -> list[dict[str, Any]]:
    """申万一级行业板块日线+估值（sw_daily，官方数据，替代 westock-data 三方脚本）。

    一次调用覆盖全部申万指数（一二三级共几百条），这里筛出一级的 31 条。
    """
    pro = _pro()
    l1_codes = _sw_l1_index_codes()
    _throttle()
    df = pro.sw_daily(trade_date=trade_date)
    if df is None or df.empty:
        return []
    sub = df[df["ts_code"].isin(l1_codes.keys())]
    boards = []
    for _, r in sub.iterrows():
        boards.append({
            "code": r["ts_code"],
            "name": r.get("name") or l1_codes.get(r["ts_code"], ""),
            "change_pct": _f(r.get("pct_change")) or 0,
            "price": _f(r.get("close")) or 0,
            "pe_ratio": _f(r.get("pe")) or 0,
            "pb_ratio": _f(r.get("pb")) or 0,
            "turnover_rate": 0,  # sw_daily 不含换手率
            "market_cap": round((_f(r.get("total_mv")) or 0) / 10000, 2),  # 万元->亿元
            "volume": _f(r.get("vol")) or 0,
            "amount": _f(r.get("amount")) or 0,
        })
    return boards


def fetch_sector_fund_flow(trade_date: str) -> list[dict[str, Any]]:
    """申万一级行业板块资金流 + 相对沪深300的20日强弱（rs_csi300_20d）。

    替代之前长期停滞（一个月未更新）的东财板块资金流同步。用已验证的
    sw_daily（申万指数日线）自算20日相对强弱，比东财旧同步更可控；资金流净额
    用 moneyflow_ind_dc（东财行业资金流，按行业名匹配到申万一级）。
    """
    pro = _pro()
    l1_codes = _sw_l1_index_codes()  # {index_code: name}

    end = trade_date
    start_dt = f"{int(trade_date[:4])}{trade_date[4:6]}{trade_date[6:8]}"
    from datetime import datetime, timedelta as _td
    start = (datetime.strptime(start_dt, "%Y%m%d") - _td(days=40)).strftime("%Y%m%d")

    # 沪深300 20日收益率（基准）
    _throttle()
    csi = pro.index_daily(ts_code="000300.SH", start_date=start, end_date=end)
    csi_ret_20d = None
    if csi is not None and not csi.empty:
        csi = csi.sort_values("trade_date")
        if len(csi) >= 20:
            csi_ret_20d = float(csi.iloc[-1]["close"]) / float(csi.iloc[-20]["close"]) - 1

    # 行业资金流（按名称匹配申万一级）
    _throttle()
    flow_df = pro.moneyflow_ind_dc(trade_date=trade_date)
    flow_map: dict[str, float] = {}
    if flow_df is not None and not flow_df.empty:
        sub = flow_df[flow_df["content_type"] == "行业"]
        for _, r in sub.iterrows():
            flow_map[str(r.get("name", ""))] = _f(r.get("net_amount"))

    boards = []
    for code, name in l1_codes.items():
        _throttle()
        try:
            df = pro.sw_daily(ts_code=code, start_date=start, end_date=end)
        except Exception:
            continue
        if df is None or df.empty:
            continue
        df = df.sort_values("trade_date")
        last = df.iloc[-1]
        change_pct = _f(last.get("pct_change"))
        rs_csi300_20d = None
        if len(df) >= 20 and csi_ret_20d is not None:
            ind_ret_20d = float(last["close"]) / float(df.iloc[-20]["close"]) - 1
            rs_csi300_20d = round((ind_ret_20d - csi_ret_20d) * 100, 2)  # 百分点

        net_inflow = flow_map.get(name)
        # net_inflow_pct 需要跟 net_inflow 同源同单位的成交额做分母，
        # moneyflow_ind_dc 和 sw_daily 的成交额单位不一致，算出来的比例没有实际意义，
        # 干脆不算（评分逻辑本来也只读 rs_csi300_20d，不读这个字段）。

        boards.append({
            "sector_code": code,
            "sector_name": name,
            "net_inflow": net_inflow,
            "net_inflow_pct": None,
            "change_pct": change_pct,
            "rs_csi300_20d": rs_csi300_20d,
        })
    return boards


def fetch_market_suspend(trade_date: str) -> set[str]:
    """某交易日全市场官方停牌名单。返回停牌的 ts_code 集合（suspend_type='S'）。

    用于校准 stock_daily_quotes.is_suspended（此前是本地"当日成交量=0"的启发式
    判断，容易漏判/误判低成交但未停牌的交易日）。
    """
    pro = _pro()
    _throttle()
    df = pro.suspend_d(trade_date=trade_date, suspend_type="S")
    if df is None or df.empty:
        return set()
    return set(df["ts_code"].tolist())


def fetch_forecast_vip(period: str) -> dict[str, dict[str, Any]]:
    """某报告期全市场业绩预告（公司自愿/强制披露，覆盖率明显低于正式财报）。"""
    pro = _pro()
    _throttle()
    df = pro.forecast_vip(
        period=period,
        fields="ts_code,ann_date,end_date,type,p_change_min,p_change_max,"
                "net_profit_min,net_profit_max,last_parent_net,summary,change_reason",
    )
    if df is None or df.empty:
        return {}
    result: dict[str, dict[str, Any]] = {}
    for _, r in df.iterrows():
        result[r["ts_code"]] = {
            "ann_date": _fmt_date(r.get("ann_date")),
            "type": r.get("type") or "",
            "p_change_min": _f(r.get("p_change_min")),
            "p_change_max": _f(r.get("p_change_max")),
            "net_profit_min": _f(r.get("net_profit_min")),
            "net_profit_max": _f(r.get("net_profit_max")),
            "last_parent_net": _f(r.get("last_parent_net")),
            "summary": r.get("summary") or "",
            "change_reason": r.get("change_reason") or "",
        }
    return result


def fetch_express_vip(period: str) -> dict[str, dict[str, Any]]:
    """某报告期全市场业绩快报（未审计初步数据，纯自愿披露，覆盖率低）。"""
    pro = _pro()
    _throttle()
    df = pro.express_vip(
        period=period,
        fields="ts_code,ann_date,end_date,revenue,operate_profit,n_income,total_assets,"
                "diluted_eps,diluted_roe,yoy_sales,yoy_dedu_np,perf_summary",
    )
    if df is None or df.empty:
        return {}
    result: dict[str, dict[str, Any]] = {}
    for _, r in df.iterrows():
        result[r["ts_code"]] = {
            "ann_date": _fmt_date(r.get("ann_date")),
            "revenue": _f(r.get("revenue")),
            "operate_profit": _f(r.get("operate_profit")),
            "n_income": _f(r.get("n_income")),
            "total_assets": _f(r.get("total_assets")),
            "diluted_eps": _f(r.get("diluted_eps")),
            "diluted_roe": _f(r.get("diluted_roe")),
            "yoy_sales": _f(r.get("yoy_sales")),
            "yoy_dedu_np": _f(r.get("yoy_dedu_np")),
            "perf_summary": r.get("perf_summary") or "",
        }
    return result


def fetch_disclosure_date(end_date: str) -> dict[str, dict[str, Any]]:
    """某报告期全市场财报披露计划（公司自己预约的日期，非法定截止日估算）。"""
    pro = _pro()
    _throttle()
    df = pro.disclosure_date(end_date=end_date)
    if df is None or df.empty:
        return {}
    result: dict[str, dict[str, Any]] = {}
    for _, r in df.iterrows():
        result[r["ts_code"]] = {
            "ann_date": _fmt_date(r.get("ann_date")),
            "pre_date": _fmt_date(r.get("pre_date")),
            "actual_date": _fmt_date(r.get("actual_date")),
        }
    return result


def fetch_market_fund_flow_l2_detail(trade_date: str) -> dict[str, dict[str, Any]]:
    """某交易日全市场个股资金流（L2 大小单明细，官方交易所口径）。返回 {ts_code: {...}}，金额单位元。"""
    pro = _pro()
    _throttle()
    df = pro.moneyflow(trade_date=trade_date)
    if df is None or df.empty:
        return {}
    result: dict[str, dict[str, Any]] = {}
    for _, r in df.iterrows():
        result[r["ts_code"]] = {
            "buy_sm_amount": _f(r.get("buy_sm_amount")) * 10000 if r.get("buy_sm_amount") is not None else None,
            "sell_sm_amount": _f(r.get("sell_sm_amount")) * 10000 if r.get("sell_sm_amount") is not None else None,
            "buy_md_amount": _f(r.get("buy_md_amount")) * 10000 if r.get("buy_md_amount") is not None else None,
            "sell_md_amount": _f(r.get("sell_md_amount")) * 10000 if r.get("sell_md_amount") is not None else None,
            "buy_lg_amount": _f(r.get("buy_lg_amount")) * 10000 if r.get("buy_lg_amount") is not None else None,
            "sell_lg_amount": _f(r.get("sell_lg_amount")) * 10000 if r.get("sell_lg_amount") is not None else None,
            "buy_elg_amount": _f(r.get("buy_elg_amount")) * 10000 if r.get("buy_elg_amount") is not None else None,
            "sell_elg_amount": _f(r.get("sell_elg_amount")) * 10000 if r.get("sell_elg_amount") is not None else None,
            "net_mf_amount": _f(r.get("net_mf_amount")) * 10000 if r.get("net_mf_amount") is not None else None,
        }
    return result


def fetch_market_fund_flow_dc(trade_date: str) -> dict[str, dict[str, Any]]:
    """某交易日全市场个股资金流（东方财富口径，与 moneyflow 的 L2 官方口径为独立数据源）。"""
    pro = _pro()
    _throttle()
    df = pro.moneyflow_dc(trade_date=trade_date)
    if df is None or df.empty:
        return {}
    result: dict[str, dict[str, Any]] = {}
    for _, r in df.iterrows():
        result[r["ts_code"]] = {
            "net_amount": _f(r.get("net_amount")) * 10000 if r.get("net_amount") is not None else None,
            "net_amount_rate": _f(r.get("net_amount_rate")),
            "buy_elg_amount": _f(r.get("buy_elg_amount")) * 10000 if r.get("buy_elg_amount") is not None else None,
            "buy_lg_amount": _f(r.get("buy_lg_amount")) * 10000 if r.get("buy_lg_amount") is not None else None,
            "buy_md_amount": _f(r.get("buy_md_amount")) * 10000 if r.get("buy_md_amount") is not None else None,
            "buy_sm_amount": _f(r.get("buy_sm_amount")) * 10000 if r.get("buy_sm_amount") is not None else None,
        }
    return result


def fetch_hsgt_top10(trade_date: str) -> list[dict[str, Any]]:
    """沪深股通当日十大成交股（沪市+深市各前十，共两次调用）。"""
    pro = _pro()
    rows: list[dict[str, Any]] = []
    for market_type in ("1", "3"):
        _throttle()
        df = pro.hsgt_top10(trade_date=trade_date, market_type=market_type)
        if df is None or df.empty:
            continue
        for _, r in df.iterrows():
            rows.append({
                "ts_code": r["ts_code"],
                "name": r.get("name") or "",
                "market_type": market_type,
                "close": _f(r.get("close")),
                "change": _f(r.get("change")),
                "rank": int(r["rank"]) if r.get("rank") is not None else None,
                "amount": _f(r.get("amount")),
                "net_amount": _f(r.get("net_amount")),
                "buy": _f(r.get("buy")),
                "sell": _f(r.get("sell")),
            })
    return rows


def fetch_market_fund_flow(trade_date: str) -> dict[str, dict[str, Any]]:
    """某交易日全市场个股资金流（主力/超大单净额）。返回 {ts_code: {...}}，单位元。

    Tushare moneyflow 接口的 buy/sell_*_amount 与 net_mf_amount 原始单位是万元，
    需 ×10000 换算为元，与 stock_daily_quotes.amount 等全库口径（元）保持一致。
    """
    pro = _pro()
    _throttle()
    df = pro.moneyflow(trade_date=trade_date)
    if df is None or df.empty:
        return {}
    result = {}
    for _, r in df.iterrows():
        elg_net = _f(r.get("buy_elg_amount")) if r.get("buy_elg_amount") is not None else None
        elg_sell = _f(r.get("sell_elg_amount")) if r.get("sell_elg_amount") is not None else None
        super_large = (
            round((elg_net - elg_sell) * 10000, 2) if (elg_net is not None and elg_sell is not None) else None
        )
        net_mf = _f(r.get("net_mf_amount"))
        result[r["ts_code"]] = {
            "main_net_inflow": net_mf * 10000 if net_mf is not None else None,
            "super_large_inflow": super_large,
        }
    return result


def fetch_market_financials_vip(period: str) -> dict[str, dict[str, Any]]:
    """某报告期全市场财务三表 + 财务指标（VIP批量接口，覆盖大部分正常上市公司）。

    返回 {ts_code: {revenue, operating_cf, roe, ...}}，report_date 用 ann_date（公告日）。
    """
    pro = _pro()
    result: dict[str, dict[str, Any]] = {}

    _throttle()
    income = pro.income_vip(
        period=period,
        fields="ts_code,end_date,ann_date,revenue,total_revenue,operate_profit,"
                "n_income,n_income_attr_p,basic_eps,rd_exp",
    )
    for _, r in income.iterrows():
        d = result.setdefault(r["ts_code"], {})
        d["revenue"] = _f(r.get("revenue"))
        d["operating_revenue"] = _f(r.get("total_revenue"))
        d["operating_profit"] = _f(r.get("operate_profit"))
        d["net_profit"] = _f(r.get("n_income"))
        d["net_profit_parent"] = _f(r.get("n_income_attr_p"))
        d["eps"] = _f(r.get("basic_eps"))
        d["rd_exp"] = _f(r.get("rd_exp"))
        d["report_date"] = _fmt_date(r.get("ann_date"))

    _throttle()
    balance = pro.balancesheet_vip(
        period=period,
        fields="ts_code,total_assets,total_liab,total_hldr_eqy_exc_min_int,total_cur_assets,"
                "total_cur_liab,money_cap,accounts_receiv,inventories,goodwill,fix_assets",
    )
    for _, r in balance.iterrows():
        d = result.setdefault(r["ts_code"], {})
        d["total_assets"] = _f(r.get("total_assets"))
        d["total_liabilities"] = _f(r.get("total_liab"))
        d["total_equity"] = _f(r.get("total_hldr_eqy_exc_min_int"))
        d["current_assets"] = _f(r.get("total_cur_assets"))
        d["current_liabilities"] = _f(r.get("total_cur_liab"))
        d["money_cap"] = _f(r.get("money_cap"))
        d["accounts_receivable"] = _f(r.get("accounts_receiv"))
        d["inventories"] = _f(r.get("inventories"))
        d["goodwill"] = _f(r.get("goodwill"))
        d["fix_assets"] = _f(r.get("fix_assets"))

    _throttle()
    cashflow = pro.cashflow_vip(
        period=period,
        fields="ts_code,n_cashflow_act,n_cashflow_inv_act,n_cash_flows_fnc_act",
    )
    for _, r in cashflow.iterrows():
        d = result.setdefault(r["ts_code"], {})
        d["operating_cf"] = _f(r.get("n_cashflow_act"))
        d["investing_cf"] = _f(r.get("n_cashflow_inv_act"))
        d["financing_cf"] = _f(r.get("n_cash_flows_fnc_act"))

    _throttle()
    indicator = pro.fina_indicator_vip(
        period=period,
        fields="ts_code,roe,roa,grossprofit_margin,netprofit_margin,debt_to_assets,current_ratio",
    )
    for _, r in indicator.iterrows():
        d = result.setdefault(r["ts_code"], {})
        d["roe"] = _f(r.get("roe"))
        d["roa"] = _f(r.get("roa"))
        d["gross_margin"] = _f(r.get("grossprofit_margin"))
        d["net_margin"] = _f(r.get("netprofit_margin"))
        d["debt_to_equity"] = _f(r.get("debt_to_assets"))
        d["current_ratio"] = _f(r.get("current_ratio"))

    return result


def fetch_ths_concept_boards() -> list[dict[str, str]]:
    """同花顺概念板块列表（type='N'，区别于 'I' 行业指数）。低频批量，一次调用覆盖全部板块。"""
    pro = _pro()
    _throttle()
    df = pro.ths_index(type="N")
    if df is None or df.empty:
        return []
    return [
        {"ts_code": str(r["ts_code"]), "name": str(r["name"])}
        for r in df.to_dict("records")
    ]


def fetch_ths_concept_members(ts_code: str) -> list[str]:
    """某同花顺概念板块的成分股（con_code），需按板块逐个调用（无 by-date 批量接口）。"""
    pro = _pro()
    _throttle()
    df = pro.ths_member(ts_code=ts_code)
    if df is None or df.empty:
        return []
    return [str(c) for c in df["con_code"].tolist()]


def fetch_dc_concept_boards(trade_date: str) -> list[dict[str, str]]:
    """东方财富概念板块当日快照（idx_type='概念板块'），按日批量覆盖全市场板块列表。"""
    pro = _pro()
    _throttle()
    df = pro.dc_index(trade_date=trade_date)
    if df is None or df.empty:
        return []
    rows = df[df["idx_type"] == "概念板块"]
    return [
        {"ts_code": str(r["ts_code"]), "name": str(r["name"])}
        for r in rows.to_dict("records")
    ]


def fetch_dc_concept_members(ts_code: str) -> list[str]:
    """某东方财富概念板块的最新成分股（con_code），需按板块逐个调用。

    dc_member 不传 trade_date 时返回近几个交易日的快照混合，这里只取
    返回结果里最新的 trade_date，避免已经调出板块的股票残留。
    """
    pro = _pro()
    _throttle()
    df = pro.dc_member(ts_code=ts_code)
    if df is None or df.empty:
        return []
    latest = df["trade_date"].max()
    return [str(c) for c in df[df["trade_date"] == latest]["con_code"].tolist()]


def fetch_company_info_bulk(exchange: str) -> list[dict[str, Any]]:
    """按交易所批量拉取上市公司基本信息（SSE/SZSE/BSE 三次调用覆盖全市场）。"""
    pro = _pro()
    _throttle()
    df = pro.stock_company(exchange=exchange)
    if df is None or df.empty:
        return []
    return df.to_dict("records")


def fetch_managers(ts_code: str) -> list[dict[str, Any]]:
    """某股票现任+历任管理层名单，仅支持按单只股票查询（无 by-exchange 批量接口）。"""
    pro = _pro()
    _throttle()
    df = pro.stk_managers(ts_code=ts_code)
    if df is None or df.empty:
        return []
    return df.to_dict("records")


def _f(v: Any) -> float | None:
    if v is None:
        return None
    try:
        import math
        f = float(v)
        return None if math.isnan(f) else f
    except (TypeError, ValueError):
        return None


def _fmt_date(d: Any) -> str | None:
    if not d:
        return None
    s = str(d)
    if len(s) == 8 and s.isdigit():
        return f"{s[:4]}-{s[4:6]}-{s[6:8]}"
    return s
