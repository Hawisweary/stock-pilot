"""市场数据 API — 北向资金 / 行业板块排行 / 涨跌停统计 / 基本面补齐"""
import time, sqlite3, json, math, subprocess, os, re
from fastapi import APIRouter, Query
from config import DB_PATH

WESTOCK_SCRIPT = os.path.expanduser(
    "~/.workbuddy/plugins/marketplaces/cb_teams_marketplace/plugins/finance-data/skills/westock-data/scripts/index.js"
)

router = APIRouter(prefix="/api/market", tags=["market"])

_cache = {
    "northbound": {"time": 0, "data": None},
    "boards": {"time": 0, "data": None},
    "limit_stats": {"time": 0, "data": None},
}

# 申万一级行业板块代码映射（西筹代码 → 中文名）
SW1_SECTORS: list[dict] = [
    {"code": "pt01801010", "name": "农林牧渔"},
    {"code": "pt01801030", "name": "基础化工"},
    {"code": "pt01801040", "name": "钢铁"},
    {"code": "pt01801050", "name": "有色金属"},
    {"code": "pt01801080", "name": "电子"},
    {"code": "pt01801110", "name": "家用电器"},
    {"code": "pt01801120", "name": "食品饮料"},
    {"code": "pt01801130", "name": "纺织服饰"},
    {"code": "pt01801140", "name": "轻工制造"},
    {"code": "pt01801150", "name": "医药生物"},
    {"code": "pt01801160", "name": "公用事业"},
    {"code": "pt01801170", "name": "交通运输"},
    {"code": "pt01801180", "name": "房地产"},
    {"code": "pt01801200", "name": "商贸零售"},
    {"code": "pt01801210", "name": "社会服务"},
    {"code": "pt01801230", "name": "综合"},
    {"code": "pt01801710", "name": "建筑材料"},
    {"code": "pt01801720", "name": "建筑装饰"},
    {"code": "pt01801730", "name": "电力设备"},
    {"code": "pt01801740", "name": "国防军工"},
    {"code": "pt01801750", "name": "计算机"},
    {"code": "pt01801760", "name": "传媒"},
    {"code": "pt01801770", "name": "通信"},
    {"code": "pt01801780", "name": "银行"},
    {"code": "pt01801790", "name": "非银金融"},
    {"code": "pt01801880", "name": "汽车"},
    {"code": "pt01801890", "name": "机械设备"},
    {"code": "pt01801950", "name": "煤炭"},
    {"code": "pt01801960", "name": "石油石化"},
    {"code": "pt01801970", "name": "环保"},
    {"code": "pt01801980", "name": "美容护理"},
]


def _run_westock(args: list[str]) -> str:
    """调用 westock-data CLI，返回 stdout"""
    cmd = ["node", WESTOCK_SCRIPT] + args
    r = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
    return r.stdout


def _parse_quote_table(stdout: str) -> dict[str, dict]:
    """从 westock-data quote 的 Markdown 表格中解析板块行情"""
    boards: dict[str, dict] = {}
    lines = stdout.strip().split("\n")
    # 找到表头行和数据行
    header_idx = None
    for i, line in enumerate(lines):
        if line.strip().startswith("| code |"):
            header_idx = i
            break
    if header_idx is None:
        return boards

    headers = [h.strip() for h in lines[header_idx].split("|")[1:-1]]
    # 找关键列索引
    col_map = {}
    for col in ["code", "name", "change_percent", "price", "volume", "amount", "pe_ratio", "pb_ratio", "turnover_rate", "total_market_cap"]:
        try:
            col_map[col] = headers.index(col)
        except ValueError:
            pass

    for line in lines[header_idx + 2:]:
        line = line.strip()
        if not line or not line.startswith("|"):
            continue
        cols = [c.strip() for c in line.split("|")[1:-1]]
        if len(cols) < len(headers):
            continue
        code = cols[col_map.get("code", 0)]
        try:
            chg_pct = float(cols[col_map.get("change_percent", 0)]) if col_map.get("change_percent") is not None else 0
        except (ValueError, IndexError):
            chg_pct = 0
        boards[code] = {
            "code": code,
            "name": cols[col_map.get("name", 0)] if col_map.get("name") is not None else "",
            "change_pct": chg_pct,
            "price": _safe_float(cols[col_map["price"]]) if col_map.get("price") is not None else 0,
            "volume": _safe_float(cols[col_map["volume"]]) if col_map.get("volume") is not None else 0,
            "amount": _safe_float(cols[col_map["amount"]]) if col_map.get("amount") is not None else 0,
            "pe_ratio": _safe_float(cols[col_map["pe_ratio"]]) if col_map.get("pe_ratio") is not None else 0,
            "pb_ratio": _safe_float(cols[col_map["pb_ratio"]]) if col_map.get("pb_ratio") is not None else 0,
            "turnover_rate": _safe_float(cols[col_map["turnover_rate"]]) if col_map.get("turnover_rate") is not None else 0,
            "market_cap": _safe_float(cols[col_map["total_market_cap"]]) if col_map.get("total_market_cap") is not None else 0,
        }
    return boards


