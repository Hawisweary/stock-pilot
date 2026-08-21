"""Phase 2:券商买入评级质量建模。
2.1 sector-ETF 基准重算 + 中性/看空移出训练(仍作特征)
2.2 point-in-time 特征(实现里严防用"未实现收益"泄漏:业绩类特征只用 resolution_date<=T 的已实现事件)
2.3 LightGBM 双头(分类:是否跑赢行业;回归:12月超额)+ 时序CV + 增益/排列重要性 + 分层
"""
from __future__ import annotations
import warnings, time, sqlite3, sys, datetime as dt
warnings.filterwarnings("ignore")
sys.path.insert(0, "backend")
import config
import numpy as np, pandas as pd, yfinance as yf
import lightgbm as lgb
from sklearn.metrics import roc_auc_score
from sklearn.inspection import permutation_importance

HORIZON = 252
BULL = {"buy","strong buy","overweight","outperform","accumulate","add","positive",
"market outperform","sector outperform","conviction buy","top pick","long-term buy","outperformer","speculative buy"}
BEAR = {"sell","strong sell","underweight","underperform","reduce","negative",
"market underperform","sector underperform","underperformer"}
def gdir(g):
    g=(g or "").strip().lower()
    return "bull" if g in BULL else "bear" if g in BEAR else "neutral"

# ---- ticker -> sector ETF 反查(用户映射,去重) ----
SEC={
'XLK':['AAPL','MSFT','NVDA','AVGO','ADBE','CRM','ORCL','CSCO','ACN','IBM','TXN','QCOM','AMAT','INTC','NOW','INTU','MU','ADI'],
'XLF':['JPM','BAC','WFC','C','GS','MS','AXP','V','MA','PYPL','BLK','SCHW','SPGI','MCO','MET','PRU','BK','CME','ICE','AJG','MMC','AON'],
'XLV':['JNJ','UNH','LLY','MRK','ABBV','TMO','ABT','DHR','PFE','BMY','AMGN','GILD','CVS','CI','MCK','ZTS','REGN','BDX','BSX','SYK','ISRG','MDT'],
'XLE':['XOM','CVX','COP','EOG','SLB','OXY','KMI','HES','DVN','FANG','HAL','BKR'],
'XLY':['AMZN','HD','MCD','NKE','SBUX','TGT','LOW','BKNG','TJX','CMG','MAR','HLT','ROST','ORLY','AZO','GPC','GM','F'],
'XLP':['PG','KO','PEP','WMT','COST','PM','MO','MDLZ','CL','KMB','SYY','GIS','STZ','KHC','ADM','MNST','CHD','CLX','EL','HSY'],
'XLI':['CAT','GE','HON','LMT','BA','RTX','UNP','UPS','FDX','DE','MMM','EMR','ETN','ITW','CMI','CARR','NOC','GD','LHX','PCAR'],
'XLU':['NEE','DUK','SO','D','AEP','EXC','SRE','PEG','XEL','ES','ETR','AEE'],
'XLRE':['AMT','PLD','CCI','EQIX','PSA','DLR','SPG','WELL','AVB','EQR','O','VTR'],
'XLC':['GOOGL','GOOG','META','NFLX','DIS','CMCSA','T','VZ','TMUS','CHTR'],
}
T2E={}
for etf,lst in SEC.items():
    for t in lst: T2E.setdefault(t,etf)
ETFS=list(SEC.keys())

# ---- 读事件(用 Phase1 已入库的原始字段) ----
c=sqlite3.connect(config.US_DB_PATH)
ev=pd.read_sql("SELECT ticker,firm,event_date,grade,action,target_price FROM us_rating_events",c)
c.close()
ev["event_date"]=pd.to_datetime(ev["event_date"])
tickers=sorted(ev.ticker.unique())
print(f"事件 {len(ev)} · 股票 {len(tickers)}")

