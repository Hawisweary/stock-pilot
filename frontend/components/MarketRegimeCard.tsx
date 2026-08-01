"use client";

import { useEffect, useState } from "react";
import Link from "next/link";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Gauge, TrendingUp, TrendingDown, Activity, Droplets, Waves, AlertTriangle } from "lucide-react";
import { api } from "@/lib/api";

interface RegimeGuidance {
  regime: string;
  regime_label: string;
  max_position: number;
  stop_width_mult: number;
  note: string;
}

interface RegimeIndexSnapshot {
  index_code: string;
  index_name: string;
  regime: string;
  regime_label: string;
  regime_bucket?: string;
  regime_bucket_label?: string;
  rsi_14?: number;
  volatility_20?: number;
  adx?: number;
  return_20d?: number;
  price_vs_ma60?: number;
}

export interface MarketRegimeData {
  trade_date?: string;
  regime?: string;
  regime_label?: string;
  regime_csi300?: string;
  regime_csi300_label?: string;
  regime_csi800?: string;
  regime_csi800_label?: string;
  primary_index?: string;
  primary_regime?: string;
  primary_regime_label?: string;
  primary_regime_bucket_label?: string;
  regime_label_agreement?: boolean;
  regime_bucket_agreement?: boolean;
  indices?: RegimeIndexSnapshot[];
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

function RegimeBadge({ snapshot }: { snapshot: RegimeIndexSnapshot }) {
  const regime = snapshot.regime || "oscillation";
  const style = REGIME_STYLE[regime] || REGIME_STYLE.oscillation;
  const Icon = style.icon;
  return (
    <div className="rounded-md border bg-card/50 p-2.5 space-y-1">
      <div className="flex items-center justify-between gap-2">
        <span className="text-[10px] font-medium text-muted-foreground">{snapshot.index_name}</span>
        <div className={`rounded p-1 ${style.className}`}>
          <Icon className="h-3.5 w-3.5" />
        </div>
      </div>
      <div className="font-semibold text-sm">{snapshot.regime_label}</div>
      {snapshot.regime_bucket_label && (
        <div className="text-[10px] text-muted-foreground">四格：{snapshot.regime_bucket_label}</div>
      )}
      <div className="grid grid-cols-2 gap-1 text-[10px] text-muted-foreground">
        <span>波动 {fmtPct(snapshot.volatility_20)}</span>
        <span>RSI {fmtNum(snapshot.rsi_14, 0)}</span>
      </div>
    </div>
  );
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

  const indices = data.indices ?? [];
  const primaryLabel = data.primary_regime_label || data.regime_label || "震荡";
  const primaryRegime = data.primary_regime || data.regime || "oscillation";
  const style = REGIME_STYLE[primaryRegime] || REGIME_STYLE.oscillation;
  const Icon = style.icon;
  const guidance = data.guidance;
  const disagree = data.regime_label_agreement === false;

  return (
    <Card>
      <CardHeader className="pb-2 flex flex-row items-center justify-between gap-2">
        <CardTitle className="text-sm flex items-center gap-2">
          <Gauge className="h-4 w-4" /> 市场状态
          <span className="text-[10px] font-normal text-muted-foreground">双轨</span>
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
            <div className="text-[10px] text-muted-foreground">推荐基准 · 中证800</div>
            <div className="font-semibold text-base">{primaryLabel}</div>
            {data.primary_regime_bucket_label && (
              <div className="text-[11px] text-muted-foreground mt-0.5">
                四格分类：{data.primary_regime_bucket_label}
              </div>
            )}
            {guidance && (
              <div className="text-[11px] text-muted-foreground mt-0.5 leading-relaxed">
                建议仓位 ≤ {Math.round(guidance.max_position * 100)}% · {guidance.note}
              </div>
            )}
            {data.weight_note && (
              <div className="text-[10px] text-primary/80 mt-1">
                V5 权重（沪深300口径）：{data.weight_note}
              </div>
            )}
          </div>
        </div>

        {!compact && indices.length >= 2 && (
          <div className="grid grid-cols-2 gap-2">
            {indices.map((snap) => (
              <RegimeBadge key={snap.index_code} snapshot={snap} />
            ))}
          </div>
        )}

        {disagree && (
          <div className="flex items-start gap-1.5 rounded-md bg-amber-500/10 px-2 py-1.5 text-[10px] text-amber-800 dark:text-amber-300">
            <AlertTriangle className="h-3.5 w-3.5 shrink-0 mt-0.5" />
            <span>
              沪深300 与 中证800 判断不一致，可能存在大小盘风格分化；策略推荐将以中证800 为准。
            </span>
          </div>
        )}

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