def _safe_float(v):
    try:
        f = float(v)
        return 0.0 if math.isnan(f) or math.isinf(f) else round(f, 2)
    except (ValueError, TypeError):
        return 0.0

@router.get("/indices/kline")
async def market_index_kline(
    code: str = "sh000001",
    period: str = "daily",
    days: int = 250,
    force: bool = Query(False, description="跳过 K 线缓存，强制拉取"),
):
    """单只大盘指数 K 线（日/周）+ 技术指标，Ashare"""
    from services.market_index import fetch_index_kline

    return fetch_index_kline(code, period=period, days=days, with_technical=True, force=force)


@router.get("/trade-calendar")
async def trade_calendar(
    year: int = Query(..., description="年份，如 2026"),
    month: int = Query(..., ge=1, le=12, description="月份 1-12"),
):
    """指定月份的 SSE 交易日历（含法定节假日/调休），本地 trade_calendar 表。"""
    from datetime import date, timedelta
    from services.trade_calendar import is_trading_day, next_trading_day

    start = date(year, month, 1)
    end = date(year + 1, 1, 1) if month == 12 else date(year, month + 1, 1)

    days = []
    d = start
    while d < end:
        days.append({"date": d.isoformat(), "is_open": is_trading_day(d)})
        d += timedelta(days=1)

    today = date.today()
    next_open = next_trading_day(today) if not is_trading_day(today) else today

    return {
        "year": year,
        "month": month,
        "days": days,
        "today": today.isoformat(),
        "today_is_open": is_trading_day(today),
        "next_open_date": next_open.isoformat(),
    }


@router.post("/sync-quotes")
async def sync_watchlist_quotes():
    """同步跟踪池日线至库内（腾讯源，补全最新交易日）"""
    from services.quote_sync import sync_active_stock_quotes

    return sync_active_stock_quotes()


@router.get("/indices")
async def market_indices(force: bool = Query(False, description="跳过指数摘要缓存，强制拉取")):
    """上证 / 沪深300 / 创业板指技术摘要（Ashare，约 90s 缓存）"""
    from services.market_index import fetch_market_index_snapshot, snapshot_to_api_payload

    try:
        snap = fetch_market_index_snapshot(force=force)
        return snapshot_to_api_payload(snap)
    except Exception as e:
        return {
            "updated_at": int(time.time()),
            "environment": "震荡",
            "environment_comment": str(e)[:80],
            "indices": [],
            "available": False,
            "error": str(e)[:120],
        }


@router.get("/northbound")
async def northbound_flow():
    """北向资金（AKShare/东财；当日为 0 时回退最近有效历史，5 分钟缓存）"""
    now = time.time()
    if now - _cache["northbound"]["time"] < 300 and _cache["northbound"]["data"]:
        return _cache["northbound"]["data"]

    try:
        from services.northbound_fetch import fetch_northbound

        result = fetch_northbound()
    except Exception as e:
        if _cache["northbound"]["data"]:
            result = _cache["northbound"]["data"]
        else:
            result = {
                "date": "",
                "net_inflow": 0,
                "cumulative": 0,
                "sh_inflow": 0,
                "sz_inflow": 0,
                "data_status": "unavailable",
                "note": str(e)[:120],
                "error": str(e)[:80],
            }

    _cache["northbound"] = {"time": now, "data": result}
    return result


