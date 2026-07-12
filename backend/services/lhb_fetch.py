"""龙虎榜 — 东财 datacenter 直连优先，ADATA/akshare 兜底"""
from __future__ import annotations

from datetime import date, timedelta
from typing import Any

from services.http_client import get

_DC = "https://datacenter-web.eastmoney.com/api/data/v1/get"
_LHB_COLUMNS = (
    "SECURITY_CODE,SECUCODE,SECURITY_NAME_ABBR,TRADE_DATE,EXPLAIN,CLOSE_PRICE,CHANGE_RATE,"
    "BILLBOARD_NET_AMT,BILLBOARD_BUY_AMT,BILLBOARD_SELL_AMT,BILLBOARD_DEAL_AMT,ACCUM_AMOUNT,"
    "TURNOVERRATE,EXPLANATION"
)


def _norm_code(code: str) -> str:
    c = (code or "").strip().upper()
    for p in ("SH", "SZ", "BJ"):
        if c.startswith(p):
            c = c[2:]
    return c.zfill(6) if c.isdigit() else c


def _norm_date(d: str | None) -> str:
    if not d:
        return date.today().strftime("%Y-%m-%d")
    d = d.strip().replace("/", "-")
    if len(d) == 8 and d.isdigit():
        return f"{d[:4]}-{d[4:6]}-{d[6:8]}"
    return d[:10]


def _format_pct(v: Any) -> float | None:
    if v is None:
        return None
    try:
        f = float(v)
        if f != f:
            return None
        if -1.5 < f < 1.5 and f != 0:
            f = f * 100
        return round(f, 2)
    except (TypeError, ValueError):
        return None


def _yuan_to_wan(v: Any) -> float | None:
    if v is None:
        return None
    try:
        f = float(v)
        if f != f:
            return None
        return round(f / 10000, 2) if abs(f) > 1000 else round(f, 2)
    except (TypeError, ValueError):
        return None


def _dc_pages(
    report_name: str,
    *,
    filter_str: str = "",
    columns: str = "ALL",
    sort_columns: str = "",
    sort_types: str = "-1",
    page_size: int = 500,
) -> list[dict]:
    params: dict[str, str] = {
        "reportName": report_name,
        "columns": columns,
        "filter": filter_str,
        "pageNumber": "1",
        "pageSize": str(page_size),
        "sortTypes": sort_types,
        "sortColumns": sort_columns,
        "source": "WEB",
        "client": "WEB",
    }
    r = get(_DC, params=params, timeout=20)
    payload = r.json()
    result = payload.get("result") or {}
    rows = result.get("data") or []
    if not rows:
        return []
    pages = int(result.get("pages") or 1)
    out = list(rows)
    for page in range(2, pages + 1):
        params["pageNumber"] = str(page)
        r2 = get(_DC, params=params, timeout=20)
        part = (r2.json().get("result") or {}).get("data") or []
        out.extend(part)
    return out


def _row_from_em(r: dict) -> dict:
    td = str(r.get("TRADE_DATE", ""))[:10]
    if len(td) == 8 and td.isdigit():
        td = f"{td[:4]}-{td[4:6]}-{td[6:8]}"
    return {
        "date": td,
        "code": _norm_code(str(r.get("SECURITY_CODE", ""))),
        "name": str(r.get("SECURITY_NAME_ABBR", "")),
        "close": r.get("CLOSE_PRICE"),
        "change_pct": _format_pct(r.get("CHANGE_RATE")),
        "turnover_pct": _format_pct(r.get("TURNOVERRATE")),
        "net_buy": _yuan_to_wan(r.get("BILLBOARD_NET_AMT")),
        "buy_amount": _yuan_to_wan(r.get("BILLBOARD_BUY_AMT")),
        "sell_amount": _yuan_to_wan(r.get("BILLBOARD_SELL_AMT")),
        "deal_amount": _yuan_to_wan(r.get("BILLBOARD_DEAL_AMT")),
        "reason": str(r.get("EXPLANATION") or r.get("EXPLAIN") or "")[:80],
    }


