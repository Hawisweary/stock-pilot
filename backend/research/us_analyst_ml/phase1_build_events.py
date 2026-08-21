"""Phase 1:分析师(券商级)评级质量 —— 事件表 + 超额收益标签 + 体检。

数据源 yfinance(upgrades_downgrades 券商级,无个人 id)。标签用「超额收益 vs SPY」对抗牛市beta。
严格 point-in-time:标签只用事件日之后的价格,特征(Phase 2)只用之前。
"""
from __future__ import annotations
import warnings, time, sqlite3, sys, datetime as dt
warnings.filterwarnings("ignore")
sys.path.insert(0, "backend")
import config
import numpy as np
import pandas as pd
import yfinance as yf

SP100 = ["AAPL","MSFT","NVDA","AMZN","GOOGL","GOOG","META","TSLA","AVGO","JPM","LLY","V","XOM",
"UNH","MA","JNJ","COST","HD","PG","WMT","NFLX","BAC","ABBV","CRM","ORCL","CVX","KO","AMD","PEP",
"TMO","LIN","ADBE","MRK","CSCO","ACN","MCD","ABT","WFC","GE","PM","IBM","TXN","QCOM","DHR","VZ",
"INTU","CAT","AXP","NOW","MS","PFE","GS","RTX","AMGN","T","UNP","SPGI","NEE","LOW","BKNG","HON",
"COP","BLK","C","UPS","BA","SCHW","DE","AMAT","MDT","GILD","BMY","ADP","LMT","SBUX","MDLZ","CB",
"MMC","PLD","ISRG","SO","INTC","MO","DUK","REGN","BX","CI","TGT","PYPL","CVS","MU","EOG","SLB",
"AON","ZTS","FDX","CL","GM","F","EMR"]
BENCH = "SPY"
HORIZON = 252  # 交易日 ≈ 12 个月

BULL = {"buy","strong buy","overweight","outperform","accumulate","add","positive",
"market outperform","sector outperform","conviction buy","top pick","long-term buy","outperformer",
"speculative buy"}
BEAR = {"sell","strong sell","underweight","underperform","reduce","negative",
"market underperform","sector underperform","underperformer"}

def gdir(g):
    g=(g or "").strip().lower()
    if g in BULL: return "bull"
    if g in BEAR: return "bear"
    return "neutral"

print("① 下载价格(S&P100 + SPY, period=max, 复权用 Adj Close)...")
tks = SP100 + [BENCH]
raw = yf.download(tks, period="max", auto_adjust=False, group_by="ticker", threads=True, progress=False)

def series(tk):
    try:
        sub = raw[tk]
    except (KeyError, TypeError):
        return None
    s = sub[["Close","Adj Close"]].dropna()
    if s.empty: return None
    s.index = pd.to_datetime(s.index)
    return s

px = {tk: series(tk) for tk in tks}
px = {k:v for k,v in px.items() if v is not None}
print(f"   有价 {len(px)}/{len(tks)} 只")
bench = px.get(BENCH)
if bench is None:
    print("SPY 无价,退出"); sys.exit(1)

print("② 抓取 upgrades_downgrades(逐只,限速)...")
events = []
for i,tk in enumerate(SP100):
    if tk not in px: continue
    try:
        ud = yf.Ticker(tk).upgrades_downgrades
    except Exception:
        ud = None
    if ud is None or ud.empty:
        time.sleep(0.5); continue
    for idx,r in ud.iterrows():
        events.append({"ticker":tk,"firm":str(r.get("Firm","") or ""),
            "event_date":pd.to_datetime(idx),"grade":str(r.get("ToGrade","") or ""),
            "action":str(r.get("Action","") or ""),
            "target":r.get("currentPriceTarget"),"prior":r.get("priorPriceTarget")})
    time.sleep(0.5)
    if (i+1)%25==0: print(f"   {i+1}/100 ...")
print(f"   原始事件 {len(events)} 条")

print("③ 计算标签(超额收益 vs SPY, 252交易日)...")
def adj_at(s, d):  # 事件日当天或之后首个交易日的 (raw_close, adj_close, 位置)
    pos = s.index.searchsorted(d)
    if pos>=len(s.index): return None
    return s.iloc[pos]["Close"], s.iloc[pos]["Adj Close"], pos

def bench_ret(d0_ts, dH_ts):
    a=adj_at(bench,d0_ts);
    if a is None: return None
    p0=a[1]; posH=bench.index.searchsorted(dH_ts)
    if posH>=len(bench.index): return None
    pH=bench.iloc[posH]["Adj Close"]
    return pH/p0-1 if p0>0 else None

