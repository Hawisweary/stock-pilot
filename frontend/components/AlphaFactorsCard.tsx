"use client";

import { useEffect, useState } from "react";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Sparkles, Info } from "lucide-react";

interface Surprise {
  period_end_date: string;
  actual_growth: number | null;
  guided_growth: number | null;
  surprise_pct: number | null;
  tier: number;
  actual_ann_date: string;
}

interface IndustryValuation {
  pe_pct_cheap: number | null;
  pb_pct_cheap: number | null;
  combined_cheap_pct: number | null;
  industry: string | null;
  tier: number;
}

function tierTone(tier: number): string {
  if (tier >= 2) return "text-red-600";
  if (tier >= 1) return "text-red-500";
  if (tier <= -2) return "text-green-600";
  if (tier <= -1) return "text-green-500";
  return "text-muted-foreground";
}

function fmtPct(v: number | null): string {
  if (v == null) return "--";
  return `${v >= 0 ? "+" : ""}${v.toFixed(1)}%`;
}

export function AlphaFactorsCard({ stockId }: { stockId: number }) {
  const [surprise, setSurprise] = useState<Surprise[]>([]);
  const [valuation, setValuation] = useState<IndustryValuation | null>(null);
  const [loading, setLoading] = useState(true);
  const [showInfo, setShowInfo] = useState(false);

  useEffect(() => {
    if (!stockId) return;
    setLoading(true);
    fetch(`/api/stocks/${stockId}/alpha-factors`)
      .then((r) => (r.ok ? r.json() : null))
      .then((d) => {
        setSurprise(d?.earnings_surprise ?? []);
        setValuation(d?.industry_neutral_valuation ?? null);
      })
      .catch(() => {})
      .finally(() => setLoading(false));
  }, [stockId]);

  if (loading) return null;
  if (surprise.length === 0 && !valuation) return null;

  return (
    <Card>
      <CardHeader className="pb-2">
        <CardTitle className="text-sm flex items-center justify-between">
          <span className="flex items-center gap-2">
            <Sparkles className="h-4 w-4" /> Alpha 因子(v1)
          </span>
          <button
            onClick={() => setShowInfo(!showInfo)}
            className="text-muted-foreground hover:text-foreground"
            title="因子说明"
          >
            <Info className="h-3.5 w-3.5" />
          </button>
        </CardTitle>
        <p className="text-[11px] text-muted-foreground">独立信号，暂未并入V5综合分，仅供参考</p>
      </CardHeader>
      <CardContent className="pt-0 space-y-3">
        {showInfo && (
          <div className="rounded-md bg-muted/40 px-2.5 py-2 space-y-2 text-[11px] text-muted-foreground leading-relaxed">
            <div>
              <span className="font-medium text-foreground">行业中性估值</span>
              ：个股 PE/PB 在同申万行业内的"便宜度"横截面分位(取代 PE/PB 越高越差的排名后转成 0~100，数值越高越便宜)，
              两者简单平均后映射为 -2~+2 档。因为是行业内比较，天然消除了行业整体估值水平差异的影响。
            </div>
            <div>
              <span className="font-medium text-foreground">盈余惊喜</span>
              ：(年报实际EPS − 年初一致预期EPS) / |年初一致预期EPS|。仅覆盖同时有"业绩快报"和"年初一致预期EPS快照"两项数据的年报期，
              覆盖率天然受限(业绩快报纯自愿披露)。偏离&gt;20%记+2档，5%~20%记+1档，±5%内中性，-20%~-5%记-1档，&lt;-20%记-2档。
            </div>
          </div>
        )}
        {valuation && (
          <div>
            <p className="text-xs font-medium mb-1">行业中性估值</p>
            <p className="text-xs text-muted-foreground">
              {valuation.industry ?? "同行业"}内便宜度分位
              <span className={`font-semibold ml-1 ${tierTone(valuation.tier)}`}>
                {valuation.combined_cheap_pct?.toFixed(0) ?? "--"}%
              </span>
              {valuation.pe_pct_cheap != null && (
                <span className="ml-2">(PE {valuation.pe_pct_cheap.toFixed(0)}% / PB {valuation.pb_pct_cheap?.toFixed(0) ?? "--"}%)</span>
              )}
            </p>
          </div>
        )}
        {surprise.length > 0 && (
          <div>
            <p className="text-xs font-medium mb-1">盈余惊喜(年报实际EPS vs 年初一致预期)</p>
            {surprise.map((s, i) => (
              <div key={i} className="text-xs text-muted-foreground py-0.5">
                {s.period_end_date}：实际 {s.actual_growth?.toFixed(2)} vs 预期 {s.guided_growth?.toFixed(2)}，
                偏离
                <span className={`font-semibold ml-1 ${tierTone(s.tier)}`}>{fmtPct(s.surprise_pct)}</span>
              </div>
            ))}
          </div>
        )}
      </CardContent>
    </Card>
  );
}