def _fetch_daily_eastmoney(report_date: str) -> list[dict]:
    flt = f"(TRADE_DATE<='{report_date}')(TRADE_DATE>='{report_date}')"
    rows = _dc_pages(
        "RPT_DAILYBILLBOARD_DETAILSNEW",
        filter_str=flt,
        columns=_LHB_COLUMNS,
        sort_columns="BILLBOARD_NET_AMT",
        sort_types="-1",
        page_size=500,
    )
    return [_row_from_em(r) for r in rows if r.get("SECURITY_CODE")]


def _resolve_lhb_date(requested: str, lookback: int = 12) -> tuple[str, list[dict]]:
    """若请求日无榜，向前找最近有数据的交易日。"""
    base = date.fromisoformat(requested)
    for i in range(lookback):
        d = (base - timedelta(days=i)).strftime("%Y-%m-%d")
        items = _fetch_daily_eastmoney(d)
        if items:
            return d, items
    return requested, []


def _adata_available() -> bool:
    try:
        import adata  # noqa: F401

        return True
    except ImportError:
        return False


def _row_from_adata_daily(r: dict) -> dict:
    return {
        "date": str(r.get("trade_date", ""))[:10],
        "code": _norm_code(str(r.get("stock_code", ""))),
        "name": str(r.get("short_name", "")),
        "close": r.get("close"),
        "change_pct": _format_pct(r.get("change_cpt")),
        "turnover_pct": _format_pct(r.get("turnover_ratio")),
        "net_buy": _yuan_to_wan(r.get("a_net_amount")),
        "buy_amount": _yuan_to_wan(r.get("a_buy_amount")),
        "sell_amount": _yuan_to_wan(r.get("a_sell_amount")),
        "deal_amount": _yuan_to_wan(r.get("a_amount")),
        "reason": str(r.get("reason", ""))[:80],
    }


def _fetch_daily_adata(report_date: str) -> list[dict]:
    from adata.sentiment.alist import AList

    df = AList().list_a_list_daily(report_date=report_date)
    if df is None or df.empty:
        return []
    return [_row_from_adata_daily(r) for r in df.to_dict("records")]


def _fetch_daily_tushare(report_date: str) -> list[dict]:
    """Tushare top_list — 结构化官方数据，比爬虫源更稳定。"""
    from services.tushare_adapter import _pro

    pro = _pro()
    d8 = report_date.replace("-", "")
    df = pro.top_list(trade_date=d8)
    if df is None or df.empty:
        return []
    rows = []
    for _, r in df.iterrows():
        code = str(r.get("ts_code", "")).split(".")[0]
        rows.append({
            "date": report_date,
            "code": _norm_code(code),
            "name": str(r.get("name", "")),
            "close": r.get("close"),
            "change_pct": _format_pct(r.get("pct_change")),
            "turnover_pct": _format_pct(r.get("turnover_rate")),
            "net_buy": _yuan_to_wan(r.get("net_amount")),
            "buy_amount": _yuan_to_wan(r.get("l_buy")),
            "sell_amount": _yuan_to_wan(r.get("l_sell")),
            "deal_amount": _yuan_to_wan(r.get("l_amount")),
            "reason": str(r.get("reason") or "")[:80],
        })
    return rows


def _fetch_daily_akshare(report_date: str) -> list[dict]:
    from services.akshare_lazy import akshare as _ak

    d8 = report_date.replace("-", "")
    try:
        df = _ak().stock_lhb_detail_em(start_date=d8, end_date=d8)
    except (TypeError, KeyError, AttributeError):
        return []
    if df is None or getattr(df, "empty", True):
        return []
    rows = []
    for _, r in df.iterrows():
        code = str(r.get("代码", "")).zfill(6)
        if not code.isdigit():
            continue
        td = str(r.get("上榜日", report_date))[:10]
        rows.append({
            "date": td,
            "code": code,
            "name": str(r.get("名称", "")),
            "close": r.get("收盘价"),
            "change_pct": _format_pct(r.get("涨跌幅")),
            "turnover_pct": _format_pct(r.get("换手率")),
            "net_buy": _yuan_to_wan(r.get("龙虎榜净买额")),
            "buy_amount": _yuan_to_wan(r.get("龙虎榜买入额")),
            "sell_amount": _yuan_to_wan(r.get("龙虎榜卖出额")),
            "deal_amount": _yuan_to_wan(r.get("龙虎榜成交额")),
            "reason": str(r.get("上榜原因", ""))[:80],
        })
    return rows


