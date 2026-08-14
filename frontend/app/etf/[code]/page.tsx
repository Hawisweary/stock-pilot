"use client";
import { useEffect, useState } from "react";
import { useParams, useRouter } from "next/navigation";
import { ArrowLeft } from "lucide-react";
import {
  LineChart, Line, XAxis, YAxis, Tooltip, ResponsiveContainer, CartesianGrid,
} from "recharts";

type Quote = { trade_date: string; close: number | null; pct_chg: number | null; amount: number | null };
type Holding = { symbol: string; name?: string | null; ratio: number | null; mkv: number | null };
type Detail = {
  code: string; ts_code: string; name: string;
  quotes: Quote[];
  info?: Record<string, unknown> & { error?: string };
  nav?: { nav_date: string; unit_nav: number | null }[] | { error?: string };
  nav_latest?: { nav_date: string; unit_nav: number | null };
  premium_pct?: number | null;
  portfolio?: { end_date?: string | null; holdings?: Holding[]; error?: string; reason?: string };
  error?: string;
};

const fmtDate = (d?: string) => (d && d.length === 8 ? `${d.slice(0, 4)}-${d.slice(4, 6)}-${d.slice(6, 8)}` : d ?? "—");
const fmtYi = (v?: number | null) => (v == null ? "—" : `${((v * 1000) / 1e8).toFixed(2)}亿`);

