from config import DB_PATH

"""股票研报 API — 生成单只股票完整分析报告"""
from fastapi import APIRouter, HTTPException
from fastapi.responses import HTMLResponse
from api_utils import execute_sql
import sqlite3, json
from datetime import date

router = APIRouter(prefix="/api/stocks", tags=["report"])


def _safe_query(conn, sql: str, params=()):
    try:
        return conn.execute(sql, params).fetchone()
    except sqlite3.OperationalError:
        return None


def _safe_query_all(conn, sql: str, params=()):
    try:
        return conn.execute(sql, params).fetchall()
    except sqlite3.OperationalError:
        return []


def _fetch_all_data(stock_id: int) -> dict:
    conn = sqlite3.connect(DB_PATH); conn.row_factory = sqlite3.Row

    result = {}

    stock = conn.execute("SELECT * FROM stocks WHERE id=?", (stock_id,)).fetchone()
    if not stock:
        conn.close()
        return {"error": "stock not found"}
    result["stock"] = dict(stock)

    cs = _safe_query(
        conn,
        "SELECT * FROM comprehensive_scores WHERE stock_id=? ORDER BY calc_date DESC LIMIT 1",
        (stock_id,),
    )
    result["comprehensive"] = dict(cs) if cs else None

    fs = _safe_query(
        conn,
        "SELECT * FROM factor_scores WHERE stock_id=? ORDER BY calc_date DESC LIMIT 1",
        (stock_id,),
    )
    result["fundamental"] = dict(fs) if fs else None

    tc = _safe_query(
        conn,
        "SELECT * FROM tech_analysis_cache WHERE stock_id=? ORDER BY created_at DESC LIMIT 1",
        (stock_id,),
    )
    if tc:
        t = dict(tc)
        if t.get("breakdown_json"):
            try: t["breakdown"] = json.loads(t["breakdown_json"])
            except: pass
        result["technical"] = t

    # 新闻面
    news = _safe_query_all(
        conn,
        "SELECT title, ai_summary as summary, sentiment_score, pub_date FROM stock_news WHERE stock_id=? ORDER BY pub_date DESC LIMIT 10",
        (stock_id,),
    )
    result["news"] = [dict(n) for n in news]

    for label, table in [("capital","capital_scores"),("policy","policy_scores"),("sentiment","sentiment_scores"),("valuation","valuation_scores")]:
        row = _safe_query(conn, f"SELECT * FROM {table} WHERE stock_id=? ORDER BY date DESC LIMIT 1", (stock_id,))
        if row:
            d = dict(row)
            if d.get("breakdown_json"):
                try: d["breakdown"] = json.loads(d["breakdown_json"])
                except: pass
            result[label] = d

    # 多周期
    mc = _safe_query(conn, "SELECT * FROM multicyc_scores WHERE stock_id=? ORDER BY date DESC LIMIT 1", (stock_id,))
    result["multicyc"] = dict(mc) if mc else None

    q = _safe_query(
        conn,
        "SELECT * FROM stock_daily_quotes WHERE stock_id=? ORDER BY trade_date DESC LIMIT 1",
        (stock_id,),
    )
    result["quote"] = dict(q) if q else None

    conn.close()
    return result


