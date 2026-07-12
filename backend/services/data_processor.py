"""
数据处理器 - 数据清洗、转换、标准化
"""
import pandas as pd
import numpy as np


def normalize_code(code: str, *, market: str = "A") -> str:
    """标准化股票代码：A 股补齐 6 位；美股等字母代码保持原样。"""
    code = str(code).strip().upper()
    if market == "US":
        return code
    if len(code) > 6:
        for prefix in ("SH", "SZ", "BJ"):
            if code.startswith(prefix):
                code = code[len(prefix) :]
                break
    if code.isalpha() and len(code) <= 5:
        return code
    if code.isdigit():
        return code.zfill(6)
    return code


def to_exchange_code(code: str) -> str:
    """获取带交易所前缀的代码"""
    code = normalize_code(code)
    if code.startswith(("6", "9")):
        return f"SH{code}"
    elif code.startswith(("0", "3")):
        return f"SZ{code}"
    elif code.startswith(("4", "8")):
        return f"BJ{code}"
    return f"SH{code}"


def to_yfinance_code(code: str, *, market: str = "A") -> str:
    """转换为 yfinance 格式（.SS=上海, .SZ=深圳；美股直接用 ticker）"""
    code = normalize_code(code, market=market)
    if market == "US" or (code.isalpha() and len(code) <= 5):
        return code
    if code.startswith(("6", "9")):
        return f"{code}.SS"
    if code.startswith(("0", "3")):
        return f"{code}.SZ"
    return f"{code}.SS"


def safe_float(val, default=None) -> float | None:
    """安全转换为 float"""
    if val is None or val == "" or (isinstance(val, float) and np.isnan(val)):
        return default
    try:
        return float(val)
    except (ValueError, TypeError):
        return default


def transform_yfinance_quotes(df: pd.DataFrame, stock_id: int) -> list[dict]:
    """转换 yfinance history() 输出为数据库格式"""
    rows = []
    for idx, row in df.iterrows():
        rows.append({
            "stock_id": stock_id,
            "trade_date": str(idx.date()),
            "open": safe_float(row.get("Open")),
            "high": safe_float(row.get("High")),
            "low": safe_float(row.get("Low")),
            "close": safe_float(row.get("Close")),
            "volume": safe_float(row.get("Volume")),
            "amount": None,  # yfinance 不提供成交额
        })
    return rows


def transform_financial_reports(
    df_income: pd.DataFrame,
    df_balance: pd.DataFrame,
    df_cf: pd.DataFrame,
    stock_id: int
) -> list[dict]:
    """将 akshare 三张财务报表合并为统一记录"""
    if df_income is None or df_income.empty:
        return []

    records = {}

    for _, row in df_income.iterrows():
        report_date_str = parse_date(row.get("REPORT_DATE"))
        if not report_date_str:
            continue

        report_type = map_report_type(row.get("REPORT_TYPE", ""), report_date_str)
        key = (report_date_str, report_type)
        records[key] = {
            "stock_id": stock_id,
            "report_date": parse_date(row.get("NOTICE_DATE")) or report_date_str,
            "period_end_date": report_date_str,
            "report_type": report_type,
            "revenue": safe_float(row.get("TOTAL_OPERATE_INCOME")),
            "operating_revenue": safe_float(row.get("OPERATE_INCOME")),
            "operating_profit": safe_float(row.get("OPERATE_PROFIT")),
            "total_operating_cost": safe_float(row.get("TOTAL_OPERATE_COST")),
            "net_profit": safe_float(row.get("NETPROFIT")),
            "net_profit_parent": safe_float(row.get("PARENT_NETPROFIT")),
            "eps": safe_float(row.get("BASIC_EPS")),
        }

    if df_balance is not None and not df_balance.empty:
        for _, row in df_balance.iterrows():
            report_date_str = parse_date(row.get("REPORT_DATE"))
            if not report_date_str:
                continue
            report_type = map_report_type(row.get("REPORT_TYPE", ""), report_date_str)
            key = (report_date_str, report_type)
            if key in records:
                r = records[key]
                r["total_assets"] = safe_float(row.get("TOTAL_ASSETS"))
                r["total_liabilities"] = safe_float(row.get("TOTAL_LIABILITIES"))
                r["total_equity"] = safe_float(row.get("TOTAL_EQUITY"))
                r["current_assets"] = safe_float(row.get("TOTAL_CURRENT_ASSETS"))
                r["current_liabilities"] = safe_float(row.get("TOTAL_CURRENT_LIAB"))
                r["bvps"] = safe_float(row.get("TOTAL_PARENT_EQUITY"))
                r["accounts_receivable"] = safe_float(
                    row.get("ACCOUNT_RECE")
                    or row.get("ACCOUNTS_RECE")
                    or row.get("NOTE_ACCOUNTS_RECE")
                    or row.get("ACCOUNTS_RECEIVABLE")
                )

    if df_cf is not None and not df_cf.empty:
        for _, row in df_cf.iterrows():
            report_date_str = parse_date(row.get("REPORT_DATE"))
            if not report_date_str:
                continue
            report_type = map_report_type(row.get("REPORT_TYPE", ""), report_date_str)
            key = (report_date_str, report_type)
            if key in records:
                r = records[key]
                r["operating_cf"] = safe_float(row.get("NETCASH_OPERATE"))
                r["investing_cf"] = safe_float(row.get("NETCASH_INVEST"))
                r["financing_cf"] = safe_float(row.get("NETCASH_FINANCE"))

    return list(records.values())


