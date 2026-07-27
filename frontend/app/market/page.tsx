"use client";

import { useState, useCallback } from "react";
import { Activity, RefreshCw } from "lucide-react";
import { syncWatchlistQuotes } from "@/lib/market";
import { useToast } from "@/lib/useToast";
import dynamic from "next/dynamic";

const LimitStatsCard = dynamic(
  () => import("@/components/LimitStatsCard").then(m => ({ default: m.LimitStatsCard })),
  {
    loading: () => (
      <div className="rounded-lg border border-border bg-card h-[240px] flex items-center justify-center">
        <div className="animate-pulse space-y-3 w-[80%]">
          <div className="grid grid-cols-2 gap-3">
            <div className="h-16 bg-muted rounded" />
            <div className="h-16 bg-muted rounded" />
          </div>
          <div className="h-4 bg-muted rounded w-2/3 mx-auto" />
        </div>
      </div>
    ),
  }
);

const MarketIndexCard = dynamic(
  () => import("@/components/MarketIndexCard").then(m => ({ default: m.MarketIndexCard })),
  {
    loading: () => (
      <div className="lg:col-span-2 rounded-lg border border-border bg-card h-[200px] animate-pulse" />
    ),
    ssr: false,
  }
);

const MarketIndexKlinePanel = dynamic(
  () =>
    import("@/components/MarketIndexKlinePanel").then((m) => ({
      default: m.MarketIndexKlinePanel,
    })),
  {
    loading: () => (
      <div className="rounded-lg border border-border bg-card h-[420px] animate-pulse" />
    ),
    ssr: false,
  }
);

const MacroIndicatorsPanel = dynamic(
  () => import("@/components/MacroIndicatorsPanel").then((m) => ({ default: m.MacroIndicatorsPanel })),
  { loading: () => <div className="rounded-lg border h-40 animate-pulse" />, ssr: false },
);

const SectorRotationCard = dynamic(
  () => import("@/components/SectorRotationCard").then((m) => ({ default: m.SectorRotationCard })),
  { loading: () => <div className="rounded-lg border h-32 animate-pulse" />, ssr: false },
);

const DragonTigerCard = dynamic(
  () => import("@/components/DragonTigerCard").then((m) => ({ default: m.DragonTigerCard })),
  { loading: () => <div className="rounded-lg border h-48 animate-pulse" />, ssr: false },
);

const ThsHotspotsCard = dynamic(
  () => import("@/components/ThsHotspotsCard").then((m) => ({ default: m.ThsHotspotsCard })),
  { loading: () => <div className="rounded-lg border h-24 animate-pulse" />, ssr: false },
);

const HsgtTop10Card = dynamic(
  () => import("@/components/HsgtTop10Card").then((m) => ({ default: m.HsgtTop10Card })),
  { loading: () => <div className="rounded-lg border h-48 animate-pulse" />, ssr: false },
);

const CapitalResonanceCard = dynamic(
  () => import("@/components/CapitalResonanceCard").then((m) => ({ default: m.CapitalResonanceCard })),
  { loading: () => <div className="rounded-lg border h-48 animate-pulse" />, ssr: false },
);

const MarketOpsButtons = dynamic(
  () => import("@/components/MarketOpsButtons").then((m) => ({ default: m.MarketOpsButtons })),
  { ssr: false },
);

const IndustryBoardsCard = dynamic(
  () => import("@/components/IndustryBoardsCard").then(m => ({ default: m.IndustryBoardsCard })),
  {
    loading: () => (
      <div className="rounded-lg border border-border bg-card flex items-center justify-center p-8">
        <div className="animate-pulse space-y-2 w-full max-w-[600px]">
          {[1, 2, 3, 4, 5, 6].map(i => (
            <div key={i} className="h-6 bg-muted rounded" style={{ width: `${85 - i * 5}%` }} />
          ))}
        </div>
      </div>
    ),
    ssr: false, // 避免 SSR 时请求数据
  }
);

const TradeCalendarCard = dynamic(
  () => import("@/components/TradeCalendarCard").then((m) => ({ default: m.TradeCalendarCard })),
  { loading: () => <div className="rounded-lg border h-64 animate-pulse" />, ssr: false },
);

const MarketRegimeCard = dynamic(
  () => import("@/components/MarketRegimeCard").then((m) => ({ default: m.MarketRegimeCard })),
  { loading: () => <div className="rounded-lg border h-48 animate-pulse" />, ssr: false },
);

const MarketBreadthCard = dynamic(
  () => import("@/components/MarketBreadthCard").then((m) => ({ default: m.MarketBreadthCard })),
  { loading: () => <div className="rounded-lg border h-48 animate-pulse" />, ssr: false },
);

export default function MarketPage() {
  const toast = useToast();
  const [refreshKey, setRefreshKey] = useState(0);
  const [refreshing, setRefreshing] = useState(false);
  const [lastRefreshAt, setLastRefreshAt] = useState<string | null>(null);
  const [dataTradeDate, setDataTradeDate] = useState<string | null>(null);

  const handleRefresh = useCallback(() => {
    setRefreshing(true);
    setRefreshKey((k) => k + 1);
    syncWatchlistQuotes()
      .then((r) => {
        if (r.latest_trade_date) setDataTradeDate(r.latest_trade_date);
        toast.success("行情已同步");
      })
      .catch(() => toast.error("行情同步失败"));
    setLastRefreshAt(
      new Date().toLocaleTimeString("zh-CN", { hour: "2-digit", minute: "2-digit", second: "2-digit" }),
    );
    setTimeout(() => setRefreshing(false), 1200);
  }, []);

  return (
    <div className="space-y-6">
      {/* 页面标题 */}
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-2xl font-bold flex items-center gap-2">
            <Activity className="h-6 w-6" />
            市场行情
          </h1>
          <p className="text-sm text-muted-foreground mt-1">
            大盘指数 · K 线 · 龙虎榜 · 行业板块 · 涨跌停统计
            {dataTradeDate && (
              <span className="ml-2 text-[11px]">· 库内行情截至 {dataTradeDate}</span>
            )}
            {lastRefreshAt && (
              <span className="ml-2 text-[11px]">· 刷新于 {lastRefreshAt}</span>
            )}
          </p>
        </div>
        <button
          onClick={handleRefresh}
          disabled={refreshing}
          className="inline-flex items-center gap-1.5 rounded-lg border border-border px-3 py-1.5 text-sm text-muted-foreground hover:bg-accent transition-colors disabled:opacity-50"
        >
          <RefreshCw className={`h-4 w-4 ${refreshing ? "animate-spin" : ""}`} />
          {refreshing ? "刷新中…" : "全部刷新"}
        </button>
      </div>

      {/* 大盘指数 */}
      <MarketIndexCard refreshKey={refreshKey} />

      {/* 大盘 K 线 */}
      <MarketIndexKlinePanel refreshKey={refreshKey} />

      <LimitStatsCard refreshKey={refreshKey} />

      <DragonTigerCard refreshKey={refreshKey} />

      <HsgtTop10Card />

      <CapitalResonanceCard />

      <MarketOpsButtons
        ops={["macro", "v5", "fusion", "ths", "review"]}
        onThsSynced={() => setRefreshKey((k) => k + 1)}
        onMacroSynced={() => setRefreshKey((k) => k + 1)}
        onV5Synced={() => setRefreshKey((k) => k + 1)}
      />

      <div className="grid grid-cols-1 lg:grid-cols-2 gap-4">
        <MacroIndicatorsPanel refreshKey={refreshKey} />
        <div className="space-y-4">
          <MarketRegimeCard />
          <SectorRotationCard refreshKey={refreshKey} />
          <ThsHotspotsCard refreshKey={refreshKey} />
          <TradeCalendarCard />
          <MarketBreadthCard />
        </div>
      </div>

      {/* 行业板块全表 */}
      <IndustryBoardsCard refreshKey={refreshKey} />
    </div>
  );
}
