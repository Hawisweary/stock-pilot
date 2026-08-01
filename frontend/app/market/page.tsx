"use client";

import { useState, useCallback } from "react";
import { Activity, RefreshCw } from "lucide-react";
import { syncWatchlistQuotes } from "@/lib/market";
import { useToast } from "@/lib/useToast";
import { LimitStatsCard } from "@/components/LimitStatsCard";
import { MarketIndexCard } from "@/components/MarketIndexCard";
import { MarketIndexKlinePanel } from "@/components/MarketIndexKlinePanel";
import { MacroIndicatorsPanel } from "@/components/MacroIndicatorsPanel";
import { SectorRotationCard } from "@/components/SectorRotationCard";
import { DragonTigerCard } from "@/components/DragonTigerCard";
import { ThsHotspotsCard } from "@/components/ThsHotspotsCard";
import { HsgtTop10Card } from "@/components/HsgtTop10Card";
import { CapitalResonanceCard } from "@/components/CapitalResonanceCard";
import { MarketOpsButtons } from "@/components/MarketOpsButtons";
import { IndustryBoardsCard } from "@/components/IndustryBoardsCard";
import { TradeCalendarCard } from "@/components/TradeCalendarCard";
import { MarketRegimeTimelineCard } from "@/components/MarketRegimeTimelineCard";
import { MarketRegimeCard } from "@/components/MarketRegimeCard";
import { RegimeLayersCompareCard } from "@/components/RegimeLayersCompareCard";
import { RegimeValidationCard } from "@/components/RegimeValidationCard";
import { StrategyRegimeMatrixCard } from "@/components/StrategyRegimeMatrixCard";
import { MarketBreadthCard } from "@/components/MarketBreadthCard";

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
  }, [toast]);

  return (
    <div className="space-y-6">
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

      <MarketIndexCard refreshKey={refreshKey} />
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
          <div className="pt-1">
            <h2 className="text-sm font-semibold tracking-tight">市场状态 · Regime</h2>
            <p className="text-[11px] text-muted-foreground mt-0.5">
              四格划分、策略矩阵与验证报告（L1→L3 研究视图）
            </p>
          </div>
          <MarketRegimeTimelineCard />
          <MarketRegimeCard />
          <RegimeLayersCompareCard />
          <StrategyRegimeMatrixCard />
          <RegimeValidationCard />
          <div className="pt-2 border-t border-border">
            <h2 className="text-sm font-semibold tracking-tight">行业与热点</h2>
          </div>
          <SectorRotationCard refreshKey={refreshKey} />
          <ThsHotspotsCard refreshKey={refreshKey} />
          <TradeCalendarCard />
          <MarketBreadthCard />
        </div>
      </div>

      <IndustryBoardsCard refreshKey={refreshKey} />
    </div>
  );
}