@router.get("/capital-resonance")
async def capital_resonance(date: str | None = Query(None, description="YYYY-MM-DD，不传则取本地最新一日")):
    """三方资金共振（Alpha因子v1）：L2大单+龙虎榜+沪深股通同日同向净买入(≥2路)的稀疏高确信信号。"""
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row

    trade_date = date
    if not trade_date:
        row = conn.execute("SELECT MAX(trade_date) FROM capital_resonance_daily").fetchone()
        trade_date = row[0] if row else None

    if not trade_date:
        conn.close()
        return {"trade_date": None, "items": []}

    rows = conn.execute(
        """SELECT r.trade_date, r.resonance_count, r.tier, r.l2_net_amount,
                  r.lhb_net_buy, r.hsgt_net_amount, s.code, s.name
           FROM capital_resonance_daily r JOIN stocks s ON s.id = r.stock_id
           WHERE r.trade_date=? ORDER BY r.resonance_count DESC, r.l2_net_amount DESC""",
        (trade_date,),
    ).fetchall()
    conn.close()

    return {"trade_date": trade_date, "items": [dict(r) for r in rows]}


@router.get("/hsgt-top10")
async def hsgt_top10(date: str | None = Query(None, description="YYYY-MM-DD，不传则取本地最新一日")):
    """沪深股通当日十大成交股（Tushare hsgt_top10，本地缓存表）。"""
    import sqlite3
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row

    trade_date = date
    if not trade_date:
        row = conn.execute("SELECT MAX(trade_date) FROM hsgt_top10_daily").fetchone()
        trade_date = row[0] if row else None

    if not trade_date:
        conn.close()
        return {"trade_date": None, "sh": [], "sz": []}

    rows = conn.execute(
        """SELECT h.market_type, h.name, s.code, h.close, h.change, h.rank,
                  h.amount, h.net_amount, h.buy, h.sell
           FROM hsgt_top10_daily h JOIN stocks s ON s.id = h.stock_id
           WHERE h.trade_date=? ORDER BY h.market_type, h.rank""",
        (trade_date,),
    ).fetchall()
    conn.close()

    sh = [dict(r) for r in rows if r["market_type"] == "1"]
    sz = [dict(r) for r in rows if r["market_type"] == "3"]
    return {"trade_date": trade_date, "sh": sh, "sz": sz}


@router.get("/breadth")
async def market_breadth(days: int = Query(30, ge=1, le=250, description="创新高新低历史天数")):
    """市场广度：创20/60/120日新高新低个股数 + 沪深交易所市场总貌（AKShare）。"""
    import sqlite3
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row

    high_low = conn.execute(
        """SELECT trade_date, close, high20, low20, high60, low60, high120, low120
           FROM market_new_high_low_daily ORDER BY trade_date DESC LIMIT ?""",
        (days,),
    ).fetchall()
    summary = conn.execute(
        """SELECT trade_date, exchange, category, count, turnover, total_mv, circ_mv, pe_avg
           FROM market_summary_daily
           WHERE trade_date = (SELECT MAX(trade_date) FROM market_summary_daily)
           ORDER BY exchange, category""",
    ).fetchall()
    conn.close()

    return {
        "high_low": [dict(r) for r in reversed(high_low)],
        "summary": [dict(r) for r in summary],
    }


def _fetch_sw_boards_with_fallback(trade_date: str, max_back: int = 5) -> tuple[list[dict], str | None]:
    """按交易日回溯拉申万一级板块，避免当日 sw_daily 尚未发布时返回空并污染缓存。"""
    from services.tushare_adapter import _pro, _throttle, fetch_sw_l1_boards

    dates: list[str] = [trade_date]
    try:
        pro = _pro()
        _throttle()
        df = pro.trade_cal(exchange="SSE", end_date=trade_date, is_open="1")
        if df is not None and not df.empty:
            dates = sorted(df["cal_date"].astype(str).tolist())[-max_back:]
    except Exception:
        pass
    for d in reversed(dates):
        boards = fetch_sw_l1_boards(d)
        if boards:
            return boards, d
    return [], trade_date


