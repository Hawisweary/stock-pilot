"use client";

import { useEffect, useState } from "react";
import Link from "next/link";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Gauge, TrendingUp, TrendingDown, Activity, Droplets, Waves } from "lucide-react";
import { api } from "@/lib/api";

interface RegimeGuidance {
  regime: string;
  regime_label: string;
  max_position: number;
  stop_width_mult: number;
  note: string;
}

export interface MarketRegimeData {
  trade_date?: string;
  regime?: string;
  regime_label?: string;
  rsi_14?: number;
  volatility_20?: number;
  ad_ratio?: number;
  amount_ratio_20?: number;
  rotation_speed?: number;
  avg_corr_20?: number;
  liquidity_score?: number;
  return_20d?: number;
  guidance?: RegimeGuidance;
  weight_note?: string;
  error?: string;
}

const REGIME_STYLE: Record<string, { icon: typeof TrendingUp; className: string }> = {
  strong_trend_up: { icon: TrendingUp, className: "text-red-600 bg-red-500/10" },
  weak_trend_up: { icon: TrendingUp, className: "text-red-600 bg-red-500/10" },
  strong_trend_down: { icon: TrendingDown, className: "text-green-600 bg-green-500/10" },
  weak_trend_down: { icon: TrendingDown, className: "text-green-600 bg-green-500/10" },
  oscillation: { icon: Activity, className: "text-muted-foreground bg-muted" },
  high_volatility: { icon: Waves, className: "text-amber-700 dark:text-amber-500 bg-amber-500/10" },
  liquidity_drought: { icon: Droplets, className: "text-blue-700 dark:text-blue-400 bg-blue-500/10" },
};

function fmtPct(v: number | undefined, digits = 1) {
  if (v == null || Number.isNaN(v)) return "—";
  return `${(v * 100).toFixed(digits)}%`;
}

function fmtNum(v: number | undefined, digits = 1) {
  if (v == null || Number.isNaN(v)) return "—";
  return v.toFixed(digits);
}

export function MarketRegimeCard({ compact = false }: { compact?: boolean }) {
  const [data, setData] = useState<MarketRegimeData | null>(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    setLoading(true);
    api
      .getMarketRegime()
      .then((d) => setData(d as unknown as MarketRegimeData))
      .catch(() => setData(null))
      .finally(() => setLoading(false));
  }, []);

  if (loading) {
    return (
      <Card>
        <CardHeader className="pb-2">
          <CardTitle className="text-sm flex items-center gap-2">
            <Gauge className="h-4 w-4" /> 市场状态
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
            <Gauge className="h-4 w-4" /> 市场状态
          </CardTitle>
        </CardHeader>
        <CardContent className="text-xs text-muted-foreground">
          {data?.error || "暂无市场状态数据"}
        </CardContent>
      </Card>
    );
  }

  const regime = data.regime || "oscillation";
  const style = REGIME_STYLE[regime] || REGIME_STYLE.oscillation;
  const Icon = style.icon;
  const label = data.regime_label || data.guidance?.regime_label || regime;
  const guidance = data.guidance;

  return (
    <Card>
      <CardHeader className="pb-2 flex flex-row items-center justify-between gap-2">
        <CardTitle className="text-sm flex items-center gap-2">
          <Gauge className="h-4 w-4" /> 市场状态
        </CardTitle>
        {data.trade_date && (
          <span className="text-[10px] text-muted-foreground font-mono">{data.trade_date}</span>
        )}
      </CardHeader>
      <CardContent className="pt-0 space-y-3">
        <div className="flex items-start gap-3">
          <div className={`rounded-md p-2 ${style.className}`}>
            <Icon className="h-5 w-5" />
          </div>
          <div className="flex-1 min-w-0">
            <div className="font-semibold text-base">{label}</div>
            {guidance && (
              <div className="text-[11px] text-muted-foreground mt-0.5 leading-relaxed">
                建议仓位 ≤ {Math.round(guidance.max_position * 100)}% · {guidance.note}
              </div>
            )}
            {data.weight_note && (
              <div className="text-[10px] text-primary/80 mt-1">
                V5 权重：{data.weight_note}
              </div>
            )}
          </div>
        </div>

        <div className={`grid gap-2 text-center text-xs ${compact ? "grid-cols-2" : "grid-cols-4"}`}>
          <Metric label="RSI(14)" value={fmtNum(data.rsi_14, 0)} />
          <Metric label="20日波动" value={fmtPct(data.volatility_20)} />
          <Metric label="涨跌比" value={data.ad_ratio != null ? fmtPct(data.ad_ratio, 0) : "—"} />
          <Metric label="成交额比" value={data.amount_ratio_20 != null ? fmtNum(data.amount_ratio_20, 2) : "—"} />
        </div>

        {!compact && (data.rotation_speed != null || data.avg_corr_20 != null) && (
          <div className="grid grid-cols-2 gap-2 text-[10px] text-muted-foreground">
            {data.rotation_speed != null && (
              <span>行业轮动 {fmtNum(data.rotation_speed, 2)}</span>
            )}
            {data.avg_corr_20 != null && (
              <span>个股相关 {fmtNum(data.avg_corr_20, 2)}</span>
            )}
          </div>
        )}

        <div className="flex justify-end">
          <Link href="/market" className="text-[10px] text-primary hover:underline">
            市场行情 →
          </Link>
        </div>
      </CardContent>
    </Card>
  );
}

function Metric({ label, value }: { label: string; value: string }) {
  return (
    <div className="rounded-md bg-muted/40 px-2 py-1.5">
      <div className="text-[10px] text-muted-foreground">{label}</div>
      <div className="font-mono font-semibold tabular-nums">{value}</div>
    </div>
  );
}