rows=[]
for e in events:
    s=px.get(e["ticker"])
    if s is None: continue
    a=adj_at(s, e["event_date"])
    if a is None: continue
    close0, adj0, pos0 = a
    posH=pos0+HORIZON
    if posH>=len(s.index): continue   # 未满12月 → 不可评
    adjH=s.iloc[posH]["Adj Close"]
    if adj0<=0 or adjH<=0 or close0<=0: continue
    dH_ts=s.index[posH]
    stock_ret=adjH/adj0-1
    br=bench_ret(e["event_date"], dH_ts)
    if br is None: continue
    excess=stock_ret-br
    d=gdir(e["grade"])
    if d=="bull": dir_ok=1 if excess>0 else 0
    elif d=="bear": dir_ok=1 if excess<0 else 0
    else: dir_ok=1 if abs(excess)<0.05 else 0
    tgt=e["target"]; terr=None; thit=None
    if tgt is not None and tgt>0:
        factor=adj0/close0
        tgt_adj=tgt*factor
        terr=(tgt_adj-adjH)/adj0
        thit=1 if adjH>=0.95*tgt_adj else 0
    rows.append((e["ticker"],e["firm"],e["event_date"].date().isoformat(),e["grade"],e["action"],d,
        float(tgt) if tgt is not None else None, float(close0), float(adjH),
        float(stock_ret),float(br),float(excess),
        float(terr) if terr is not None else None, int(dir_ok),
        int(thit) if thit is not None else None))
print(f"   可评事件(满12月) {len(rows)} 条")

print("④ 写入 us_rating_events ...")
c=sqlite3.connect(config.US_DB_PATH,timeout=60)
c.execute("DROP TABLE IF EXISTS us_rating_events")
c.execute("""CREATE TABLE us_rating_events(
 event_id INTEGER PRIMARY KEY AUTOINCREMENT, ticker TEXT, firm TEXT, event_date TEXT,
 grade TEXT, action TEXT, dir_class TEXT, target_price REAL, price_at_event REAL,
 adj_price_12m REAL, stock_ret_12m REAL, bench_ret_12m REAL, excess_return_12m REAL,
 target_error_pct REAL, direction_correct INT, target_hit INT)""")
c.executemany("""INSERT INTO us_rating_events
 (ticker,firm,event_date,grade,action,dir_class,target_price,price_at_event,adj_price_12m,
  stock_ret_12m,bench_ret_12m,excess_return_12m,target_error_pct,direction_correct,target_hit)
 VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""", rows)
c.commit()

print("\n"+"="*54+"\n           PHASE 1 体检报告\n"+"="*54)
df=pd.read_sql("SELECT * FROM us_rating_events", c)
# #1 规模
print(f"[1] 事件 {len(df)} · 券商 {df.firm.nunique()} 家 · 股票 {df.ticker.nunique()} 只")
# #2 raw 方向正确率
print(f"[2] 方向正确率(全体, 超额口径): {df.direction_correct.mean()*100:.1f}%")
# #3 按评级拆
for k,lab in [("bull","看涨"),("bear","看空"),("neutral","中性")]:
    sub=df[df.dir_class==k]
    if len(sub): print(f"[3] {lab}组: n={len(sub)}  方向正确率 {sub.direction_correct.mean()*100:.1f}%  平均超额 {sub.excess_return_12m.mean()*100:+.1f}%")
# #4 券商能力自相关(前半 vs 后半)
pairs=[]
for firm,g in df.groupby("firm"):
    if len(g)<20: continue
    g=g.sort_values("event_date")
    mid=len(g)//2
    a=g.iloc[:mid].direction_correct.mean(); b=g.iloc[mid:].direction_correct.mean()
    pairs.append((a,b))
if len(pairs)>=8:
    A=np.array([p[0] for p in pairs]); B=np.array([p[1] for p in pairs])
    corr=np.corrcoef(A,B)[0,1]
    print(f"[4] 券商能力自相关(前/后半段, {len(pairs)}家≥20事件): r = {corr:.3f}")
else:
    print(f"[4] 券商能力自相关: 样本券商不足({len(pairs)}家),无法可靠估计")
# #5 目标价误差 MAD
te=df.target_error_pct.dropna()
if len(te):
    med=te.median(); mad=(te-med).abs().median()
    print(f"[5] 目标价误差 target_error_pct: 中位 {med*100:+.1f}%  |  MAD {mad*100:.1f}%  |  中位绝对误差 {te.abs().median()*100:.1f}%  (n={len(te)})")
c.close()
print("="*54)
