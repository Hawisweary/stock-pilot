"use client";
import { useEffect, useState, useCallback } from "react";
import { useRouter } from "next/navigation";

type EtfRow = {
  code: string;
  name: string;
  management?: string;
  fund_type?: string;
  invest_type?: string;
  list_date?: string;
  category?: string | null;
  is_repr?: number | null;
  cluster_id?: number | null;
  composite_score?: number | null;
  rank_in_category?: number | null;
  close?: number | null;
  pct_chg?: number | null;
  change?: number | null;
  vol?: number | null;
  amount?: number | null;
  trade_date?: string | null;
};

type Sort = "amount" | "pct_chg" | "close" | "score";

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
  const router = useRouter();
  const [rows, setRows] = useState<EtfRow[] | null>(null);
  const [asOf, setAsOf] = useState<string | null>(null);
  const [q, setQ] = useState("");
  const [sort, setSort] = useState<Sort>("amount");
  const [category, setCategory] = useState<string>("");
  const [reprOnly, setReprOnly] = useState(false);
  const [cats, setCats] = useState<{ category: string; n: number; reps: number }[]>([]);
  const [page, setPage] = useState(0);
  const PAGE = 100;

  const load = useCallback(() => {
    const params = new URLSearchParams({ sort, limit: "2000" });
    if (q.trim()) params.set("q", q.trim());
    if (category) params.set("category", category);
    if (reprOnly) params.set("repr_only", "true");
    fetch(`/api/etf/list?${params}`)
      .then((r) => r.json())
      .then((d) => {
        setRows(d.etfs ?? []);
        setAsOf(d.as_of ?? null);
        if (d.categories?.length) setCats(d.categories);
        setPage(0);
      })
      .catch(() => setRows([]));
  }, [q, sort, category, reprOnly]);

  useEffect(() => {
    const t = setTimeout(load, 250);
    return () => clearTimeout(t);
  }, [load]);

  const total = rows?.length ?? 0;
  const pageCount = Math.max(1, Math.ceil(total / PAGE));
  const pageRows = rows ? rows.slice(page * PAGE, (page + 1) * PAGE) : [];

  const SORTS: { id: Sort; label: string }[] = [
    { id: "score", label: "综合分" },
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
            场内 ETF · 数据 {asOf ?? "—"} · 共 {rows?.length ?? 0} 只
            {reprOnly ? "（去重代表）" : ""} · 分类+相关性聚类去重
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

      {/* 分类筛选 + 只看代表 */}
      <div className="flex flex-wrap items-center gap-1.5">
        <button
          onClick={() => setCategory("")}
          className={`text-xs px-2.5 py-1 rounded-md border transition-colors ${
            category === "" ? "bg-primary text-primary-foreground border-primary" : "hover:bg-accent border-border"
          }`}
        >
          全部
        </button>
        {["宽基", "行业", "主题", "风格策略", "QDII", "债券货币", "商品", "其他"].map((c) => {
          const info = cats.find((x) => x.category === c);
          if (!info) return null;
          return (
            <button
              key={c}
              onClick={() => setCategory(category === c ? "" : c)}
              className={`text-xs px-2.5 py-1 rounded-md border transition-colors ${
                category === c ? "bg-primary text-primary-foreground border-primary" : "hover:bg-accent border-border"
              }`}
            >
              {c} <span className="opacity-60">{info.n}</span>
            </button>
          );
        })}
        <label className="ml-2 flex items-center gap-1.5 text-xs cursor-pointer select-none">
          <input type="checkbox" checked={reprOnly} onChange={(e) => setReprOnly(e.target.checked)} className="accent-[var(--primary)]" />
          只看代表（去重后 {cats.reduce((s, c) => s + (c.reps || 0), 0)} 只）
        </label>
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
                <th className="px-3 py-2 text-left font-medium">类别</th>
                <th className="px-3 py-2 text-right font-medium">综合分</th>
                <th className="px-3 py-2 text-right font-medium">最新价</th>
                <th className="px-3 py-2 text-right font-medium">涨跌幅</th>
                <th className="px-3 py-2 text-right font-medium">成交额</th>
                <th className="px-3 py-2 text-left font-medium">管理人</th>
              </tr>
            </thead>
            <tbody>
              {pageRows.map((r) => (
                <tr
                  key={r.code}
                  onClick={() => router.push(`/etf/${r.code}`)}
                  className="border-t border-border hover:bg-accent/40 cursor-pointer"
                >
                  <td className="px-3 py-1.5 font-mono text-xs tabular-nums">{r.code}</td>
                  <td className="px-3 py-1.5">
                    {r.is_repr ? <span title="聚类代表(同类最活跃)" className="text-[var(--up)] mr-0.5">★</span> : null}
                    {r.name}
                  </td>
                  <td className="px-3 py-1.5">
                    {r.category ? (
                      <span className="text-[10px] px-1.5 py-0.5 rounded border border-border text-muted-foreground">{r.category}</span>
                    ) : "—"}
                  </td>
                  <td className="px-3 py-1.5 text-right tabular-nums">
                    {r.composite_score == null ? (
                      <span className="text-muted-foreground">—</span>
                    ) : r.is_repr ? (
                      <span className="font-semibold">
                        {r.composite_score.toFixed(2)}
                        {r.rank_in_category != null && (
                          <span className="text-[10px] text-muted-foreground ml-1">#{r.rank_in_category}</span>
                        )}
                      </span>
                    ) : (
                      <span className="text-muted-foreground" title="继承自同簇代表ETF的分数">≈{r.composite_score.toFixed(2)}</span>
                    )}
                  </td>
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