def _fetch_lhb_daily_live(requested: str) -> tuple[str, list[dict], str, list[str]]:
    """Tushare 官方数据为主，东财/adata/akshare 兜底。返回 (resolved, items, source, errors)。"""
    source = "tushare"
    err_parts: list[str] = []
    items: list[dict] = []
    resolved = requested

    try:
        items = _fetch_daily_tushare(requested)
    except Exception as e:
        err_parts.append(f"tushare:{e}")

    if not items:
        source = "eastmoney"
        try:
            items = _fetch_daily_eastmoney(requested)
            if not items:
                resolved, items = _resolve_lhb_date(requested)
        except Exception as e:
            err_parts.append(f"eastmoney:{e}")

    if not items and _adata_available():
        source = "adata"
        try:
            items = _fetch_daily_adata(resolved)
        except Exception as e:
            err_parts.append(f"adata:{e}")

    if not items:
        source = "akshare"
        try:
            items = _fetch_daily_akshare(resolved)
        except Exception as e:
            err_parts.append(f"akshare:{e}")

    return resolved, items, source if items else "", err_parts


def fetch_lhb_daily(
    report_date: str | None = None,
    limit: int = 80,
    *,
    force: bool = False,
) -> dict:
    """当日全市场龙虎榜：优先读 lhb_market_daily，缺失再拉东财并落库。"""
    from services.lhb_sync import load_lhb_market_from_db, save_lhb_market_to_db
    from services.market_data_cache import cached_lhb_daily

    requested = _norm_date(report_date)
    cache_key = f"lhb:{requested}:{limit}"

    def _build() -> dict:
        db_items = load_lhb_market_from_db(requested)
        resolved = requested
        source = "db"
        err_parts: list[str] = []
        items = db_items

        if force:
            resolved, live_items, live_source, err_parts = _fetch_lhb_daily_live(requested)
            if live_items:
                source = live_source or "eastmoney"
                save_lhb_market_to_db(resolved, live_items, source=source)
                items = load_lhb_market_from_db(resolved)
            elif db_items:
                items = db_items
                resolved = requested
        elif not items:
            resolved, items, live_source, err_parts = _fetch_lhb_daily_live(requested)
            source = live_source or source
            if items:
                save_lhb_market_to_db(resolved, items, source=source or "eastmoney")
                items = load_lhb_market_from_db(resolved)

        if not items and not force:
            # 请求日无库内数据时，向前找最近有榜日（仅读库）
            base = date.fromisoformat(requested)
            for i in range(1, 13):
                d = (base - timedelta(days=i)).strftime("%Y-%m-%d")
                items = load_lhb_market_from_db(d)
                if items:
                    resolved = d
                    source = "db"
                    break

        items.sort(key=lambda x: abs(x.get("net_buy") or 0), reverse=True)
        out: dict[str, Any] = {
            "date": resolved,
            "requested_date": requested,
            "count": len(items),
            "items": items[: max(1, min(limit, 200))],
            "source": source if items else None,
            "cached": source == "db",
            "error": "; ".join(err_parts) if not items and err_parts else None,
        }
        if resolved != requested and items:
            out["note"] = f"请求日 {requested} 无榜，已展示最近有数据日 {resolved}"
        return out

    out = cached_lhb_daily(cache_key, force, _build)
    if not force:
        out = dict(out)
        out["cached"] = True
    return out


