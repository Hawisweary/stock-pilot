"use client";

import { useEffect, useState } from "react";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import {
  fetchDimensionDetail,
  fetchTechnicalCache,
  fetchTechnicalWeekly,
} from "@/lib/marketExtras";

const DIM_META = [
  { key: "capital" as const, label: "资金面", fields: ["flow_score", "turn_score", "change_score"] },
  { key: "policy" as const, label: "政策面", fields: ["composite_score"] },
  { key: "sentiment" as const, label: "情绪面", fields: ["turn_score", "vol_score"] },
];

function MetricGrid({ data, fields }: { data: Record<string, unknown>; fields: string[] }) {
  const rows = fields
    .map((f) => ({ label: f, value: data[f] }))
    .filter((r) => r.value != null && r.value !== "");
  if (!rows.length && data.composite_score != null) {
    rows.push({ label: "综合", value: data.composite_score });
  }
  if (!rows.length) return <p className="text-xs text-muted-foreground">暂无明细</p>;
  return (
    <div className="flex flex-wrap gap-2">
      {rows.map((r) => (
        <span key={r.label} className="text-xs rounded bg-muted/50 px-2 py-0.5">
          {r.label}: <b>{String(r.value)}</b>
        </span>
      ))}
    </div>
  );
}

export function DimensionDetailPanel({ stockId }: { stockId: number }) {
  const [dims, setDims] = useState<Record<string, Record<string, unknown>>>({});
  const [tech, setTech] = useState<{ cached: boolean; analysis: Record<string, unknown> | null } | null>(null);
  const [weekly, setWeekly] = useState<Record<string, unknown> | null>(null);

  useEffect(() => {
    if (!stockId) return;
    Promise.all(
      DIM_META.map(async (d) => {
        try {
          const data = await fetchDimensionDetail(stockId, d.key);
          return [d.key, data] as const;
        } catch {
          return [d.key, {}] as const;
        }
      }),
    ).then((pairs) => setDims(Object.fromEntries(pairs)));

    fetchTechnicalCache(stockId).then(setTech).catch(() => setTech(null));
    fetchTechnicalWeekly(stockId)
      .then((w) => setWeekly(w.data?.[w.data.length - 1] || null))
      .catch(() => setWeekly(null));
  }, [stockId]);

  const analysis = tech?.analysis;

  return (
    <Card>
      <CardHeader className="pb-2">
        <CardTitle className="text-sm">四维度评分明细</CardTitle>
      </CardHeader>
      <CardContent className="space-y-3 pt-0">
        {DIM_META.map((d) => (
          <div key={d.key}>
            <div className="text-xs font-medium mb-1">{d.label}</div>
            <MetricGrid data={dims[d.key] || {}} fields={d.fields} />
          </div>
        ))}

        <div className="border-t pt-2">
          <div className="text-xs font-medium mb-1">
            技术面 AI {tech?.cached ? <span className="text-green-600 font-normal">（缓存）</span> : null}
          </div>
          {analysis ? (
            <div className="text-xs space-y-1">
              {analysis.score != null && <p>评分: <b>{String(analysis.score)}</b></p>}
              {typeof analysis.advice === "string" && analysis.advice && (
                <p className="text-primary">{analysis.advice}</p>
              )}
              {typeof analysis.reasoning === "string" && analysis.reasoning && (
                <p className="text-muted-foreground line-clamp-4">{analysis.reasoning}</p>
              )}
              {analysis.market && typeof analysis.market === "object" && (
                <p className="text-[10px] text-muted-foreground">
                  大盘: {JSON.stringify(analysis.market).slice(0, 120)}…
                </p>
              )}
              {(analysis as { cached_at?: string }).cached_at && (
                <p className="text-[10px] text-muted-foreground">更新 {(analysis as { cached_at?: string }).cached_at}</p>
              )}
            </div>
          ) : (
            <p className="text-xs text-muted-foreground">暂无技术面 AI 缓存，请在评分卡点击「AI」或数据页全量技术面。</p>
          )}
        </div>

        {weekly && (
          <div className="border-t pt-2">
            <div className="text-xs font-medium mb-1">周线指标（最新）</div>
            <div className="flex flex-wrap gap-2 text-xs">
              {["rsi14", "macd_bar", "macd_dif", "close"].map((k) =>
                weekly[k] != null ? (
                  <span key={k} className="rounded bg-muted/50 px-2 py-0.5">
                    {k}: <b>{String(weekly[k])}</b>
                  </span>
                ) : null,
              )}
            </div>
          </div>
        )}
      </CardContent>
    </Card>
  );
}
