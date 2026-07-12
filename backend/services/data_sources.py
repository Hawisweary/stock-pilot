"""
增强数据源 - 整合多源直连API
"""
import json, re, time, socket
from services.http_client import get as http_get
from typing import Optional

# 全局超时保护，防止网络卡死
socket.setdefaulttimeout(8)

UA = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36"


# ============================================================
# 1. 腾讯财经 — 实时PE/PB/市值/换手率（解决估值分缺失）
# ============================================================

_TENCENT_QUOTE_CHUNK_SIZE = 200


def tencent_quote(codes: list[str]) -> dict[str, dict]:
    """
    批量获取腾讯财经实时行情（PE/PB/市值等）
    返回: {code: {name, price, pe_ttm, pb, mcap_yi, turnover_pct, ...}}

    大批量代码会按 200 个一组分批并发请求 —— 单个 GET 塞入几千个代码会
    让 URL 超过服务端接受长度，腾讯接口直接空响应且不报错，调用方拿到
    静默的空字典（曾导致涨跌停统计全市场统计恒为 0）。
    """
    if len(codes) > _TENCENT_QUOTE_CHUNK_SIZE:
        from concurrent.futures import ThreadPoolExecutor, as_completed

        chunks = [
            codes[i : i + _TENCENT_QUOTE_CHUNK_SIZE]
            for i in range(0, len(codes), _TENCENT_QUOTE_CHUNK_SIZE)
        ]
        result: dict[str, dict] = {}
        with ThreadPoolExecutor(max_workers=8) as pool:
            futures = [pool.submit(_tencent_quote_batch, c) for c in chunks]
            for fut in as_completed(futures):
                try:
                    result.update(fut.result())
                except Exception:
                    # 单批网络超时/异常不应打断整体请求 —— 缺这一批数据，
                    # 好过把调用方（涨跌停统计等）的整个请求打成 500。
                    continue
        return result
    return _tencent_quote_batch(codes)


def _tencent_quote_batch(codes: list[str]) -> dict[str, dict]:
    """单批（<=200个代码）腾讯行情请求，不做分片。"""
    prefixed = []
    for c in codes:
        # 北交所: 8/4 开头（原新三板精选层）+ 92 开头（2023起新股统一编号）
        # 必须在 "9 开头 → sh" 之前判断，否则 920xxx 会被误当成 sh 股票
        if c.startswith(("8", "4", "92", "93")):
            prefixed.append(f"bj{c}")
        elif c.startswith(("6", "9")):
            prefixed.append(f"sh{c}")
        else:
            prefixed.append(f"sz{c}")

    url = "https://qt.gtimg.cn/q=" + ",".join(prefixed)
    r = http_get(url, headers={"User-Agent": UA}, timeout=5)
    r.encoding = "gbk"
    data = r.text

    result = {}
    for line in data.strip().split(";"):
        if not line.strip() or "=" not in line or '"' not in line:
            continue
        vals = line.split('"')[1].split("~")
        if len(vals) < 53:
            continue
        key = line.split("=")[0].split("_")[-1]
        code = key[2:]
        result[code] = {
            "name": vals[1],
            "price": float(vals[3]) if vals[3] else 0,
            "last_close": float(vals[4]) if vals[4] else 0,
            "open": float(vals[5]) if vals[5] else 0,
            "change_amt": float(vals[31]) if vals[31] else 0,
            "change_pct": float(vals[32]) if vals[32] else 0,
            "high": float(vals[33]) if vals[33] else 0,
            "low": float(vals[34]) if vals[34] else 0,
            "amount_wan": float(vals[37]) if vals[37] else 0,
            "turnover_pct": float(vals[38]) if vals[38] else 0,
            "pe_ttm": float(vals[39]) if vals[39] else 0,
            "amplitude_pct": float(vals[43]) if vals[43] else 0,
            "mcap_yi": float(vals[44]) if vals[44] else 0,  # 总市值(亿)
            "float_mcap_yi": float(vals[45]) if vals[45] else 0,
            "pb": float(vals[46]) if vals[46] else 0,
            "limit_up": float(vals[47]) if vals[47] else 0,
            "limit_down": float(vals[48]) if vals[48] else 0,
            "vol_ratio": float(vals[49]) if vals[49] else 0,
            "pe_static": float(vals[52]) if vals[52] else 0,
        }
    return result


def is_at_limit_up(q: dict) -> bool:
    """按交易所涨跌停价判断（兼容主板10%/科创创业板20%/ST5%）。"""
    price = float(q.get("price") or 0)
    limit_up = float(q.get("limit_up") or 0)
    if price > 0 and limit_up > 0:
        return price >= limit_up * 0.998
    chg = float(q.get("change_pct") or 0)
    return chg >= 9.8


def is_at_limit_down(q: dict) -> bool:
    price = float(q.get("price") or 0)
    limit_down = float(q.get("limit_down") or 0)
    if price > 0 and limit_down > 0:
        return price <= limit_down * 1.002
    chg = float(q.get("change_pct") or 0)
    return chg <= -9.8


# ============================================================
# 2. 东财 push2 — 股票基本信息（中文名+行业+市值+流通股）
# ============================================================