def _fetch_seats_eastmoney(code: str, report_date: str) -> list[dict]:
    code = _norm_code(code)
    flt = f"(TRADE_DATE='{report_date}')(SECURITY_CODE=\"{code}\")"
    seats: list[dict] = []
    for side, report, sort_col in (
        ("buy", "RPT_BILLBOARD_DAILYDETAILSBUY", "BUY"),
        ("sell", "RPT_BILLBOARD_DAILYDETAILSSELL", "SELL"),
    ):
        rows = _dc_pages(
            report,
            filter_str=flt,
            sort_columns=sort_col,
            sort_types="-1",
            page_size=50,
        )
        for r in rows:
            seats.append({
                "side": side,
                "name": str(r.get("OPERATEDEPT_NAME", ""))[:40],
                "net_amount": _yuan_to_wan(r.get("NET")),
                "buy_amount": _yuan_to_wan(r.get("BUY")),
                "sell_amount": _yuan_to_wan(r.get("SELL")),
                "reason": str(r.get("EXPLANATION", ""))[:40],
            })
    return seats[:20]


def _seats_from_adata(code: str, report_date: str) -> list[dict]:
    from adata.sentiment.alist import AList

    df = AList().get_a_list_info(stock_code=code, report_date=report_date)
    if df is None or df.empty:
        return []
    seats = []
    for _, r in df.iterrows():
        buy = r.get("a_buy_amount")
        sell = r.get("a_sell_amount")
        side = "buy" if (buy or 0) >= (sell or 0) else "sell"
        seats.append({
            "side": side,
            "name": str(r.get("operate_name", ""))[:40],
            "net_amount": _yuan_to_wan(r.get("a_net_amount")),
            "buy_amount": _yuan_to_wan(buy),
            "sell_amount": _yuan_to_wan(sell),
            "reason": str(r.get("reason", ""))[:40],
        })
    return seats[:20]


def _list_dates_eastmoney(code: str, limit: int) -> list[str]:
    code = _norm_code(code)
    rows = _dc_pages(
        "RPT_LHB_BOARDDATE",
        filter_str=f'(SECURITY_CODE="{code}")',
        columns="SECURITY_CODE,TRADE_DATE",
        sort_columns="TRADE_DATE",
        sort_types="-1",
        page_size=min(limit, 200),
    )
    dates: list[str] = []
    for r in rows:
        s = str(r.get("TRADE_DATE", ""))[:10]
        if len(s) == 8 and s.isdigit():
            s = f"{s[:4]}-{s[4:6]}-{s[6:8]}"
        if s:
            dates.append(s)
    return dates[:limit]


def _list_dates_akshare(code: str, limit: int) -> list[str]:
    from services.akshare_lazy import akshare as _ak

    try:
        df = _ak().stock_lhb_stock_detail_date_em(symbol=code)
    except (TypeError, KeyError, AttributeError):
        return []
    if df is None or df.empty:
        return []
    col = "交易日" if "交易日" in df.columns else df.columns[-1]
    dates = []
    for v in df[col].tolist():
        s = str(v)[:10].replace("/", "-")
        if len(s) == 8 and s.isdigit():
            s = f"{s[:4]}-{s[4:6]}-{s[6:8]}"
        dates.append(s)
    return dates[:limit]


def _history_from_daily_scan(code: str, days: int) -> list[dict]:
    code = _norm_code(code)
    records: list[dict] = []
    today = date.today()
    for i in range(days):
        d = (today - timedelta(days=i)).strftime("%Y-%m-%d")
        try:
            for row in _fetch_daily_eastmoney(d):
                if row["code"] == code:
                    records.append(row)
                    break
        except Exception:
            continue
        if len(records) >= 15:
            break
    return records


