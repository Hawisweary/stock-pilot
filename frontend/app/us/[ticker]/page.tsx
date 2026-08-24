"use client";
import { useEffect, useState } from "react";
import { useParams, useRouter } from "next/navigation";
import { ArrowLeft } from "lucide-react";
import { KLineChart } from "@/components/KLineChart";
import { InfoDot } from "@/components/InfoDot";

type Bar = { trade_date: string; open: number; high: number; low: number; close: number; volume: number | null };
type Freq = "D" | "W" | "M" | "Y";
const FREQS: { id: Freq; label: string }[] = [
  { id: "D", label: "日K" }, { id: "W", label: "周K" }, { id: "M", label: "月K" }, { id: "Y", label: "年K" },
];
// 初始可视窗口(近端优先):日K≈半年 / 周K≈2.5年 / 月K≈8年 / 年K全部;更早可拖动缩放
const INIT_BARS: Record<Freq, number | undefined> = { D: 130, W: 130, M: 96, Y: undefined };

type Quote = { trade_date: string; close: number | null; volume: number | null };
type Detail = {
  ticker: string; name?: string | null; exchange?: string;
  quotes: Quote[];
  fundamentals?: { market_cap?: number | null; pe?: number | null; pb?: number | null; peg?: number | null; enterprise_val?: number | null; error?: string };
  profile?: {
    sector?: string | null; industry?: string | null; beta?: number | null; forward_pe?: number | null;
    market_cap?: number | null; trailing_pe?: number | null; pb?: number | null;
    div_yield?: number | null; wk52_high?: number | null; wk52_low?: number | null;
    target_mean?: number | null; target_high?: number | null; target_low?: number | null;
    recommendation?: string | null; num_analysts?: number | null;
    profit_margin?: number | null; roe?: number | null; rev_growth?: number | null; eps_growth?: number | null;
    eps?: number | null; employees?: number | null; error?: string;
  };
  analyst?: {
    targets?: { current?: number | null; high?: number | null; low?: number | null; mean?: number | null; median?: number | null } | null;
    distribution?: { period?: string; strong_buy?: number; buy?: number; hold?: number; sell?: number; strong_sell?: number } | null;
    actions?: {
      date: string; firm: string; action: string; to_grade: string; from_grade: string;
      target?: number | null; prior_target?: number | null;
    }[];
    error?: string;
  };
  accuracy?: {
    n: number; horizon_days: number; direction_pct: number; reach_pct: number;
    mape_pct: number; bias_pct: number; eval_from?: string; eval_to?: string;
    rating?: {
      horizon_days: number;
      bull_n?: number; bull_win_pct?: number; bull_avg_pct?: number;
      bear_n?: number; bear_win_pct?: number; bear_avg_pct?: number;
      spread_pct?: number;
    } | null;
  } | null;
  financials?: {
    annual?: { period: string; revenue?: number | null; net_income?: number | null; eps?: number | null }[];
    earnings?: { date: string; eps_est?: number | null; eps_actual?: number | null; surprise_pct?: number | null }[];
    next_earnings?: string | null;
  } | null;
  description?: string | null;
  cn_intro?: { what?: string; highlights?: string; financials?: string; valuation?: string; risk?: string } | null;
  cached_at?: string | null;
  error?: string;
};

// 美股惯例:涨绿跌红(与A股相反),与K线配色一致
const US_UP = "#22c55e";
const US_DOWN = "#ef4444";

const RATING_CN: Record<string, { label: string; color: string }> = {
  strong_buy: { label: "强烈买入", color: US_UP },
  buy: { label: "买入", color: US_UP },
  hold: { label: "持有", color: "var(--muted-foreground)" },
  underperform: { label: "减持", color: US_DOWN },
  sell: { label: "卖出", color: US_DOWN },
};
const pct = (v?: number | null) => (v == null ? "—" : `${(v * 100).toFixed(1)}%`);

const fmtCap = (v?: number | null) => {
  if (v == null) return "—";
  if (v >= 1e12) return `$${(v / 1e12).toFixed(2)}T`;
  if (v >= 1e9) return `$${(v / 1e9).toFixed(2)}B`;
  if (v >= 1e6) return `$${(v / 1e6).toFixed(1)}M`;
  return `$${v}`;
};
const fmtNum = (v?: number | null, d = 2) => (v == null ? "—" : v.toFixed(d));

