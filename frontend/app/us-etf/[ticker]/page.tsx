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
const INIT_BARS: Record<Freq, number | undefined> = { D: 130, W: 130, M: 96, Y: undefined };

type Detail = {
  ticker: string; name?: string | null; category?: string | null; fund_family?: string | null;
  quotes: { trade_date: string; close: number | null }[];
  profile?: {
    aum?: number | null; expense_ratio?: number | null; yield?: number | null;
    ytd_return?: number | null; three_year_return?: number | null; beta3?: number | null; nav?: number | null;
    asset_classes?: Record<string, number> | null;
    sectors?: { sector: string; weight: number }[];
    holdings?: { symbol: string; name: string; weight: number }[];
    overview?: string | null;
    cn_intro?: { what?: string; tracks?: string; holdings?: string; fit?: string; risk?: string } | null;
  };
  error?: string;
};

const US_UP = "#22c55e", US_DOWN = "#ef4444";
const fmtAum = (v?: number | null) => v == null ? "—" : v >= 1e12 ? `$${(v / 1e12).toFixed(2)}T` : v >= 1e9 ? `$${(v / 1e9).toFixed(1)}B` : `$${(v / 1e6).toFixed(0)}M`;
const pctOf = (v?: number | null) => v == null ? "—" : `${(v * 100).toFixed(1)}%`;
const SECTOR_CN: Record<string, string> = {
  technology: "科技", financial_services: "金融", healthcare: "医疗", consumer_cyclical: "可选消费",
  consumer_defensive: "必选消费", communication_services: "通信", industrials: "工业", energy: "能源",
  utilities: "公用事业", realestate: "房地产", basic_materials: "原材料",
};

