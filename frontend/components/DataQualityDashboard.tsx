"use client";

import { useEffect, useMemo, useState } from "react";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { api } from "@/lib/api";
import { useToast } from "@/lib/useToast";
import {
  AlertTriangle,
  CheckCircle2,
  Info,
  RefreshCw,
  ShieldAlert,
  TrendingUp,
  BarChart3,
  Activity,
} from "lucide-react";

interface DataQualityAlert {
  stock_id: number;
  anomaly_score: number;
  severity: string;
  flags: string[];
}

interface DataQualitySummary {
  trade_date: string;
  total_alerts: number;
  by_severity: Record<string, number>;
  top_alerts: DataQualityAlert[];
}

interface MarketRegime {
  trade_date: string;
  index_code?: string;
  regime?: string;
  rsi_14?: number;
  volatility_20?: number;
  adx?: number;
  return_20d?: number;
  return_60d?: number;
  price_vs_ma20?: number;
  price_vs_ma60?: number;
  updated_at?: string;
}

interface VolatilitySummary {
  trade_date: string;
  total_records: number;
  avg_realized_vol_20: number;
  avg_forecast_vol_20: number;
  avg_turnover_20: number;
  avg_amount_20: number;
  avg_amihud_illiq_20: number;
  top_volatility: Array<{
    stock_id: number;
    realized_vol_20: number;
    forecast_vol_20: number;
    avg_turnover_20: number;
  }>;
}

const SEVERITY_CONFIG: Record<string, { label: string; icon: typeof ShieldAlert; className: string }> = {
  critical: { label: "严重", icon: ShieldAlert, className: "bg-red-500/10 text-red-700 dark:text-red-400 border-red-500/20" },
  warning: { label: "警告", icon: AlertTriangle, className: "bg-amber-500/10 text-amber-700 dark:text-amber-400 border-amber-500/20" },
  info: { label: "提示", icon: Info, className: "bg-blue-500/10 text-blue-700 dark:text-blue-400 border-blue-500/20" },
};

const REGIME_LABELS: Record<string, string> = {
  strong_trend_up: "强势上涨",
  weak_trend_up: "弱势上涨",
  strong_trend_down: "强势下跌",
  weak_trend_down: "弱势下跌",
  oscillation: "震荡",
  high_volatility: "高波动",
};