print("① 下载价格(股票+行业ETF+SPY+VIX)...")
allt=sorted(set(tickers)|set(ETFS)|{"SPY","^VIX"})
raw=yf.download(allt, period="max", auto_adjust=False, group_by="ticker", threads=True, progress=False)
def ser(tk):
    try: sub=raw[tk]
    except (KeyError,TypeError): return None
    s=sub[["Close","Adj Close"]].dropna()
    if s.empty: return None
    s.index=pd.to_datetime(s.index); return s
PX={tk:ser(tk) for tk in allt}; PX={k:v for k,v in PX.items() if v is not None}
vix=PX.get("^VIX")
print(f"   有价 {len(PX)}/{len(allt)}")

def adj_ret(tk, d0, d1):
    s=PX.get(tk)
    if s is None: return None
    i0=s.index.searchsorted(d0); i1=s.index.searchsorted(d1)
    if i0>=len(s.index) or i1>=len(s.index): return None
    a0=s.iloc[i0]["Adj Close"]; a1=s.iloc[i1]["Adj Close"]
    return (a1/a0-1) if a0>0 else None

def vol_mom(tk, d, win):
    s=PX.get(tk)
    if s is None: return None,None
    i=s.index.searchsorted(d)
    if i<win or i>=len(s.index): return None,None
    seg=s.iloc[i-win:i]["Adj Close"].values
    rets=np.diff(np.log(seg))
    vol=float(np.std(rets)*np.sqrt(252)) if len(rets)>2 else None
    mom=float(seg[-1]/seg[0]-1)
    return vol,mom

print("② 计算标签(sector-ETF 超额)+ 逐事件情境特征 ...")
ev=ev.sort_values("event_date").reset_index(drop=True)
recs=[]
for r in ev.itertuples(index=False):
    s=PX.get(r.ticker)
    if s is None: continue
    i0=s.index.searchsorted(r.event_date)
    iH=i0+HORIZON
    if iH>=len(s.index): continue
    d0=s.index[i0]; dH=s.index[iH]; close0=s.iloc[i0]["Close"]; adj0=s.iloc[i0]["Adj Close"]
    if adj0<=0 or close0<=0: continue
    etf=T2E.get(r.ticker,"SPY")
    sret=adj_ret(r.ticker,d0,dH); bret=adj_ret(etf,d0,dH)
    if sret is None or bret is None: continue
    excess=sret-bret
    d=gdir(r.grade)
    dir_ok = 1 if excess>0 else 0   # 相对行业基准是否跑赢(买入视角)
    tgt=r.target_price; terr=None
    if tgt and tgt>0:
        terr=(tgt*(adj0/close0)-s.iloc[iH]["Adj Close"])/adj0
    tp=(tgt/close0-1) if (tgt and close0>0) else None
    v60,_=vol_mom(r.ticker,d0,60)
    _,sm20=vol_mom(r.ticker,d0,20); _,spym=vol_mom("SPY",d0,20); _,secm=vol_mom(etf,d0,20)
    vx=None
    if vix is not None:
        iv=vix.index.searchsorted(d0)
        if iv<len(vix.index): vx=float(vix.iloc[iv]["Adj Close"])
    recs.append(dict(ticker=r.ticker,firm=r.firm,event_date=d0,orig_date=r.event_date,res_date=dH,grade=r.grade,dclass=d,
        excess=excess,dir_ok=dir_ok,terr=terr,is_sell=1 if d=="bear" else 0,is_bull=1 if d=="bull" else 0,
        target_premium=tp, upgrade=1 if str(r.action).lower()=="up" else 0,
        vol60=v60, mom20=(sm20-spym) if (sm20 is not None and spym is not None) else None,
        secmom20=secm, vix=vx))
D=pd.DataFrame(recs).sort_values("event_date").reset_index(drop=True)
print(f"   有标签事件 {len(D)}(sector口径)")

print("③ point-in-time 券商特征(业绩类只用已实现事件, 防泄漏)...")
from collections import defaultdict
fh=defaultdict(list)   # firm -> [(res_date,event_date,excess,dir_ok,terr,is_sell)]
fth=defaultdict(list)  # (firm,ticker) -> 同上
def wstats(hist, T, days, need_resolved=True):
    lo=T-pd.Timedelta(days=days)
    ex=[];di=[];te=[]
    for res,evd,e,d_,t_,sl in hist:
        if need_resolved and res>T: continue      # 未实现,不能用
        ref=res if need_resolved else evd
        if ref< lo or ref>T: continue
        ex.append(e); di.append(d_);
        if t_ is not None: te.append(t_)
    return ex,di,te
