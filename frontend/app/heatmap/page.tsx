"use client";
import { useEffect, useState, useMemo } from "react";
import { useRouter } from "next/navigation";
import { X } from "lucide-react";

function heatColor(score: number) {
  if (score >= 80) return "#16a34a";
  if (score >= 65) return "#22c55e";
  if (score >= 50) return "#84cc16";
  if (score >= 35) return "#eab308";
  if (score >= 20) return "#f97316";
  return "#ef4444";
}

const DIM_CN: Record<string, string> = {
  fundamental: "基本面", val: "估值面", technical: "技术面",
  capital: "资金面", sentiment: "新闻面", policy: "政策面",
  mood: "情绪面", quality: "质量", industry: "行业", market_env: "环境",
  composite_v5: "综合",
};

type HeatRow = Record<string, string | number | null | undefined> & {
  stock_id: number; code: string; name: string; industry_sw?: string;
};

export default function HeatmapPage() {
  const [data, setData] = useState<{ dims: string[]; matrix: HeatRow[] } | null>(null);
  const [activeDim, setActiveDim] = useState("composite_v5");
  const [selected, setSelected] = useState<HeatRow | null>(null);
  const router = useRouter();

  useEffect(() => {
    fetch("/api/v5/heatmap")
      .then((r) => r.json())
      .then(setData)
      .catch(() => setData({ dims: [], matrix: [] }));
  }, []);

  // Group by industry
  const grouped = useMemo(() => {
    if (!data?.matrix.length) return [];
    const map = new Map<string, HeatRow[]>();
    for (const row of data.matrix) {
      const ind = (row.industry_sw as string) || "其他";
      const arr = map.get(ind) ?? [];
      arr.push(row);
      map.set(ind, arr);
    }
    // sort each group by activeDim desc
    const sorted: { industry: string; rows: HeatRow[] }[] = [];
    map.forEach((rows, industry) => {
      sorted.push({
        industry,
        rows: [...rows].sort((a, b) => {
          const va = Number(a[activeDim] ?? 0);
          const vb = Number(b[activeDim] ?? 0);
          return vb - va;
        }),
      });
    });
    // sort groups by avg score desc
    return sorted.sort((a, b) => {
      const avgA = a.rows.reduce((s, r) => s + Number(r[activeDim] ?? 0), 0) / a.rows.length;
      const avgB = b.rows.reduce((s, r) => s + Number(r[activeDim] ?? 0), 0) / b.rows.length;
      return avgB - avgA;
    });
  }, [data, activeDim]);

  if (!data) return <div className="animate-pulse h-96 bg-muted rounded" />;

  const dims = data.dims.length ? data.dims : ["composite_v5"];

  return (
    <div className="space-y-4">
      <div className="flex items-start justify-between">
        <div>
          <h1 className="text-xl font-bold">V5 评分热力图</h1>
          <p className="text-xs text-muted-foreground mt-1">按行业分组 · 色块颜色 = 选中维度得分</p>
        </div>
        {/* 维度切换 */}
        <div className="flex flex-wrap gap-1 max-w-sm justify-end">
          {dims.map((d) => (
            <button
              key={d}
              onClick={() => setActiveDim(d)}
              className={`text-[10px] px-2 py-0.5 rounded border transition-colors ${
                activeDim === d
                  ? "bg-primary text-primary-foreground border-primary"
                  : "hover:bg-accent border-border"
              }`}
            >
              {DIM_CN[d] ?? d}
            </button>
          ))}
        </div>
      </div>

      {data.matrix.length === 0 ? (
        <p className="text-sm text-muted-foreground">暂无 V5 数据，请先在数据页同步 V5 或重算评分。</p>
      ) : (
        <div className="flex gap-4">
          {/* Treemap-style grid */}
          <div className="flex-1 space-y-4">
            {grouped.map(({ industry, rows }) => {
              const avg = rows.reduce((s, r) => s + Number(r[activeDim] ?? 0), 0) / rows.length;
              return (
                <div key={industry}>
                  <div className="flex items-center gap-2 mb-1.5">
                    <span className="text-xs font-semibold text-muted-foreground">{industry}</span>
                    <span
                      className="text-[10px] font-bold px-1.5 py-0.5 rounded text-white"
                      style={{ backgroundColor: heatColor(avg) }}
                    >
                      均 {avg.toFixed(0)}
                    </span>
                  </div>
                  <div className="flex flex-wrap gap-1.5">
                    {rows.map((row) => {
                      const v = row[activeDim];
                      const num = v == null ? null : Number(v);
                      const bg = num == null ? "#e5e7eb" : heatColor(num);
                      return (
                        <button
                          key={row.code}
                          onClick={() => setSelected(row)}
                          className="rounded p-1.5 text-left transition-all hover:scale-105 hover:shadow-md"
                          style={{
                            backgroundColor: bg,
                            color: num != null && num >= 50 ? "#fff" : "#1a1a1a",
                            minWidth: "62px",
                          }}
                        >
                          <div className="text-[9px] font-mono opacity-80">{row.code}</div>
                          <div className="text-[10px] font-semibold truncate max-w-[56px]">
                            {String(row.name).slice(0, 4)}
                          </div>
                          <div className="text-xs font-bold">{num != null ? num.toFixed(0) : "—"}</div>
                        </button>
                      );
                    })}
                  </div>
                </div>
              );
            })}
          </div>

          {/* Detail drawer */}
          {selected && (
            <div className="w-56 shrink-0 rounded-xl border bg-card p-4 space-y-3 h-fit sticky top-4">
              <div className="flex items-start justify-between">
                <div>
                  <div className="font-bold text-sm">{selected.name}</div>
                  <div className="text-xs font-mono text-muted-foreground">{selected.code}</div>
                </div>
                <button onClick={() => setSelected(null)} className="text-muted-foreground hover:text-foreground">
                  <X className="h-4 w-4" />
                </button>
              </div>
              <div className="space-y-1.5">
                {dims.map((d) => {
                  const num = selected[d] == null ? null : Number(selected[d]);
                  const bg = num == null ? "#e5e7eb" : heatColor(num);
                  return (
                    <div key={d} className="flex items-center justify-between text-xs">
                      <span className="text-muted-foreground">{DIM_CN[d] ?? d}</span>
                      <span
                        className="font-bold px-1.5 py-0.5 rounded text-[10px] text-white"
                        style={{ backgroundColor: num != null ? bg : "#9ca3af" }}
                      >
                        {num != null ? num.toFixed(0) : "—"}
                      </span>
                    </div>
                  );
                })}
              </div>
              <button
                onClick={() => router.push(`/stocks/${selected.stock_id}`)}
                className="w-full text-xs bg-primary text-primary-foreground rounded px-3 py-1.5 hover:opacity-90"
              >
                查看详情 →
              </button>
            </div>
          )}
        </div>
      )}
    </div>
  );
}