def _build_report_html(data: dict) -> str:
    s = data["stock"]
    comp = data.get("comprehensive") or {}
    fund = data.get("fundamental") or {}
    tech = data.get("technical") or {}
    quote = data.get("quote") or {}

    def score_row(label, score, w=""):
        if score is None: return ""
        color = "green" if score>=70 else ("orange" if score>=40 else "red")
        return f'<tr><td class="label">{label}</td><td style="color:{color};font-weight:bold">{score}</td><td class="w">{w}</td></tr>'

    rows = ""
    for label, key, w in [
        ("基本面", comp.get("fundamental_score"), "28%"),
        ("技术面", comp.get("technical_score"), "18%"),
        ("新闻面", comp.get("sentiment_score", comp.get("news_score")), "8%"),
        ("资金面", comp.get("capital_score"), "18%"),
        ("政策面", comp.get("policy_score"), "8%"),
        ("情绪面", comp.get("mood_score"), "8%"),
        ("估值面", comp.get("val_score"), "12%"),
    ]:
        rows += score_row(label, key, w)

    # 新闻列表
    news_html = ""
    for n in data.get("news", [])[:5]:
        news_html += f'<li>{n["pub_date"]}: {n["title"]} (情感{n.get("sentiment_score","?")})</li>'

    # 技术面信号
    tech_signals = ""
    if tech.get("breakdown"):
        b = tech["breakdown"]
        tech_signals = f"""
        <div class="grid">
            <div class="item">RSI14: {b.get('rsi14','?')}</div>
            <div class="item">KDJ-K: {b.get('kdj_k','?')}</div>
            <div class="item">MACD: {b.get('macd_signal','?')}</div>
        </div>"""

    # 多周期
    multicyc_html = ""
    if data.get("multicyc"):
        m = data["multicyc"]
        multicyc_html = f"""
        <div class="card">
            <h3>📊 多周期分析</h3>
            <p>{m.get('signal','')}</p>
            <p>日线{m.get('daily',{}).get('trend','')} | 周线{m.get('weekly',{}).get('trend','')} | 月线{m.get('monthly',{}).get('trend','')}</p>
        </div>"""

    return f"""<!DOCTYPE html>
<html lang="zh">
<head>
<meta charset="utf-8">
<title>{s['code']} {s['name']} 投研报告</title>
<style>
*{{margin:0;padding:0;box-sizing:border-box}}
body{{font-family:-apple-system,BlinkMacSystemFont,sans-serif;max-width:800px;margin:0 auto;padding:20px;color:#333;line-height:1.6}}
h1{{font-size:24px;margin-bottom:4px}}
h2{{font-size:16px;margin:20px 0 8px;border-bottom:2px solid #7c3aed;padding-bottom:4px}}
h3{{font-size:14px;margin:8px 0}}
.subtitle{{color:#888;font-size:13px}}
.card{{background:#f8f9fa;border-radius:8px;padding:16px;margin:12px 0}}
.grid{{display:grid;grid-template-columns:repeat(auto-fit,minmax(120px,1fr));gap:8px}}
.item{{background:#fff;padding:8px;border-radius:4px;text-align:center}}
.item .val{{font-size:20px;font-weight:bold}}
.item .lbl{{font-size:11px;color:#888}}
table{{width:100%;border-collapse:collapse;margin:8px 0}}
td{{padding:6px 8px;border-bottom:1px solid #eee;font-size:13px}}
td.label{{width:80px;color:#666}}
td.w{{width:40px;color:#999;font-size:11px}}
.big{{font-size:32px;font-weight:bold}}
.red{{color:#dc2626}}
.green{{color:#16a34a}}
.orange{{color:#e67e22}}
.purple{{color:#7c3aed}}
.badge{{display:inline-block;padding:2px 8px;border-radius:10px;font-size:11px;margin:0 2px}}
.badge-green{{background:#dcfce7;color:#16a34a}}
.badge-red{{background:#fee2e2;color:#dc2626}}
ul{{padding-left:20px;font-size:13px}}
li{{margin:4px 0}}
.footer{{margin-top:30px;text-align:center;color:#999;font-size:11px;border-top:1px solid #eee;padding-top:10px}}
@media print{{body{{padding:10px}} .card{{break-inside:avoid}}}}
</style>
</head>
<body>
<h1>{s['code']} {s['name']}</h1>
<p class="subtitle">{s.get('industry','')} | {s.get('market','')} | 研报日期 {date.today()}</p>

<div class="card">
    <div class="big purple">{comp.get('composite_score','?')}</div>
    <div class="subtitle">综合评分 /100</div>
    <table>{rows}</table>
</div>

<h2>🏛️ 基本面</h2>
<div class="grid">
    <div class="item"><div class="val">{fund.get('composite_score','?')}</div><div class="lbl">综合</div></div>
    <div class="item"><div class="val">{fund.get('profitability_score','?')}</div><div class="lbl">盈利</div></div>
    <div class="item"><div class="val">{fund.get('growth_score','?')}</div><div class="lbl">成长</div></div>
    <div class="item"><div class="val">{fund.get('value_score','?')}</div><div class="lbl">价值</div></div>
    <div class="item"><div class="val">{fund.get('momentum_score','?')}</div><div class="lbl">动量</div></div>
    <div class="item"><div class="val">{fund.get('safety_score','?')}</div><div class="lbl">安全</div></div>
</div>

<h2>📈 技术面</h2>
<div class="grid">
    <div class="item"><div class="val">{tech.get('score','?')}</div><div class="lbl">综合</div></div>
    <div class="item"><div class="val">{tech.get('signal','?')}</div><div class="lbl">信号</div></div>
</div>
{tech_signals}
{multicyc_html}

<h2>💰 资金面</h2>
<div class="grid">
    <div class="item"><div class="val">{data.get('capital',{}).get('composite_score','?')}</div><div class="lbl">综合</div></div>
    <div class="item"><div class="val">{data.get('capital',{}).get('flow_score','?')}</div><div class="lbl">主力流向</div></div>
    <div class="item"><div class="val">{data.get('capital',{}).get('turnover_score','?')}</div><div class="lbl">换手率</div></div>
</div>

<h2>🏛️ 政策面</h2>
<div class="grid">
    <div class="item"><div class="val">{data.get('policy',{}).get('composite_score','?')}</div><div class="lbl">综合</div></div>
</div>

<h2>🌡️ 情绪面</h2>
<div class="grid">
    <div class="item"><div class="val">{data.get('sentiment',{}).get('composite_score','?')}</div><div class="lbl">综合</div></div>
</div>

<h2>💎 估值面</h2>
<div class="grid">
    <div class="item"><div class="val">{data.get('valuation',{}).get('composite_score','?')}</div><div class="lbl">综合</div></div>
    <div class="item"><div class="val">{data.get('valuation',{}).get('pe_score','?')}</div><div class="lbl">PE</div></div>
    <div class="item"><div class="val">{data.get('valuation',{}).get('pb_score','?')}</div><div class="lbl">PB</div></div>
</div>

<h2>📰 近期新闻</h2>
<ul>{news_html}</ul>

<h2>📊 行情</h2>
<p>最新收盘: ¥{quote.get('close','?')} | 涨跌: {quote.get('change_pct','?')}% | 换手: {quote.get('turnover','?')}%</p>

<p class="footer">AI 基本面研究员自动生成 | 仅供参考，不构成投资建议</p>
</body>
</html>"""


@router.get("/{stock_id}/report", response_class=HTMLResponse)
async def stock_report(stock_id: int):
    data = _fetch_all_data(stock_id)
    if "error" in data:
        raise HTTPException(status_code=404, detail="股票不存在")
    return HTMLResponse(_build_report_html(data))


@router.get("/{stock_id}/report/export", response_class=HTMLResponse)
async def export_stock_report(stock_id: int):
    """下载 HTML 研报（浏览器可打印为 PDF）"""
    data = _fetch_all_data(stock_id)
    if "error" in data:
        raise HTTPException(status_code=404, detail="股票不存在")
    code = data["stock"].get("code", str(stock_id))
    filename = f"{code}_research_report.html"
    return HTMLResponse(
        _build_report_html(data),
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )
