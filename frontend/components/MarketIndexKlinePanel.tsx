"use client";

import { useCallback, useEffect, useMemo, useState } from "react";
import { AlertCircle, CandlestickChart, RefreshCw } from "lucide-react";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { KLineChart } from "@/components/KLineChart";
import {
  fetchMarketIndexKline,
  MARKET_INDEX_OPTIONS,
  type IndexKlineBar,
  type MarketIndexCode,
} from "@/lib/market";

type Period = "daily" | "weekly";

function toChartBars(rows: IndexKlineBar[]) {
  return rows
    .filter((b) => b.close != null && b.open != null)
    .map((b) => ({
      date: b.date,
      open: Number(b.open),
      high: Number(b.high ?? b.close),
      low: Number(b.low ?? b.close),
      close: Number(b.close),
    }));
}

export function MarketIndexKlinePanel({ refreshKey = 0 }: { refreshKey?: number }) {
  const [indexCode, setIndexCode] = useState<MarketIndexCode>(MARKET_INDEX_OPTIONS[0].code);
  const [period, setPeriod] = useState<Period>("daily");
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [kline, setKline] = useState<IndexKlineBar[]>([]);
  const [technical, setTechnical] = useState<any[]>([]);
  const [title, setTitle] = useState(MARKET_INDEX_OPTIONS[0].name);
  const [asOfDate, setAsOfDate] = useState<string | null>(null);

  const days = 250;

  const load = useCallback(async (force = false) => {
    setLoading(true);
    setError(null);
    try {
      const res = await fetchMarketIndexKline(indexCode, period, days, { force });
      if (res.error && !res.kline?.length) {
        setError(res.error);
        setKline([]);
        setTechnical([]);
      } else {
        setTitle(res.name || indexCode);
        setKline(res.kline || []);
        setTechnical(res.technical || []);
        setAsOfDate(res.as_of_trade_date ?? res.kline?.at(-1)?.date ?? null);
        if (res.error) setError(res.error);
      }
    } catch (e) {
      setError(e instanceof Error ? e.message : "加载 K 线失败");
      setKline([]);
      setTechnical([]);
    } finally {
      setLoading(false);
    }
  }, [indexCode, period, days]);

  useEffect(() => {
    load(true);
    const t = setInterval(() => load(true), 90_000);
    return () => clearInterval(t);
  }, [load, refreshKey]);

  const chartData = useMemo(() => toChartBars(kline), [kline]);
  const indexLabel = MARKET_INDEX_OPTIONS.find((o) => o.code === indexCode)?.name ?? title;

  return (
    <Card>
      <CardHeader className="flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between pb-2">
        <div>
          <CardTitle className="text-base flex items-center gap-2">
            <CandlestickChart className="h-4 w-4" />
            大盘 K 线
          </CardTitle>
          <p className="text-[10px] text-muted-foreground font-normal mt-0.5">
            {indexLabel} · {period === "daily" ? "日线" : "周线"} · 近 {days} 根
            {asOfDate ? ` · 截至 ${asOfDate}` : ""}
            {loading && kline.length > 0 ? " · 更新中…" : ""}
          </p>
        </div>
        <div className="flex flex-wrap items-center gap-2">
          <div className="inline-flex rounded-md border border-border p-0.5 text-xs">
            {MARKET_INDEX_OPTIONS.map((opt) => (
              <button
                key={opt.code}
                type="button"
                onClick={() => setIndexCode(opt.code)}
                className={`px-2.5 py-1 rounded-sm transition-colors ${
                  indexCode === opt.code
                    ? "bg-primary text-primary-foreground"
                    : "text-muted-foreground hover:text-foreground"
                }`}
              >
                {opt.name.replace("指数", "").replace("板", "")}
              </button>
            ))}
          </div>
          <div className="inline-flex rounded-md border border-border p-0.5 text-xs">
            {(["daily", "weekly"] as const).map((p) => (
              <button
                key={p}
                type="button"
                onClick={() => setPeriod(p)}
                className={`px-2.5 py-1 rounded-sm transition-colors ${
                  period === p
                    ? "bg-muted font-medium"
                    : "text-muted-foreground hover:text-foreground"
                }`}
              >
                {p === "daily" ? "日线" : "周线"}
              </button>
            ))}
          </div>
          <button
            type="button"
            onClick={() => load(true)}
            className="p-1.5 text-muted-foreground hover:text-foreground rounded border border-border"
            aria-label="刷新 K 线"
          >
            <RefreshCw className={`h-4 w-4 ${loading ? "animate-spin" : ""}`} />
          </button>
        </div>
      </CardHeader>
      <CardContent>
        {error && chartData.length === 0 ? (
          <div className="flex items-center gap-2 text-red-500 text-sm py-8 justify-center">
            <AlertCircle className="h-4 w-4" />
            {error}
          </div>
        ) : chartData.length === 0 && loading ? (
          <div className="h-[360px] rounded-lg bg-muted/40 animate-pulse" />
        ) : chartData.length === 0 ? (
          <p className="text-sm text-muted-foreground text-center py-8">暂无 K 线数据</p>
        ) : (
          <div className={loading ? "opacity-60 pointer-events-none" : ""}>
            <KLineChart data={chartData} height={380} />
          </div>
        )}
        {error && chartData.length > 0 && (
          <p className="text-[10px] text-amber-600 mt-2">{error}</p>
        )}
      </CardContent>
    </Card>
  );
}