def transform_financial_indicators(df: pd.DataFrame, stock_id: int) -> list[dict]:
    """转换 akshare stock_financial_analysis_indicator 输出为数据库格式"""
    if df is None or df.empty:
        return []

    rows = []
    for _, row in df.iterrows():
        record = {"stock_id": stock_id, "calc_date": parse_date(row.get("日期") or row.get("REPORT_DATE"))}

        for col in df.columns:
            col_str = str(col)
            val = safe_float(row.get(col))
            if val is None:
                continue

            if "净资产收益率" in col_str and "摊薄" not in col_str and "加权" in col_str:
                record["roe"] = val
            elif "总资产报酬率" in col_str:
                record["roa"] = val
            elif "销售毛利率" in col_str:
                record["gross_margin"] = val
            elif "销售净利率" in col_str:
                record["net_margin"] = val
            elif "资产负债率" in col_str:
                record["debt_to_equity"] = round(val / (100 - val), 4) if val < 100 else 99.0
            elif "流动比率" in col_str and "速动" not in col_str:
                record["current_ratio"] = val
            elif "利息保障倍数" in col_str:
                record["interest_coverage_ratio"] = val

        if record.get("calc_date"):
            rows.append(record)

    return rows


def _transform_financial_abstract(df: pd.DataFrame, stock_id: int) -> list[dict]:
    """将 akshare stock_financial_abstract 宽表转为 financial_indicators 格式
    输入: 每行一个指标, 每列一个报告期 (如 20251231, 20241231...)
    输出: [{stock_id, calc_date, roe, roa, gross_margin, net_margin, ...}, ...]
    """
    if df is None or df.empty:
        return []

    # 指标名→字段名映射（模糊匹配，支持后缀如(ROE)）
    indicator_map = [
        ("净资产收益率", "roe"),
        ("总资产报酬率", "roa"),
        ("毛利率", "gross_margin"),
        ("净利率", "net_margin"),
        ("资产负债率", "debt_to_equity"),
        ("流动比率", "current_ratio"),
        ("利息保障倍数", "interest_coverage_ratio"),
    ]

    # 收集每个日期的指标值: {date: {field: value}}
    date_cols = [c for c in df.columns if isinstance(c, str) and len(c) == 8 and c.isdigit()]
    records: dict[str, dict] = {}

    for _, row in df.iterrows():
        indicator = str(row["指标"]).strip()
        # 模糊匹配: "净资产收益率(ROE)" → "roe"
        field = None
        for key, fld in indicator_map:
            if key in indicator:
                field = fld
                break
        if not field:
            continue
        for dc in date_cols:
            val = safe_float(row.get(dc))
            if val is None:
                continue
            # 格式化日期: 20251231 → 2025-12-31
            calc_date = f"{dc[:4]}-{dc[4:6]}-{dc[6:8]}"
            if calc_date not in records:
                records[calc_date] = {"stock_id": stock_id, "calc_date": calc_date}
            records[calc_date][field] = val

    # 资产负债率特殊处理: 百分比→比率
    for rec in records.values():
        if "debt_to_equity" in rec and rec["debt_to_equity"] > 1:
            rec["debt_to_equity"] = round(rec["debt_to_equity"] / (100 - rec["debt_to_equity"]), 4)

    return list(records.values())


def parse_date(date_val) -> str:
    """解析日期为标准格式 YYYY-MM-DD"""
    if date_val is None:
        return ""
    s = str(date_val).strip()[:10]
    return s


def map_report_type(type_str: str, period_end_date: str = "") -> str:
    """映射报告类型"""
    s = str(type_str).strip()
    if "年报" in s or "年度" in s:
        return "annual"
    if "一季" in s or "1季" in s:
        return "q1"
    if "中报" in s or "半年" in s or "二季" in s or "2季" in s:
        return "q2"
    if "三季" in s or "3季" in s:
        return "q3"
    if "季报" in s or "季度" in s:
        return "quarterly"
    if period_end_date and len(period_end_date) >= 7:
        m = period_end_date[5:7]
        if m == "12":
            return "annual"
        if m == "03":
            return "q1"
        if m == "06":
            return "q2"
        if m == "09":
            return "q3"
    return "annual"


def is_quarterly_report_type(report_type: str, period_end_date: str = "") -> bool:
    if report_type in ("q1", "q2", "q3", "quarterly"):
        return True
    if not period_end_date or len(period_end_date) < 7:
        return False
    month = period_end_date[5:7]
    return month in ("03", "06", "09") or (month == "12" and report_type != "annual")


def compute_cagr(values: list[float]) -> float | None:
    """计算复合年增长率 (CAGR)"""
    if not values or len(values) < 2:
        return None
    valid = [v for v in values if v is not None and v > 0]
    if len(valid) < 2:
        return None
    return (valid[-1] / valid[0]) ** (1 / (len(valid) - 1)) - 1