def act_ratio(hist, T, days):  # 活跃度/看空占比:用 event_date(评级当日已知,不泄漏)
    lo=T-pd.Timedelta(days=days); n=0; sells=0
    for res,evd,e,d_,t_,sl in hist:
        if evd< lo or evd>T: continue
        n+=1; sells+=sl
    return n, (sells/n if n else None)

feat_rows=[]
for r in D.itertuples(index=False):
    T=r.event_date
    gex,gdi,gte=wstats(fh[r.firm],T,365)
    gex2,_,_=wstats(fh[r.firm],T,730)
    tex,tdi,_=wstats(fth[(r.firm,r.ticker)],T,365)
    n12,sr12=act_ratio(fh[r.firm],T,365)
    n3t,_=act_ratio(fth[(r.firm,r.ticker)],T,90)
    sharpe=(np.mean(gex2)/(np.std(gex2)+1e-9)) if len(gex2)>=5 else None
    feat_rows.append(dict(
        firm_g_excess_12m=np.mean(gex) if gex else None,
        firm_g_acc_12m=np.mean(gdi) if gdi else None,
        firm_g_bias_12m=np.mean(gte) if gte else None,
        firm_g_sharpe=sharpe,
        firm_g_events_12m=n12, firm_g_sell_ratio=sr12,
        firm_t_excess_6m=np.mean(tex) if tex else None,
        firm_t_acc_12m=np.mean(tdi) if tdi else None,
        firm_t_count_3m=n3t,
        target_premium=r.target_premium, upgrade=r.upgrade,
        vol60=r.vol60, mom20=r.mom20, secmom20=r.secmom20, vix=r.vix,
    ))
    # 加入历史(在算完特征之后 → 保证当前事件不看自己)
    rec=(r.res_date,r.event_date,r.excess,r.dir_ok,r.terr,r.is_sell)
    fh[r.firm].append(rec); fth[(r.firm,r.ticker)].append(rec)
F=pd.DataFrame(feat_rows)
D=D.drop(columns=[c for c in F.columns if c in D.columns]).reset_index(drop=True)
D=pd.concat([D,F],axis=1)

# ---- 只留买入事件做训练(看空/中性移出,但其历史已进特征) ----
train=D[D.is_bull==1].copy()
train["year"]=train.event_date.dt.year
FEATS=["firm_g_excess_12m","firm_g_acc_12m","firm_g_bias_12m","firm_g_sharpe","firm_g_events_12m",
"firm_g_sell_ratio","firm_t_excess_6m","firm_t_acc_12m","firm_t_count_3m","target_premium","upgrade",
"vol60","mom20","secmom20","vix"]
print(f"   买入训练样本 {len(train)} · 特征 {len(FEATS)}维")

print("④ LightGBM 时序CV(扩展窗口, 按年)...")
years=sorted(train.year.unique())
val_years=[y for y in years if y>=years[0]+3 and y<=max(years)]  # 前3年做初始训练
oof=[]
gain_imp=np.zeros(len(FEATS))
nfold=0
for vy in val_years:
    tr=train[train.year<vy]; va=train[train.year==vy]
    if len(tr)<500 or len(va)<50: continue
    Xtr,Xva=tr[FEATS],va[FEATS]
    # Head1 分类:跑赢行业
    clf=lgb.LGBMClassifier(n_estimators=300,learning_rate=0.03,num_leaves=31,
        subsample=0.8,colsample_bytree=0.8,min_child_samples=40,reg_lambda=1.0,verbose=-1)
    clf.fit(Xtr,tr.dir_ok)
    p=clf.predict_proba(Xva)[:,1]
    # Head2 回归:12月超额
    reg=lgb.LGBMRegressor(n_estimators=300,learning_rate=0.03,num_leaves=31,
        subsample=0.8,colsample_bytree=0.8,min_child_samples=40,reg_lambda=1.0,verbose=-1)
    reg.fit(Xtr,tr.excess)
    pr=reg.predict(Xva)
    gain_imp+=clf.booster_.feature_importance(importance_type="gain"); nfold+=1
    o=va[["event_date","orig_date","firm","ticker","dir_ok","excess","vix"]].copy(); o["p"]=p; o["pred_excess"]=pr; o["year"]=vy
    oof.append(o)
