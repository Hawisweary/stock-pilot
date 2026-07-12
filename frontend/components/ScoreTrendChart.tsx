"use client";

import { useEffect, useState } from "react";
import {
  LineChart, Line, XAxis, YAxis, CartesianGrid,
  Tooltip, Legend, ResponsiveContainer,
} from "recharts";
import { scoreColor } from "@/lib/scoreColors";

interface TrendPoint {
  calc_date: string;
  score: number | null;
  fundamental_score: number | null;
  technical_score: number | null;
  sentiment_score: number | null;
  capital_score: number | null;
  policy_score: number | null;
  mood_score: number | null;
  val_score: number | null;
}

const DIMS: { key: keyof TrendPoint; label: string; color: string }[] = [
  { key: "score", label: "综合分", color: "#7c3aed" },
  { key: "fundamental_score", label: "基本面", color: "#2563eb" },
  { key: "val_score", label: "估值面", color: "#16a34a" },
  { key: "technical_score", label: "技术面", color: "#d97706" },
  { key: "capital_score", label: "资金面", color: "#dc2626" },
  { key: "sentiment_score", label: "新闻面", color: "#0891b2" },
  { key: "policy_score", label: "政策面", color: "#7c3aed" },
  { key: "mood_score", label: "情绪面", color: "#be185d" },
];

const DAYS_OPTIONS = [7, 30, 90] as const;

interface Props { stockId: number }

export function ScoreTrendChart({ stockId }: Props) {
  const [days, setDays] = useState<7 | 30 | 90>(30);
  const [activeDims, setActiveDims] = useState<Set<string>>(new Set(["score"]));
  const [data, setData] = useState<TrendPoint[]>([]);
  const [industryTrend, setIndustryTrend] = useState<{ date: string; avg_score: number }[]>([]);
  const [industryName, setIndustryName] = useState<string>("");
  const [showBenchmark, setShowBenchmark] = useState(false);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    if (!stockId) return;
    setLoading(true);
    Promise.all([
      fetch(`/api/scores/trend/${stockId}?days=${days}`).then(r => r.json()),
      fetch(`/api/scores/industry-trend?stock_id=${stockId}&days=${days}`).then(r => r.json()).catch(() => null),
    ]).then(([d, ind]) => {
      setData(d.trend ?? []);
      if (ind?.available && ind.trend?.length) {
        setIndustryTrend(ind.trend);
        setIndustryName(ind.industry ?? "行业");
      } else {
        setIndustryTrend([]);
      }
    }).catch(() => setData([]))
      .finally(() => setLoading(false));
  }, [stockId, days]);

  const toggleDim = (key: string) => {
    setActiveDims((prev) => {
      const next = new Set(prev);
      if (next.has(key)) { if (next.size > 1) next.delete(key); }
      else next.add(key);
      return next;
    });
  };

  if (loading) {
    return <div className="h-48 bg-muted animate-pulse rounded-lg" />;
  }

  if (data.length === 0) {
    return (
      <div className="h-32 flex items-center justify-center text-sm text-muted-foreground">
        暂无历史评分数据
      </div>
    );
  }

  // 合并行业均分到每个数据点
  const indMap = new Map(industryTrend.map(p => [p.date, p.avg_score]));
  const mergedData = data.map(d => ({ ...d, industry_avg: indMap.get(d.calc_date) ?? null }));

  return (
    <div className="space-y-3">
      {/* 控制栏 */}
      <div className="flex flex-wrap items-center gap-2">
        <div className="flex gap-1">
          {DAYS_OPTIONS.map((d) => (
            <button
              key={d}
              onClick={() => setDays(d)}
              className={`px-2 py-0.5 text-xs rounded border transition-colors ${
                days === d
                  ? "bg-primary text-primary-foreground border-primary"
                  : "border-border hover:bg-accent"
              }`}
            >
              {d}天
            </button>
          ))}
        </div>
        {industryTrend.length > 0 && (
          <button
            onClick={() => setShowBenchmark(b => !b)}
            className={`px-2 py-0.5 text-xs rounded border transition-colors ${
              showBenchmark
                ? "bg-gray-500 text-white border-gray-500"
                : "border-border text-muted-foreground hover:bg-accent"
            }`}
          >
            {industryName}均分
          </button>
        )}
        <div className="flex flex-wrap gap-1.5">
          {DIMS.map((dim) => (
            <button
              key={dim.key}
              onClick={() => toggleDim(dim.key)}
              className={`px-2 py-0.5 text-xs rounded border transition-colors ${
                activeDims.has(dim.key)
                  ? "text-white border-transparent"
                  : "border-border text-muted-foreground hover:bg-accent"
              }`}
              style={activeDims.has(dim.key) ? { backgroundColor: dim.color } : {}}
            >
              {dim.label}
            </button>
          ))}
        </div>
      </div>

      {/* 折线图 */}
      <ResponsiveContainer width="100%" height={220}>
        <LineChart data={mergedData} margin={{ top: 4, right: 8, bottom: 0, left: -20 }}>
          <CartesianGrid strokeDasharray="3 3" stroke="hsl(var(--border))" />
          <XAxis
            dataKey="calc_date"
            tick={{ fontSize: 10, fill: "hsl(var(--muted-foreground))" }}
            tickFormatter={(v: string) => v.slice(5)}
            interval="preserveStartEnd"
          />
          <YAxis
            domain={[0, 100]}
            tick={{ fontSize: 10, fill: "hsl(var(--muted-foreground))" }}
            width={28}
          />
          <Tooltip
            contentStyle={{
              background: "hsl(var(--popover))",
              border: "1px solid hsl(var(--border))",
              borderRadius: "6px",
              fontSize: 11,
            }}
            formatter={(value: unknown) =>
              value != null ? [(value as number).toFixed(1)] : ["—"]
            }
            labelFormatter={(label: unknown) => `日期 ${label}`}
          />
          {DIMS.filter((d) => activeDims.has(d.key)).map((dim) => (
            <Line
              key={dim.key}
              type="monotone"
              dataKey={dim.key}
              stroke={dim.color}
              dot={false}
              strokeWidth={dim.key === "score" ? 2.5 : 1.5}
              connectNulls
              name={dim.label}
            />
          ))}
          {showBenchmark && (
            <Line
              type="monotone"
              dataKey="industry_avg"
              stroke="#9ca3af"
              dot={false}
              strokeWidth={1.5}
              strokeDasharray="4 2"
              connectNulls
              name={`${industryName}均分`}
            />
          )}
        </LineChart>
      </ResponsiveContainer>
    </div>
  );
}
