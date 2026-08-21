"use client";
import { useEffect, useState } from "react";
import { useParams, useRouter } from "next/navigation";
import { ArrowLeft } from "lucide-react";
import { KLineChart } from "@/components/KLineChart";

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
  description?: string | null;
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

export default function UsDetailPage() {
  const { ticker } = useParams<{ ticker: string }>();
  const router = useRouter();
  const [d, setD] = useState<Detail | null>(null);
  const [loading, setLoading] = useState(true);
  const [freq, setFreq] = useState<Freq>("D");
  const [adjust, setAdjust] = useState(true);
  const [bars, setBars] = useState<Bar[] | null>(null);
  const [barsLoading, setBarsLoading] = useState(true);

  useEffect(() => {
    setLoading(true);
    fetch(`/api/us/${ticker}`)
      .then((r) => r.json())
      .then(setD)
      .catch(() => setD({ error: "加载失败" } as Detail))
      .finally(() => setLoading(false));
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

  const Stat = ({ label, value }: { label: string; value: React.ReactNode }) => (
    <div className="rounded-xl border bg-card p-3">
      <div className="text-[11px] text-muted-foreground">{label}</div>
      <div className="text-lg font-bold tabular-nums">{value}</div>
    </div>
  );
  const Kv = ({ k, v }: { k: string; v: React.ReactNode }) => (
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
        <Stat label="市值" value={fmtCap(f?.market_cap ?? p?.market_cap)} />
        <Stat label="PE" value={fmtNum(f?.pe ?? p?.trailing_pe)} />
        <Stat label="PB" value={fmtNum(f?.pb ?? p?.pb)} />
        <Stat label="PEG" value={fmtNum(f?.peg)} />
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
              <Kv k="ROE" v={pct(p.roe)} />
              <Kv k="利润率" v={pct(p.profit_margin)} />
              <Kv k="营收增长" v={pct(p.rev_growth)} />
              <Kv k="盈利增长" v={pct(p.eps_growth)} />
              <Kv k="前瞻PE" v={fmtNum(p.forward_pe)} />
              <Kv k="Beta" v={fmtNum(p.beta)} />
              <Kv k="股息率" v={p.div_yield != null ? `${p.div_yield.toFixed(2)}%` : "—"} />
              <Kv k="EPS" v={p.eps != null ? `$${p.eps.toFixed(2)}` : "—"} />
              <Kv k="员工" v={p.employees != null ? p.employees.toLocaleString() : "—"} />
              <Kv k="52周高" v={p.wk52_high != null ? `$${p.wk52_high.toFixed(0)}` : "—"} />
              <Kv k="52周低" v={p.wk52_low != null ? `$${p.wk52_low.toFixed(0)}` : "—"} />
            </div>
          </div>
        </div>
      )}

      <div className="rounded-xl border bg-card p-4">
        <div className="flex items-center justify-between mb-2 flex-wrap gap-2">
          <div className="text-sm font-semibold">K线 · 蜡烛图 + 指标</div>
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

      {d.description && (
        <div className="rounded-xl border bg-card p-4">
          <div className="text-sm font-semibold mb-1.5">公司简介</div>
          <p className="text-xs text-muted-foreground leading-relaxed">{d.description}</p>
        </div>
      )}
    </div>
  );
}
