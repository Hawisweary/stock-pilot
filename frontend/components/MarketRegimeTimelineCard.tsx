"use client";

import { useCallback, useEffect, useMemo, useState } from "react";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { CalendarRange } from "lucide-react";
import {
  Bar,
  BarChart,
  CartesianGrid,
  Cell,
  Legend,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";
import { api } from "@/lib/api";

type RegimeSegment = {
  bucket: string;
  bucket_label: string;
  start_date: string;
  end_date: string;
  days: number;
};

type RegimeHistory = {
  primary_label?: string;
  sample_days?: number;
  start_date?: string;
  end_date?: string;
  bucket_order?: string[];
  bucket_labels?: Record<string, string>;
  distribution?: Record<string, number>;
  distribution_pct?: Record<string, number>;
  distribution_raw?: Record<string, number> | null;
  distribution_raw_pct?: Record<string, number> | null;
  persistence_min_days?: number;
  persistence_asymmetric?: boolean;
  persistence_confirm_days?: Record<string, number> | null;
  segments?: RegimeSegment[];
  segments_raw?: RegimeSegment[] | null;
  error?: string;
};

const BUCKET_COLORS: Record<string, string> = {
  trend_up: "#ef4444",
  high_vol: "#f59e0b",
  oscillation: "#94a3b8",
  trend_down: "#22c55e",
};

const PERIOD_OPTIONS = [
  { label: "1年", days: 252 },
  { label: "1.5年", days: 365 },
  { label: "3年", days: 730 },
] as const;

function PeriodToggle({
  days,
  onChange,
}: {
  days: number;
  onChange: (d: number) => void;
}) {
  return (
    <div className="flex gap-1">
      {PERIOD_OPTIONS.map((opt) => (
        <button
          key={opt.days}
          type="button"
          onClick={() => onChange(opt.days)}
          className={`rounded px-2 py-0.5 text-[10px] transition-colors ${
            days === opt.days
              ? "bg-primary text-primary-foreground"
              : "bg-muted text-muted-foreground hover:bg-muted/80"
          }`}
        >
          {opt.label}
        </button>
      ))}
    </div>
  );
}

function RegimeTimeline({
  label,
  segments,
  totalDays,
  height = 28,
  muted = false,
}: {
  label: string;
  segments: RegimeSegment[];
  totalDays: number;
  height?: number;
  muted?: boolean;
}) {
  const [hover, setHover] = useState<RegimeSegment | null>(null);
  if (!segments.length || totalDays <= 0) return null;

  return (
    <div className="space-y-1.5">
      <div className="text-[10px] font-medium text-muted-foreground">{label}</div>
      <div
        className={`flex w-full overflow-hidden rounded-md border ${muted ? "opacity-55" : ""}`}
        style={{ height }}
      >
        {segments.map((seg) => {
          const widthPct = Math.max((seg.days / totalDays) * 100, 0.35);
          return (
            <div
              key={`${label}-${seg.start_date}-${seg.bucket}-${seg.end_date}`}
              className="h-full cursor-default transition-opacity hover:opacity-80"
              style={{
                width: `${widthPct}%`,
                backgroundColor: BUCKET_COLORS[seg.bucket] || BUCKET_COLORS.oscillation,
                minWidth: seg.days <= 2 ? "2px" : undefined,
              }}
              onMouseEnter={() => setHover(seg)}
              onMouseLeave={() => setHover(null)}
            />
          );
        })}
      </div>
      {hover && (
        <div className="rounded-md border bg-card px-2.5 py-1.5 text-[11px]">
          <span
            className="inline-block h-2 w-2 rounded-full mr-1.5 align-middle"
            style={{ backgroundColor: BUCKET_COLORS[hover.bucket] }}
          />
          <span className="font-medium">{hover.bucket_label}</span>
          <span className="text-muted-foreground">
            {" "}
            · {hover.start_date} → {hover.end_date} · {hover.days} 交易日
          </span>
        </div>
      )}
    </div>
  );
}

function TrendSegmentTable({ segments }: { segments: RegimeSegment[] }) {
  const trend = segments.filter((s) => s.bucket === "trend_up" || s.bucket === "trend_down");
  if (!trend.length) return null;
  return (
    <div className="rounded-md border overflow-hidden">
      <div className="px-2.5 py-1.5 text-[10px] font-medium bg-muted/40 border-b">趋势波段明细</div>
      <div className="max-h-[140px] overflow-y-auto">
        <table className="w-full text-[10px]">
          <thead>
            <tr className="text-muted-foreground border-b">
              <th className="text-left font-normal px-2 py-1">状态</th>
              <th className="text-left font-normal px-2 py-1">起始</th>
              <th className="text-left font-normal px-2 py-1">结束</th>
              <th className="text-right font-normal px-2 py-1">天数</th>
            </tr>
          </thead>
          <tbody>
            {trend.map((s) => (
              <tr key={`${s.start_date}-${s.bucket}`} className="border-b last:border-0">
                <td className="px-2 py-1">
                  <span
                    className="inline-block h-1.5 w-1.5 rounded-full mr-1"
                    style={{ backgroundColor: BUCKET_COLORS[s.bucket] }}
                  />
                  {s.bucket_label}
                </td>
                <td className="px-2 py-1 font-mono">{s.start_date}</td>
                <td className="px-2 py-1 font-mono">{s.end_date}</td>
                <td className="px-2 py-1 text-right font-mono font-semibold">{s.days}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}

export function MarketRegimeTimelineCard() {
  const [days, setDays] = useState(730);
  const [data, setData] = useState<RegimeHistory | null>(null);
  const [loading, setLoading] = useState(true);

  const load = useCallback(() => {
    setLoading(true);
    api
      .getMarketRegimeHistory({ days, primary: "csi800" })
      .then((d) => setData(d as unknown as RegimeHistory))
      .catch(() => setData(null))
      .finally(() => setLoading(false));
  }, [days]);

  useEffect(() => {
    load();
  }, [load]);

  const compareChartData = useMemo(() => {
    if (!data?.bucket_order || !data.distribution) return [];
    return data.bucket_order.map((b) => ({
      label: data.bucket_labels?.[b] || b,
      confirmed: data.distribution?.[b] ?? 0,
      raw: data.distribution_raw?.[b] ?? 0,
      fill: BUCKET_COLORS[b] || BUCKET_COLORS.oscillation,
    }));
  }, [data]);

  if (loading) {
    return (
      <Card>
        <CardHeader className="pb-2">
          <CardTitle className="text-sm flex items-center gap-2">
            <CalendarRange className="h-4 w-4" /> 市场状态周期
          </CardTitle>
        </CardHeader>
        <CardContent className="text-xs text-muted-foreground">加载中…</CardContent>
      </Card>
    );
  }

  if (!data || data.error) {
    return (
      <Card>
        <CardHeader className="pb-2">
          <CardTitle className="text-sm flex items-center gap-2">
            <CalendarRange className="h-4 w-4" /> 市场状态周期
          </CardTitle>
        </CardHeader>
        <CardContent className="text-xs text-muted-foreground">
          {data?.error || "暂无历史状态数据"}
        </CardContent>
      </Card>
    );
  }

  const segments = data.segments ?? [];
  const segmentsRaw = data.segments_raw ?? [];

  return (
    <Card>
      <CardHeader className="pb-2 flex flex-row items-center justify-between gap-2">
        <CardTitle className="text-sm flex items-center gap-2">
          <CalendarRange className="h-4 w-4" /> 市场状态周期
          <span className="text-[10px] font-normal text-muted-foreground">
            {data.primary_label} ·{" "}
            {data.persistence_asymmetric
              ? `↑${data.persistence_confirm_days?.trend_up} ↓${data.persistence_confirm_days?.trend_down}天确认`
              : `${data.persistence_min_days ?? 5}日确认`}
          </span>
        </CardTitle>
        <PeriodToggle days={days} onChange={setDays} />
      </CardHeader>
      <CardContent className="pt-0 space-y-4">
        <div className="text-[10px] text-muted-foreground">
          {data.start_date} → {data.end_date} · {data.sample_days} 交易日 · 确认段 {segments.length}
          段
          {segmentsRaw.length > 0 && ` · 日频段 ${segmentsRaw.length} 段`}
        </div>

        <div className="flex flex-wrap gap-3 text-[10px]">
          {Object.entries(BUCKET_COLORS).map(([k, c]) => (
            <span key={k} className="inline-flex items-center gap-1">
              <span className="h-2 w-2 rounded-sm" style={{ backgroundColor: c }} />
              {data.bucket_labels?.[k]}
            </span>
          ))}
        </div>

        {segmentsRaw.length > 0 && (
          <RegimeTimeline
            label="日频快照（raw）"
            segments={segmentsRaw}
            totalDays={data.sample_days ?? 1}
            height={22}
            muted
          />
        )}
        <RegimeTimeline
          label="持续确认（confirmed）"
          segments={segments}
          totalDays={data.sample_days ?? 1}
          height={32}
        />
        <div className="flex justify-between text-[10px] text-muted-foreground font-mono px-0.5">
          <span>{data.start_date}</span>
          <span>{data.end_date}</span>
        </div>

        <TrendSegmentTable segments={segments} />

        <div className="grid grid-cols-4 gap-2 text-center text-[10px]">
          {(data.bucket_order ?? []).map((b) => (
            <div key={b} className="rounded-md bg-muted/30 px-1 py-1.5">
              <div className="flex items-center justify-center gap-1">
                <span
                  className="inline-block h-2 w-2 rounded-full"
                  style={{ backgroundColor: BUCKET_COLORS[b] }}
                />
                <span>{data.bucket_labels?.[b]}</span>
              </div>
              <div className="font-mono font-semibold tabular-nums mt-0.5">
                {data.distribution_pct?.[b] ?? 0}%
              </div>
              <div className="text-muted-foreground">
                {data.distribution?.[b] ?? 0} 天
                {data.distribution_raw && (
                  <span className="block opacity-70">raw {data.distribution_raw[b] ?? 0}</span>
                )}
              </div>
            </div>
          ))}
        </div>

        {compareChartData.length > 0 && data.distribution_raw && (
          <div className="h-[160px]">
            <ResponsiveContainer width="100%" height="100%">
              <BarChart data={compareChartData} margin={{ top: 4, right: 8, left: -16, bottom: 0 }}>
                <CartesianGrid strokeDasharray="3 3" className="stroke-muted" vertical={false} />
                <XAxis dataKey="label" tick={{ fontSize: 10 }} />
                <YAxis tick={{ fontSize: 10 }} width={28} label={{ value: "天", angle: 0, position: "insideTopLeft", fontSize: 10 }} />
                <Tooltip contentStyle={{ fontSize: 11 }} />
                <Legend wrapperStyle={{ fontSize: 10 }} />
                <Bar dataKey="raw" name="日频" fill="#94a3b8" radius={[3, 3, 0, 0]} opacity={0.45} />
                <Bar dataKey="confirmed" name="确认" radius={[3, 3, 0, 0]}>
                  {compareChartData.map((entry) => (
                    <Cell key={entry.label} fill={entry.fill} />
                  ))}
                </Bar>
              </BarChart>
            </ResponsiveContainer>
          </div>
        )}
      </CardContent>
    </Card>
  );
}
