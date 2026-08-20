"use client";
import { useEffect, useState } from "react";
import { useParams, useRouter } from "next/navigation";
import { ArrowLeft } from "lucide-react";
import { LineChart, Line, XAxis, YAxis, Tooltip, ResponsiveContainer, CartesianGrid } from "recharts";

type Quote = { trade_date: string; close: number | null; volume: number | null };
type Detail = {
  ticker: string; name?: string | null; exchange?: string;
  quotes: Quote[];
  fundamentals?: { market_cap?: number | null; pe?: number | null; pb?: number | null; peg?: number | null; enterprise_val?: number | null; error?: string };
  description?: string | null;
  error?: string;
};

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

  useEffect(() => {
    setLoading(true);
    fetch(`/api/us/${ticker}`)
      .then((r) => r.json())
      .then(setD)
      .catch(() => setD({ error: "加载失败" } as Detail))
      .finally(() => setLoading(false));
  }, [ticker]);

  if (loading) return <div className="animate-pulse h-96 bg-muted rounded-lg" />;
  if (!d || d.error) return <div className="text-sm text-muted-foreground">未找到该美股（{d?.error ?? ticker}）</div>;

  const chart = d.quotes.map((q) => ({ date: q.trade_date.slice(5), close: q.close }));
  const last = d.quotes[d.quotes.length - 1];
  const prev = d.quotes[d.quotes.length - 2];
  const chg = last?.close != null && prev?.close ? (last.close / prev.close - 1) * 100 : null;
  const f = d.fundamentals && !d.fundamentals.error ? d.fundamentals : null;

  const Stat = ({ label, value }: { label: string; value: React.ReactNode }) => (
    <div className="rounded-xl border bg-card p-3">
      <div className="text-[11px] text-muted-foreground">{label}</div>
      <div className="text-lg font-bold tabular-nums">{value}</div>
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
          <div className="text-xs font-mono text-muted-foreground">{d.ticker} · {d.exchange}</div>
        </div>
        <div className="text-right">
          <div className="text-2xl font-bold tabular-nums">{last?.close != null ? `$${last.close.toFixed(2)}` : "—"}</div>
          <div className="text-sm font-medium tabular-nums" style={{ color: (chg ?? 0) > 0 ? "var(--up)" : (chg ?? 0) < 0 ? "var(--down)" : "var(--muted-foreground)" }}>
            {chg != null ? `${chg > 0 ? "+" : ""}${chg.toFixed(2)}%` : "—"}
            <span className="text-muted-foreground ml-1 font-normal">{last?.trade_date}</span>
          </div>
        </div>
      </div>

      <div className="grid grid-cols-2 md:grid-cols-4 gap-3">
        <Stat label="市值" value={fmtCap(f?.market_cap)} />
        <Stat label="PE" value={fmtNum(f?.pe)} />
        <Stat label="PB" value={fmtNum(f?.pb)} />
        <Stat label="PEG" value={fmtNum(f?.peg)} />
      </div>

      <div className="rounded-xl border bg-card p-4">
        <div className="text-sm font-semibold mb-2">价格走势 · 近 {d.quotes.length} 交易日</div>
        {chart.length > 1 ? (
          <ResponsiveContainer width="100%" height={280}>
            <LineChart data={chart} margin={{ top: 5, right: 8, left: -8, bottom: 0 }}>
              <CartesianGrid strokeDasharray="3 3" stroke="var(--border)" vertical={false} />
              <XAxis dataKey="date" tick={{ fontSize: 10 }} minTickGap={40} stroke="var(--muted-foreground)" />
              <YAxis domain={["auto", "auto"]} tick={{ fontSize: 10 }} width={52} stroke="var(--muted-foreground)" />
              <Tooltip contentStyle={{ fontSize: 12, borderRadius: 8, border: "1px solid var(--border)", background: "var(--card)" }} />
              <Line type="monotone" dataKey="close" stroke="var(--up)" dot={false} strokeWidth={1.6} />
            </LineChart>
          </ResponsiveContainer>
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