type Extras = {
  estimates?: {
    periods?: { label: string; eps_avg?: number | null; eps_growth?: number | null; n?: number; rev_growth?: number | null }[];
    revisions?: { up_30d?: number; down_30d?: number } | null;
  } | null;
  holders?: { inst_pct?: number | null; insider_pct?: number | null; inst_count?: number | null;
    top?: { holder: string; pct?: number | null; chg?: number | null }[] } | null;
  insider?: { buy_shares?: number | null; sell_shares?: number | null; net_shares?: number | null } | null;
  actions?: { dividends?: { date: string; amount?: number | null }[]; splits?: { date: string; ratio?: number | null }[] } | null;
  news?: { title: string; publisher?: string | null; link?: string | null; date?: string }[] | null;
};

export default function UsDetailPage() {
  const { ticker } = useParams<{ ticker: string }>();
  const router = useRouter();
  const [d, setD] = useState<Detail | null>(null);
  const [loading, setLoading] = useState(true);
  const [freq, setFreq] = useState<Freq>("D");
  const [adjust, setAdjust] = useState(true);
  const [bars, setBars] = useState<Bar[] | null>(null);
  const [barsLoading, setBarsLoading] = useState(true);
  const [extras, setExtras] = useState<Extras | null>(null);

  useEffect(() => {
    setLoading(true);
    fetch(`/api/us/${ticker}`)
      .then((r) => r.json())
      .then(setD)
      .catch(() => setD({ error: "加载失败" } as Detail))
      .finally(() => setLoading(false));
    setExtras(null);
    fetch(`/api/us/${ticker}/extras`).then((r) => r.json()).then(setExtras).catch(() => setExtras(null));
  }, [ticker]);

  useEffect(() => {
    setBarsLoading(true);
    fetch(`/api/us/${ticker}/klines?freq=${freq}&adjust=${adjust}`)
      .then((r) => r.json())
      .then((j) => setBars(j.bars ?? []))
      .catch(() => setBars([]))
      .finally(() => setBarsLoading(false));
  }, [ticker, freq, adjust]);

  if (loading) return <div className="animate-pulse h-96 bg-muted rounded-lg" />;
  if (!d || d.error) return <div className="text-sm text-muted-foreground">未找到该美股（{d?.error ?? ticker}）</div>;

  const valid = d.quotes.filter((q) => q.close != null);
  const last = valid[valid.length - 1];
  const prev = valid[valid.length - 2];
  const chg = last?.close != null && prev?.close ? (last.close / prev.close - 1) * 100 : null;
  const f = d.fundamentals && !d.fundamentals.error ? d.fundamentals : null;
  const p = d.profile && !d.profile.error ? d.profile : null;
  const an = d.analyst && !d.analyst.error ? d.analyst : null;
  const dist = an?.distribution ?? null;
  const distTotal = dist ? (dist.strong_buy ?? 0) + (dist.buy ?? 0) + (dist.hold ?? 0) + (dist.sell ?? 0) + (dist.strong_sell ?? 0) : 0;
  const distSegs = dist ? [
    { key: "强买", n: dist.strong_buy ?? 0, color: "#16a34a" },
    { key: "买入", n: dist.buy ?? 0, color: "#4ade80" },
    { key: "持有", n: dist.hold ?? 0, color: "#9ca3af" },
    { key: "减持", n: dist.sell ?? 0, color: "#f87171" },
    { key: "卖出", n: dist.strong_sell ?? 0, color: "#dc2626" },
  ] : [];
  const actionCN: Record<string, string> = { init: "首次", main: "维持", reit: "重申", up: "上调", down: "下调" };

  const acc = d.accuracy ?? null;
  // 偏差判定:正=目标价系统性高于真实价(过度乐观);负=低于(过度保守)
  const biasVerdict = acc == null ? null
    : acc.bias_pct > 10 ? { label: "偏乐观", color: US_DOWN, note: "目标价系统性高于真实价" }
    : acc.bias_pct < -10 ? { label: "偏保守", color: US_UP, note: "目标价系统性低于真实价" }
    : { label: "较中性", color: "var(--muted-foreground)", note: "目标价与真实价大体相符" };
  const dirVerdict = acc == null ? null
    : acc.direction_pct >= 70 ? "方向可信"
    : acc.direction_pct >= 55 ? "方向一般"
    : "方向参考价值低";

  const Stat = ({ label, value }: { label: React.ReactNode; value: React.ReactNode }) => (
    <div className="rounded-xl border bg-card p-3">
      <div className="text-[11px] text-muted-foreground">{label}</div>
      <div className="text-lg font-bold tabular-nums">{value}</div>
    </div>
  );
  const Kv = ({ k, v }: { k: React.ReactNode; v: React.ReactNode }) => (
    <div className="flex justify-between gap-2 border-b border-border/40 py-0.5">
      <span className="text-muted-foreground">{k}</span>
      <span className="font-medium tabular-nums text-right truncate">{v}</span>
    </div>
  );

  return (
    <div className="space-y-4">
      <button onClick={() => router.push("/us")} className="flex items-center gap-1 text-xs text-muted-foreground hover:text-foreground">
        <ArrowLeft className="h-3.5 w-3.5" /> 返回美股列表
      </button>

      <div className="flex items-end justify-between flex-wrap gap-2">
        <div>
          <h1 className="text-xl font-bold">{d.name || d.ticker}</h1>
          <div className="text-xs text-muted-foreground">
            <span className="font-mono">{d.ticker} · {d.exchange}</span>
            {p?.sector && <span className="ml-2">· {p.sector}</span>}
          </div>
        </div>
        <div className="text-right">
          <div className="text-2xl font-bold tabular-nums">{last?.close != null ? `$${last.close.toFixed(2)}` : "—"}</div>
          <div className="text-sm font-medium tabular-nums" style={{ color: (chg ?? 0) > 0 ? US_UP : (chg ?? 0) < 0 ? US_DOWN : "var(--muted-foreground)" }}>
            {chg != null ? `${chg > 0 ? "+" : ""}${chg.toFixed(2)}%` : "—"}
            <span className="text-muted-foreground ml-1 font-normal">{last?.trade_date}</span>
          </div>
        </div>
      </div>

      <div className="grid grid-cols-2 md:grid-cols-4 gap-3">
        <Stat label={<>市值<InfoDot k="market_cap" /></>} value={fmtCap(f?.market_cap ?? p?.market_cap)} />
        <Stat label={<>PE<InfoDot k="pe" /></>} value={fmtNum(f?.pe ?? p?.trailing_pe)} />
        <Stat label={<>PB<InfoDot k="pb" /></>} value={fmtNum(f?.pb ?? p?.pb)} />
        <Stat label={<>PEG<InfoDot k="peg" /></>} value={fmtNum(f?.peg)} />
      </div>

      {p && (p.sector || p.target_mean != null) && (
        <div className="grid grid-cols-1 lg:grid-cols-3 gap-4">
          {/* 分析师 */}
          <div className="rounded-xl border bg-card p-4">
            <div className="flex items-baseline justify-between mb-2">
              <div className="text-sm font-semibold">分析师</div>
              {p.recommendation && (
                <span className="text-[11px] font-bold px-2 py-0.5 rounded text-white"
                  style={{ backgroundColor: RATING_CN[p.recommendation]?.color ?? "var(--muted-foreground)" }}>
                  {RATING_CN[p.recommendation]?.label ?? p.recommendation}
                </span>
              )}
            </div>
            {p.target_mean != null ? (
              <div className="space-y-1.5">
                <div className="flex items-baseline gap-2">
                  <span className="text-2xl font-bold tabular-nums">${p.target_mean.toFixed(0)}</span>
                  {last?.close != null && (
                    <span className="text-sm font-medium tabular-nums"
                      style={{ color: p.target_mean > last.close ? US_UP : US_DOWN }}>
                      {p.target_mean > last.close ? "+" : ""}{((p.target_mean / last.close - 1) * 100).toFixed(1)}% 空间
                    </span>
                  )}
                </div>
                <div className="text-xs text-muted-foreground">
                  目标区间 ${fmtNum(p.target_low, 0)}–${fmtNum(p.target_high, 0)} · {p.num_analysts ?? "?"} 位分析师
                </div>
              </div>
            ) : <div className="text-xs text-muted-foreground py-2">无分析师覆盖</div>}
          </div>

          {/* 质量 / 成长 */}
          <div className="lg:col-span-2 rounded-xl border bg-card p-4">
            <div className="text-sm font-semibold mb-2">质量 · 成长 · 风险</div>
            <div className="grid grid-cols-2 sm:grid-cols-4 gap-x-4 gap-y-2 text-xs">
              <Kv k="行业" v={p.industry || p.sector || "—"} />
              <Kv k={<>ROE<InfoDot k="roe" /></>} v={pct(p.roe)} />
              <Kv k="利润率" v={pct(p.profit_margin)} />
              <Kv k="营收增长" v={pct(p.rev_growth)} />
              <Kv k="盈利增长" v={pct(p.eps_growth)} />
              <Kv k={<>前瞻PE<InfoDot k="forward_pe" /></>} v={fmtNum(p.forward_pe)} />
              <Kv k={<>Beta<InfoDot k="beta" /></>} v={fmtNum(p.beta)} />
              <Kv k={<>股息率<InfoDot k="div_yield" /></>} v={p.div_yield != null ? `${p.div_yield.toFixed(2)}%` : "—"} />
              <Kv k="EPS" v={p.eps != null ? `$${p.eps.toFixed(2)}` : "—"} />
              <Kv k="员工" v={p.employees != null ? p.employees.toLocaleString() : "—"} />
              <Kv k="52周高" v={p.wk52_high != null ? `$${p.wk52_high.toFixed(0)}` : "—"} />
              <Kv k="52周低" v={p.wk52_low != null ? `$${p.wk52_low.toFixed(0)}` : "—"} />
            </div>
          </div>
        </div>
      )}

      {an && (distTotal > 0 || (an.actions && an.actions.length > 0)) && (
        <div className="rounded-xl border bg-card p-4 space-y-4">
          <div className="flex items-baseline justify-between">
            <div className="text-sm font-semibold">分析师详情</div>
            <div className="text-[11px] text-muted-foreground">
              数据源 Yahoo Finance 聚合 · 卖方分析师{d.cached_at ? ` · 更新 ${d.cached_at.slice(0, 10)}` : ""}
            </div>
          </div>

          {distTotal > 0 && (
            <div className="space-y-1.5">
              <div className="flex items-center justify-between text-xs text-muted-foreground">
                <span>评级分布</span>
                <span>{distTotal} 位分析师{an.targets?.median != null ? ` · 目标中位数 $${an.targets.median.toFixed(0)}` : ""}</span>
              </div>
              <div className="flex h-6 w-full overflow-hidden rounded-md">
                {distSegs.filter((s) => s.n > 0).map((s) => (
                  <div key={s.key} style={{ width: `${(s.n / distTotal) * 100}%`, backgroundColor: s.color }}
                    className="flex items-center justify-center text-[10px] font-medium text-white" title={`${s.key} ${s.n}`}>
                    {s.n / distTotal > 0.06 ? s.n : ""}
                  </div>
                ))}
              </div>
              <div className="flex flex-wrap gap-x-3 gap-y-1 text-[11px] text-muted-foreground">
                {distSegs.map((s) => (
                  <span key={s.key} className="flex items-center gap-1">
                    <span className="inline-block h-2 w-2 rounded-sm" style={{ backgroundColor: s.color }} />
                    {s.key} {s.n}
                  </span>
                ))}
              </div>
            </div>
          )}

          {an.actions && an.actions.length > 0 && (
            <div className="space-y-1">
              <div className="text-xs text-muted-foreground">近期评级动作</div>
              <div className="overflow-x-auto">
                <table className="w-full text-xs">
                  <thead className="text-[11px] text-muted-foreground">
                    <tr className="border-b border-border/60">
                      <th className="py-1 text-left font-medium">日期</th>
                      <th className="py-1 text-left font-medium">机构</th>
                      <th className="py-1 text-left font-medium">动作</th>
                      <th className="py-1 text-left font-medium">评级</th>
                      <th className="py-1 text-right font-medium">目标价</th>
                    </tr>
                  </thead>
                  <tbody>
                    {an.actions.map((a, i) => {
                      const up = a.target != null && a.prior_target != null && a.prior_target > 0 && a.target > a.prior_target;
                      const down = a.target != null && a.prior_target != null && a.prior_target > 0 && a.target < a.prior_target;
                      return (
                        <tr key={i} className="border-b border-border/30">
                          <td className="py-1 tabular-nums text-muted-foreground">{a.date}</td>
                          <td className="py-1 truncate max-w-[140px]">{a.firm || "—"}</td>
                          <td className="py-1 text-muted-foreground">{actionCN[a.action] ?? a.action}</td>
                          <td className="py-1">{a.to_grade || "—"}</td>
                          <td className="py-1 text-right tabular-nums font-medium"
                            style={{ color: up ? US_UP : down ? US_DOWN : undefined }}>
                            {a.target != null ? `$${a.target.toFixed(0)}` : "—"}
                            {up ? " ↑" : down ? " ↓" : ""}
                          </td>
                        </tr>
                      );
                    })}
                  </tbody>
                </table>
              </div>
            </div>
          )}
        </div>
      )}

      {acc && (
        <div className="rounded-xl border bg-card p-4 space-y-3">
          <div className="flex items-baseline justify-between flex-wrap gap-2">
            <div className="text-sm font-semibold">
              分析师准确度<InfoDot k="analyst_accuracy" />
              {biasVerdict && (
                <span className="ml-2 text-[11px] font-bold px-2 py-0.5 rounded"
                  style={{ backgroundColor: biasVerdict.color, color: "#fff" }}>
                  {biasVerdict.label}
                </span>
              )}
            </div>
            <div className="text-[11px] text-muted-foreground">
              回测 {acc.n} 条历史目标 · {Math.round(acc.horizon_days / 30)}个月前瞻 · {acc.eval_from}~{acc.eval_to}
            </div>
          </div>

          <div className="grid grid-cols-2 md:grid-cols-4 gap-3">
            <div className="rounded-lg border bg-background p-2.5">
              <div className="text-[11px] text-muted-foreground">方向准确率</div>
              <div className="text-lg font-bold tabular-nums">{acc.direction_pct}%</div>
              <div className="text-[10px] text-muted-foreground">{dirVerdict}</div>
            </div>
            <div className="rounded-lg border bg-background p-2.5">
              <div className="text-[11px] text-muted-foreground">12月内触及目标</div>
              <div className="text-lg font-bold tabular-nums">{acc.reach_pct}%</div>
              <div className="text-[10px] text-muted-foreground">目标价被摸到的比例</div>
            </div>
            <div className="rounded-lg border bg-background p-2.5">
              <div className="text-[11px] text-muted-foreground">平均误差 MAPE</div>
              <div className="text-lg font-bold tabular-nums">{acc.mape_pct}%</div>
              <div className="text-[10px] text-muted-foreground">越低越准</div>
            </div>
            <div className="rounded-lg border bg-background p-2.5">
              <div className="text-[11px] text-muted-foreground">系统性偏差</div>
              <div className="text-lg font-bold tabular-nums" style={{ color: biasVerdict?.color }}>
                {acc.bias_pct > 0 ? "+" : ""}{acc.bias_pct}%
              </div>
              <div className="text-[10px] text-muted-foreground">{biasVerdict?.note}</div>
            </div>
          </div>

          {acc.rating && (
            <div className="rounded-lg border bg-background p-3 space-y-2">
              <div className="flex items-baseline justify-between flex-wrap gap-2">
                <div className="text-xs font-semibold">评级方向验证 · 后 {Math.round((acc.rating.horizon_days) / 30)} 个月</div>
                {acc.rating.spread_pct != null && (
                  <div className="text-[11px]">
                    <span className="text-muted-foreground">多空价差 </span>
                    <span className="font-bold tabular-nums" style={{ color: acc.rating.spread_pct > 0 ? US_UP : US_DOWN }}>
                      {acc.rating.spread_pct > 0 ? "+" : ""}{acc.rating.spread_pct}%
                    </span>
                    <span className="text-muted-foreground">
                      {" "}· {acc.rating.spread_pct >= 5 ? "评级有区分力" : acc.rating.spread_pct <= 0 ? "区分力弱(牛股普涨)" : "区分力一般"}
                    </span>
                  </div>
                )}
              </div>
              <div className="grid grid-cols-1 sm:grid-cols-2 gap-2 text-xs">
                {acc.rating.bull_n != null && (
                  <div className="flex items-center justify-between gap-2 rounded-md px-2.5 py-1.5"
                    style={{ backgroundColor: "color-mix(in srgb, var(--up) 8%, transparent)" }}>
                    <span className="font-medium" style={{ color: US_UP }}>看涨评级</span>
                    <span className="text-muted-foreground tabular-nums">
                      {acc.rating.bull_n}次 · 后市上涨 <b className="text-foreground">{acc.rating.bull_win_pct}%</b> · 均 {(acc.rating.bull_avg_pct ?? 0) > 0 ? "+" : ""}{acc.rating.bull_avg_pct}%
                    </span>
                  </div>
                )}
                {acc.rating.bear_n != null && (
                  <div className="flex items-center justify-between gap-2 rounded-md px-2.5 py-1.5"
                    style={{ backgroundColor: "color-mix(in srgb, var(--down) 8%, transparent)" }}>
                    <span className="font-medium" style={{ color: US_DOWN }}>看空评级</span>
                    <span className="text-muted-foreground tabular-nums">
                      {acc.rating.bear_n}次 · 后市下跌 <b className="text-foreground">{acc.rating.bear_win_pct}%</b> · 均 {(acc.rating.bear_avg_pct ?? 0) > 0 ? "+" : ""}{acc.rating.bear_avg_pct}%
                    </span>
                  </div>
                )}
              </div>
            </div>
          )}

          <p className="text-[11px] text-muted-foreground leading-relaxed">
            方法:取发布满 {Math.round(acc.horizon_days / 30)} 个月的历史目标价/评级,与复权后真实价对照(消除拆股)。
            目标价看<b>高估/低估</b>,评级看<b>方向对不对</b>;牛股里"上涨概率"会普遍虚高,真正的信号是<b>多空价差</b>(看涨组比看空组多涨多少)。逐股口径,不可跨股比较。
          </p>
        </div>
      )}

      {d.financials && ((d.financials.earnings?.length ?? 0) > 0 || (d.financials.annual?.length ?? 0) > 0) && (
        <div className="rounded-xl border bg-card p-4 space-y-3">
          <div className="flex items-baseline justify-between flex-wrap gap-2">
            <div className="text-sm font-semibold">财务与财报</div>
            {d.financials.next_earnings && (
              <div className="text-[11px] text-muted-foreground">下次财报日 <span className="font-medium text-foreground">{d.financials.next_earnings}</span></div>
            )}
          </div>

          {(d.financials.annual?.length ?? 0) > 0 && (
            <div className="grid grid-cols-2 sm:grid-cols-4 gap-2">
              {d.financials.annual!.slice(-4).map((a) => (
                <div key={a.period} className="rounded-lg border bg-background p-2">
                  <div className="text-[11px] text-muted-foreground">{a.period} 财年</div>
                  <div className="text-sm font-bold tabular-nums">{fmtCap(a.revenue)}</div>
                  <div className="text-[10px] text-muted-foreground">营收 · 净利 {fmtCap(a.net_income)}</div>
                  <div className="text-[10px] text-muted-foreground">EPS {a.eps != null ? `$${a.eps.toFixed(2)}` : "—"}</div>
                </div>
              ))}
            </div>
          )}

          {(d.financials.earnings?.length ?? 0) > 0 && (
            <div>
              <div className="text-xs text-muted-foreground mb-1">财报超预期(EPS 实际 vs 预期)</div>
              <div className="overflow-x-auto rounded-lg border">
                <table className="w-full text-xs">
                  <thead className="bg-muted/40 text-[11px] text-muted-foreground">
                    <tr>
                      <th className="px-2.5 py-1.5 text-left font-medium">财报日</th>
                      <th className="px-2.5 py-1.5 text-right font-medium">预期 EPS</th>
                      <th className="px-2.5 py-1.5 text-right font-medium">实际 EPS</th>
                      <th className="px-2.5 py-1.5 text-right font-medium">超预期</th>
                    </tr>
                  </thead>
                  <tbody>
                    {d.financials.earnings!.map((e) => (
                      <tr key={e.date} className="border-t border-border/60">
                        <td className="px-2.5 py-1.5 tabular-nums text-muted-foreground">{e.date}</td>
                        <td className="px-2.5 py-1.5 text-right tabular-nums">{e.eps_est != null ? `$${e.eps_est.toFixed(2)}` : "—"}</td>
                        <td className="px-2.5 py-1.5 text-right tabular-nums font-medium">{e.eps_actual != null ? `$${e.eps_actual.toFixed(2)}` : "—"}</td>
                        <td className="px-2.5 py-1.5 text-right tabular-nums font-medium"
                          style={{ color: e.surprise_pct == null ? "var(--muted-foreground)" : e.surprise_pct >= 0 ? US_UP : US_DOWN }}>
                          {e.surprise_pct != null ? `${e.surprise_pct > 0 ? "+" : ""}${e.surprise_pct.toFixed(1)}%` : "—"}
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
              <div className="text-[10px] text-muted-foreground/70 mt-1">超预期为正=实际盈利超过分析师预期(beat),连续 beat 常反映经营强于预期。数据 yfinance,仅供参考。</div>
            </div>
          )}
        </div>
      )}

      {/* 前瞻一致预期 */}
      {extras?.estimates && (extras.estimates.periods?.length ?? 0) > 0 && (
        <div className="rounded-xl border bg-card p-4 space-y-2">
          <div className="text-sm font-semibold">前瞻一致预期<InfoDot k="target_price" /></div>
          <div className="grid grid-cols-2 gap-3">
            {extras.estimates.periods!.map((p2) => (
              <div key={p2.label} className="rounded-lg border bg-background p-2.5">
                <div className="text-[11px] text-muted-foreground">{p2.label} · {p2.n} 位分析师</div>
                <div className="text-base font-bold tabular-nums">EPS ${fmtNum(p2.eps_avg)}
                  {p2.eps_growth != null && <span className="text-xs ml-1" style={{ color: p2.eps_growth >= 0 ? US_UP : US_DOWN }}>{p2.eps_growth > 0 ? "+" : ""}{(p2.eps_growth * 100).toFixed(1)}%</span>}
                </div>
                <div className="text-[11px] text-muted-foreground">营收增速 {p2.rev_growth != null ? `${(p2.rev_growth * 100).toFixed(1)}%` : "—"}</div>
              </div>
            ))}
          </div>
          {extras.estimates.revisions && (
            <div className="text-[11px] text-muted-foreground">
              近30天 EPS 预期修正:<span style={{ color: US_UP }}>上调 {extras.estimates.revisions.up_30d}</span> · <span style={{ color: US_DOWN }}>下调 {extras.estimates.revisions.down_30d}</span> 家
              {(extras.estimates.revisions.up_30d ?? 0) > (extras.estimates.revisions.down_30d ?? 0) ? "(情绪偏暖)" : "(情绪偏冷)"}
            </div>
          )}
        </div>
      )}

      {/* 持股结构 + 内部人 */}
      {extras?.holders && (extras.holders.inst_pct != null || (extras.holders.top?.length ?? 0) > 0) && (
        <div className="rounded-xl border bg-card p-4 space-y-2">
          <div className="text-sm font-semibold">持股结构 · 谁在持有</div>
          <div className="flex gap-4 text-xs flex-wrap">
            <span>机构持股 <b className="tabular-nums">{extras.holders.inst_pct != null ? `${(extras.holders.inst_pct * 100).toFixed(1)}%` : "—"}</b></span>
            <span>内部人 <b className="tabular-nums">{extras.holders.insider_pct != null ? `${(extras.holders.insider_pct * 100).toFixed(2)}%` : "—"}</b></span>
            <span>机构数 <b className="tabular-nums">{extras.holders.inst_count?.toLocaleString() ?? "—"}</b></span>
            {extras.insider?.net_shares != null && (
              <span>内部人近6月净 <b className="tabular-nums" style={{ color: extras.insider.net_shares >= 0 ? US_UP : US_DOWN }}>{extras.insider.net_shares >= 0 ? "+" : ""}{Math.round(extras.insider.net_shares).toLocaleString()} 股</b></span>
            )}
          </div>
          {(extras.holders.top?.length ?? 0) > 0 && (
            <div className="grid grid-cols-1 sm:grid-cols-2 gap-x-4 gap-y-0.5 text-[11px]">
              {extras.holders.top!.slice(0, 6).map((h) => (
                <div key={h.holder} className="flex justify-between border-b border-border/40 py-0.5">
                  <span className="text-muted-foreground truncate mr-2">{h.holder}</span>
                  <span className="tabular-nums shrink-0">{h.pct != null ? `${(h.pct * 100).toFixed(2)}%` : "—"}
                    {h.chg != null && <span style={{ color: h.chg >= 0 ? US_UP : US_DOWN }}> {h.chg > 0 ? "▲" : h.chg < 0 ? "▼" : ""}</span>}</span>
                </div>
              ))}
            </div>
          )}
        </div>
      )}

      {/* 分红 + 新闻 */}
      {extras && ((extras.actions?.dividends?.length ?? 0) > 0 || (extras.news?.length ?? 0) > 0) && (
        <div className="grid grid-cols-1 lg:grid-cols-2 gap-4">
          {(extras.actions?.dividends?.length ?? 0) > 0 && (
            <div className="rounded-xl border bg-card p-4">
              <div className="text-sm font-semibold mb-2">分红 · 拆股历史</div>
              <div className="text-[11px] text-muted-foreground space-y-0.5">
                {extras.actions!.dividends!.slice(-6).reverse().map((dv) => (
                  <div key={dv.date} className="flex justify-between border-b border-border/40 py-0.5">
                    <span>{dv.date}</span><span className="tabular-nums">每股 ${fmtNum(dv.amount)}</span>
                  </div>
                ))}
                {(extras.actions?.splits?.length ?? 0) > 0 && (
                  <div className="pt-1">拆股:{extras.actions!.splits!.map((s) => `${s.date} (${s.ratio}:1)`).join(" · ")}</div>
                )}
              </div>
            </div>
          )}
          {(extras.news?.length ?? 0) > 0 && (
            <div className="rounded-xl border bg-card p-4">
              <div className="text-sm font-semibold mb-2">近期新闻</div>
              <div className="space-y-1.5">
                {extras.news!.slice(0, 6).map((nw, i) => (
                  <a key={i} href={nw.link || "#"} target="_blank" rel="noopener noreferrer"
                    className="block text-xs leading-snug hover:text-primary">
                    · {nw.title}
                    <span className="text-[10px] text-muted-foreground ml-1">{nw.publisher}{nw.date ? ` · ${nw.date}` : ""}</span>
                  </a>
                ))}
              </div>
            </div>
          )}
        </div>
      )}

      <div className="rounded-xl border bg-card p-4">
        <div className="flex items-center justify-between mb-2 flex-wrap gap-2">
          <div className="text-sm font-semibold">K线 · 蜡烛图 + 指标<InfoDot k="adjust" /></div>
          <div className="flex items-center gap-2 flex-wrap">
            <button
              onClick={() => setAdjust((v) => !v)}
              title="前复权消除拆股/分红造成的价格断崖"
              className={`text-xs px-2.5 py-1 rounded-md border transition-colors ${
                adjust ? "bg-primary text-primary-foreground border-primary" : "hover:bg-accent border-border"
              }`}
            >
              {adjust ? "前复权" : "不复权"}
            </button>
            <div className="w-px h-4 bg-border" />
            <div className="flex gap-1">
              {FREQS.map((fq) => (
                <button
                  key={fq.id}
                  onClick={() => setFreq(fq.id)}
                  className={`text-xs px-2.5 py-1 rounded-md border transition-colors ${
                    freq === fq.id ? "bg-primary text-primary-foreground border-primary" : "hover:bg-accent border-border"
                  }`}
                >
                  {fq.label}
                </button>
              ))}
            </div>
          </div>
        </div>
        {barsLoading && bars == null ? (
          <div className="animate-pulse h-[420px] bg-muted rounded" />
        ) : bars && bars.length > 1 ? (
          <KLineChart
            data={bars.map((b) => ({
              date: b.trade_date, open: b.open, high: b.high, low: b.low, close: b.close,
              volume: b.volume ?? undefined,
            }))}
            height={420}
            initialVisibleBars={INIT_BARS[freq]}
            greenUp
          />
        ) : <div className="text-xs text-muted-foreground py-12 text-center">暂无行情</div>}
      </div>

      {(d.cn_intro || d.description) && (
        <div className="rounded-xl border bg-card p-4">
          <div className="text-sm font-semibold mb-2">公司简介</div>
          {d.cn_intro ? (
            <div className="space-y-2">
              {([
                ["主营业务", d.cn_intro.what], ["核心看点", d.cn_intro.highlights],
                ["财务特征", d.cn_intro.financials], ["估值评级", d.cn_intro.valuation],
                ["风险提示", d.cn_intro.risk],
              ] as [string, string | undefined][]).filter(([, v]) => v).map(([k, v]) => (
                <div key={k} className="flex gap-2 text-xs">
                  <span className="shrink-0 w-16 font-medium text-muted-foreground">{k}</span>
                  <span className="leading-relaxed" style={{ color: k === "风险提示" ? US_DOWN : undefined }}>{v}</span>
                </div>
              ))}
              {d.description && (
                <details className="mt-2">
                  <summary className="text-[11px] text-muted-foreground cursor-pointer hover:text-foreground">英文原文</summary>
                  <p className="text-[11px] text-muted-foreground leading-relaxed mt-1">{d.description}</p>
                </details>
              )}
              <div className="text-[10px] text-muted-foreground/70 pt-1">AI 生成,仅供参考,不构成投资建议</div>
            </div>
          ) : (
            <p className="text-xs text-muted-foreground leading-relaxed">{d.description}</p>
          )}
        </div>
      )}
    </div>
  );
}