@router.get("/boards")
async def industry_boards(force: bool = Query(False, description="跳过板块缓存，强制拉取")):
    """申万一级行业板块涨跌幅排行（Tushare Pro 官方数据为主，westock-data 兜底，
    盘中30秒缓存，收盘后缓存到下次开盘）"""
    now = time.time()
    from services.market_data_cache import TTL_BOARDS_SEC
    from services.trade_pricing import seconds_until_next_open

    ttl = seconds_until_next_open() or TTL_BOARDS_SEC
    cached = _cache["boards"]["data"]
    if (
        not force
        and cached
        and now - _cache["boards"]["time"] < ttl
        and cached.get("total", 0) > 0
        and cached.get("all_boards")
    ):
        return cached
    if force:
        _cache["boards"] = {"time": 0, "data": None}

    boards: list[dict] = []
    trade_date_used: str | None = None
    try:
        # 1. Tushare sw_daily 全市场申万一级指数（官方数据，主数据源）
        from services.tushare_adapter import latest_trading_date

        trade_date = latest_trading_date(time.strftime("%Y%m%d"))
        boards, trade_date_used = _fetch_sw_boards_with_fallback(trade_date or time.strftime("%Y%m%d"))
    except Exception:
        boards = []

    if not boards:
        # 2. Tushare 失败时兜底：westock-data 盘中实时报价
        try:
            codes = ",".join(s["code"] for s in SW1_SECTORS)
            quote_out = _run_westock(["quote", codes])
            quote_map = _parse_quote_table(quote_out)

            for sector in SW1_SECTORS:
                q = quote_map.get(sector["code"], {})
                boards.append({
                    "code": sector["code"],
                    "name": sector["name"],
                    "change_pct": q.get("change_pct", 0),
                    "price": q.get("price", 0),
                    "pe_ratio": q.get("pe_ratio", 0),
                    "pb_ratio": q.get("pb_ratio", 0),
                    "turnover_rate": q.get("turnover_rate", 0),
                    "market_cap": q.get("market_cap", 0),
                    "volume": q.get("volume", 0),
                    "amount": q.get("amount", 0),
                })
            if not any(b["price"] for b in boards):
                boards = []
        except Exception as e:
            return {"error": str(e), "detail": "板块数据获取失败（Tushare 与 westock 均不可用）", "degraded": True}

    try:
        # 按涨跌幅排序
        boards.sort(key=lambda x: x["change_pct"], reverse=True)
        top5 = boards[:5]
        bottom5 = boards[-5:][::-1]

        # 计算涨跌家数统计
        up_count = sum(1 for b in boards if b["change_pct"] > 0)
        down_count = sum(1 for b in boards if b["change_pct"] < 0)
        flat_count = sum(1 for b in boards if b["change_pct"] == 0)
        avg_chg = round(sum(b["change_pct"] for b in boards) / len(boards), 2) if boards else 0

        display_date = time.strftime("%Y-%m-%d")
        if trade_date_used and len(trade_date_used) == 8:
            display_date = f"{trade_date_used[:4]}-{trade_date_used[4:6]}-{trade_date_used[6:8]}"

        result = {
            "date": display_date,
            "total": len(boards),
            "up_count": up_count,
            "down_count": down_count,
            "flat_count": flat_count,
            "avg_change_pct": avg_chg,
            "top_gainers": top5,
            "top_losers": bottom5,
            "all_boards": boards,
        }
        if boards:
            _cache["boards"] = {"time": now, "data": result}
        return result
    except Exception as e:
        return {"error": str(e), "detail": "板块数据获取失败，westock-data 调用异常", "degraded": True}