OOF=pd.concat(oof)
print(f"   折数 {nfold} · OOF样本 {len(OOF)} · 验证年 {sorted(OOF.year.unique())}")

print("\n"+"="*58+"\n              PHASE 2 结果\n"+"="*58)
auc=roc_auc_score(OOF.dir_ok,OOF.p)
ic=np.corrcoef(OOF.pred_excess,OOF.excess)[0,1]
ric=pd.Series(OOF.pred_excess).corr(pd.Series(OOF.excess.values),method="spearman")
base=OOF.dir_ok.mean()
print(f"[分类] OOF AUC = {auc:.3f}   (>0.58 有产品价值; 基准跑赢率 {base*100:.1f}%)")
print(f"[回归] IC = {ic:.3f}   RankIC = {ric:.3f}")
# 分层:按预测概率十分位看实际超额
OOF["dec"]=pd.qcut(OOF.p,10,labels=False,duplicates="drop")
g=OOF.groupby("dec").agg(n=("excess","size"),实际超额=("excess","mean"),跑赢率=("dir_ok","mean"))
print("\n[分层] 按模型置信度十分位(D9=最高):")
for d,row in g.iterrows():
    print(f"   D{int(d)}: n={int(row.n):4d}  实际平均超额 {row.实际超额*100:+6.1f}%  跑赢行业 {row.跑赢率*100:4.0f}%")
top=g.loc[g.index.max()]; bot=g.loc[g.index.min()]
print(f"\n[多空] Top10%超额 {top.实际超额*100:+.1f}%  −  Bottom10%超额 {bot.实际超额*100:+.1f}%  =  价差 {(top.实际超额-bot.实际超额)*100:+.1f}%")

gain_imp/=max(nfold,1)
imp=pd.Series(gain_imp,index=FEATS).sort_values(ascending=False)
print("\n[增益重要性 TOP12]:")
for k,v in imp.head(12).items(): print(f"   {k:22s} {v:10.0f}")

# 排列重要性(最后一折验证集,更抗过拟合的诚实度量)
if nfold>0:
    lastvy=sorted(OOF.year.unique())[-1]
    tr=train[train.year<lastvy]; va=train[train.year==lastvy]
    clf=lgb.LGBMClassifier(n_estimators=300,learning_rate=0.03,num_leaves=31,subsample=0.8,
        colsample_bytree=0.8,min_child_samples=40,reg_lambda=1.0,verbose=-1).fit(tr[FEATS],tr.dir_ok)
    pi=permutation_importance(clf,va[FEATS],va.dir_ok,n_repeats=8,random_state=0,scoring="roc_auc")
    pis=pd.Series(pi.importances_mean,index=FEATS).sort_values(ascending=False)
    print(f"\n[排列重要性 TOP8 @验证{lastvy}, ΔAUC]:")
    for k,v in pis.head(8).items(): print(f"   {k:22s} {v:+.4f}")

# ============ 上线前安检:top-decile 跨市场稳定性 ============
thr=OOF.p.quantile(0.9)
TOP=OOF[OOF.p>=thr].copy()
ETF_OF={t:e for e,lst in SEC.items() for t in lst}
TOP["sector"]=TOP.ticker.map(lambda t:ETF_OF.get(t,"SPY"))
print("\n"+"="*58+f"\n     安检:高置信买入(top10%, n={len(TOP)}) 稳定性\n"+"="*58)

