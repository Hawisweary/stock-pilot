"use client";

import { useEffect, useState, useCallback } from "react";
import { Dialog, DialogContent, DialogHeader, DialogTitle } from "@/components/ui/dialog";
import { useToast } from "@/lib/useToast";
import { exportCsv } from "@/lib/csvExport";
import { ArrowUpDown } from "lucide-react";

interface Stock {
  id: number;
  code: string;
  name: string;
}

interface V5Row {
  stock_id: number;
  code: string;
  name: string;
  composite_v5?: number | null;
  veto_status?: string | null;
  fundamental_score?: number | null;
  technical_score?: number | null;
  sentiment_score?: number | null;
  capital_score?: number | null;
  policy_score?: number | null;
  mood_score?: number | null;
  val_score?: number | null;
  quality_score?: number | null;
  industry_score?: number | null;
  market_env_score?: number | null;
}

const DIM_COLS: { key: keyof V5Row; label: string }[] = [
  { key: "composite_v5", label: "综合分" },
  { key: "fundamental_score", label: "基本面" },
  { key: "technical_score", label: "技术面" },
  { key: "sentiment_score", label: "新闻面" },
  { key: "capital_score", label: "资金面" },
  { key: "policy_score", label: "政策面" },
  { key: "mood_score", label: "情绪面" },
  { key: "val_score", label: "估值面" },
  { key: "quality_score", label: "质量" },
  { key: "industry_score", label: "行业" },
  { key: "market_env_score", label: "市场环境" },
];

function scoreColor(v: number | null | undefined) {
  if (v == null) return "text-muted-foreground";
  if (v >= 60) return "text-red-600 font-semibold";
  if (v >= 40) return "text-amber-600";
  return "text-green-700";
}

function scoreCell(v: number | null | undefined) {
  if (v == null) return <span className="text-muted-foreground text-xs">—</span>;
  return <span className={scoreColor(v)}>{v.toFixed(1)}</span>;
}

interface Props {
  open: boolean;
  onClose: () => void;
  groupId: number;
  groupName: string;
  stocks: Stock[];
}

export function GroupCompareTable({ open, onClose, groupId, groupName, stocks }: Props) {
  const toast = useToast();
  const [rows, setRows] = useState<V5Row[]>([]);
  const [loading, setLoading] = useState(false);
  const [sortKey, setSortKey] = useState<keyof V5Row>("composite_v5");
  const [sortDir, setSortDir] = useState<"desc" | "asc">("desc");

  const load = useCallback(async () => {
    if (stocks.length === 0) return;
    setLoading(true);
    try {
      const res = await fetch("/api/v5/scores/bulk", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ stock_ids: stocks.map((s) => s.id) }),
      });
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      const data = await res.json();
      setRows(data.scores ?? []);
    } catch (e: unknown) {
      toast.error(e instanceof Error ? e.message : "加载对比数据失败");
    } finally {
      setLoading(false);
    }
  }, [stocks, toast]);

  useEffect(() => {
    if (open) load();
  }, [open, load]);

  const handleSort = (key: keyof V5Row) => {
    if (key === sortKey) {
      setSortDir((d) => (d === "desc" ? "asc" : "desc"));
    } else {
      setSortKey(key);
      setSortDir("desc");
    }
  };

  const sorted = [...rows].sort((a, b) => {
    const va = (a[sortKey] as number | null) ?? -Infinity;
    const vb = (b[sortKey] as number | null) ?? -Infinity;
    return sortDir === "desc" ? vb - va : va - vb;
  });

  return (
    <Dialog open={open} onOpenChange={onClose}>
      <DialogContent className="sm:max-w-5xl max-h-[85vh] flex flex-col p-0 gap-0">
        <DialogHeader className="px-5 pt-4 pb-3 border-b shrink-0 flex flex-row items-center justify-between">
          <DialogTitle className="text-base">
            分组对比 — {groupName}
            <span className="text-sm font-normal text-muted-foreground ml-2">({stocks.length} 只)</span>
          </DialogTitle>
          <button
            onClick={() => exportCsv(
              `compare_${groupName}.csv`,
              ["代码", "名称", ...DIM_COLS.map((c) => c.label), "状态"],
              sorted.map((r) => [
                r.code, r.name,
                ...DIM_COLS.map((c) => (r[c.key] as number | null)?.toFixed(1) ?? ""),
                r.veto_status ?? "",
              ])
            )}
            className="text-xs text-muted-foreground hover:text-foreground px-2 py-1 rounded border hover:bg-accent transition-colors shrink-0"
          >
            导出 CSV
          </button>
        </DialogHeader>

        <div className="overflow-auto flex-1">
          {loading ? (
            <div className="flex items-center justify-center h-32 text-sm text-muted-foreground animate-pulse">
              加载中…
            </div>
          ) : (
            <table className="w-full text-xs border-collapse">
              <thead className="sticky top-0 bg-card z-10 border-b">
                <tr>
                  <th className="py-2 px-3 text-left font-medium text-muted-foreground w-28">股票</th>
                  {DIM_COLS.map((col) => (
                    <th
                      key={col.key}
                      className="py-2 px-2 text-right font-medium text-muted-foreground cursor-pointer hover:text-foreground select-none whitespace-nowrap"
                      onClick={() => handleSort(col.key)}
                    >
                      <span className="flex items-center justify-end gap-0.5">
                        {col.label}
                        {sortKey === col.key ? (
                          <span className="text-primary">{sortDir === "desc" ? "↓" : "↑"}</span>
                        ) : (
                          <ArrowUpDown className="h-2.5 w-2.5 opacity-30" />
                        )}
                      </span>
                    </th>
                  ))}
                  <th className="py-2 px-2 text-right font-medium text-muted-foreground">状态</th>
                </tr>
              </thead>
              <tbody>
                {sorted.map((r) => (
                  <tr key={r.stock_id} className="border-b hover:bg-muted/40 transition-colors">
                    <td className="py-2 px-3">
                      <div className="font-medium truncate max-w-[6rem]">{r.name}</div>
                      <div className="font-mono text-muted-foreground">{r.code}</div>
                    </td>
                    {DIM_COLS.map((col) => (
                      <td key={col.key} className="py-2 px-2 text-right tabular-nums">
                        {scoreCell(r[col.key] as number | null)}
                      </td>
                    ))}
                    <td className="py-2 px-2 text-right">
                      {r.veto_status === "exclude" ? (
                        <span className="text-gray-500">回避</span>
                      ) : r.veto_status === "reduce" ? (
                        <span className="text-amber-600">减仓</span>
                      ) : (
                        <span className="text-muted-foreground">正常</span>
                      )}
                    </td>
                  </tr>
                ))}
                {sorted.length === 0 && !loading && (
                  <tr>
                    <td colSpan={DIM_COLS.length + 2} className="py-10 text-center text-muted-foreground">
                      暂无 V5 评分数据
                    </td>
                  </tr>
                )}
              </tbody>
            </table>
          )}
        </div>
      </DialogContent>
    </Dialog>
  );
}