export default function EtfDetailPage() {
  const { code } = useParams<{ code: string }>();
  const router = useRouter();
  const [d, setD] = useState<Detail | null>(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    setLoading(true);
    fetch(`/api/etf/${code}`)
      .then((r) => r.json())
      .then((x) => setD(x))
      .catch(() => setD({ error: "加载失败" } as Detail))
      .finally(() => setLoading(false));
  }, [code]);

  if (loading) return <div className="animate-pulse h-96 bg-muted rounded-lg" />;
  if (!d || d.error) return <div className="text-sm text-muted-foreground">未找到该 ETF（{d?.error ?? code}）</div>;

  const info = d.info && !d.info.error ? d.info : null;
  const holdings = d.portfolio && !d.portfolio.error ? d.portfolio.holdings ?? [] : [];
  const chartData = d.quotes.map((q) => ({ date: q.trade_date.slice(5), close: q.close }));
  const last = d.quotes[d.quotes.length - 1];
  const premium = d.premium_pct;

  const InfoItem = ({ k, v }: { k: string; v: React.ReactNode }) => (
    <div className="flex justify-between text-xs py-1 border-b border-border/50 last:border-0">
      <span className="text-muted-foreground">{k}</span>
      <span className="font-medium text-right">{v ?? "—"}</span>
    </div>
  );

  return (
    <div className="space-y-4">
      <button onClick={() => router.push("/etf")} className="flex items-center gap-1 text-xs text-muted-foreground hover:text-foreground">
        <ArrowLeft className="h-3.5 w-3.5" /> 返回 ETF 列表
      </button>

      {/* 头部 */}
      <div className="flex items-end justify-between flex-wrap gap-2">
        <div>
          <h1 className="text-xl font-bold">{d.name}</h1>
          <div className="text-xs font-mono text-muted-foreground">{d.code} · {d.ts_code}</div>
        </div>
        <div className="text-right">
          <div className="text-2xl font-bold tabular-nums">{last?.close ?? "—"}</div>
          <div className="text-sm font-medium tabular-nums" style={{ color: (last?.pct_chg ?? 0) > 0 ? "var(--up)" : (last?.pct_chg ?? 0) < 0 ? "var(--down)" : "var(--muted-foreground)" }}>
            {last?.pct_chg != null ? `${last.pct_chg > 0 ? "+" : ""}${last.pct_chg}%` : "—"}
            <span className="text-muted-foreground ml-1 font-normal">{fmtDate(last?.trade_date?.replace(/-/g, ""))}</span>
          </div>
        </div>
      </div>

      {/* 关键指标行 */}
      <div className="grid grid-cols-2 md:grid-cols-4 gap-3">
        <Stat label="折溢价" value={premium != null ? `${premium > 0 ? "+" : ""}${premium}%` : "—"}
          sub={d.nav_latest ? `净值 ${d.nav_latest.unit_nav} @${fmtDate(d.nav_latest.nav_date)}` : undefined}
          color={premium != null ? (premium > 0 ? "var(--up)" : "var(--down)") : undefined} />
        <Stat label="单位净值" value={d.nav_latest?.unit_nav ?? "—"} />
        <Stat label="成交额" value={fmtYi(last?.amount)} />
        <Stat label="管理/托管费" value={info ? `${info.m_fee ?? "—"}% / ${info.c_fee ?? "—"}%` : "—"} />
      </div>

      <div className="grid grid-cols-1 lg:grid-cols-3 gap-4">
        {/* 价格走势 */}
        <div className="lg:col-span-2 rounded-xl border bg-card p-4">
          <div className="text-sm font-semibold mb-2">价格走势 · 近 {d.quotes.length} 交易日</div>
          {chartData.length > 1 ? (
            <ResponsiveContainer width="100%" height={260}>
              <LineChart data={chartData} margin={{ top: 5, right: 8, left: -8, bottom: 0 }}>
                <CartesianGrid strokeDasharray="3 3" stroke="var(--border)" vertical={false} />
                <XAxis dataKey="date" tick={{ fontSize: 10 }} minTickGap={40} stroke="var(--muted-foreground)" />
                <YAxis domain={["auto", "auto"]} tick={{ fontSize: 10 }} width={48} stroke="var(--muted-foreground)" />
                <Tooltip contentStyle={{ fontSize: 12, borderRadius: 8, border: "1px solid var(--border)", background: "var(--card)" }} />
                <Line type="monotone" dataKey="close" stroke="var(--up)" dot={false} strokeWidth={1.6} />
              </LineChart>
            </ResponsiveContainer>
          ) : <div className="text-xs text-muted-foreground py-12 text-center">暂无行情</div>}
        </div>

        {/* 信息卡 */}
        <div className="rounded-xl border bg-card p-4">
          <div className="text-sm font-semibold mb-2">基本信息</div>
          {info ? (
            <div>
              <InfoItem k="管理人" v={info.management as string} />
              <InfoItem k="托管人" v={info.custodian as string} />
              <InfoItem k="跟踪基准" v={info.benchmark as string} />
              <InfoItem k="投资类型" v={info.invest_type as string} />
              <InfoItem k="基金类型" v={info.fund_type as string} />
              <InfoItem k="发行份额" v={info.issue_amount != null ? `${info.issue_amount}亿份` : undefined} />
              <InfoItem k="成立日" v={fmtDate(info.found_date as string)} />
              <InfoItem k="上市日" v={fmtDate(info.list_date as string)} />
            </div>
          ) : <div className="text-xs text-muted-foreground py-8 text-center">信息暂不可用</div>}
        </div>
      </div>

      {/* 前十大持仓 */}
      <div className="rounded-xl border bg-card p-4">
        <div className="flex items-baseline justify-between mb-2">
          <div className="text-sm font-semibold">前十大重仓</div>
          <div className="text-[11px] text-muted-foreground">报告期 {fmtDate(d.portfolio?.end_date ?? undefined)}</div>
        </div>
        {holdings.length ? (
          <div className="grid grid-cols-1 sm:grid-cols-2 gap-x-6 gap-y-1">
            {holdings.map((h, i) => (
              <div key={h.symbol} className="flex items-center gap-2 text-xs py-1 border-b border-border/50">
                <span className="text-muted-foreground w-4 tabular-nums">{i + 1}</span>
                <span className="font-mono text-muted-foreground w-16">{h.symbol.split(".")[0]}</span>
                <span className="flex-1 truncate">{h.name ?? "—"}</span>
                <span className="tabular-nums font-medium">{h.ratio != null ? `${h.ratio}%` : "—"}</span>
              </div>
            ))}
          </div>
        ) : <div className="text-xs text-muted-foreground py-6 text-center">{d.portfolio?.reason || d.portfolio?.error || "持仓数据暂不可用"}</div>}
      </div>
    </div>
  );
}

function Stat({ label, value, sub, color }: { label: string; value: React.ReactNode; sub?: string; color?: string }) {
  return (
    <div className="rounded-xl border bg-card p-3">
      <div className="text-[11px] text-muted-foreground">{label}</div>
      <div className="text-lg font-bold tabular-nums" style={color ? { color } : undefined}>{value}</div>
      {sub && <div className="text-[10px] text-muted-foreground mt-0.5">{sub}</div>}
    </div>
  );
}