# 同比变动超过该倍率（默认 10 倍 ≈ ±1000%）视为基数过小、参考意义有限
YOY_MAX_RATIO = 10.0


def compute_yoy_meta(
    cur: float | None,
    prev: float | None,
    *,
    max_ratio: float = YOY_MAX_RATIO,
) -> dict:
    """计算同比及其可信度元数据。

    返回字段:
      yoy_pct: 可信时返回百分比（供展示/评分），不可信时为 None
      yoy_raw_pct: 原始百分比（prev≠0 时始终计算）
      yoy_decimal: 可信时返回小数（供因子引擎），不可信时为 None
      yoy_reliable: 是否可信
      yoy_note: 不可信时的说明
      profit_change: 绝对变动 (cur - prev)
      change_ratio: |cur-prev|/|prev|
    """
    empty = {
        "yoy_pct": None,
        "yoy_raw_pct": None,
        "yoy_decimal": None,
        "yoy_reliable": False,
        "yoy_note": None,
        "change": None,
        "change_ratio": None,
    }
    if cur is None or prev is None:
        return {**empty, "yoy_note": "缺少对比期数据"}
    if prev == 0:
        return {**empty, "yoy_note": "基期数值为 0，无法计算同比"}

    change = cur - prev
    ratio = abs(change / prev)
    raw_pct = round(change / abs(prev) * 100, 1)

    if ratio > max_ratio:
        return {
            "yoy_pct": None,
            "yoy_raw_pct": raw_pct,
            "yoy_decimal": None,
            "yoy_reliable": False,
            "yoy_note": f"变动超过 {max_ratio:.0f} 倍（约 {ratio:.0f} 倍），基数过小导致同比失真",
            "change": change,
            "change_ratio": ratio,
        }

    return {
        "yoy_pct": raw_pct,
        "yoy_raw_pct": raw_pct,
        "yoy_decimal": change / abs(prev),
        "yoy_reliable": True,
        "yoy_note": None,
        "change": change,
        "change_ratio": ratio,
    }


def _apply_growth_fields(cur: dict, field: str, meta: dict) -> None:
    """field 如 revenue_yoy / profit_qoq"""
    cur[field] = meta["yoy_pct"]
    cur[f"{field}_raw"] = meta["yoy_raw_pct"]
    cur[f"{field}_reliable"] = meta["yoy_reliable"]
    cur[f"{field}_note"] = meta["yoy_note"]
    if field == "profit_yoy":
        cur["profit_yoy_change"] = meta["change"]
        cur["profit_yoy_change_ratio"] = meta["change_ratio"]


def enrich_reports_with_yoy(
    reports: list[dict],
    *,
    max_ratio: float = YOY_MAX_RATIO,
) -> None:
    """为财报序列就地补充 YoY/QoQ 及可信度字段。"""
    for i, cur in enumerate(reports):
        rev_cur = cur.get("revenue") or 0
        np_cur = cur.get("net_profit_parent")
        if np_cur is None:
            np_cur = cur.get("net_profit") or 0

        if i > 0:
            prev = reports[i - 1]
            rev_prev = prev.get("revenue") or 0
            np_prev = prev.get("net_profit_parent")
            if np_prev is None:
                np_prev = prev.get("net_profit") or 0
            _apply_growth_fields(cur, "revenue_qoq", compute_yoy_meta(rev_cur, rev_prev, max_ratio=max_ratio))
            _apply_growth_fields(cur, "profit_qoq", compute_yoy_meta(np_cur, np_prev, max_ratio=max_ratio))

        cur_date = cur.get("period_end_date", "")
        for j in range(i - 1, -1, -1):
            prev_date = reports[j].get("period_end_date", "")
            if (
                prev_date[:4] == str(int(cur_date[:4]) - 1)
                and prev_date[5:7] == cur_date[5:7]
            ):
                prev_yr = reports[j]
                rev_yr = prev_yr.get("revenue") or 0
                np_yr = prev_yr.get("net_profit_parent")
                if np_yr is None:
                    np_yr = prev_yr.get("net_profit") or 0
                _apply_growth_fields(cur, "revenue_yoy", compute_yoy_meta(rev_cur, rev_yr, max_ratio=max_ratio))
                _apply_growth_fields(cur, "profit_yoy", compute_yoy_meta(np_cur, np_yr, max_ratio=max_ratio))
                break


def select_quarterly_reports(
    all_reports: list[dict],
    periods: int = 8,
) -> tuple[list[dict], list[dict], str]:
    """筛选季报序列并标注数据粒度。"""
    quarterly = [
        r
        for r in all_reports
        if is_quarterly_report_type(r.get("report_type", ""), r.get("period_end_date", ""))
    ]
    reports = quarterly if len(quarterly) >= 2 else all_reports
    reports = list(reversed(reports[-(periods + 4) :]))
    granularity = "quarterly" if len(quarterly) >= 2 else "annual_fallback"
    return reports, quarterly, granularity
