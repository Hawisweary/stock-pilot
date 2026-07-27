"use client";

import { useEffect, useState, useCallback, useRef } from "react";
import { useRouter } from "next/navigation";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import { StockCard } from "@/components/StockCard";
import { MarketIndexCard } from "@/components/MarketIndexCard";
import { ThsHotspotsCard } from "@/components/ThsHotspotsCard";
import { MacroIndicatorsPanel } from "@/components/MacroIndicatorsPanel";
import { SectorRotationCard } from "@/components/SectorRotationCard";
import { MarketOpsButtons } from "@/components/MarketOpsButtons";
import { api, DashboardOverview, clearCache, pollFetchUntilDone, V5_RECALC_EVENT, getV5RecalcTimestamp, postSparkline, SparklineSeries, V5MarketScope, V5_MARKET_SCOPES } from "@/lib/api"
import { ScoreSparkline } from "@/components/ScoreSparkline";
import { DataHealthCard } from "@/components/DataHealthCard";
import { ScoreAlertCard } from "@/components/ScoreAlertCard";
import { UpcomingEarningsCard } from "@/components/UpcomingEarningsCard";
import { PortfolioPnlCard } from "@/components/PortfolioPnlCard";
import { MarketRegimeCard } from "@/components/MarketRegimeCard";
import { exportCsv } from "@/lib/csvExport";
import { scoreTextClass } from "@/lib/scoreColors";
import { useToast } from "@/lib/useToast";
import { RefreshCw, Bot, FileText, Flame, Landmark, Rocket, Trophy, NotebookPen } from "lucide-react";
import { Skeleton, ErrorState, StatTile } from "@/components/ui/data-ui";

