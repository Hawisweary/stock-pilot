"use client";

import { useEffect, useState } from "react";

interface ValData {
  available: boolean;
  date?: string;
  industry?: string;
  industry_sample_n?: number;
  val_score?: number;
  pe?: number;
  pb?: number;
  pe_cheap_pct?: number;
  pb_cheap_pct?: number;
  live_pe?: number;
  live_pb?: number;
  live_market_cap?: number;
  live_date?: string;
}

interface Props { stockId: number }

function PercentileBar({ label, value, raw, unit }: {
  label: string;
  value: number | null | undefined;
  raw: number | null | undefined;
  unit: string;
}) {
  if (value == null) return null;
  const pct = Math.max(0, Math.min(100, value));
  // 颜色：pct高=便宜=绿；低=贵=红
  const color = pct >= 60 ? "bg-green-500" : pct >= 35 ? "bg-amber-400" : "bg-red-500";

  return (
    <div className="space-y-1">
      <div className="flex items-center justify-between text-xs">
        <span className="text-muted-foreground">{label}</span>
        <span className="font-medium tabular-nums">
          {raw != null ? `${raw.toFixed(1)}${unit}` : "—"}
          <span className="text-muted-foreground ml-1.5">({pct.toFixed(0)}%便宜)</span>
        </span>
      </div>
      <div className="relative h-1.5 rounded-full bg-muted overflow-hidden">
        <div className={`absolute left-0 top-0 h-full rounded-full transition-all ${color}`}
          style={{ width: `${pct}%` }} />
        {/* 中位线 */}
        <div className="absolute top-0 bottom-0 w-px bg-border/60" style={{ left: "50%" }} />
      </div>
    </div>
  );
}

export function ValuationPercentileBar({ stockId }: Props) {
  const [data, setData] = useState<ValData | null>(null);

  useEffect(() => {
    if (!stockId) return;
    fetch(`/api/v5/valuation-percentile/${stockId}`)
      .then(r => r.ok ? r.json() : null)
      .then(d => setData(d))
      .catch(() => {});
  }, [stockId]);

  if (!data || !data.available) return null;

  const hasLive = data.live_pe != null || data.live_pb != null;
  const liveDate = data.live_date ?? "";
  const scoreDate = data.date ?? "";
  const isStale = hasLive && liveDate !== scoreDate;

  return (
    <div className="rounded-lg border bg-card px-3 py-2.5 space-y-2.5">
      <div className="flex items-center justify-between">
        <p className="text-xs font-medium">估值百分位</p>
        <span className="text-xs text-muted-foreground">
          {data.industry ?? "全市场"}
          {data.industry_sample_n ? ` · ${data.industry_sample_n}只` : ""}
        </span>
      </div>
      <PercentileBar label="市盈率 PE-TTM" value={data.pe_cheap_pct} raw={data.pe} unit="x" />
      <PercentileBar label="市净率 PB" value={data.pb_cheap_pct} raw={data.pb} unit="x" />
      {hasLive && isStale && (
        <div className="flex gap-3 pt-1 border-t border-border/50 text-xs text-muted-foreground">
          <span className="font-medium text-foreground">今日实时</span>
          {data.live_pe != null && <span>PE {data.live_pe.toFixed(1)}x</span>}
          {data.live_pb != null && <span>PB {data.live_pb.toFixed(2)}x</span>}
          {data.live_market_cap != null && (
            <span>{(data.live_market_cap / 1e8).toFixed(0)}亿</span>
          )}
        </div>
      )}
      <p className="text-[10px] text-muted-foreground">
        百分位越高 = 相对同行业越便宜；中线 = 行业中位数
        {scoreDate && <span> · 评分日期 {scoreDate}</span>}
      </p>
    </div>
  );
}