def eastmoney_stock_info(code: str) -> dict:
    """
    东财个股基本面信息 — 比 akshare stock_individual_info_em 更稳定
    返回: {code, name, industry, mcap, float_mcap, total_shares, list_date}
    """
    market_code = 1 if code.startswith("6") else 0
    params = {
        "fltt": "2",
        "invt": "2",
        "fields": "f57,f58,f84,f85,f116,f117,f127,f189,f43,f20",
        "secid": f"{market_code}.{code}",
        "ut": "b2884a393a59ad64002292a3e90d46a5",
    }
    d: dict = {}
    for host in ("push2delay.eastmoney.com", "push2.eastmoney.com"):
        try:
            r = http_get(
                f"https://{host}/api/qt/stock/get",
                params=params,
                headers={"User-Agent": UA, "Referer": "https://quote.eastmoney.com/"},
                timeout=10,
            )
            d = r.json().get("data") or {}
            if d.get("f57"):
                break
        except Exception:
            continue
    return {
        "code": d.get("f57", ""),
        "name": d.get("f58", ""),
        "industry": d.get("f127", ""),
        "total_shares": d.get("f84", 0),
        "float_shares": d.get("f85", 0),
        "mcap": d.get("f116", 0),          # 总市值(元)
        "float_mcap": d.get("f117", 0),    # 流通市值(元)
        "list_date": str(d.get("f189", "")),
        "price": d.get("f43", 0),
        "pe_ttm": d.get("f163", 0) if d.get("f163") else 0,
    }


# ============================================================
# 3. 行业对比 — 东财行业板块排名
# ============================================================

def industry_comparison(top_n: int = 20) -> list[dict]:
    """全行业涨跌幅排名"""
    url = "https://push2.eastmoney.com/api/qt/clist/get"
    params = {
        "pn": "1", "pz": "100", "po": "1", "np": "1",
        "fltt": "2", "invt": "2",
        "fs": "m:90+t:2",
        "fields": "f2,f3,f4,f12,f13,f14,f104,f105,f128,f136,f140,f141,f207",
    }
    r = http_get(url, params=params, headers={"User-Agent": UA}, timeout=15)
    d = r.json()
    items = d.get("data", {}).get("diff", [])
    rows = []
    for i, item in enumerate(items):
        rows.append({
            "rank": i + 1,
            "name": item.get("f14", ""),
            "change_pct": item.get("f3", 0),
            "code": item.get("f12", ""),
            "up_count": item.get("f104", 0),
            "down_count": item.get("f105", 0),
            "leader": item.get("f140", ""),
            "leader_change": item.get("f136", 0),
        })
    return rows


# ============================================================
# 4. 资金面 — 东财 datacenter 统一查询
# ============================================================

DATACENTER_URL = "https://datacenter-web.eastmoney.com/api/data/v1/get"

def _eastmoney_datacenter(report_name: str, columns: str = "ALL",
                          filter_str: str = "", page_size: int = 50,
                          sort_columns: str = "", sort_types: str = "-1") -> list[dict]:
    """东财数据中心统一查询"""
    params = {
        "reportName": report_name, "columns": columns,
        "filter": filter_str, "pageNumber": "1", "pageSize": str(page_size),
        "sortColumns": sort_columns, "sortTypes": sort_types,
        "source": "WEB", "client": "WEB",
    }
    r = http_get(DATACENTER_URL, params=params, headers={"User-Agent": UA}, timeout=15)
    d = r.json()
    if d.get("result") and d["result"].get("data"):
        return d["result"]["data"]
    return []


def margin_trading(code: str, page_size: int = 30) -> list[dict]:
    """融资融券明细"""
    data = _eastmoney_datacenter(
        "RPTA_WEB_RZRQ_GGMX",
        filter_str=f'(SCODE="{code}")',
        page_size=page_size,
        sort_columns="DATE", sort_types="-1",
    )
    return [{
        "date": str(row.get("DATE", ""))[:10],
        "rzye": row.get("RZYE", 0),
        "rzmre": row.get("RZMRE", 0),
        "rqye": row.get("RQYE", 0),
    } for row in data]


def dividend_history(code: str, page_size: int = 10) -> list[dict]:
    """分红历史"""
    data = _eastmoney_datacenter(
        "RPT_SHAREBONUS_DET",
        filter_str=f'(SECURITY_CODE="{code}")',
        page_size=page_size,
        sort_columns="EX_DIVIDEND_DATE", sort_types="-1",
    )
    return [{
        "date": str(row.get("EX_DIVIDEND_DATE", ""))[:10],
        "bonus_rmb": row.get("PRETAX_BONUS_RMB", 0),
    } for row in data]


# ============================================================
# 5. 新闻 — 东财个股新闻
# ============================================================

def eastmoney_stock_news(code: str, page_size: int = 10) -> list[dict]:
    """东财个股新闻"""
    cb = "jQuery_news"
    url = "https://search-api-web.eastmoney.com/search/jsonp"
    inner = json.dumps({
        "uid": "", "keyword": code,
        "type": ["cmsArticleWebOld"],
        "client": "web", "clientType": "web", "clientVersion": "curr",
        "param": {"cmsArticleWebOld": {
            "searchScope": "default", "sort": "default",
            "pageIndex": 1, "pageSize": page_size, "preTag": "", "postTag": ""
        }},
    }, separators=(',', ':'))
    params = {"cb": cb, "param": inner}
    r = http_get(url, params=params, headers={"User-Agent": UA, "Referer": "https://so.eastmoney.com/"}, timeout=15)

    text = r.text
    try:
        json_str = text[text.index("(") + 1 : text.rindex(")")]
        d = json.loads(json_str)
        articles = d.get("result", {}).get("cmsArticleWebOld", {}).get("list", [])
        import re
        return [{
            "title": re.sub(r'<[^>]+>', '', a.get("title", "")),
            "content": re.sub(r'<[^>]+>', '', a.get("content", ""))[:200],
            "time": a.get("date", ""),
            "source": a.get("mediaName", ""),
        } for a in articles]
    except Exception:
        return []