def fetch_lhb_stock(
    code: str,
    report_date: str | None = None,
    *,
    history_days: int = 60,
    limit: int = 10,
) -> dict:
    """单股龙虎榜：指定日详情（含席位），或未指定日时返回历史上榜记录"""
    code = _norm_code(code)
    if not code or len(code) != 6:
        return {"code": code, "records": [], "error": "invalid code"}

    report_date = _norm_date(report_date) if report_date else None
    source = "eastmoney"

    if report_date:
        record: dict | None = None
        seats: list[dict] = []
        daily = fetch_lhb_daily(report_date, limit=500)
        resolved = daily.get("date") or report_date
        for it in daily.get("items") or []:
            if it.get("code") == code:
                record = {**it, "code": code, "date": resolved}
                break

        try:
            seats = _fetch_seats_eastmoney(code, resolved)
        except Exception:
            seats = []

        if not seats and _adata_available():
            try:
                seats = _seats_from_adata(code, resolved)
            except Exception:
                pass

        if record and seats:
            record["seats"] = seats

        if not record:
            source = "akshare"
            try:
                from services.akshare_lazy import akshare as _ak

                d8 = resolved.replace("-", "")
                df = _ak().stock_lhb_stock_detail_em(symbol=code, date=d8, flag="买入")
                if df is not None and not df.empty:
                    r = df.iloc[0]
                    record = {
                        "date": resolved,
                        "code": code,
                        "name": str(r.get("名称", "")),
                        "close": r.get("收盘价"),
                        "change_pct": _format_pct(r.get("涨跌幅")),
                        "net_buy": _yuan_to_wan(r.get("净买额")),
                        "buy_amount": _yuan_to_wan(r.get("买入额")),
                        "sell_amount": _yuan_to_wan(r.get("卖出额")),
                        "reason": str(r.get("解读", r.get("上榜原因", "")))[:80],
                        "seats": seats,
                    }
            except (TypeError, KeyError, AttributeError, IndexError):
                record = None

        if not record:
            return {
                "code": code,
                "date": resolved,
                "records": [],
                "source": None,
                "error": "该日未上榜或无数据",
            }
        record["source"] = source
        return {
            "code": code,
            "date": resolved,
            "records": [record],
            "source": source,
        }

    records: list[dict] = []
    err: str | None = None

    dates = _list_dates_eastmoney(code, limit)
    if dates:
        source = "eastmoney_dates"
        for d in dates:
            day_res = fetch_lhb_stock(code, d, history_days=0, limit=1)
            recs = day_res.get("records") or []
            if recs:
                records.append(recs[0])
    else:
        dates = _list_dates_akshare(code, limit)
        if dates:
            source = "akshare_dates"
            for d in dates:
                day_res = fetch_lhb_stock(code, d, history_days=0, limit=1)
                recs = day_res.get("records") or []
                if recs:
                    records.append(recs[0])

    if not records:
        try:
            records = _history_from_daily_scan(code, history_days)
            source = "eastmoney_scan"
        except Exception as e:
            err = str(e)[:120]

    if not records:
        try:
            from services.akshare_lazy import akshare as _ak

            df = _ak().stock_lhb_stock_statistic_em(symbol="近一月")
            if df is not None and not df.empty and "代码" in df.columns:
                sub = df[df["代码"].astype(str).str.zfill(6) == code]
                for _, r in sub.head(limit).iterrows():
                    records.append({
                        "date": str(r.get("最近上榜日", r.get("上榜日", "")))[:10],
                        "code": code,
                        "name": str(r.get("名称", "")),
                        "net_buy": _yuan_to_wan(r.get("龙虎榜净买额")),
                        "reason": "近一月统计",
                        "source": "akshare_stat",
                    })
                if records:
                    source = "akshare_stat"
        except Exception as e:
            err = err or str(e)[:120]

    return {
        "code": code,
        "records": records[:limit],
        "source": source if records else None,
        "error": err if not records else None,
    }


def dragon_tiger_board(code: str, report_date: str | None = None) -> dict:
    """兼容旧 astock_data 接口"""
    out = fetch_lhb_stock(code, report_date, limit=10)
    return {
        "code": out.get("code", code),
        "records": out.get("records", []),
        "source": out.get("source"),
        "error": out.get("error"),
    }