print("\n[A] 按年份(固定全局 top10% 阈值):")
ya=TOP.groupby("year").agg(n=("excess","size"),超额=("excess","mean"),胜率=("dir_ok","mean"))
neg=0
for y,r in ya.iterrows():
    flag="" if (r.超额>0 and r.胜率>0.5) else "  ⚠"
    if not (r.超额>0 and r.胜率>0.5): neg+=1
    print(f"   {int(y)}: n={int(r.n):4d}  超额 {r.超额*100:+6.1f}%  胜率 {r.胜率*100:4.0f}%{flag}")
print(f"   → 崩盘年数(超额≤0或胜率≤50%): {neg}/{len(ya)}")

print("\n[B] 按 VIX 分位(每组内取各自 top10%):")
OOF2=OOF.dropna(subset=["vix"]).copy()
OOF2["vgrp"]=pd.qcut(OOF2.vix,3,labels=["低波动","中","高波动"])
for g in ["低波动","中","高波动"]:
    sub=OOF2[OOF2.vgrp==g]
    t=sub[sub.p>=sub.p.quantile(0.9)]
    vlo,vhi=sub.vix.min(),sub.vix.max()
    flag="" if (t.excess.mean()>0 and t.dir_ok.mean()>0.5) else "  ⚠"
    print(f"   {g}(VIX {vlo:.0f}-{vhi:.0f}): n={len(t):4d}  超额 {t.excess.mean()*100:+6.1f}%  胜率 {t.dir_ok.mean()*100:4.0f}%{flag}")

print("\n[C] 按行业(固定全局 top10% 阈值):")
sa=TOP.groupby("sector").agg(n=("excess","size"),超额=("excess","mean"),胜率=("dir_ok","mean")).sort_values("n",ascending=False)
for s,r in sa.iterrows():
    if r.n<20: continue
    flag="" if (r.超额>0 and r.胜率>0.5) else "  ⚠"
    print(f"   {s}: n={int(r.n):4d}  超额 {r.超额*100:+6.1f}%  胜率 {r.胜率*100:4.0f}%{flag}")
print("="*58)

# ============ 内部归档(仅研究,前端严禁引用)============
print("\n⑤ 内部归档落库(INTERNAL_RESEARCH_ONLY, UI 不引用)...")
OOF=OOF.copy(); OOF["ml_high_conviction"]=(OOF.p>=OOF.p.quantile(0.9)).astype(int)
conn=sqlite3.connect(config.US_DB_PATH,timeout=60)
out=OOF[["orig_date","event_date","firm","ticker","p","pred_excess","dir_ok","excess","year","ml_high_conviction"]].copy()
out["orig_date"]=out.orig_date.astype(str).str[:10]; out["event_date"]=out.event_date.astype(str).str[:10]
conn.execute("DROP TABLE IF EXISTS us_rating_ml_oof_internal")
out.to_sql("us_rating_ml_oof_internal",conn,index=False)
# us_rating_events 加内部标记列并精确回填(按 firm+ticker+原始GradeDate)
cur=conn.cursor()
cols={r[1] for r in cur.execute("PRAGMA table_info(us_rating_events)")}
if "ml_high_conviction" not in cols:
    cur.execute("ALTER TABLE us_rating_events ADD COLUMN ml_high_conviction INT DEFAULT 0")
cur.execute("UPDATE us_rating_events SET ml_high_conviction=0")
nbf=0
for r in out[out.ml_high_conviction==1].itertuples(index=False):
    cur.execute("UPDATE us_rating_events SET ml_high_conviction=1 WHERE ticker=? AND firm=? AND event_date=?",
                (r.ticker,r.firm,r.orig_date)); nbf+=cur.rowcount
conn.commit()
n_int=conn.execute("SELECT COUNT(*) FROM us_rating_ml_oof_internal").fetchone()[0]
n_hc=conn.execute("SELECT COUNT(*) FROM us_rating_events WHERE ml_high_conviction=1").fetchone()[0]
conn.close()
print(f"   us_rating_ml_oof_internal: {n_int} 行  |  us_rating_events.ml_high_conviction=1: {n_hc} 行(回填{nbf})")
print("   ⚠ 两处均为内部研究产物,api/前端不查询,不对外暴露。")