export default function EtfDetailPage() {
  const { ticker } = useParams<{ ticker: string }>();
  const router = useRouter();
  const [d, setD] = useState<Detail | null>(null);
  const [loading, setLoading] = useState(true);
  const [freq, setFreq] = useState<Freq>("D");
  const [adjust, setAdjust] = useState(true);
  const [bars, setBars] = useState<Bar[] | null>(null);

  useEffect(() => {
    setLoading(true);
    fetch(`/api/us-etf/${ticker}`).then((r) => r.json()).then(setD)
      .catch(() => setD({ error: "加载失败" } as Detail)).finally(() => setLoading(false));
  }, [ticker]);
  useEffect(() => {
    fetch(`/api/us/${ticker}/klines?freq=${freq}&adjust=${adjust}`)
      .then((r) => r.json()).then((j) => setBars(j.bars ?? [])).catch(() => setBars([]));
  }, [ticker, freq, adjust]);

  if (loading) return <div className="animate-pulse h-96 bg-muted rounded-lg" />;
  if (!d || d.error) return <div className="text-sm text-muted-foreground">未找到该 ETF（{d?.error ?? ticker}）</div>;

  const p = d.profile ?? {};
  const valid = (d.quotes ?? []).filter((q) => q.close != null);
  const last = valid[valid.length - 1]?.close ?? null;
  const prev = valid[valid.length - 2]?.close ?? null;
  const chg = last != null && prev ? (last / prev - 1) * 100 : null;
  const ac = p.asset_classes ?? null;

  const Stat = ({ label, value, sub }: { label: string; value: React.ReactNode; sub?: string }) => (
    <div className="rounded-xl border bg-card p-3">
      <div className="text-[11px] text-muted-foreground">{label}</div>
      <div className="text-lg font-bold tabular-nums">{value}</div>
      {sub && <div className="text-[10px] text-muted-foreground">{sub}</div>}
    </div>
  );

  return (
    <div className="space-y-4">
      <button onClick={() => router.push("/us-etf")} className="flex items-center gap-1 text-xs text-muted-foreground hover:text-foreground">
        <ArrowLeft className="h-3.5 w-3.5" /> 返回 ETF 列表
      </button>

      <div className="flex items-end justify-between flex-wrap gap-2">
        <div>
          <h1 className="text-xl font-bold">{d.name || d.ticker}</h1>
          <div className="text-xs text-muted-foreground">
            <span className="font-mono">{d.ticker}</span>
            {d.category && <span className="ml-2">· {d.category}</span>}
            {d.fund_family && <span className="ml-2">· {d.fund_family}</span>}
          </div>
        </div>
        <div className="text-right">
          <div className="text-2xl font-bold tabular-nums">{last != null ? `$${last.toFixed(2)}` : "—"}</div>
          <div className="text-sm font-medium tabular-nums" style={{ color: (chg ?? 0) > 0 ? US_UP : (chg ?? 0) < 0 ? US_DOWN : "var(--muted-foreground)" }}>
            {chg != null ? `${chg > 0 ? "+" : ""}${chg.toFixed(2)}%` : "—"}
          </div>
        </div>
      </div>

      <div className="grid grid-cols-2 md:grid-cols-5 gap-3">
        <Stat label="规模 AUM" value={fmtAum(p.aum)} />
        <Stat label="费率" value={p.expense_ratio != null ? `${p.expense_ratio}%` : "—"} sub="年化" />
        <Stat label="股息率" value={pctOf(p.yield)} />
        <Stat label="今年回报" value={p.ytd_return != null ? `${p.ytd_return > 0 ? "+" : ""}${p.ytd_return.toFixed(1)}%` : "—"} />
        <Stat label="3年年化" value={pctOf(p.three_year_return)} sub={`β ${p.beta3?.toFixed(2) ?? "—"}`} />
      </div>

      <div className="grid grid-cols-1 lg:grid-cols-3 gap-4">
        {/* 前十持仓 */}
        <div className="lg:col-span-2 rounded-xl border bg-card p-4">
          <div className="text-sm font-semibold mb-2">前十持仓</div>
          {p.holdings && p.holdings.length > 0 ? (
            <div className="space-y-1.5">
              {p.holdings.slice(0, 10).map((h) => (
                <div key={h.symbol} className="flex items-center gap-2 text-xs">
                  <span className="font-mono font-semibold w-14">{h.symbol}</span>
                  <span className="text-muted-foreground truncate flex-1">{h.name}</span>
                  <div className="w-28 h-2 rounded bg-muted overflow-hidden">
                    <div className="h-full rounded" style={{ width: `${Math.min(h.weight * 100 * 4, 100)}%`, backgroundColor: "var(--primary)" }} />
                  </div>
                  <span className="tabular-nums w-12 text-right font-medium">{(h.weight * 100).toFixed(1)}%</span>
                </div>
              ))}
            </div>
          ) : <div className="text-xs text-muted-foreground py-2">该 ETF 无持仓明细(可能为债券/大宗类)</div>}
        </div>

        {/* 配置 + 板块 */}
        <div className="rounded-xl border bg-card p-4 space-y-3">
          <div>
            <div className="text-sm font-semibold mb-1.5">资产配置</div>
            {ac ? (
              <div className="space-y-1 text-xs">
                {[["stockPosition", "股票"], ["bondPosition", "债券"], ["cashPosition", "现金"], ["otherPosition", "其他"]]
                  .filter(([k]) => (ac[k] ?? 0) > 0.0005).map(([k, lab]) => (
                    <div key={k} className="flex justify-between"><span className="text-muted-foreground">{lab}</span>
                      <span className="tabular-nums font-medium">{((ac[k] ?? 0) * 100).toFixed(1)}%</span></div>
                  ))}
              </div>
            ) : <div className="text-xs text-muted-foreground">—</div>}
          </div>
          {p.sectors && p.sectors.length > 0 && (
            <div>
              <div className="text-sm font-semibold mb-1.5">板块权重</div>
              <div className="space-y-1 text-xs">
                {[...p.sectors].sort((a, b) => b.weight - a.weight).slice(0, 5).map((s) => (
                  <div key={s.sector} className="flex justify-between">
                    <span className="text-muted-foreground">{SECTOR_CN[s.sector] || s.sector}</span>
                    <span className="tabular-nums font-medium">{(s.weight * 100).toFixed(1)}%</span></div>
                ))}
              </div>
            </div>
          )}
        </div>
      </div>

      {/* K线(复用个股同款) */}
      <div className="rounded-xl border bg-card p-4">
        <div className="flex items-center justify-between mb-2 flex-wrap gap-2">
          <div className="text-sm font-semibold">K线 · 蜡烛图 + 指标</div>
          <div className="flex items-center gap-2 flex-wrap">
            <button onClick={() => setAdjust((v) => !v)}
              className={`text-xs px-2.5 py-1 rounded-md border transition-colors ${adjust ? "bg-primary text-primary-foreground border-primary" : "hover:bg-accent border-border"}`}>
              {adjust ? "前复权" : "不复权"}
            </button>
            <div className="w-px h-4 bg-border" />
            <div className="flex gap-1">
              {FREQS.map((fq) => (
                <button key={fq.id} onClick={() => setFreq(fq.id)}
                  className={`text-xs px-2.5 py-1 rounded-md border transition-colors ${freq === fq.id ? "bg-primary text-primary-foreground border-primary" : "hover:bg-accent border-border"}`}>
                  {fq.label}
                </button>
              ))}
            </div>
          </div>
        </div>
        {bars && bars.length > 1 ? (
          <KLineChart
            data={bars.map((b) => ({
              date: b.trade_date, open: b.open, high: b.high, low: b.low, close: b.close,
              volume: b.volume ?? undefined,
            }))}
            height={420} initialVisibleBars={INIT_BARS[freq]} greenUp
          />
        ) : <div className="text-xs text-muted-foreground py-16 text-center">加载中…</div>}
      </div>

      {(p.cn_intro || p.overview) && (
        <div className="rounded-xl border bg-card p-4">
          <div className="text-sm font-semibold mb-2">简介</div>
          {p.cn_intro ? (
            <div className="space-y-2">
              {([
                ["是什么", p.cn_intro.what], ["跟踪策略", p.cn_intro.tracks],
                ["持仓特征", p.cn_intro.holdings], ["适合谁", p.cn_intro.fit],
                ["风险提示", p.cn_intro.risk],
              ] as [string, string | undefined][]).filter(([, v]) => v).map(([k, v]) => (
                <div key={k} className="flex gap-2 text-xs">
                  <span className="shrink-0 w-16 font-medium text-muted-foreground">{k}</span>
                  <span className={`leading-relaxed ${k === "风险提示" ? "text-[var(--down)]" : ""}`}>{v}</span>
                </div>
              ))}
              {p.overview && (
                <details className="mt-2">
                  <summary className="text-[11px] text-muted-foreground cursor-pointer hover:text-foreground">英文原文</summary>
                  <p className="text-[11px] text-muted-foreground leading-relaxed mt-1">{p.overview}</p>
                </details>
              )}
              <div className="text-[10px] text-muted-foreground/70 pt-1">AI 生成,仅供参考,不构成投资建议</div>
            </div>
          ) : (
            <p className="text-xs text-muted-foreground leading-relaxed">{p.overview}</p>
          )}
        </div>
      )}
    </div>
  );
}
