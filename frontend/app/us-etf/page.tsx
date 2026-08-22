"use client";
import { useEffect, useState, useCallback } from "react";
import { useRouter } from "next/navigation";

type Row = {
  ticker: string; name?: string | null; category?: string | null; fund_family?: string | null;
  aum?: number | null; expense_ratio?: number | null; close?: number | null; chg?: number | null;
};
type Sort = "aum" | "pct_chg" | "expense" | "close";

const US_UP = "#22c55e", US_DOWN = "#ef4444";
function pctColor(v?: number | null) {
  if (v == null) return "var(--muted-foreground)";
  return v > 0 ? US_UP : v < 0 ? US_DOWN : "var(--muted-foreground)";
}
function fmtAum(v?: number | null) {
  if (v == null) return "—";
  if (v >= 1e12) return `$${(v / 1e12).toFixed(2)}T`;
  if (v >= 1e9) return `$${(v / 1e9).toFixed(1)}B`;
  if (v >= 1e6) return `$${(v / 1e6).toFixed(0)}M`;
  return `$${v}`;
}

export default function UsEtfPage() {
  const router = useRouter();
  const [rows, setRows] = useState<Row[] | null>(null);
  const [asOf, setAsOf] = useState<string | null>(null);
  const [q, setQ] = useState("");
  const [sort, setSort] = useState<Sort>("aum");

  const load = useCallback(() => {
    const p = new URLSearchParams({ sort, limit: "300" });
    if (q.trim()) p.set("q", q.trim());
    fetch(`/api/us-etf/list?${p}`)
      .then((r) => r.json())
      .then((d) => { setRows(d.etfs ?? []); setAsOf(d.as_of ?? null); })
      .catch(() => setRows([]));
  }, [q, sort]);
  useEffect(() => { const t = setTimeout(load, 250); return () => clearTimeout(t); }, [load]);

  const SORTS: { id: Sort; label: string }[] = [
    { id: "aum", label: "规模" }, { id: "pct_chg", label: "涨跌幅" },
    { id: "expense", label: "费率" }, { id: "close", label: "价格" },
  ];

  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-end justify-between gap-3">
        <div>
          <h1 className="text-xl font-bold">美股 ETF</h1>
          <p className="text-xs text-muted-foreground mt-1">
            精选流动性 · 数据 {asOf ?? "—"} · 共 {rows?.length ?? 0} 只 · 按规模排序
          </p>
        </div>
        <div className="flex items-center gap-2">
          <input value={q} onChange={(e) => setQ(e.target.value)} placeholder="搜 代码 / 名称 / 类别"
            className="h-8 w-48 rounded-md border border-border bg-background px-2.5 text-sm outline-none focus:ring-1 focus:ring-ring" />
          <div className="flex gap-1">
            {SORTS.map((s) => (
              <button key={s.id} onClick={() => setSort(s.id)}
                className={`text-xs px-2.5 py-1 rounded-md border transition-colors ${
                  sort === s.id ? "bg-primary text-primary-foreground border-primary" : "hover:bg-accent border-border"}`}>
                {s.label}
              </button>
            ))}
          </div>
        </div>
      </div>

      {rows == null ? (
        <div className="animate-pulse h-96 bg-muted rounded" />
      ) : rows.length === 0 ? (
        <p className="text-sm text-muted-foreground">暂无 ETF 数据(先跑 us_etf_sync)。</p>
      ) : (
        <div className="overflow-x-auto rounded-lg border border-border">
          <table className="w-full text-sm">
            <thead className="bg-muted/40 text-xs text-muted-foreground">
              <tr>
                <th className="px-3 py-2 text-left font-medium">代码</th>
                <th className="px-3 py-2 text-left font-medium">名称</th>
                <th className="px-3 py-2 text-left font-medium">类别</th>
                <th className="px-3 py-2 text-right font-medium">规模</th>
                <th className="px-3 py-2 text-right font-medium">费率</th>
                <th className="px-3 py-2 text-right font-medium">最新价</th>
                <th className="px-3 py-2 text-right font-medium">涨跌幅</th>
              </tr>
            </thead>
            <tbody>
              {rows.map((r) => (
                <tr key={r.ticker} onClick={() => router.push(`/us-etf/${r.ticker}`)}
                  className="border-t border-border hover:bg-accent/40 cursor-pointer">
                  <td className="px-3 py-1.5 font-mono text-xs font-semibold">{r.ticker}</td>
                  <td className="px-3 py-1.5 text-xs text-muted-foreground truncate max-w-[240px]">{r.name || "—"}</td>
                  <td className="px-3 py-1.5 text-xs text-muted-foreground">{r.category || "—"}</td>
                  <td className="px-3 py-1.5 text-right tabular-nums">{fmtAum(r.aum)}</td>
                  <td className="px-3 py-1.5 text-right tabular-nums text-muted-foreground">
                    {r.expense_ratio != null ? `${r.expense_ratio}%` : "—"}</td>
                  <td className="px-3 py-1.5 text-right tabular-nums">{r.close != null ? r.close.toFixed(2) : "—"}</td>
                  <td className="px-3 py-1.5 text-right tabular-nums font-medium" style={{ color: pctColor(r.chg) }}>
                    {r.chg != null ? `${r.chg > 0 ? "+" : ""}${r.chg.toFixed(2)}%` : "—"}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}
