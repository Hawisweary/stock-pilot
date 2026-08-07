"use client";
import { useEffect, useState, useCallback } from "react";

type EtfRow = {
  code: string;
  name: string;
  management?: string;
  fund_type?: string;
  invest_type?: string;
  list_date?: string;
  close?: number | null;
  pct_chg?: number | null;
  change?: number | null;
  vol?: number | null;
  amount?: number | null;
  trade_date?: string | null;
};

type Sort = "amount" | "pct_chg" | "close";

function pctColor(v?: number | null) {
  if (v == null) return "var(--muted-foreground)";
  if (v > 0) return "var(--up)";
  if (v < 0) return "var(--down)";
  return "var(--muted-foreground)";
}

function fmtAmount(v?: number | null) {
  if (v == null) return "—";
  // amount 单位为千元 → 换算成亿元
  const yi = (v * 1000) / 1e8;
  return yi >= 1 ? `${yi.toFixed(2)}亿` : `${(yi * 10000).toFixed(0)}万`;
}

export default function EtfPage() {
  const [rows, setRows] = useState<EtfRow[] | null>(null);
  const [asOf, setAsOf] = useState<string | null>(null);
  const [q, setQ] = useState("");
  const [sort, setSort] = useState<Sort>("amount");
  const [page, setPage] = useState(0);
  const PAGE = 100;

  const load = useCallback(() => {
    const params = new URLSearchParams({ sort, limit: "2000" });
    if (q.trim()) params.set("q", q.trim());
    fetch(`/api/etf/list?${params}`)
      .then((r) => r.json())
      .then((d) => {
        setRows(d.etfs ?? []);
        setAsOf(d.as_of ?? null);
        setPage(0);
      })
      .catch(() => setRows([]));
  }, [q, sort]);

  useEffect(() => {
    const t = setTimeout(load, 250);
    return () => clearTimeout(t);
  }, [load]);

  const total = rows?.length ?? 0;
  const pageCount = Math.max(1, Math.ceil(total / PAGE));
  const pageRows = rows ? rows.slice(page * PAGE, (page + 1) * PAGE) : [];

  const SORTS: { id: Sort; label: string }[] = [
    { id: "amount", label: "成交额" },
    { id: "pct_chg", label: "涨跌幅" },
    { id: "close", label: "价格" },
  ];

  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-end justify-between gap-3">
        <div>
          <h1 className="text-xl font-bold">ETF 列表</h1>
          <p className="text-xs text-muted-foreground mt-1">
            场内 ETF · 数据 {asOf ?? "—"} · 共 {rows?.length ?? 0} 只（Tushare fund_daily）
          </p>
        </div>
        <div className="flex items-center gap-2">
          <input
            value={q}
            onChange={(e) => setQ(e.target.value)}
            placeholder="搜代码 / 名称"
            className="h-8 w-44 rounded-md border border-border bg-background px-2.5 text-sm outline-none focus:ring-1 focus:ring-ring"
          />
          <div className="flex gap-1">
            {SORTS.map((s) => (
              <button
                key={s.id}
                onClick={() => setSort(s.id)}
                className={`text-xs px-2.5 py-1 rounded-md border transition-colors ${
                  sort === s.id
                    ? "bg-primary text-primary-foreground border-primary"
                    : "hover:bg-accent border-border"
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
        <p className="text-sm text-muted-foreground">暂无 ETF 数据，请先在调度或数据页同步。</p>
      ) : (
        <div className="overflow-x-auto rounded-lg border border-border">
          <table className="w-full text-sm">
            <thead className="bg-muted/40 text-xs text-muted-foreground">
              <tr>
                <th className="px-3 py-2 text-left font-medium">代码</th>
                <th className="px-3 py-2 text-left font-medium">名称</th>
                <th className="px-3 py-2 text-right font-medium">最新价</th>
                <th className="px-3 py-2 text-right font-medium">涨跌幅</th>
                <th className="px-3 py-2 text-right font-medium">成交额</th>
                <th className="px-3 py-2 text-left font-medium">类型</th>
                <th className="px-3 py-2 text-left font-medium">管理人</th>
              </tr>
            </thead>
            <tbody>
              {pageRows.map((r) => (
                <tr key={r.code} className="border-t border-border hover:bg-accent/40">
                  <td className="px-3 py-1.5 font-mono text-xs tabular-nums">{r.code}</td>
                  <td className="px-3 py-1.5">{r.name}</td>
                  <td className="px-3 py-1.5 text-right tabular-nums">
                    {r.close != null ? r.close.toFixed(3) : "—"}
                  </td>
                  <td
                    className="px-3 py-1.5 text-right tabular-nums font-medium"
                    style={{ color: pctColor(r.pct_chg) }}
                  >
                    {r.pct_chg != null ? `${r.pct_chg > 0 ? "+" : ""}${r.pct_chg.toFixed(2)}%` : "—"}
                  </td>
                  <td className="px-3 py-1.5 text-right tabular-nums text-muted-foreground">
                    {fmtAmount(r.amount)}
                  </td>
                  <td className="px-3 py-1.5 text-xs text-muted-foreground">
                    {r.invest_type || r.fund_type || "—"}
                  </td>
                  <td className="px-3 py-1.5 text-xs text-muted-foreground truncate max-w-[180px]">
                    {(r.management || "").replace(/基金管理.*$/, "").replace(/股份有限公司|有限公司/, "") || "—"}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}

      {rows && total > PAGE && (
        <div className="flex items-center justify-between text-xs text-muted-foreground">
          <span>
            第 {page * PAGE + 1}–{Math.min((page + 1) * PAGE, total)} / 共 {total} 只
          </span>
          <div className="flex items-center gap-2">
            <button
              onClick={() => setPage((p) => Math.max(0, p - 1))}
              disabled={page === 0}
              className="px-2.5 py-1 rounded-md border border-border hover:bg-accent disabled:opacity-40 disabled:cursor-not-allowed"
            >
              上一页
            </button>
            <span className="tabular-nums">{page + 1} / {pageCount}</span>
            <button
              onClick={() => setPage((p) => Math.min(pageCount - 1, p + 1))}
              disabled={page >= pageCount - 1}
              className="px-2.5 py-1 rounded-md border border-border hover:bg-accent disabled:opacity-40 disabled:cursor-not-allowed"
            >
              下一页
            </button>
          </div>
        </div>
      )}
    </div>
  );
}
