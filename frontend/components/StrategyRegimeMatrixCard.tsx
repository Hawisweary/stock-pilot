"use client";

import { useEffect, useState } from "react";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Grid3X3, Trophy, AlertCircle } from "lucide-react";
import { api } from "@/lib/api";

type MatrixCell = {
  sample_days?: number;
  sharpe?: number | null;
  ann_return_pct?: number | null;
  sample_sufficient?: boolean;
  recommended?: boolean;
};

type MatrixPayload = {
  as_of_date?: string;
  bucket_order?: string[];
  bucket_labels?: Record<string, string>;
  matrix?: Record<string, Record<string, MatrixCell>>;
  recommendation?: {
    current_bucket_label?: string;
    confidence?: number;
    regime_summary?: string;
    primary?: { strategy?: string; sharpe?: number; sample_days?: number; ann_return_pct?: number };
    alternatives?: Array<{ strategy?: string; sharpe?: number }>;
    avoid?: { strategy?: string; sharpe?: number };
    hard_rule_strategy?: string;
  };
};

const STRATEGY_LABELS: Record<string, string> = {
  composite: "V5综合",
  momentum: "动量",
  turtle: "海龟",
  index_enhance: "指数增强",
  sector_rotation: "行业轮动",
};

function cellColor(sharpe: number | null | undefined, sufficient: boolean | undefined) {
  if (!sufficient) return "bg-muted/30 text-muted-foreground";
  if (sharpe == null) return "bg-muted/40";
  if (sharpe >= 1.5) return "bg-green-500/15 text-green-700 dark:text-green-400";
  if (sharpe >= 0.5) return "bg-emerald-500/10";
  if (sharpe >= 0) return "bg-muted/50";
  return "bg-red-500/10 text-red-700 dark:text-red-400";
}

export function StrategyRegimeMatrixCard({ compact = false }: { compact?: boolean }) {
  const [data, setData] = useState<MatrixPayload | null>(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    setLoading(true);
    api
      .getStrategyRegimeMatrix(false)
      .then((d) => setData(d as unknown as MatrixPayload))
      .catch(() => setData(null))
      .finally(() => setLoading(false));
  }, []);

  if (loading) {
    return (
      <Card>
        <CardHeader className="pb-2">
          <CardTitle className="text-sm flex items-center gap-2">
            <Grid3X3 className="h-4 w-4" /> 策略×状态矩阵
          </CardTitle>
        </CardHeader>
        <CardContent className="text-xs text-muted-foreground">加载中…</CardContent>
      </Card>
    );
  }

  if (!data?.matrix) {
    return (
      <Card>
        <CardHeader className="pb-2">
          <CardTitle className="text-sm flex items-center gap-2">
            <Grid3X3 className="h-4 w-4" /> 策略×状态矩阵
          </CardTitle>
        </CardHeader>
        <CardContent className="text-xs text-muted-foreground">矩阵数据暂不可用</CardContent>
      </Card>
    );
  }

  const buckets = data.bucket_order ?? [];
  const strategies = Object.keys(data.matrix);
  const rec = data.recommendation;

  return (
    <Card>
      <CardHeader className="pb-2 flex flex-row items-center justify-between gap-2">
        <CardTitle className="text-sm flex items-center gap-2">
          <Grid3X3 className="h-4 w-4" /> 策略×状态矩阵
          <span className="text-[10px] font-normal text-muted-foreground">CSI800 四格</span>
        </CardTitle>
        {data.as_of_date && (
          <span className="text-[10px] text-muted-foreground font-mono">{data.as_of_date}</span>
        )}
      </CardHeader>
      <CardContent className="pt-0 space-y-3 text-xs">
        {rec?.primary && (
          <div className="rounded-md border bg-primary/5 px-2.5 py-2 space-y-1">
            <div className="flex items-center gap-1.5 font-medium">
              <Trophy className="h-3.5 w-3.5 text-primary" />
              当前 {rec.current_bucket_label} → 推荐 {STRATEGY_LABELS[rec.primary.strategy ?? ""] ?? rec.primary.strategy}
            </div>
            <div className="text-[10px] text-muted-foreground pl-5">
              夏普 {rec.primary.sharpe ?? "—"} · {rec.primary.sample_days ?? 0}天样本 · 置信 {Math.round((rec.confidence ?? 0) * 100)}%
            </div>
            {rec.alternatives && rec.alternatives.length > 0 && (
              <div className="text-[10px] text-muted-foreground pl-5">
                备选：{rec.alternatives.map((a) => STRATEGY_LABELS[a.strategy ?? ""] ?? a.strategy).join("、")}
              </div>
            )}
            {rec.avoid && (
              <div className="text-[10px] text-amber-700 dark:text-amber-400 pl-5 flex items-center gap-1">
                <AlertCircle className="h-3 w-3" />
                回避 {STRATEGY_LABELS[rec.avoid.strategy ?? ""] ?? rec.avoid.strategy}
              </div>
            )}
          </div>
        )}

        {!compact && (
          <div className="overflow-x-auto">
            <table className="w-full text-[10px] border-collapse">
              <thead>
                <tr>
                  <th className="text-left p-1 text-muted-foreground font-normal">策略</th>
                  {buckets.map((b) => (
                    <th key={b} className="p-1 text-center text-muted-foreground font-normal min-w-[4rem]">
                      {data.bucket_labels?.[b] ?? b}
                    </th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {strategies.map((s) => (
                  <tr key={s} className="border-t border-border/50">
                    <td className="p-1 font-medium whitespace-nowrap">
                      {STRATEGY_LABELS[s] ?? s}
                    </td>
                    {buckets.map((b) => {
                      const cell = data.matrix?.[s]?.[b];
                      const sh = cell?.sharpe;
                      return (
                        <td key={b} className="p-0.5">
                          <div
                            className={`rounded px-1 py-1 text-center tabular-nums ${cellColor(sh, cell?.sample_sufficient)}`}
                            title={`${cell?.sample_days ?? 0}天 · 年化${cell?.ann_return_pct ?? "—"}%`}
                          >
                            {sh != null ? sh.toFixed(2) : "—"}
                            {cell?.recommended && <span className="text-[8px] ml-0.5">★</span>}
                          </div>
                        </td>
                      );
                    })}
                  </tr>
                ))}
              </tbody>
            </table>
            <div className="text-[9px] text-muted-foreground mt-1">单元格 = 夏普 · ★ = 硬规则推荐策略</div>
          </div>
        )}
      </CardContent>
    </Card>
  );
}