export default function DashboardPage() {
  const toast = useToast();
  const router = useRouter();
  const [overview, setOverview] = useState<DashboardOverview | null>(null);
  const [quality, setQuality] = useState<any>(null);
  const [macro, setMacro] = useState<any>(null);
  const [review, setReview] = useState<any>(null);
  const [hotspots, setHotspots] = useState<any>(null);
  const [thsHot, setThsHot] = useState<any>(null);
  const [mlTop, setMlTop] = useState<
    { code: string; name: string; score: number; model_version?: string; is_demo?: boolean }[]
  >([]);
  const [v5Rank, setV5Rank] = useState<import("@/lib/api").V5ScoreRow[]>([]);
  const [v5CalcDate, setV5CalcDate] = useState<string | null>(null);
  const [v5Scope, setV5Scope] = useState<V5MarketScope>("A");
  const [v5ScopeLabel, setV5ScopeLabel] = useState("全部 A 股");
  const [sparklines, setSparklines] = useState<SparklineSeries>({});
  const lastV5TsRef = useRef(0);
  const [rankSortKey, setRankSortKey] = useState("score");
  const [rankSortDir, setRankSortDir] = useState<"asc" | "desc">("desc");
  const [loading, setLoading] = useState(true);
  const [macroKey, setMacroKey] = useState(0);

  // 排序后的排名数据
  const sortedRank = [...v5Rank].sort((a, b) => {
    const va = (a as unknown as Record<string, unknown>)[rankSortKey] ?? 0;
    const vb = (b as unknown as Record<string, unknown>)[rankSortKey] ?? 0;
    return rankSortDir === "desc" ? Number(vb) - Number(va) : Number(va) - Number(vb);
  });
  const avgV5 =
    v5Rank.length > 0
      ? v5Rank.reduce((s, r) => s + (r.score ?? r.composite_v5 ?? 0), 0) / v5Rank.length
      : null;

  const handleRankSort = (key: string) => {
    if (rankSortKey === key) {
      setRankSortDir(d => d === "desc" ? "asc" : "desc");
    } else {
      setRankSortKey(key);
      setRankSortDir("desc");
    }
  };

  const sortArrow = (key: string) => rankSortKey === key ? (rankSortDir === "desc" ? " ↓" : " ↑") : "";
  const [refreshing, setRefreshing] = useState(false);
  const [refreshMsg, setRefreshMsg] = useState("");
  const pollAbortRef = useRef<AbortController | null>(null);

  useEffect(() => {
    return () => {
      pollAbortRef.current?.abort();
    };
  }, []);

  const loadData = useCallback(async () => {
    setLoading(true);
    try {
      const data = await api.dashboardOverview();
      setOverview(data);
    } catch (e) {
      console.error("dashboard overview failed", e);
      setLoading(false);
      return;
    }
    // overview 就绪即展示页面，次要数据后台加载（避免阻塞卡片挂载）
    setLoading(false);

    void (async () => {
      try {
        const [mlRes, v5Batch, qRes, mRes, revRes, hotRes, thsRes] = await Promise.all([
          api.mlTop(8).catch(() => ({ enabled: false, predictions: [] as { code: string; name: string; score: number }[] })),
          api.getV5ScoresBatch({ scope: v5Scope }).catch(() => ({ scores: [] as import("@/lib/api").V5ScoreRow[], calc_date: null as string | null, scope_label: "" })),
          fetch("/api/fusion/quality").then((r) => (r.ok ? r.json() : null)).catch(() => null),
          fetch("/api/macro/score").then((r) => (r.ok ? r.json() : null)).catch(() => null),
          fetch("/api/review/latest").then((r) => (r.ok ? r.json() : null)).catch(() => null),
          fetch("/api/hotspots").then((r) => (r.ok ? r.json() : null)).catch(() => null),
          fetch("/api/signals/hotspots").then((r) => (r.ok ? r.json() : null)).catch(() => null),
        ]);
        setMlTop(mlRes.enabled ? (mlRes.predictions || []) : []);
        if (qRes) setQuality(qRes);
        if (mRes) setMacro(mRes);
        setReview(revRes?.review || null);
        if (hotRes) setHotspots(hotRes);
        setThsHot(thsRes?.hotspots || []);
        const scores = v5Batch.scores || [];
        setV5Rank(scores);
        setV5CalcDate(v5Batch.calc_date ?? null);
        setV5ScopeLabel(v5Batch.scope_label ?? V5_MARKET_SCOPES.find((s) => s.id === v5Scope)?.label ?? v5Scope);
        lastV5TsRef.current = getV5RecalcTimestamp();
        if (scores.length > 0) {
          postSparkline(scores.map((s) => s.stock_id), 30)
            .then(setSparklines)
            .catch(() => {});
        }
      } catch (e) {
        console.error("dashboard secondary load failed", e);
      }
    })();
  }, [v5Scope]);

  useEffect(() => {
    loadData();
    const onV5 = () => loadData();
    const onVisible = () => {
      if (document.visibilityState !== "visible") return;
      const ts = getV5RecalcTimestamp();
      if (ts > lastV5TsRef.current) loadData();
    };
    let channel: BroadcastChannel | null = null;
    try {
      channel = new BroadcastChannel(V5_RECALC_EVENT);
      channel.onmessage = () => loadData();
    } catch {
      /* ignore */
    }
    window.addEventListener(V5_RECALC_EVENT, onV5);
    document.addEventListener("visibilitychange", onVisible);
    return () => {
      window.removeEventListener(V5_RECALC_EVENT, onV5);
      document.removeEventListener("visibilitychange", onVisible);
      channel?.close();
    };
  }, [loadData]);

  const handleRefreshAll = async () => {
    pollAbortRef.current?.abort();
    const ac = new AbortController();
    pollAbortRef.current = ac;

    setRefreshing(true);
    setRefreshMsg("正在同步 V5 数据与评分...");
    try {
      await fetch("/api/v5/sync", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ mode: "daily" }),
      });
      clearCache();

      setRefreshMsg("正在后台增量抓取股票数据...");
      const data = await api.fetchAll("incremental");
      if (data.warning) {
        toast.info(data.warning);
      }
      if (data.status === "already_running") {
        setRefreshMsg(`抓取进行中 ${data.progress || ""}`);
      } else if (data.count === 0) {
        setRefreshMsg("");
        setRefreshing(false);
        toast.error(data.message || "没有跟踪的股票");
        return;
      }

      await pollFetchUntilDone(
        (p) => setRefreshMsg(`增量抓取 ${p}`),
        2000,
        ac.signal
      );
      setRefreshMsg("数据更新完成（如需最新 V5 评分请到数据页重算）");
      await loadData();
    } catch (e: unknown) {
      const msg = e instanceof Error ? e.message : String(e);
      if (e instanceof DOMException && e.name === "AbortError") {
        return;
      }
      toast.error("刷新失败: " + msg);
      setRefreshMsg("刷新失败: " + msg);
    } finally {
      setRefreshing(false);
      pollAbortRef.current = null;
    }
  };

  if (loading) {
    return (
      <div className="space-y-4">
        <div className="flex items-center justify-between">
          <Skeleton className="h-7 w-40" />
          <Skeleton className="h-7 w-24" />
        </div>
        <div className="grid grid-cols-3 divide-x divide-border rounded-md border border-border">
          {[1, 2, 3].map((i) => (
            <div key={i} className="px-3 py-2 space-y-1.5">
              <Skeleton className="h-2.5 w-14" />
              <Skeleton className="h-6 w-20" />
              <Skeleton className="h-2.5 w-24" />
            </div>
          ))}
        </div>
        <div className="grid grid-cols-1 lg:grid-cols-3 gap-3">
          {[1, 2, 3].map((i) => <Skeleton key={i} className="h-40" />)}
        </div>
        <Skeleton className="h-64" />
      </div>
    );
  }

  if (!overview) return <ErrorState message="Dashboard 加载失败" onRetry={loadData} />;

  return (
    <div className="space-y-4">
      <div className="flex items-center justify-between border-b border-border pb-3">
        <div className="flex items-baseline gap-3">
          <h1 className="text-xl font-semibold tracking-tight">Dashboard</h1>
          <Badge variant={overview.stale_stocks > 0 ? "destructive" : "outline"} className="text-[11px]">
            {overview.stale_stocks > 0
              ? `${overview.stale_stocks} 只数据逾期`
              : "数据正常"}
          </Badge>
        </div>
        <div className="flex items-center gap-2">
          <span className="text-[11px] text-muted-foreground font-mono">
            更新 {overview.last_update || "—"}
          </span>
          <Button
            size="sm"
            variant="outline"
            onClick={handleRefreshAll}
            disabled={refreshing}
            className="gap-1"
          >
            <RefreshCw className={`h-3.5 w-3.5 ${refreshing ? "animate-spin" : ""}`} />
            {refreshing ? "刷新中..." : "一键刷新"}
          </Button>
        </div>
      </div>

      {/* 顶部状态条：宏观 / V5均分 / 数据状态 — 硬化分区,色彩只落在数值 */}
      <div className="grid grid-cols-3 divide-x divide-border rounded-md border border-border bg-card">
        <StatTile
          label="宏观环境"
          value={macro ? macro.score : "—"}
          sub={macro?.label ?? ""}
          tone={macro?.score >= 60 ? "up" : macro?.score < 40 ? "down" : "neutral"}
        />
        <StatTile
          label="V5 均分"
          value={avgV5 != null ? avgV5.toFixed(1) : "—"}
          sub={`${v5Rank.length} 只 · ${v5ScopeLabel}`}
          tone="accent"
        />
        <StatTile
          label="数据状态"
          value={overview.stale_stocks > 0 ? `${overview.stale_stocks} 逾期` : "正常"}
          sub={`${overview.active_stocks}/${overview.stock_count} 活跃`}
          tone={overview.stale_stocks > 0 ? "up" : "neutral"}
        />
      </div>

      {/* 预警 + 财报日历 + 持仓盈亏 */}
      <div className="grid grid-cols-1 lg:grid-cols-3 gap-4">
        <ScoreAlertCard />
        <UpcomingEarningsCard />
        <PortfolioPnlCard />
      </div>

      {/* 市场状态（独立一行，不挤占原有三卡布局） */}
      <MarketRegimeCard compact />

      {/* U2-3: 数据健康看板 */}
      <DataHealthCard
        staleCount={overview.stale_stocks ?? 0}
        staleList={overview.stale_stock_list ?? []}
        dataQuality={overview.data_quality}
        onRefreshed={loadData}
      />
      {/* 刷新进度 */}
      {refreshMsg && (
        <div className={`rounded-md p-3 flex items-center gap-2 text-sm border ${
          refreshMsg.includes("失败") || refreshMsg.includes("超时") ? "bg-destructive/10 border-destructive/20 text-destructive" :
          "bg-primary/5 border-primary/20 text-primary"
        }`}>
          <RefreshCw className={`h-4 w-4 ${refreshMsg.includes("完成") || refreshMsg.includes("失败") ? "" : "animate-spin"}`} />
          <span className="flex-1">{refreshMsg}</span>
          <button onClick={() => setRefreshMsg("")} className="text-current opacity-50 hover:opacity-100 ml-2 text-lg leading-none">&times;</button>
        </div>
      )}

      {/* 大盘指数（技术面评分参考环境） */}
      <MarketIndexCard compact />

      {mlTop.length > 0 && (
        <Card>
          <CardHeader className="pb-2 flex flex-row items-center justify-between">
            <CardTitle className="text-sm flex items-center gap-1.5">
              <Bot className="h-4 w-4 text-muted-foreground" /> ML 预测 Top {mlTop.length}
            </CardTitle>
            <button onClick={() => router.push("/qlib")} className="text-xs text-primary hover:underline">详情 →</button>
          </CardHeader>
          <CardContent className="grid grid-cols-2 md:grid-cols-4 divide-x divide-border text-xs">
            {mlTop.map((p) => {
              const demo = p.is_demo ?? (p.model_version?.startsWith("demo_") ?? false);
              return (
                <div
                  key={p.code}
                  className={`px-2.5 py-1 ${demo ? "bg-muted/50 border border-dashed border-muted-foreground/30 rounded-sm" : ""}`}
                  title="实验性排序信号，未经样本外验证；请结合 V5 判断。"
                >
                  <div className="font-mono text-muted-foreground">{p.code}</div>
                  <div className="truncate">{p.name}</div>
                  <div className="font-mono font-semibold text-primary">
                    {p.score?.toFixed?.(1) ?? p.score}
                    {demo && <span className="ml-1 text-[9px] font-normal text-muted-foreground">Demo</span>}
                  </div>
                  <div className="text-[10px] text-muted-foreground truncate">{p.model_version || "ml"}</div>
                </div>
              );
            })}
          </CardContent>
        </Card>
      )}

      {/* 数据质量 + 宏观 */}
      <div className="grid grid-cols-2 gap-4">
        {quality && (
          <Card><CardContent className="p-4">
            <div className="text-xs text-muted-foreground mb-1">数据质量</div>
            <div className="flex items-center gap-2 text-sm">
              <span className="w-2 h-2 rounded-full bg-primary" />{quality.summary?.green||0}
              <span className="w-2 h-2 rounded-full bg-amber-500 ml-2" />{quality.summary?.yellow||0}
              <span className="w-2 h-2 rounded-full bg-destructive ml-2" />{quality.summary?.red||0}
              <span className="text-xs text-muted-foreground ml-2">/ {quality.summary?.total||0}</span>
            </div>
          </CardContent></Card>
        )}
        {macro && (
          <Card><CardContent className="p-4">
            <div className="text-xs text-muted-foreground mb-1">宏观环境</div>
            <div className="flex items-center gap-2">
              <span className={`text-lg font-bold font-mono tabular-nums ${scoreTextClass(macro.score)}`}>{macro.score}分</span>
              <span className="text-xs">{macro.label}</span>
            </div>
            {macro.indicators && (
              <div className="flex gap-3 mt-1 text-[10px] text-muted-foreground">
                {macro.indicators.pmi_manufacturing && <span>PMI:{macro.indicators.pmi_manufacturing}</span>}
                {macro.indicators.cpi_yoy && <span>CPI:{macro.indicators.cpi_yoy}%</span>}
                {macro.indicators.lpr_1y && <span>LPR:{macro.indicators.lpr_1y}%</span>}
              </div>
            )}
          </CardContent></Card>
        )}
      </div>

      {/* 每日综述 */}
      <div className="space-y-2">
        <MarketOpsButtons
          ops={["review", "ths", "macro"]}
          onMacroSynced={() => {
            setMacroKey((k) => k + 1);
            fetch("/api/macro/score").then((r) => r.json()).then(setMacro).catch(() => {});
          }}
        />
        {review && (
          <Card><CardContent className="px-4 py-3">
            <div className="text-xs font-medium text-muted-foreground mb-2 flex items-center gap-1.5">
              <NotebookPen className="h-3.5 w-3.5" /> 每日综述
            </div>
            <div className="text-xs space-y-1 leading-relaxed">
              <div>{review.review}</div>
              <div className="text-primary">{review.focus}</div>
              <div className="text-destructive">{review.risks}</div>
            </div>
          </CardContent></Card>
        )}
      </div>

      {/* 同花顺热点 */}
      <ThsHotspotsCard />

      {/* 宏观指标明细 + 行业轮动 */}
      <div className="grid grid-cols-1 lg:grid-cols-2 gap-4">
        <MacroIndicatorsPanel compact refreshKey={macroKey} />
        <SectorRotationCard />
      </div>

      {/* 舆情热点 */}
      {hotspots && hotspots.count > 0 && (
        <Card><CardContent className="px-4 py-3">
          <div className="text-xs font-medium text-destructive mb-2 flex items-center gap-1.5">
            <Flame className="h-3.5 w-3.5" /> 舆情热点 ({hotspots.count})
          </div>
          {hotspots.hotspots?.slice(0,5).map((h:any,i:number) => (
            <div key={i} className="text-xs py-1 border-b border-border last:border-0 flex items-center gap-1.5">
              <span className="font-mono text-muted-foreground">{h.code}</span>
              <span className="flex-1 truncate">{h.name}</span>
              <span className="font-mono text-[11px] text-destructive">{h.cnt}条 · 情感 {h.avg_sentiment?.toFixed(1)}</span>
            </div>
          ))}
        </CardContent></Card>
      )}

      {/* 双榜：价值 Top3 + 动量 Top3 */}
      {sortedRank.length > 0 && (() => {
        // 动量临时分 = technical×0.45 + capital×0.40 + industry×0.15（M1前用维度分假拼）
        const momentumRank = [...v5Rank]
          .map(s => {
            const r = s as unknown as Record<string, number | null>;
            const mom = (r.technical_score ?? 0) * 0.45
              + (r.capital_score ?? 0) * 0.40
              + (r.industry_score ?? 0) * 0.15;
            return { ...s, _mom: mom };
          })
          .sort((a, b) => b._mom - a._mom);
        return (
          <div className="grid grid-cols-1 lg:grid-cols-2 gap-6">
            <div>
              <div className="flex items-center gap-2 mb-2 border-b border-border pb-1.5">
                <Landmark className="h-4 w-4 text-muted-foreground" />
                <h2 className="text-sm font-semibold">价值 Top 3</h2>
                <span className="text-[10px] text-muted-foreground">composite_v5（长持/研究）</span>
              </div>
              <div className="grid grid-cols-3 gap-3">
                {sortedRank.slice(0, 3).map((stock) => (
                  <StockCard key={stock.stock_id} stock={stock} onClick={() => router.push(`/stocks/${stock.stock_id}`)} />
                ))}
              </div>
            </div>
            <div>
              <div className="flex items-center gap-2 mb-2 border-b border-border pb-1.5">
                <Rocket className="h-4 w-4 text-muted-foreground" />
                <h2 className="text-sm font-semibold">动量 Top 3</h2>
                <span className="text-[10px] text-muted-foreground">技术×0.45 + 资金×0.40（追涨/轮动）</span>
              </div>
              <div className="grid grid-cols-3 gap-3">
                {momentumRank.slice(0, 3).map((stock) => (
                  <StockCard key={stock.stock_id} stock={stock} onClick={() => router.push(`/stocks/${stock.stock_id}`)} />
                ))}
              </div>
            </div>
          </div>
        );
      })()}

      {/* 评分分布 + PDF导出 */}
      <div className="grid grid-cols-2 gap-4">
        <Card>
          <CardHeader><CardTitle className="text-base">V5 评分分布 ({v5Rank.length}只 · {v5ScopeLabel})</CardTitle></CardHeader>
          <CardContent>
            <ScoreDistribution scores={sortedRank} />
          </CardContent>
        </Card>
        <Card>
          <CardHeader><CardTitle className="text-base">操作</CardTitle></CardHeader>
          <CardContent className="space-y-2">
            <button onClick={() => window.open("/api/report/pdf", "_blank")}
              className="w-full px-3 py-1.5 border border-border rounded-md text-sm hover:bg-muted transition-colors flex items-center justify-center gap-1.5">
              <FileText className="h-3.5 w-3.5" /> 导出 PDF 报告
            </button>
            <button onClick={() => router.push("/stocks?view=grouped")}
              className="w-full px-3 py-1.5 border border-border rounded-md text-sm hover:bg-muted transition-colors">
              管理股票分组
            </button>
          </CardContent>
        </Card>
      </div>

      {/* V5 评分排名 */}
      {v5Rank.length > 0 && (
        <Card>
          <CardHeader className="flex flex-row items-center justify-between gap-3 flex-wrap">
            <CardTitle className="text-sm flex items-center gap-1.5 flex-wrap">
              <Trophy className="h-4 w-4 text-muted-foreground" />
              V5 评分排名 · {v5ScopeLabel} ({v5Rank.length}只)
              {v5CalcDate ? <span className="text-[11px] font-normal font-mono text-muted-foreground ml-2">评分日 {v5CalcDate}</span> : null}
            </CardTitle>
            <div className="flex items-center gap-2">
              <label className="text-xs text-muted-foreground whitespace-nowrap" htmlFor="v5-scope">
                范围
              </label>
              <select
                id="v5-scope"
                value={v5Scope}
                onChange={(e) => setV5Scope(e.target.value as V5MarketScope)}
                className="h-8 rounded-md border border-border bg-background px-2 text-xs"
              >
                {V5_MARKET_SCOPES.map((opt) => (
                  <option key={opt.id} value={opt.id}>{opt.label}</option>
                ))}
              </select>
            </div>
            <button
              onClick={() => exportCsv(
                `v5_ranking_${v5CalcDate ?? "latest"}.csv`,
                ["代码", "名称", "综合分", "状态"],
                sortedRank.map((r) => [r.code, r.name, (r.score ?? r.composite_v5)?.toFixed(1) ?? "", r.veto_status ?? ""])
              )}
              className="text-xs text-muted-foreground hover:text-foreground px-2 py-1 rounded border hover:bg-accent transition-colors"
            >
              导出 CSV
            </button>
          </CardHeader>
          <CardContent className="p-0">
            <div className="max-h-[500px] overflow-auto">
              <table className="w-full text-sm min-w-[400px]">
                <thead className="sticky top-0 bg-card z-10">
                  <tr className="border-b text-left text-muted-foreground">
                    <th className="py-2 px-2 w-8">#</th>
                    <th className="py-2 px-2">代码</th>
                    <th className="py-2 px-2">名称</th>
                    <th className="py-2 px-2 text-right cursor-pointer select-none hover:text-foreground"
                        onClick={() => handleRankSort("score")}>
                      综合分{sortArrow("score")}
                    </th>
                    <th className="py-2 px-2 text-right">30天</th>
                    <th className="py-2 px-2 text-right">状态</th>
                  </tr>
                </thead>
                <tbody>
                  {sortedRank.map((r, i) => (
                    <tr key={r.stock_id} className="border-b hover:bg-muted/50 cursor-pointer"
                        onClick={() => router.push(`/stocks/${r.stock_id}`)}>
                      <td className="py-1.5 px-2 font-mono text-xs text-muted-foreground">{i + 1}</td>
                      <td className="py-1.5 px-2 font-mono">{r.code}</td>
                      <td className="py-1.5 px-2">{r.name}</td>
                      <td className={`py-1.5 px-2 text-right font-mono font-semibold ${scoreTextClass(r.score ?? r.composite_v5)}`}>
                        {(r.score ?? r.composite_v5)?.toFixed(1) ?? "-"}
                      </td>
                      <td className="py-1.5 px-2 text-right">
                        <ScoreSparkline data={sparklines[String(r.stock_id)]} />
                      </td>
                      <td className="py-1.5 px-2 text-right text-xs">
                        {r.veto_status === "exclude" ? (
                          <span className="text-up font-medium">回避</span>
                        ) : r.veto_status === "reduce" ? (
                          <span className="text-amber-600 dark:text-amber-500">减仓</span>
                        ) : (
                          <span className="text-muted-foreground">正常</span>
                        )}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </CardContent>
        </Card>
      )}
    </div>
  );
}

function ScoreDistribution({ scores }: { scores: { score?: number | null; composite_v5?: number | null }[] }) {
  const bands = [
    { label: "80-100", lo: 80, hi: 100, color: "bg-primary" },
    { label: "60-80",  lo: 60, hi: 80,  color: "bg-primary/70" },
    { label: "40-60",  lo: 40, hi: 60,  color: "bg-primary/45" },
    { label: "20-40",  lo: 20, hi: 40,  color: "bg-primary/25" },
    { label: "0-20",   lo: 0,  hi: 20,  color: "bg-primary/12" },
  ];
  const counts = bands.map(({ lo, hi }) =>
    scores.filter((s) => { const v = s.score ?? s.composite_v5; return v != null && v >= lo && v < hi; }).length
  );
  const max = Math.max(...counts, 1);

  return (
    <div className="space-y-2">
      {bands.map(({ label, color }, i) => (
        <div key={label} className="flex items-center gap-2 text-xs">
          <span className="w-14 text-right text-muted-foreground tabular-nums">{label}</span>
          <div className="flex-1 h-5 bg-muted rounded overflow-hidden">
            <div
              className={`h-full ${color} rounded transition-all flex items-center pl-1.5`}
              style={{ width: `${Math.max((counts[i] / max) * 100, counts[i] > 0 ? 6 : 0)}%` }}
            >
              {counts[i] > 0 && <span className="text-[10px] text-white font-bold">{counts[i]}</span>}
            </div>
          </div>
          {counts[i] === 0 && <span className="text-xs text-muted-foreground w-4">0</span>}
        </div>
      ))}
    </div>
  );
}