@router.get("/limit-stats")
async def limit_stats(force: bool = Query(False, description="跳过涨跌停短缓存")):
    """涨跌停统计（基于腾讯实时行情，盘中45s缓存，收盘后缓存到下次开盘）"""
    from services.data_sources import is_at_limit_down, is_at_limit_up, tencent_quote
    from services.market_data_cache import TTL_LIMIT_STATS_SEC, cached_limit_stats
    from services.trade_pricing import seconds_until_next_open

    ttl = seconds_until_next_open() or TTL_LIMIT_STATS_SEC

    def _compute() -> dict:
        conn = sqlite3.connect(DB_PATH)
        stocks = conn.execute("SELECT id, code, name FROM stocks WHERE is_active=1").fetchall()
        conn.close()
        codes = [s[1] for s in stocks]
        quotes = tencent_quote(codes)

        limit_up_list: list[dict] = []
        limit_down_list: list[dict] = []
        up5_list: list[dict] = []
        down5_list: list[dict] = []

        for sid, code, name in stocks:
            q = quotes.get(code, {})
            price = q.get("price", 0) or 0
            last_close = q.get("last_close", 0) or 0
            change_pct = q.get("change_pct", 0) or 0
            if last_close <= 0:
                continue
            row = {
                "stock_id": sid,
                "code": code,
                "name": name or q.get("name") or code,
                "price": round(float(price), 2),
                "change_pct": round(float(change_pct), 2),
            }
            if is_at_limit_up(q):
                limit_up_list.append(row)
            elif is_at_limit_down(q):
                limit_down_list.append(row)
            if change_pct >= 5:
                up5_list.append(row)
            if change_pct <= -5:
                down5_list.append(row)

        limit_up_list.sort(key=lambda x: -x["change_pct"])
        limit_down_list.sort(key=lambda x: x["change_pct"])
        up5_list.sort(key=lambda x: -x["change_pct"])
        down5_list.sort(key=lambda x: x["change_pct"])

        return {
            "limit_up": len(limit_up_list),
            "limit_down": len(limit_down_list),
            "up_over_5pct": len(up5_list),
            "down_over_5pct": len(down5_list),
            "total": len(stocks),
            "limit_up_stocks": limit_up_list,
            "limit_down_stocks": limit_down_list,
            "up_over_5pct_stocks": up5_list,
            "down_over_5pct_stocks": down5_list,
            "cached": False,
            "cache_ttl_sec": ttl,
        }

    out = cached_limit_stats(force, _compute, ttl_sec=ttl)
    if not force:
        out = dict(out)
        out["cached"] = True
    return out


@router.get("/fundamentals/{stock_id}")
async def stock_fundamentals(stock_id: int):
    """基本面摘要（EPS/毛利率/净利率）"""
    # 优先从 factor_scores 的 score_detail_json 读
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    row = conn.execute("""
        SELECT score_detail_json FROM factor_scores
        WHERE stock_id=? ORDER BY calc_date DESC LIMIT 1
    """, (stock_id,)).fetchone()
    stock = conn.execute("SELECT code FROM stocks WHERE id=?", (stock_id,)).fetchone()
    conn.close()

    if not stock:
        raise __import__("fastapi").HTTPException(status_code=404, detail="stock not found")

    result = {"stock_id": stock_id, "code": stock[0]}

    if row and row[0]:
        detail = json.loads(row[0])
        quality = detail.get("quality", {}).get("raw", {})
        growth = detail.get("growth", {})
        result["roe"] = quality.get("roe")
        result["gm"] = quality.get("gm")     # 毛利率（可能None）
        result["nm"] = quality.get("nm")     # 净利率（可能None）
        result["revenue_cagr_3y"] = round(growth.get("revenue_cagr_3y", 0) * 100, 1) if growth.get("revenue_cagr_3y") else None
        result["profit_cagr_3y"] = round(growth.get("profit_cagr_3y", 0) * 100, 1) if growth.get("profit_cagr_3y") else None
    else:
        result["roe"] = None

    # 如果毛利率/EPS为空，尝试用AKShare补齐
    if result.get("gm") is None:
        try:
            import akshare as ak
            df = ak.stock_financial_abstract(stock=stock[0], indicator="按年度")
            if not df.empty:
                last = df.iloc[-1]
                result["gm"] = float(last.get("销售毛利率", 0))
                result["nm"] = float(last.get("销售净利率", 0))
                result["eps"] = float(last.get("基本每股收益", 0))
                result["roic"] = float(last.get("投入资本回报率ROIC", 0))
                result["source"] = "akshare_fallback"
            else:
                result["source"] = "factor_scores"
        except Exception as e:
            result["source"] = "factor_scores"
            result["fallback_error"] = str(e)[:50]
    else:
        result["source"] = "factor_scores"

    return result