export function DataQualityDashboard() {
  const toast = useToast();
  const [loading, setLoading] = useState(true);
  const [detecting, setDetecting] = useState(false);
  const [syncingRegime, setSyncingRegime] = useState(false);
  const [syncingVol, setSyncingVol] = useState(false);
  const [summary, setSummary] = useState<DataQualitySummary | null>(null);
  const [regime, setRegime] = useState<MarketRegime | null>(null);
  const [volatility, setVolatility] = useState<VolatilitySummary | null>(null);
  const [selectedDate, setSelectedDate] = useState<string>("");

  const load = async () => {
    setLoading(true);
    try {
      const [dq, reg, vol] = await Promise.all([
        api.getDataQualitySummary(selectedDate || undefined).catch(() => null),
        api.getMarketRegime(selectedDate || undefined).catch(() => null),
        api.getVolatilityForecast(selectedDate || undefined).catch(() => null),
      ]);
      if (dq) setSummary(dq);
      if (reg) setRegime(reg as unknown as MarketRegime);
      if (vol) setVolatility(vol);
    } catch (e: unknown) {
      toast.error(e instanceof Error ? e.message : "加载数据质量看板失败");
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    load();
  }, [selectedDate]);

  const severityCounts = useMemo(() => {
    if (!summary) return [];
    return Object.entries(summary.by_severity).map(([sev, count]) => {
      const cfg = SEVERITY_CONFIG[sev as keyof typeof SEVERITY_CONFIG] || SEVERITY_CONFIG.info;
      return { severity: sev, count, ...cfg };
    });
  }, [summary]);

  const handleDetect = async () => {
    setDetecting(true);
    try {
      const res = await api.detectDataQuality(selectedDate || undefined);
      toast.success(`数据质量检测完成：${res.total_alerts} 条告警`);
      await load();
    } catch (e: unknown) {
      toast.error(e instanceof Error ? e.message : "检测失败");
    } finally {
      setDetecting(false);
    }
  };

  const handleSyncRegime = async () => {
    setSyncingRegime(true);
    try {
      await api.syncMarketRegime(selectedDate || undefined);
      toast.success("市场状态已同步");
      await load();
    } catch (e: unknown) {
      toast.error(e instanceof Error ? e.message : "同步失败");
    } finally {
      setSyncingRegime(false);
    }
  };

  const handleSyncVol = async () => {
    setSyncingVol(true);
    try {
      await api.syncVolatilityForecast(selectedDate || undefined);
      toast.success("波动率预测已同步");
      await load();
    } catch (e: unknown) {
      toast.error(e instanceof Error ? e.message : "同步失败");
    } finally {
      setSyncingVol(false);
    }
  };

  if (loading) {
    return (
      <div className="space-y-4">
        <div className="h-8 w-48 bg-muted rounded animate-pulse" />
        <div className="grid grid-cols-1 md:grid-cols-3 gap-4">
          {[1, 2, 3].map((i) => (
            <div key={i} className="h-32 bg-muted rounded animate-pulse" />
          ))}
        </div>
      </div>
    );
  }

  return (
    <div className="space-y-6">
      <div className="flex flex-col sm:flex-row sm:items-center justify-between gap-4">
        <div>
          <h1 className="text-2xl font-bold tracking-tight">数据质量看板</h1>
          <p className="text-sm text-muted-foreground">
            {summary?.trade_date || regime?.trade_date || "—"}
          </p>
        </div>
        <div className="flex items-center gap-2">
          <input
            type="date"
            value={selectedDate}
            onChange={(e) => setSelectedDate(e.target.value)}
            className="px-3 py-2 text-sm rounded-md border bg-background"
          />
          <Button onClick={handleDetect} disabled={detecting} size="sm">
            <RefreshCw className={`h-4 w-4 mr-1 ${detecting ? "animate-spin" : ""}`} />
            检测质量
          </Button>
          <Button onClick={handleSyncRegime} disabled={syncingRegime} size="sm" variant="outline">
            <TrendingUp className="h-4 w-4 mr-1" />
            市场状态
          </Button>
          <Button onClick={handleSyncVol} disabled={syncingVol} size="sm" variant="outline">
            <BarChart3 className="h-4 w-4 mr-1" />
            波动率
          </Button>
        </div>
      </div>

      {/* 概览卡片 */}
      <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-4 gap-4">
        <Card>
          <CardHeader className="pb-2">
            <CardTitle className="text-sm flex items-center gap-2">
              <Activity className="h-4 w-4 text-primary" />
              数据质量告警
            </CardTitle>
          </CardHeader>
          <CardContent>
            <div className="text-3xl font-bold">{summary?.total_alerts ?? 0}</div>
            <div className="flex gap-2 mt-2 flex-wrap">
              {severityCounts.map((s) => {
                const Icon = s.icon;
                return (
                  <Badge key={s.severity} variant="outline" className={s.className}>
                    <Icon className="h-3 w-3 mr-1" />
                    {s.label} {s.count}
                  </Badge>
                );
              })}
              {!severityCounts.length && (
                <Badge variant="outline" className="bg-emerald-500/10 text-emerald-700 border-emerald-500/20">
                  <CheckCircle2 className="h-3 w-3 mr-1" />
                  无告警
                </Badge>
              )}
            </div>
          </CardContent>
        </Card>

        <Card>
          <CardHeader className="pb-2">
            <CardTitle className="text-sm flex items-center gap-2">
              <TrendingUp className="h-4 w-4 text-primary" />
              市场状态
            </CardTitle>
          </CardHeader>
          <CardContent>
            <div className="text-2xl font-bold">
              {REGIME_LABELS[regime?.regime ?? ""] || regime?.regime || "—"}
            </div>
            <div className="text-xs text-muted-foreground mt-2 space-y-1">
              <div className="flex justify-between">
                <span>RSI(14)</span>
                <span className="font-mono">{regime?.rsi_14?.toFixed(2) ?? "—"}</span>
              </div>
              <div className="flex justify-between">
                <span>20日波动</span>
                <span className="font-mono">{(regime?.volatility_20 != null ? `${(regime.volatility_20 * 100).toFixed(2)}%` : "—")}</span>
              </div>
              <div className="flex justify-between">
                <span>20日收益</span>
                <span className={`font-mono ${(regime?.return_20d ?? 0) >= 0 ? "text-emerald-600" : "text-red-600"}`}>
                  {(regime?.return_20d != null ? `${(regime.return_20d * 100).toFixed(2)}%` : "—")}
                </span>
              </div>
            </div>
          </CardContent>
        </Card>

        <Card>
          <CardHeader className="pb-2">
            <CardTitle className="text-sm flex items-center gap-2">
              <BarChart3 className="h-4 w-4 text-primary" />
              平均已实现波动率
            </CardTitle>
          </CardHeader>
          <CardContent>
            <div className="text-3xl font-bold">
              {volatility?.avg_realized_vol_20 != null
                ? `${(volatility.avg_realized_vol_20 * 100).toFixed(2)}%`
                : "—"}
            </div>
            <p className="text-xs text-muted-foreground mt-1">20日日均对数收益率标准差</p>
          </CardContent>
        </Card>

        <Card>
          <CardHeader className="pb-2">
            <CardTitle className="text-sm flex items-center gap-2">
              <BarChart3 className="h-4 w-4 text-primary" />
              预测波动率(EWMA)
            </CardTitle>
          </CardHeader>
          <CardContent>
            <div className="text-3xl font-bold">
              {volatility?.avg_forecast_vol_20 != null
                ? `${(volatility.avg_forecast_vol_20 * 100).toFixed(2)}%`
                : "—"}
            </div>
            <p className="text-xs text-muted-foreground mt-1">未来20日 EWMA 预测</p>
          </CardContent>
        </Card>
      </div>

      {/* 质量告警 TOP 列表 */}
      <Card>
        <CardHeader>
          <CardTitle className="text-base">质量告警 TOP20</CardTitle>
        </CardHeader>
        <CardContent>
          {!summary?.top_alerts?.length ? (
            <p className="text-sm text-muted-foreground">暂无告警</p>
          ) : (
            <div className="overflow-x-auto">
              <table className="w-full text-sm">
                <thead>
                  <tr className="border-b text-muted-foreground">
                    <th className="text-left py-2 font-medium">股票ID</th>
                    <th className="text-left py-2 font-medium">异常分</th>
                    <th className="text-left py-2 font-medium">级别</th>
                    <th className="text-left py-2 font-medium">标签</th>
                  </tr>
                </thead>
                <tbody>
                  {summary.top_alerts.map((alert) => {
                    const cfg = SEVERITY_CONFIG[alert.severity] || SEVERITY_CONFIG.info;
                    const Icon = cfg.icon;
                    return (
                      <tr key={alert.stock_id} className="border-b last:border-0">
                        <td className="py-2 font-mono">{alert.stock_id}</td>
                        <td className="py-2 font-mono">{alert.anomaly_score.toFixed(1)}</td>
                        <td className="py-2">
                          <Badge variant="outline" className={cfg.className}>
                            <Icon className="h-3 w-3 mr-1" />
                            {cfg.label}
                          </Badge>
                        </td>
                        <td className="py-2">
                          <div className="flex flex-wrap gap-1">
                            {alert.flags.map((flag) => (
                              <span key={flag} className="text-[10px] px-1.5 py-0.5 rounded bg-muted">
                                {flag}
                              </span>
                            ))}
                          </div>
                        </td>
                      </tr>
                    );
                  })}
                </tbody>
              </table>
            </div>
          )}
        </CardContent>
      </Card>

      {/* 高波动股票 TOP */}
      <Card>
        <CardHeader>
          <CardTitle className="text-base">高波动股票 TOP20</CardTitle>
        </CardHeader>
        <CardContent>
          {!volatility?.top_volatility?.length ? (
            <p className="text-sm text-muted-foreground">暂无数据</p>
          ) : (
            <div className="overflow-x-auto">
              <table className="w-full text-sm">
                <thead>
                  <tr className="border-b text-muted-foreground">
                    <th className="text-left py-2 font-medium">股票ID</th>
                    <th className="text-left py-2 font-medium">预测波动率</th>
                    <th className="text-left py-2 font-medium">已实现波动率</th>
                    <th className="text-left py-2 font-medium">20日均换手</th>
                  </tr>
                </thead>
                <tbody>
                  {volatility.top_volatility.map((row) => (
                    <tr key={row.stock_id} className="border-b last:border-0">
                      <td className="py-2 font-mono">{row.stock_id}</td>
                      <td className="py-2 font-mono">{(row.forecast_vol_20 * 100).toFixed(2)}%</td>
                      <td className="py-2 font-mono">{(row.realized_vol_20 * 100).toFixed(2)}%</td>
                      <td className="py-2 font-mono">{row.avg_turnover_20.toFixed(2)}%</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </CardContent>
      </Card>
    </div>
  );
}
