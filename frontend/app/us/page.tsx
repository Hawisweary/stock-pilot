"use client";
import { useEffect, useState, useCallback } from "react";

type UsRow = {
  ticker: string;
  name?: string | null;
  exchange?: string;
  close?: number | null;
  chg?: number | null;
  volume?: number | null;
  trade_date?: string | null;
};

type Sort = "dollar_vol" | "pct_chg" | "close" | "volume";

function pctColor(v?: number | null) {
  if (v == null) return "var(--muted-foreground)";
  // 美股惯例:涨绿跌红(与A股相反)。用中性 up/down 变量,这里保持项目 up=红,故直接映射颜色
  if (v > 0) return "var(--up)";
  if (v < 0) return "var(--down)";
  return "var(--muted-foreground)";
}

function fmtVol(v?: number | null) {
  if (v == null) return "—";
  if (v >= 1e6) return `${(v / 1e6).toFixed(1)}M`;
  if (v >= 1e3) return `${(v / 1e3).toFixed(1)}K`;
  return `${v}`;
}

export default function UsPage() {
  const [rows, setRows] = useState<UsRow[] | null>(null);
  const [asOf, setAsOf] = useState<string | null>(null);
  const [q, setQ] = useState("");
  const [sort, setSort] = useState<Sort>("dollar_vol");
  const [page, setPage] = useState(0);
  const PAGE = 100;

  const load = useCallback(() => {
    const params = new URLSearchParams({ sort, limit: "3000" });
    if (q.trim()) params.set("q", q.trim());
    fetch(`/api/us/list?${params}`)
      .then((r) => r.json())
      .then((d) => {
        setRows(d.stocks ?? []);
        setAsOf(d.as_of ?? null);
        setPage(0);
      })
      .catch(() => setRows([]));
  }, [q, sort]);

  useEffect(() => {
    const t = setTimeout(load, 250);
    return () => clearTimeout(t);
  }, [load]);

  const SORTS: { id: Sort; label: string }[] = [
    { id: "dollar_vol", label: "成交额" },
    { id: "pct_chg", label: "涨跌幅" },
    { id: "volume", label: "成交量" },
    { id: "close", label: "价格" },
  ];

  const total = rows?.length ?? 0;
  const pageCount = Math.max(1, Math.ceil(total / PAGE));
  const pageRows = rows ? rows.slice(page * PAGE, (page + 1) * PAGE) : [];

  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-end justify-between gap-3">
        <div>
          <h1 className="text-xl font-bold">美股</h1>
          <p className="text-xs text-muted-foreground mt-1">
            全市场美股 · 数据 {asOf ?? "—"} · 共 {total} 只 · Tiingo + yfinance 双轨
          </p>
        </div>
        <div className="flex items-center gap-2">
          <input
            value={q}
            onChange={(e) => setQ(e.target.value)}
            placeholder="搜 ticker / 名称"
            className="h-8 w-44 rounded-md border border-border bg-background px-2.5 text-sm outline-none focus:ring-1 focus:ring-ring"
          />
          <div className="flex gap-1">
            {SORTS.map((s) => (
              <button
                key={s.id}
                onClick={() => setSort(s.id)}
                className={`text-xs px-2.5 py-1 rounded-md border transition-colors ${
                  sort === s.id ? "bg-primary text-primary-foreground border-primary" : "hover:bg-accent border-border"
                }`}
              >
                {s.label}
              </button>
            ))}
          </div>
        </div>
      </div>

      {rows == null ? (
        <div className="animate-pulse h-96 bg-muted rounded" />
      ) : rows.length === 0 ? (
        <p className="text-sm text-muted-foreground">暂无美股数据（回填进行中或先跑 us_sync）。</p>
      ) : (
        <div className="overflow-x-auto rounded-lg border border-border">
          <table className="w-full text-sm">
            <thead className="bg-muted/40 text-xs text-muted-foreground">
              <tr>
                <th className="px-3 py-2 text-left font-medium">代码</th>
                <th className="px-3 py-2 text-left font-medium">名称</th>
                <th className="px-3 py-2 text-left font-medium">交易所</th>
                <th className="px-3 py-2 text-right font-medium">最新价</th>
                <th className="px-3 py-2 text-right font-medium">涨跌幅</th>
                <th className="px-3 py-2 text-right font-medium">成交量</th>
              </tr>
            </thead>
            <tbody>
              {pageRows.map((r) => (
                <tr key={r.ticker} className="border-t border-border hover:bg-accent/40">
                  <td className="px-3 py-1.5 font-mono text-xs font-semibold">{r.ticker}</td>
                  <td className="px-3 py-1.5 text-xs text-muted-foreground truncate max-w-[220px]">{r.name || "—"}</td>
                  <td className="px-3 py-1.5 text-xs text-muted-foreground">{r.exchange || "—"}</td>
                  <td className="px-3 py-1.5 text-right tabular-nums">{r.close != null ? r.close.toFixed(2) : "—"}</td>
                  <td className="px-3 py-1.5 text-right tabular-nums font-medium" style={{ color: pctColor(r.chg) }}>
                    {r.chg != null ? `${r.chg > 0 ? "+" : ""}${r.chg.toFixed(2)}%` : "—"}
                  </td>
                  <td className="px-3 py-1.5 text-right tabular-nums text-muted-foreground">{fmtVol(r.volume)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}

      {rows && total > PAGE && (
        <div className="flex items-center justify-between text-xs text-muted-foreground">
          <span>第 {page * PAGE + 1}–{Math.min((page + 1) * PAGE, total)} / 共 {total} 只</span>
          <div className="flex items-center gap-2">
            <button onClick={() => setPage((p) => Math.max(0, p - 1))} disabled={page === 0}
              className="px-2.5 py-1 rounded-md border border-border hover:bg-accent disabled:opacity-40">上一页</button>
            <span className="tabular-nums">{page + 1} / {pageCount}</span>
            <button onClick={() => setPage((p) => Math.min(pageCount - 1, p + 1))} disabled={page >= pageCount - 1}
              className="px-2.5 py-1 rounded-md border border-border hover:bg-accent disabled:opacity-40">下一页</button>
          </div>
        </div>
      )}
    </div>
  );
}
