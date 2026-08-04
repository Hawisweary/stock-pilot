"use client";

import { useState, useEffect, useRef, Suspense } from "react";
import { useSearchParams } from "next/navigation";
import { Bell, AlertTriangle, Lock } from "lucide-react";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { BetaShell, BetaTabs, BETA_TABS } from "@/components/BetaShell";
import { BetaDualChart } from "@/components/BetaDualChart";
import { Skeleton, EmptyState, StatTile } from "@/components/ui/data-ui";
import { api } from "@/lib/api";
import { useToast } from "@/lib/useToast";
import type {
  PortfolioDetail, PortfolioSummary, PortfolioPosition, PositionPerf,
  PortfolioMetrics, PortfolioCompare, BuildPreview, PortfolioSettings,
  FeePreview, PricingContext,
} from "@/types/beta";
import {
  applyPortfolioPreset,
  applyStrategyDefaults,
  FALLBACK_STRATEGIES,
  PORTFOLIO_PRESETS,
  type PortfolioPreset,
  type StrategyOption,
} from "@/lib/strategies";

type Tab = "holdings" | "perf" | "compare" | "settings";

export default function PortfolioPage() {
  return (
    <Suspense fallback={<div className="p-8 animate-pulse bg-muted rounded h-64" />}>
      <PortfolioInner />
    </Suspense>
  );
}

function PortfolioInner() {
  const sp = useSearchParams();
  const toast = useToast();
  const [pfs, setPfs] = useState<PortfolioSummary[]>([]);
  const [selected, setSelected] = useState<PortfolioDetail | null>(null);
  const [metrics, setMetrics] = useState<PortfolioMetrics | null>(null);
  const [tab, setTab] = useState<Tab>("holdings");
  const [tradeCode, setTradeCode] = useState("");
  const [tradeShares, setTradeShares] = useState(100);
  const [feePreview, setFeePreview] = useState<FeePreview | null>(null);
  const [pricingCtx, setPricingCtx] = useState<PricingContext | null>(null);
  const [searchQ, setSearchQ] = useState("");
  const [searchHits, setSearchHits] = useState<{ code: string; name: string }[]>([]);
  const [buildN, setBuildN] = useState(5);
  const [buildMin, setBuildMin] = useState(50);
  const [buildStrategy, setBuildStrategy] = useState("composite");
  const [buildComboId, setBuildComboId] = useState<number | "">("");
  const [buildLookback, setBuildLookback] = useState(20);
  const [buildSectorWindow, setBuildSectorWindow] = useState(5);
  const [buildPerSector, setBuildPerSector] = useState(2);
  const [buildStyle, setBuildStyle] = useState<"equal" | "weighted">("equal");
  const [strategies, setStrategies] = useState<StrategyOption[]>(FALLBACK_STRATEGIES);
  const [combinations, setCombinations] = useState<{ id: number; name: string }[]>([]);
  const [preview, setPreview] = useState<BuildPreview | null>(null);
  const [compare, setCompare] = useState<PortfolioCompare | null>(null);
  const [compareDays, setCompareDays] = useState(90);
  const [scoreAlerts, setScoreAlerts] = useState<{ stock_id: number; code: string; name: string; shares: number; composite_v5: number | null; score_label?: string; veto_status: string; trigger: string }[]>([]);
  const [replacingCode, setReplacingCode] = useState<string | null>(null);
  const [renaming, setRenaming] = useState(false);
  const [navSeries, setNavSeries] = useState<{ dates: string[]; nav: number[]; benchmark: (number | null)[] } | null>(null);
  const [newName, setNewName] = useState("");
  const [createCash, setCreateCash] = useState(100000);
  const [settingsForm, setSettingsForm] = useState<Partial<PortfolioSettings>>({});
  const [journalFilter, setJournalFilter] = useState<"all" | "BUY" | "SELL">("all");
  const [rebalancePreview, setRebalancePreview] = useState<{
    due: boolean; days_left: number; schedule: string; strategy: string;
    preview_buy: { code: string; name: string; score: number; est_shares?: number | null }[];
    preview_sell: { code: string; name: string; shares: number }[];
    skip_next_rebalance: boolean; reason?: string;
  } | null>(null);
  const [skipBusy, setSkipBusy] = useState(false);
  const [turtlePreview, setTurtlePreview] = useState<{
    signal_count: number;
    monitored: number;
    signals: { code: string; name: string; shares: number; sellable_shares: number; price?: number; stop_price?: number; exit_reason?: string }[];
    lookback: number;
    exit_period: number;
  } | null>(null);
  const [turtleBusy, setTurtleBusy] = useState(false);

  const load = () => {
    api.portfolioList().then(setPfs).catch(() => toast.error("加载组合失败"));
    api.portfolioPricingContext().then(setPricingCtx).catch(() => {});
  };
  useEffect(() => { load(); }, []);

  useEffect(() => {
    const s = sp.get("strategy");
    if (!s) return;
    setBuildStrategy(s);
    const defs = applyStrategyDefaults(s, strategies);
    if (defs.top_n != null) setBuildN(defs.top_n);
    if (defs.min_score != null) setBuildMin(defs.min_score);
    if (defs.lookback != null) setBuildLookback(defs.lookback);
  }, [sp, strategies]);

  const urlPortfolioHandled = useRef(false);
  useEffect(() => {
    if (urlPortfolioHandled.current || pfs.length === 0) return;
    const idParam = sp.get("id");
    if (!idParam) return;
    const pid = Number(idParam);
    if (!Number.isFinite(pid) || !pfs.some((p) => p.id === pid)) return;
    urlPortfolioHandled.current = true;
    select(pid);
  }, [sp, pfs]);
  useEffect(() => {
    api.strategyList(true).then((r) => {
      if (r.strategies?.length) setStrategies(r.strategies);
    }).catch(() => {});
    api.factorCombinationsList().then((r) => {
      setCombinations(
        (r.combinations || []).map((c) => ({
          id: Number(c.id),
          name: String(c.name || `方案#${c.id}`),
        })).filter((c) => c.id > 0),
      );
    }).catch(() => {});
  }, []);
  useEffect(() => {
    const t = setInterval(() => {
      api.portfolioPricingContext().then(setPricingCtx).catch(() => {});
    }, 60_000);
    return () => clearInterval(t);
  }, []);

  useEffect(() => {
    if (!selected?.id) return;
    const ms = pricingCtx?.mode === "intraday" ? 30_000 : 120_000;
    const t = setInterval(() => {
      api.portfolioGet(selected.id).then((pf) => {
        setSelected(pf);
        if (pf.pricing) setPricingCtx(pf.pricing);
      }).catch(() => {});
    }, ms);
    return () => clearInterval(t);
  }, [selected?.id, pricingCtx?.mode]);

  useEffect(() => {
    if (searchQ.length < 1) { setSearchHits([]); return; }
    const t = setTimeout(() => {
      api.stockSearch(searchQ).then((r) => setSearchHits((r.results || []) as { code: string; name: string }[]));
    }, 300);
    return () => clearTimeout(t);
  }, [searchQ]);

  useEffect(() => {
    if (!selected?.id || !tradeCode || tradeShares < 100) { setFeePreview(null); return; }
    const t = setTimeout(() => {
      api.portfolioEstimateFees(selected.id, tradeCode, "buy", tradeShares)
        .then((r) => setFeePreview({
          cash_delta: r.cash_delta,
          commission: r.commission,
          tax: r.tax,
          price: r.price,
          price_label: r.price_label,
          quote_date: r.quote_date,
          price_source: r.price_source,
          can_trade: r.can_trade,
          block_reason: r.block_reason,
        }))
        .catch(() => setFeePreview(null));
    }, 400);
    return () => clearTimeout(t);
  }, [selected?.id, tradeCode, tradeShares]);

  const select = async (id: number) => {
    try {
      const pf = await api.portfolioGet(id);
      setSelected(pf);
      setNewName(pf.name);
      setSettingsForm(pf.settings || {});
      setBuildN(pf.settings?.default_top_n ?? 5);
      setBuildMin(pf.settings?.default_min_score ?? 50);
      setBuildStrategy(pf.settings?.default_strategy ?? "composite");
      setBuildComboId(pf.settings?.default_combination_id ?? "");
      setBuildLookback(pf.settings?.default_lookback ?? 20);
      setBuildSectorWindow(pf.settings?.default_sector_window ?? 5);
      setBuildPerSector(pf.settings?.default_per_sector ?? 2);
      setBuildStyle(pf.settings?.default_pos_style ?? "equal");
      setPreview(null);
      setCompare(null);
      setNavSeries(null);
      setScoreAlerts([]);
      setRebalancePreview(null);
      if (pf.pricing) setPricingCtx(pf.pricing);
      const m = await api.portfolioMetrics(id);
      setMetrics(m);
      api.portfolioNavSeries(id, 3650).then(setNavSeries).catch(() => {});
      api.portfolioScoreAlerts(id, 40).then((r) => setScoreAlerts(r.alerts)).catch(() => {});
      api.portfolioRebalancePreview(id).then(setRebalancePreview).catch(() => {});
    } catch (e) {
      toast.error(e instanceof Error ? e.message : "加载失败");
    }
  };

  const create = async () => {
    await api.portfolioCreate("组合" + (pfs.length + 1), createCash);
    toast.success("已创建组合");
    load();
  };

  const doTrade = async (action: "buy" | "sell", code?: string, shares?: number) => {
    if (!selected) return;
    const c = code || tradeCode;
    const sh = shares ?? tradeShares;
    if (!c) { toast.error("请输入股票代码"); return; }
    if (sh < 100 || sh % 100 !== 0) { toast.error("数量须为 100 的整数倍"); return; }
    try {
      const r = await api.portfolioTrade(selected.id, { code: c, action, shares: sh });
      const fee = (Number(r.commission) || 0) + (Number(r.tax) || 0);
      const label = (r as { price_label?: string }).price_label;
      toast.success(`${action === "buy" ? "买入" : "卖出"} ${r.code} ${r.shares}股 @ ¥${Number(r.price).toFixed(2)}${label ? ` · ${label}` : ""} · 费用 ¥${fee.toFixed(2)}`);
      select(selected.id);
      load();
    } catch (e) { toast.error(e instanceof Error ? e.message : "交易失败"); }
  };

  const buildBody = () => ({
    top_n: buildN,
    min_score: buildMin,
    strategy: buildStrategy,
    pos_style: buildStyle,
    ...(buildStrategy === "factor_combination" && buildComboId
      ? { combination_id: Number(buildComboId) }
      : {}),
    ...(buildStrategy === "momentum" || buildStrategy === "turtle" ? { lookback: buildLookback } : {}),
    ...(buildStrategy === "sector_rotation" ? { sector_window: buildSectorWindow, per_sector: buildPerSector } : {}),
  });

  const runPreview = async () => {
    if (!selected) return;
    if (buildStrategy === "factor_combination" && !buildComboId) {
      toast.error("请选择合成因子方案");
      return;
    }
    try {
      setPreview(await api.portfolioBuildPreview(selected.id, buildBody()));
    } catch (e) { toast.error(e instanceof Error ? e.message : "预览失败"); }
  };

  const buildTop = async () => {
    if (!selected) return;
    if (buildStrategy === "factor_combination" && !buildComboId) {
      toast.error("请选择合成因子方案");
      return;
    }
    if (preview && !confirm(`确认建仓 ${preview.preview.length} 只？`)) return;
    try {
      const r = await api.portfolioBuildTop(selected.id, buildBody());
      const execDate = r.execute_date ?? "下一交易日";
      if (r.queued) {
        toast.success(`建仓计划已入队，${execDate} 9:30 开盘执行`);
      } else {
        toast.info(r.reason ?? "建仓计划未重复入队");
      }
      setPreview(null);
      select(selected.id);
      load();
    } catch (e) { toast.error(e instanceof Error ? e.message : "建仓失败"); }
  };

  const [execingPending, setExecingPending] = useState(false);
  const execPending = async () => {
    setExecingPending(true);
    try {
      const r = await api.portfolioExecutePending();
      if (r.executed > 0) {
        toast.success(`已执行 ${r.executed} 单待办建仓/调仓（含调仓 ${r.rebalances ?? 0}）`);
      } else {
        toast.info(r.reason ?? "暂无到期待办");
      }
      if (selected) select(selected.id);
      load();
    } catch (e) { toast.error(e instanceof Error ? e.message : "执行待办失败"); }
    finally { setExecingPending(false); }
  };

  const runCompare = async () => {
    if (!selected) return;
    try {
      setCompare(await api.portfolioCompare(selected.id, {
        days: compareDays, top_n: buildN, min_score: buildMin,
        strategy: buildStrategy, pos_style: buildStyle, rebalance: "weekly",
        lookback: buildLookback,
      }));
    } catch (e) { toast.error(e instanceof Error ? e.message : "对比失败"); }
  };

  const applyPreset = (preset: PortfolioPreset, syncSettings = false) => {
    const applied = applyPortfolioPreset(preset);
    setBuildStrategy(applied.buildStrategy);
    setBuildN(applied.buildN);
    setBuildMin(applied.buildMin);
    setBuildLookback(applied.buildLookback);
    setBuildSectorWindow(applied.buildSectorWindow);
    setBuildPerSector(applied.buildPerSector);
    setBuildStyle(applied.buildStyle);
    setSettingsForm((prev) => ({ ...prev, ...applied.settings }));
    setPreview(null);
    setTurtlePreview(null);
    if (syncSettings && selected) {
      if (preset.id === "turtle") {
        api.portfolioSyncTurtle(selected.id, {
          lookback: preset.lookback,
          top_n: preset.top_n,
          min_score: preset.min_score,
          rebalance_schedule: preset.rebalance_schedule,
        }).then(() => {
          toast.success(`已应用「${preset.label}」并保存为默认策略`);
          select(selected.id);
        }).catch((e) => toast.error(e instanceof Error ? e.message : "保存预设失败"));
      } else {
        api.portfolioUpdateSettings(selected.id, applied.settings).then(() => {
          toast.success(`已应用「${preset.label}」并保存为默认策略`);
          select(selected.id);
        }).catch((e) => toast.error(e instanceof Error ? e.message : "保存预设失败"));
      }
    } else {
      toast.success(`已应用预设「${preset.label}」`);
    }
  };

  const checkTurtleExits = async () => {
    if (!selected) return;
    setTurtleBusy(true);
    try {
      const r = await api.portfolioTurtleExitsPreview(selected.id);
      setTurtlePreview(r);
      if (r.signal_count === 0) {
        toast.success(`已检查 ${r.monitored} 只持仓，暂无出场信号`);
      } else {
        toast.info(`发现 ${r.signal_count} 只触发出场信号`);
      }
    } catch (e) {
      toast.error(e instanceof Error ? e.message : "检查失败");
    } finally {
      setTurtleBusy(false);
    }
  };

  const executeTurtleExits = async () => {
    if (!selected || !turtlePreview?.signal_count) return;
    if (!confirm(`确认卖出 ${turtlePreview.signal_count} 只触发出场信号的持仓？`)) return;
    setTurtleBusy(true);
    try {
      const r = await api.portfolioTurtleExits(selected.id);
      toast.success(`已执行出场，卖出 ${r.exited} 只`);
      setTurtlePreview(null);
      select(selected.id);
      load();
    } catch (e) {
      toast.error(e instanceof Error ? e.message : "执行失败");
    } finally {
      setTurtleBusy(false);
    }
  };

  const saveSettings = async () => {
    if (!selected) return;
    try {
      const pf = await api.portfolioUpdateSettings(selected.id, settingsForm);
      setSelected(pf);
      toast.success("设置已保存");
    } catch (e) { toast.error(e instanceof Error ? e.message : "保存失败"); }
  };

  const removePf = async (id: number) => {
    if (!confirm("删除组合？")) return;
    try {
      await api.portfolioDelete(id);
      setSelected(null);
      load();
    } catch (e) { toast.error(e instanceof Error ? e.message : "删除失败"); }
  };

  const positions = selected?.positions || [];
  const showTurtleTools =
    buildStrategy === "turtle" ||
    selected?.settings?.default_strategy === "turtle" ||
    positions.some((p) => p.turtle_stop_price != null);
  const history = selected?.history || [];
  const journal = (selected?.journal || []).filter((j) =>
    journalFilter === "all" ? true : j.action === journalFilter
  );

  return (
    <BetaShell title="模拟交易" subtitle="实时/EOD 定价 · 滑点 · 涨跌停 · T+1">
      <BetaTabs active="portfolio" tabs={BETA_TABS} />

      {pricingCtx && (
        <PricingBanner ctx={pricingCtx} />
      )}

      <div className="flex flex-wrap justify-between items-center gap-2">
        <span className="text-sm text-muted-foreground">{pfs.length} 个组合</span>
        <div className="flex gap-2 items-center">
          <input type="number" value={createCash} onChange={(e) => setCreateCash(+e.target.value)} className="w-28 border rounded px-2 py-1 text-xs" placeholder="初始资金" />
          <button onClick={create} className="rounded bg-primary text-primary-foreground px-3 py-1.5 text-sm">+ 新建</button>
        </div>
      </div>

      <div className="grid md:grid-cols-3 gap-2">
        {pfs.map((p) => (
          <div key={p.id} className={`p-3 rounded-lg border ${selected?.id === p.id ? "border-primary ring-1" : ""}`}>
            <button onClick={() => select(p.id)} className="w-full text-left">
              <div className="font-bold text-sm">{p.name}</div>
              <div className="text-lg">¥{Number(p.total_value).toLocaleString()}</div>
            </button>
            <div className="flex gap-3 mt-1">
              <button
                onClick={() => { select(p.id); setNewName(p.name); setRenaming(true); }}
                className="text-[10px] text-muted-foreground hover:text-primary"
              >重命名</button>
              <button onClick={() => removePf(p.id)} className="text-[10px] text-muted-foreground hover:text-destructive">删除</button>
            </div>
          </div>
        ))}
      </div>

      {selected && (
        <>
          {metrics && (
            <div className="grid grid-cols-2 md:grid-cols-5 gap-2 text-center text-xs">
              <Stat label="累计收益" value={`${metrics.total_return_pct}%`} positive={metrics.total_return_pct >= 0} />
              <Stat label="最大回撤" value={`${metrics.max_drawdown_pct}%`} />
              <Stat label="胜率" value={`${metrics.win_rate_pct}%`} />
              <Stat label="运行天数" value={String(metrics.days_running)} />
              <Stat label="已实现盈亏" value={`¥${metrics.realized_pnl}`} positive={metrics.realized_pnl >= 0} />
            </div>
          )}

          <div className="flex gap-2 border-b pb-1">
            {(["holdings", "perf", "compare", "settings"] as Tab[]).map((t) => (
              <button key={t} onClick={() => setTab(t)}
                className={`px-3 py-1 text-xs rounded-t ${tab === t ? "bg-muted font-medium" : "text-muted-foreground"}`}>
                {t === "holdings" ? "持仓交易" : t === "perf" ? "表现分析" : t === "compare" ? "vs 回测" : "设置"}
              </button>
            ))}
            <a href={api.portfolioExportUrl(selected.id)} className="ml-auto text-xs text-primary underline self-center">导出 CSV</a>
          </div>

          {tab === "holdings" && (
            <div className="grid md:grid-cols-2 gap-4">
              <Card>
                <CardHeader className="flex flex-row items-center justify-between">
                  {renaming ? (
                    <div className="flex gap-2 items-center">
                      <input value={newName} onChange={(e) => setNewName(e.target.value)} className="border rounded px-2 py-1 text-sm" />
                      <button onClick={async () => { await api.portfolioRename(selected.id, newName.trim()); setRenaming(false); select(selected.id); load(); }} className="text-xs text-primary">保存</button>
                      <button onClick={() => setRenaming(false)} className="text-xs text-muted-foreground">取消</button>
                    </div>
                  ) : (
                    <CardTitle className="text-base flex items-center gap-2">
                      {selected.name}
                      <span className="text-[10px] font-normal text-muted-foreground">{selected.created_at?.slice(0, 10)}</span>
                      <button onClick={() => setRenaming(true)} className="text-[10px] font-normal text-muted-foreground">重命名</button>
                    </CardTitle>
                  )}
                </CardHeader>
                <CardContent className="space-y-3">
                  <div className="grid grid-cols-2 md:grid-cols-4 gap-2 text-center text-xs">
                    <div className="bg-muted rounded p-2"><div className="font-bold font-mono tabular-nums">¥{Number(selected.total_value).toLocaleString()}</div>总资产</div>
                    <div className="bg-muted rounded p-2"><div className="font-bold font-mono tabular-nums">¥{Number(selected.cash).toLocaleString()}</div>现金</div>
                    <div className="bg-muted rounded p-2">
                      <div className={`font-bold font-mono tabular-nums ${Number(selected.account_pnl ?? selected.pnl) >= 0 ? "text-up" : "text-down"}`}>
                        {fmtPct(selected.account_pnl_pct ?? selected.pnl_pct)}
                      </div>
                      <div className="text-[10px] text-muted-foreground">账户累计</div>
                      {selected.realized_pnl != null && (
                        <div className="text-[9px] text-muted-foreground mt-0.5">
                          已实现 ¥{Number(selected.realized_pnl).toLocaleString()}
                        </div>
                      )}
                    </div>
                    <div className="bg-muted rounded p-2">
                      <div className={`font-bold font-mono tabular-nums ${holdingsPnlColor(selected)}`}>
                        {fmtPct(
                          selected.display_unrealized_pnl_pct ?? selected.market_unrealized_pnl_pct ?? selected.unrealized_pnl_pct,
                          selected.is_unrealized_friction_only,
                        )}
                      </div>
                      <div className="text-[10px] text-muted-foreground">持仓浮盈</div>
                      {selected.unrealized_pnl_pct != null && !selected.is_unrealized_friction_only && (
                        <div className="text-[9px] text-muted-foreground mt-0.5">
                          含成本 {fmtPct(selected.unrealized_pnl_pct)}
                        </div>
                      )}
                    </div>
                  </div>
                  {(selected.account_pnl_pct ?? selected.pnl_pct) < -1 &&
                    Math.abs(selected.display_unrealized_pnl_pct ?? selected.market_unrealized_pnl_pct ?? 0) < 1 && (
                    <p className="text-[10px] text-muted-foreground text-center leading-relaxed">
                      账户累计亏损主要来自历史已平仓交易（已实现 ¥{Number(selected.realized_pnl ?? 0).toLocaleString()}），
                      当前 {selected.positions.length} 只持仓几乎持平。
                    </p>
                  )}
                  {navSeries && navSeries.dates.length > 1 && (
                    <BetaDualChart
                      title="净值走势（归一化 100）"
                      strategy={navSeries.dates.map((d, i) => ({ date: d, value: navSeries.nav[i] / 100 }))}
                      benchmark={
                        navSeries.benchmark.some((v) => v != null)
                          ? navSeries.dates.map((d, i) => ({ date: d, value: (navSeries.benchmark[i] ?? navSeries.nav[i]) / 100 }))
                          : undefined
                      }
                    />
                  )}
                  {rebalancePreview?.due && (
                    <div className="rounded-md border border-primary/20 bg-primary/5 p-3 space-y-2">
                      <div className="flex items-center justify-between">
                        <div className="text-xs font-medium text-primary flex items-center gap-1">
                          <Bell className="h-3.5 w-3.5" /> {rebalancePreview.days_left === 0 ? "今日收盘" : "明日收盘"}生成调仓计划，{rebalancePreview.execute_date ?? "下一交易日"} 9:30 开盘执行
                          <span className="text-primary/70 font-normal ml-1">
                            （{rebalancePreview.schedule === "weekly" ? "每周" : "每月"}调仓）
                          </span>
                        </div>
                        <button
                          disabled={skipBusy}
                          onClick={async () => {
                            if (!selected) return;
                            setSkipBusy(true);
                            try {
                              const newSkip = !rebalancePreview.skip_next_rebalance;
                              await api.portfolioSkipRebalance(selected.id, newSkip);
                              setRebalancePreview((p) => p ? { ...p, skip_next_rebalance: newSkip } : p);
                              toast.success(newSkip ? "已取消本次调仓" : "已恢复本次调仓");
                            } catch { toast.error("操作失败"); }
                            finally { setSkipBusy(false); }
                          }}
                          className={`text-[10px] px-2 py-0.5 rounded border transition-colors ${
                            rebalancePreview.skip_next_rebalance
                              ? "border-border text-muted-foreground bg-muted hover:bg-muted/70"
                              : "border-amber-500/40 text-amber-700 dark:text-amber-500 hover:bg-amber-500/10"
                          }`}
                        >
                          {rebalancePreview.skip_next_rebalance ? "✓ 已取消，点击恢复" : "取消本次调仓"}
                        </button>
                      </div>
                      {rebalancePreview.skip_next_rebalance && (
                        <p className="text-[10px] text-primary/70">本次调仓已被取消，系统将跳过并顺延计划。</p>
                      )}
                      {!rebalancePreview.skip_next_rebalance && (
                        <div className="grid grid-cols-2 gap-2 text-[11px]">
                          {rebalancePreview.preview_buy.length > 0 && (
                            <div>
                              <p className="text-up font-medium mb-1">预计买入 ({rebalancePreview.preview_buy.length})</p>
                              {rebalancePreview.preview_buy.map((s) => (
                                <div key={s.code} className="flex justify-between text-muted-foreground">
                                  <span><span className="font-mono">{s.code}</span> {s.name}</span>
                                  <span className="text-up flex gap-2">{s.est_shares != null && <span>{s.est_shares}股</span>}{
                                    (() => {
                                      const st = rebalancePreview.strategy;
                                      const V5_LABELS: Record<string, string> = {
                                        composite: "V5综合", fundamental: "基本面", quality: "质量",
                                        industry: "行业", capital: "资金面", valuation: "估值",
                                        technical: "技术", market_env: "大盘", policy: "政策",
                                        news: "新闻", mood: "情绪", index_enhance: "指增",
                                      };
                                      if (st === "momentum") return `动量 ${s.score}`;
                                      if (st === "turtle") return `突破 ${s.score}`;
                                      if (st === "sector_rotation") return `强度 ${s.score}`;
                                      if (st === "dual_ma" || st === "factor_combination" || st?.startsWith("F")) return `因子 ${s.score}`;
                                      return `${V5_LABELS[st] ?? "V5"} ${s.score}`;
                                    })()
                                  }</span>
                                </div>
                              ))}
                            </div>
                          )}
                          {rebalancePreview.preview_sell.length > 0 && (
                            <div>
                              <p className="text-down font-medium mb-1">预计卖出 ({rebalancePreview.preview_sell.length})</p>
                              {rebalancePreview.preview_sell.map((s) => (
                                <div key={s.code} className="flex justify-between text-muted-foreground">
                                  <span><span className="font-mono">{s.code}</span> {s.name}</span>
                                  <span>{s.shares}股</span>
                                </div>
                              ))}
                            </div>
                          )}
                          {rebalancePreview.preview_buy.length === 0 && rebalancePreview.preview_sell.length === 0 && (
                            <p className="text-muted-foreground col-span-2">持仓与目标一致，调仓时无需换手</p>
                          )}
                        </div>
                      )}
                    </div>
                  )}
                  {scoreAlerts.length > 0 && (
                    <div className="rounded-md border border-amber-500/30 bg-amber-500/10 p-3 space-y-2">
                      <div className="text-xs font-medium text-amber-800 dark:text-amber-400 flex items-center gap-1">
                        <AlertTriangle className="h-3.5 w-3.5" /> 触发式调仓信号（{scoreAlerts.length} 只）
                      </div>
                      {scoreAlerts.map((a) => (
                        <div key={a.code} className="flex items-center justify-between text-xs gap-2">
                          <span>
                            <span className="font-mono font-bold">{a.code}</span>
                            <span className="ml-1 text-muted-foreground">{a.name}</span>
                            <span className="ml-2">
                              {a.trigger === "exclude"
                                ? <span className="text-destructive font-medium">强否决</span>
                                : <span className="text-amber-700 dark:text-amber-500">{a.score_label ?? "V5"} {a.composite_v5?.toFixed(1) ?? "—"}</span>}
                            </span>
                          </span>
                          <button
                            disabled={replacingCode === a.code}
                            onClick={async () => {
                              setReplacingCode(a.code);
                              try {
                                const r = await api.portfolioReplacePosition(selected.id, {
                                  sell_code: a.code,
                                  strategy: buildStrategy,
                                  min_score: buildMin,
                                });
                                if (r.warning) toast.error(r.warning);
                                else toast.success(`已替换：卖出 ${r.sold.code}，买入 ${r.bought.code}（${r.bought.name}）${r.bought.shares}股`);
                                select(selected.id);
                              } catch (e) {
                                toast.error(e instanceof Error ? e.message : "替换失败");
                              } finally {
                                setReplacingCode(null);
                              }
                            }}
                            className="text-[10px] px-2 py-0.5 rounded border border-amber-500/40 text-amber-700 dark:text-amber-500 hover:bg-amber-500/10 disabled:opacity-50 whitespace-nowrap"
                          >
                            {replacingCode === a.code ? "替换中…" : "一键替换"}
                          </button>
                        </div>
                      ))}
                    </div>
                  )}
                  <div className="overflow-x-auto">
                    <table className="w-full text-[11px]">
                      <thead><tr className="text-muted-foreground border-b">
                        <th className="text-left py-1">代码 / 名称</th><th>持仓</th><th>现价</th><th>成本</th>
                        {showTurtleTools && <th>止损</th>}
                        <th>权重</th><th>浮盈</th><th></th>
                      </tr></thead>
                      <tbody>
                        {positions.map((p) => (
                          <PositionRow
                            key={p.code}
                            p={p}
                            showStop={showTurtleTools}
                            onSell={() => doTrade("sell", p.code, Math.min(p.sellable_shares, p.shares))}
                          />
                        ))}
                      </tbody>
                    </table>
                  </div>
                  <div className="flex flex-wrap gap-1.5">
                    {PORTFOLIO_PRESETS.map((preset) => (
                      <button
                        key={preset.id}
                        type="button"
                        onClick={() => applyPreset(preset, false)}
                        className="text-[10px] px-2 py-1 rounded border hover:bg-muted"
                      >
                        {preset.label}
                      </button>
                    ))}
                    {buildStrategy === "turtle" && selected && (
                      <button
                        type="button"
                        onClick={() => applyPreset(PORTFOLIO_PRESETS.find((p) => p.id === "turtle")!, true)}
                        className="text-[10px] px-2 py-1 rounded border border-primary text-primary hover:bg-primary/5"
                      >
                        保存海龟预设
                      </button>
                    )}
                  </div>
                  <BuildControls buildN={buildN} setBuildN={setBuildN} buildMin={buildMin} setBuildMin={setBuildMin}
                    buildStrategy={buildStrategy} setBuildStrategy={setBuildStrategy} buildStyle={buildStyle} setBuildStyle={setBuildStyle}
                    buildComboId={buildComboId} setBuildComboId={setBuildComboId} buildLookback={buildLookback} setBuildLookback={setBuildLookback}
                    buildSectorWindow={buildSectorWindow} setBuildSectorWindow={setBuildSectorWindow}
                    buildPerSector={buildPerSector} setBuildPerSector={setBuildPerSector}
                    strategies={strategies} combinations={combinations}
                    onPreview={runPreview} onBuild={buildTop} />
                  <div className="flex items-center gap-2 text-[10px] text-muted-foreground">
                    <button
                      type="button"
                      onClick={execPending}
                      disabled={execingPending}
                      className="border border-border rounded px-2 py-1 hover:bg-muted transition-colors disabled:opacity-50"
                    >
                      {execingPending ? "执行中…" : "立即执行待办"}
                    </button>
                    <span>建仓/调仓默认排到下个交易日开盘执行；点此立即补跑到期待办。</span>
                  </div>
                  {showTurtleTools && (
                    <div className="border rounded p-2 text-[10px] bg-muted/30 space-y-2">
                      <div className="flex flex-wrap items-center gap-2">
                        <span className="font-medium">海龟止损</span>
                        <span className="text-muted-foreground">
                          lookback {buildLookback} · 每日收盘扫描 2N 止损，次日 9:30 开盘执行
                        </span>
                        <button
                          type="button"
                          disabled={turtleBusy}
                          onClick={checkTurtleExits}
                          className="border px-2 py-1 rounded bg-background disabled:opacity-50"
                        >
                          {turtleBusy ? "检查中…" : "检查出场"}
                        </button>
                        {turtlePreview && turtlePreview.signal_count > 0 && (
                          <button
                            type="button"
                            disabled={turtleBusy}
                            onClick={executeTurtleExits}
                            className="bg-amber-600 text-white px-2 py-1 rounded disabled:opacity-50"
                          >
                            执行卖出 ({turtlePreview.signal_count})
                          </button>
                        )}
                      </div>
                      {turtlePreview && (
                        <div className="space-y-1">
                          {turtlePreview.signals.length === 0 ? (
                            <p className="text-muted-foreground">监控 {turtlePreview.monitored} 只，暂无出场信号</p>
                          ) : (
                            turtlePreview.signals.map((s) => (
                              <div key={s.code} className="flex justify-between gap-2 text-amber-800">
                                <span>{s.code} {s.name}</span>
                                <span>
                                  {s.exit_reason === "stop" ? "2N止损" : s.exit_reason} · 可卖 {s.sellable_shares}股
                                  {s.stop_price != null ? ` · 止损 ¥${s.stop_price}` : ""}
                                </span>
                              </div>
                            ))
                          )}
                        </div>
                      )}
                    </div>
                  )}
                  {preview && (
                    <div className="border rounded p-2 text-[10px] bg-muted/50">
                      <div className="font-medium mb-1">建仓预览</div>
                      {preview.preview.map((p) => (
                        <div key={p.code} className="flex justify-between gap-2">
                          <span>{p.code} {p.name}</span>
                          <span className="text-right">{p.shares}股 · ¥{p.est_cost}{p.price_label ? <span className="text-muted-foreground ml-1">({p.quote_date})</span> : null}</span>
                        </div>
                      ))}
                    </div>
                  )}
                  <TradePanel searchQ={searchQ} setSearchQ={setSearchQ} searchHits={searchHits} setSearchHits={setSearchHits}
                    tradeCode={tradeCode} setTradeCode={setTradeCode} tradeShares={tradeShares} setTradeShares={setTradeShares}
                    feePreview={feePreview} onBuy={() => doTrade("buy")} onSell={() => doTrade("sell")} />
                </CardContent>
              </Card>
              <Card>
                <CardHeader className="flex flex-row justify-between items-center">
                  <CardTitle className="text-sm">交易记录</CardTitle>
                  <select value={journalFilter} onChange={(e) => setJournalFilter(e.target.value as "all" | "BUY" | "SELL")} className="text-[10px] border rounded px-1">
                    <option value="all">全部</option><option value="BUY">买入</option><option value="SELL">卖出</option>
                  </select>
                </CardHeader>
                <CardContent className="max-h-96 overflow-auto text-xs">
                  {journal.map((j, i) => (
                    <div key={i} className="border-b border-border py-1 flex justify-between gap-2 font-mono tabular-nums">
                      <span>{j.trade_date} {j.code} @ {Number(j.price).toFixed(2)}{j.quote_date && j.quote_date !== j.trade_date ? ` (${j.quote_date})` : ""}</span>
                      <span className={j.action === "BUY" ? "text-up shrink-0" : "text-down shrink-0"}>
                        {j.action === "BUY" ? "买" : "卖"} {j.shares}
                        {j.price_source ? <span className="text-muted-foreground ml-1">{j.price_source === "realtime" ? "实时" : "收盘"}</span> : null}
                        {j.commission != null ? ` · ¥${((j.commission || 0) + (j.tax || 0)).toFixed(2)}` : ""}
                      </span>
                    </div>
                  ))}
                </CardContent>
              </Card>
            </div>
          )}

          {tab === "perf" && selected && <PerformanceTab pid={selected.id} />}

          {tab === "compare" && (
            <div className="space-y-3">
              <div className="flex flex-wrap gap-2 items-end">
                <label className="text-xs">对比天数<input type="number" value={compareDays} onChange={(e) => setCompareDays(+e.target.value)} className="block w-20 border rounded px-1 mt-1" /></label>
                <BuildControls buildN={buildN} setBuildN={setBuildN} buildMin={buildMin} setBuildMin={setBuildMin}
                  buildStrategy={buildStrategy} setBuildStrategy={setBuildStrategy} buildStyle={buildStyle} setBuildStyle={setBuildStyle}
                  buildComboId={buildComboId} setBuildComboId={setBuildComboId} buildLookback={buildLookback} setBuildLookback={setBuildLookback}
                  buildSectorWindow={buildSectorWindow} setBuildSectorWindow={setBuildSectorWindow}
                  buildPerSector={buildPerSector} setBuildPerSector={setBuildPerSector}
                  strategies={strategies} combinations={combinations} hideBuild />
                <button onClick={runCompare} className="bg-primary text-primary-foreground text-xs px-3 py-1.5 rounded hover:bg-primary/90 transition-colors">运行对比</button>
              </div>
              {compare && !compare.error && (
                <>
                  <div className="grid grid-cols-3 gap-2 text-center text-xs">
                    <div className="bg-muted rounded p-2"><div className="font-bold font-mono tabular-nums text-primary">{compare.sim_return_pct}%</div>模拟盘</div>
                    <div className="bg-muted rounded p-2"><div className="font-bold font-mono tabular-nums">{compare.backtest_return_pct}%</div>回测</div>
                    <div className="bg-muted rounded p-2"><div className={`font-bold font-mono tabular-nums ${compare.gap_pct >= 0 ? "text-up" : "text-down"}`}>{compare.gap_pct}%</div>差距</div>
                  </div>
                  <BetaDualChart title="模拟盘 vs 回测" strategy={compare.simulated_curve.map((p: {date:string;value:number}) => ({...p, value: p.value/100}))} benchmark={compare.backtest_curve.map((p: {date:string;value:number}) => ({...p, value: p.value/100}))} />
                  {compare.benchmark_curve && compare.benchmark_curve.length > 0 && (
                    <BetaDualChart title="回测 vs 等权基准" strategy={compare.backtest_curve.map((p: {date:string;value:number}) => ({...p, value: p.value/100}))} benchmark={compare.benchmark_curve.map((p: {date:string;value:number}) => ({...p, value: p.value/100}))} />
                  )}
                </>
              )}
            </div>
          )}

          {tab === "settings" && (
            <Card>
              <CardHeader><CardTitle className="text-sm">组合设置 · 风控 · 定时调仓</CardTitle></CardHeader>
              <CardContent className="grid md:grid-cols-2 gap-3 text-xs">
                <label>定时调仓
                  <select
                    value={settingsForm.rebalance_schedule || "none"}
                    disabled={settingsForm.default_strategy === "turtle"}
                    onChange={(e) => setSettingsForm({ ...settingsForm, rebalance_schedule: e.target.value as PortfolioSettings["rebalance_schedule"] })}
                    className="block w-full border rounded px-2 py-1 mt-1 disabled:opacity-60"
                  >
                    <option value="none">关闭</option><option value="weekly">每周</option><option value="monthly">每月</option>
                  </select>
                  {settingsForm.default_strategy === "turtle" && (
                    <span className="text-[10px] text-muted-foreground">海龟策略仅手动入场 + 日频止损，无定时调仓</span>
                  )}
                </label>
                <label>单票最大权重 %
                  <input type="number" value={settingsForm.max_weight_pct ?? 30} onChange={(e) => setSettingsForm({ ...settingsForm, max_weight_pct: +e.target.value })}
                    className="block w-full border rounded px-2 py-1 mt-1" />
                </label>
                <label>最低现金保留 %
                  <input type="number" value={settingsForm.min_cash_pct ?? 5} onChange={(e) => setSettingsForm({ ...settingsForm, min_cash_pct: +e.target.value })}
                    className="block w-full border rounded px-2 py-1 mt-1" />
                </label>
                <label>默认策略
                  <select value={settingsForm.default_strategy || "composite"} onChange={(e) => {
                    const id = e.target.value;
                    const defs = applyStrategyDefaults(id, strategies);
                    setSettingsForm({
                      ...settingsForm,
                      default_strategy: id,
                      ...(defs.top_n != null ? { default_top_n: defs.top_n } : {}),
                      ...(defs.min_score != null ? { default_min_score: defs.min_score } : {}),
                      ...(defs.lookback != null ? { default_lookback: defs.lookback } : {}),
                    });
                  }}
                    className="block w-full border rounded px-2 py-1 mt-1">
                    {strategies.map((s) => <option key={s.id} value={s.id}>{s.label}</option>)}
                  </select>
                </label>
                {settingsForm.default_strategy === "factor_combination" && (
                  <label>默认合成方案
                    <select
                      value={settingsForm.default_combination_id ?? ""}
                      onChange={(e) => setSettingsForm({
                        ...settingsForm,
                        default_combination_id: e.target.value ? Number(e.target.value) : undefined,
                      })}
                      className="block w-full border rounded px-2 py-1 mt-1"
                    >
                      <option value="">请选择</option>
                      {combinations.map((c) => <option key={c.id} value={c.id}>{c.name}</option>)}
                    </select>
                  </label>
                )}
                {(settingsForm.default_strategy === "momentum" || settingsForm.default_strategy === "turtle") && (
                  <label>lookback
                    <input type="number" value={settingsForm.default_lookback ?? 20}
                      onChange={(e) => setSettingsForm({ ...settingsForm, default_lookback: +e.target.value })}
                      className="block w-full border rounded px-2 py-1 mt-1" min={5} max={250} />
                  </label>
                )}
                {settingsForm.default_strategy === "sector_rotation" && (
                  <>
                    <label>行业窗口(日)
                      <input type="number" value={settingsForm.default_sector_window ?? 5}
                        onChange={(e) => setSettingsForm({ ...settingsForm, default_sector_window: +e.target.value })}
                        className="block w-full border rounded px-2 py-1 mt-1" min={2} max={20} />
                    </label>
                    <label>每行业选股
                      <input type="number" value={settingsForm.default_per_sector ?? 2}
                        onChange={(e) => setSettingsForm({ ...settingsForm, default_per_sector: +e.target.value })}
                        className="block w-full border rounded px-2 py-1 mt-1" min={1} max={5} />
                    </label>
                  </>
                )}
                <label>默认 Top N<input type="number" value={settingsForm.default_top_n ?? 5} onChange={(e) => setSettingsForm({ ...settingsForm, default_top_n: +e.target.value })} className="block w-full border rounded px-2 py-1 mt-1" /></label>
                <label>默认最低分<input type="number" value={settingsForm.default_min_score ?? 50} onChange={(e) => setSettingsForm({ ...settingsForm, default_min_score: +e.target.value })} className="block w-full border rounded px-2 py-1 mt-1" /></label>
                <label>默认定权
                  <select value={settingsForm.default_pos_style || "equal"} onChange={(e) => setSettingsForm({ ...settingsForm, default_pos_style: e.target.value as "equal" | "weighted" })}
                    className="block w-full border rounded px-2 py-1 mt-1">
                    <option value="equal">等权</option><option value="weighted">按分加权</option>
                  </select>
                </label>
                {selected.settings?.last_rebalance_date && (
                  <p className="text-muted-foreground col-span-2">上次调仓：{selected.settings.last_rebalance_date}</p>
                )}
                <button onClick={saveSettings} className="col-span-2 bg-primary text-primary-foreground rounded py-2 text-sm">保存设置</button>
              </CardContent>
            </Card>
          )}
        </>
      )}
    </BetaShell>
  );
}

function fmtPct(v: number | undefined | null, frictionOnly?: boolean) {
  if (v == null || Number.isNaN(v)) return "—";
  if (frictionOnly || Math.abs(v) < 0.01) return "≈0%";
  return `${v >= 0 ? "+" : ""}${v.toFixed(2)}%`;
}

function holdingsPnlColor(pf: PortfolioDetail) {
  if (pf.is_unrealized_friction_only) return "text-muted-foreground";
  const v = pf.display_unrealized_pnl_pct ?? pf.market_unrealized_pnl_pct ?? pf.unrealized_pnl_pct ?? 0;
  if (Math.abs(v) < 0.01) return "text-muted-foreground";
  return v >= 0 ? "text-up" : "text-down";
}

function positionMainPct(p: PortfolioPosition) {
  if (p.is_friction_only) return 0;
  return p.display_pnl_pct ?? p.market_pnl_pct ?? p.pnl_pct;
}

function positionPnlColor(p: PortfolioPosition) {
  if (p.is_friction_only || Math.abs(positionMainPct(p)) < 0.01) return "text-muted-foreground";
  return positionMainPct(p) >= 0 ? "text-up" : "text-down";
}

function Stat({ label, value, positive }: { label: string; value: string; positive?: boolean }) {
  return (
    <div className="bg-muted rounded p-2">
      <div className={`font-bold font-mono tabular-nums ${positive === true ? "text-up" : positive === false ? "text-down" : ""}`}>{value}</div>
      <div className="text-muted-foreground">{label}</div>
    </div>
  );
}

function BuildControls(props: {
  buildN: number; setBuildN: (n: number) => void;
  buildMin: number; setBuildMin: (n: number) => void;
  buildStrategy: string; setBuildStrategy: (s: string) => void;
  buildComboId: number | ""; setBuildComboId: (v: number | "") => void;
  buildLookback: number; setBuildLookback: (n: number) => void;
  buildSectorWindow: number; setBuildSectorWindow: (n: number) => void;
  buildPerSector: number; setBuildPerSector: (n: number) => void;
  buildStyle: "equal" | "weighted"; setBuildStyle: (s: "equal" | "weighted") => void;
  strategies: StrategyOption[];
  combinations: { id: number; name: string }[];
  onPreview?: () => void; onBuild?: () => void; hideBuild?: boolean;
}) {
  const {
    buildN, setBuildN, buildMin, setBuildMin, buildStrategy, setBuildStrategy,
    buildComboId, setBuildComboId, buildLookback, setBuildLookback,
    buildSectorWindow, setBuildSectorWindow, buildPerSector, setBuildPerSector,
    buildStyle, setBuildStyle, strategies, combinations, onPreview, onBuild, hideBuild,
  } = props;
  const onStrategyChange = (id: string) => {
    setBuildStrategy(id);
    const defs = applyStrategyDefaults(id, strategies);
    if (defs.top_n != null) setBuildN(defs.top_n);
    if (defs.min_score != null) setBuildMin(defs.min_score);
    if (defs.lookback != null) setBuildLookback(defs.lookback);
  };
  return (
    <div className="flex flex-wrap gap-2 items-end border-t pt-2">
      <label className="text-xs">Top N<input type="number" value={buildN} onChange={(e) => setBuildN(+e.target.value)} className="block w-16 border rounded px-1 mt-1" min={1} max={50} /></label>
      <label className="text-xs">最低分<input type="number" value={buildMin} onChange={(e) => setBuildMin(+e.target.value)} className="block w-16 border rounded px-1 mt-1" /></label>
      <label className="text-xs">策略
        <select value={buildStrategy} onChange={(e) => onStrategyChange(e.target.value)} className="block border rounded px-1 mt-1 text-xs">
          {strategies.map((s) => <option key={s.id} value={s.id}>{s.label}</option>)}
        </select>
      </label>
      {buildStrategy === "factor_combination" && (
        <label className="text-xs">合成方案
          <select value={buildComboId} onChange={(e) => setBuildComboId(e.target.value ? Number(e.target.value) : "")} className="block border rounded px-1 mt-1 text-xs max-w-[140px]">
            <option value="">请选择</option>
            {combinations.map((c) => <option key={c.id} value={c.id}>{c.name}</option>)}
          </select>
        </label>
      )}
      {(buildStrategy === "momentum" || buildStrategy === "turtle") && (
        <label className="text-xs">lookback
          <input type="number" value={buildLookback} onChange={(e) => setBuildLookback(+e.target.value)} className="block w-16 border rounded px-1 mt-1" min={5} max={250} />
        </label>
      )}
      {buildStrategy === "sector_rotation" && (
        <>
          <label className="text-xs">窗口
            <input type="number" value={buildSectorWindow} onChange={(e) => setBuildSectorWindow(+e.target.value)} className="block w-12 border rounded px-1 mt-1" min={2} max={20} />
          </label>
          <label className="text-xs">每行业
            <input type="number" value={buildPerSector} onChange={(e) => setBuildPerSector(+e.target.value)} className="block w-12 border rounded px-1 mt-1" min={1} max={5} />
          </label>
        </>
      )}
      <label className="text-xs">权重
        <select value={buildStyle} onChange={(e) => setBuildStyle(e.target.value as "equal" | "weighted")} className="block border rounded px-1 mt-1 text-xs">
          <option value="equal">等权</option><option value="weighted">按分加权</option>
        </select>
      </label>
      {onPreview && <button onClick={onPreview} className="border text-xs px-2 py-1.5 rounded">预览</button>}
      {!hideBuild && onBuild && <button onClick={onBuild} className="bg-primary text-primary-foreground text-xs px-2 py-1.5 rounded hover:bg-primary/90 transition-colors">Top N 建仓</button>}
    </div>
  );
}

function PricingBanner({ ctx }: { ctx: PricingContext }) {
  const tone = ctx.can_trade
    ? ctx.mode === "intraday" ? "bg-primary/5 border-primary/20 text-foreground" : "bg-muted border-border text-foreground"
    : "bg-amber-500/10 border-amber-500/30 text-amber-800 dark:text-amber-400";
  return (
    <div className={`text-xs border rounded px-3 py-2 ${tone}`}>
      <div className="font-medium">{ctx.session_label}</div>
      <div className="text-[10px] mt-0.5 opacity-90">
        库内最新行情 {ctx.latest_quote_date} · 成交日记 {ctx.trade_date}
        · 滑点 {(ctx.slippage_pct * 100).toFixed(2)}%
        · 现价优先腾讯实时，失败时用库内 EOD
        {ctx.mode === "intraday" ? " · 盘中按实时价+滑点" : ctx.mode === "eod" ? " · 收盘后按 EOD 收盘价" : ""}
        {ctx.block_reason ? ` · ${ctx.block_reason}` : ""}
      </div>
    </div>
  );
}

function TradePanel(props: {
  searchQ: string; setSearchQ: (s: string) => void;
  searchHits: { code: string; name: string }[]; setSearchHits: (h: { code: string; name: string }[]) => void;
  tradeCode: string; setTradeCode: (s: string) => void;
  tradeShares: number; setTradeShares: (n: number) => void;
  feePreview: FeePreview | null;
  onBuy: () => void; onSell: () => void;
}) {
  const { searchQ, setSearchQ, searchHits, setSearchHits, tradeCode, setTradeCode, tradeShares, setTradeShares, feePreview, onBuy, onSell } = props;
  return (
    <div className="space-y-2">
      <div className="relative">
        <input placeholder="搜索股票…" value={searchQ} onChange={(e) => setSearchQ(e.target.value)} className="w-full border rounded px-2 py-1 text-sm" />
        {searchHits.length > 0 && (
          <div className="absolute z-10 bg-background border rounded shadow max-h-32 overflow-auto w-full">
            {searchHits.map((h) => (
              <button key={h.code} className="block w-full text-left px-2 py-1 text-xs hover:bg-muted"
                onClick={() => { setTradeCode(h.code); setSearchQ(h.name); setSearchHits([]); }}>{h.code} {h.name}</button>
            ))}
          </div>
        )}
      </div>
      <div className="flex gap-2 items-center">
        <input placeholder="代码" value={tradeCode} onChange={(e) => setTradeCode(e.target.value)} className="flex-1 border rounded px-2 py-1 text-sm" />
        <input type="number" value={tradeShares} onChange={(e) => setTradeShares(+e.target.value)} step={100} min={100} className="w-20 border rounded px-2 py-1 text-sm" />
        <button onClick={onBuy} className="text-white px-2 py-1 text-sm rounded" style={{ background: "var(--up)" }}>买</button>
        <button onClick={onSell} className="text-white px-2 py-1 text-sm rounded" style={{ background: "var(--down)" }}>卖</button>
      </div>
      {feePreview && tradeCode && (
        <div className="text-[10px] text-muted-foreground space-y-0.5">
          {feePreview.price_label && <p>{feePreview.price_label}{feePreview.quote_date ? ` · 行情日 ${feePreview.quote_date}` : ""}</p>}
          <p>预估：佣金 ¥{feePreview.commission.toFixed(2)} · 合计扣款 ¥{Math.abs(feePreview.cash_delta).toFixed(2)}</p>
          {feePreview.can_trade === false && feePreview.block_reason && (
            <p className="text-amber-700 dark:text-amber-500">当前不可成交：{feePreview.block_reason}</p>
          )}
        </div>
      )}
    </div>
  );
}

function PositionRow({ p, showStop, onSell }: { p: PortfolioPosition; showStop?: boolean; onSell: () => void }) {
  const belowStop = p.turtle_stop_price != null && p.price != null && p.price < p.turtle_stop_price;
  return (
    <tr className="border-b border-border">
      <td className="py-1">
        <div className="font-mono">{p.code}</div>
        {p.name ? <div className="text-[10px] text-muted-foreground truncate max-w-[88px]" title={p.name}>{p.name}</div> : null}
      </td>
      <td className="text-center font-mono tabular-nums">{p.shares}{p.t1_locked > 0 ? <span className="text-amber-600 dark:text-amber-500 ml-0.5 inline-flex items-center" title="T+1"><Lock className="h-2.5 w-2.5" />{p.t1_locked}</span> : null}</td>
      <td className="text-center font-mono tabular-nums">
        {p.price != null ? Number(p.price).toFixed(2) : "—"}
        {p.price_source === "realtime" ? (
          <div className="text-[9px] text-primary">实时</div>
        ) : p.quote_date ? (
          <div className="text-[9px] text-muted-foreground" title="库内收盘价">收盘 {p.quote_date.slice(5)}</div>
        ) : null}
      </td>
      <td className="text-center font-mono tabular-nums">{p.avg_cost?.toFixed(2)}</td>
      {showStop && (
        <td className={`text-center text-[10px] font-mono tabular-nums ${belowStop ? "text-amber-700 dark:text-amber-500 font-medium" : "text-muted-foreground"}`}>
          {p.turtle_stop_price != null ? p.turtle_stop_price.toFixed(2) : "—"}
        </td>
      )}
      <td className="text-center font-mono tabular-nums">{p.weight_pct}%</td>
      <td className={`text-center font-mono tabular-nums ${positionPnlColor(p)}`} title={p.is_friction_only ? "市价几乎未动，微亏来自买入成本" : undefined}>
        {fmtPct(positionMainPct(p), p.is_friction_only)}
        {!p.is_friction_only && Math.abs((p.pnl_pct ?? 0) - positionMainPct(p)) >= 0.05 && (
          <div className="text-[9px] text-muted-foreground">含成本 {fmtPct(p.pnl_pct)}</div>
        )}
      </td>
      <td className="text-right">{p.sellable_shares >= 100 && <button onClick={onSell} className="text-[10px] text-down underline">卖</button>}</td>
    </tr>
  );
}

function NetChart({ data }: { data: { snapshot_date: string; total_value: number }[] }) {
  const ref = useRef<HTMLCanvasElement>(null);
  useEffect(() => {
    const cv = ref.current; if (!cv || data.length < 2) return;
    const ctx = cv.getContext("2d")!;
    const W = cv.width = cv.offsetWidth * 2, H = 200;
    const vals = data.map((d) => d.total_value);
    const min = Math.min(...vals) * 0.99, max = Math.max(...vals) * 1.01;
    ctx.clearRect(0, 0, W, H);
    const primary = getComputedStyle(document.documentElement).getPropertyValue("--primary").trim() || "#6366f1";
    ctx.beginPath(); ctx.strokeStyle = primary; ctx.lineWidth = 2;
    data.forEach((d, i) => {
      const x = 20 + (i / (data.length - 1)) * (W - 40);
      const y = 20 + (1 - (d.total_value - min) / (max - min)) * (H - 40);
      i === 0 ? ctx.moveTo(x, y) : ctx.lineTo(x, y);
    });
    ctx.stroke();
  }, [data]);
  return <canvas ref={ref} className="w-full" style={{ height: 80 }} />;
}

// ========== 表现分析 tab(组合级归因 + 单票展开)==========
function PerformanceTab({ pid }: { pid: number }) {
  const [attr, setAttr] = useState<Awaited<ReturnType<typeof api.portfolioAttribution>> | null>(null);
  const [loading, setLoading] = useState(true);
  const [expanded, setExpanded] = useState<string | null>(null);
  const [perf, setPerf] = useState<Record<string, PositionPerf>>({});

  useEffect(() => {
    setLoading(true);
    api.portfolioAttribution(pid).then(setAttr).catch(() => setAttr(null)).finally(() => setLoading(false));
  }, [pid]);

  const toggle = async (code: string, held: boolean) => {
    if (expanded === code) { setExpanded(null); return; }
    setExpanded(code);
    if (held && !perf[code]) {
      try { const p = await api.portfolioPositionPerf(pid, code); setPerf((prev) => ({ ...prev, [code]: p })); }
      catch { /* ignore */ }
    }
  };

  if (loading) return <div className="space-y-2">{[...Array(6)].map((_, i) => <Skeleton key={i} className="h-8" />)}</div>;
  if (!attr || !attr.rows?.length) return <EmptyState title="暂无表现数据" hint="建仓后即可分析每只持仓的贡献与超额" />;

  const maxAbs = Math.max(...attr.rows.map((r) => Math.abs(r.contribution_pp)), 0.01);
  return (
    <div className="space-y-3">
      <div className="grid grid-cols-3 divide-x divide-border rounded-md border border-border bg-card">
        <StatTile label="总贡献" value={`${attr.total_contribution_pp >= 0 ? "+" : ""}${attr.total_contribution_pp}pp`}
          tone={attr.total_contribution_pp >= 0 ? "up" : "down"} sub={`总盈亏 ¥${attr.total_pnl.toLocaleString()}`} />
        <StatTile label="功臣" value={String(attr.winners.length)} tone="up" sub="正贡献个股" />
        <StatTile label="拖累" value={String(attr.losers.length)} tone="down" sub="负贡献个股" />
      </div>
      <div className="text-[10px] text-muted-foreground px-1">收益归因:每只持过的股(现持仓未实现 + 已平仓已实现)对初始资金的贡献。点开看单票曲线与超额。</div>
      <div className="rounded-md border border-border bg-card divide-y divide-border">
        {attr.rows.map((r) => {
          const up = r.total_pnl >= 0;
          const w = Math.abs(r.contribution_pp) / maxAbs * 100;
          const p = perf[r.code];
          return (
            <div key={r.code}>
              <button onClick={() => toggle(r.code, r.still_held)} className="w-full flex items-center gap-2 px-3 py-2 text-xs hover:bg-muted/40 text-left">
                <span className="w-14 font-mono shrink-0">{r.code}</span>
                <span className="w-16 truncate shrink-0">{r.name}</span>
                {!r.still_held && <span className="text-[9px] text-muted-foreground shrink-0">已平</span>}
                <div className="flex-1 h-2.5 min-w-[40px]">
                  <div className={`h-full rounded ${up ? "bg-up" : "bg-down"}`} style={{ width: `${w}%` }} />
                </div>
                <span className={`w-16 text-right font-mono tabular-nums shrink-0 ${up ? "text-up" : "text-down"}`}>{up ? "+" : ""}{r.contribution_pp}pp</span>
                <span className="w-20 text-right font-mono tabular-nums text-muted-foreground shrink-0 hidden sm:block">¥{r.total_pnl.toLocaleString()}</span>
              </button>
              {expanded === r.code && (
                <div className="px-3 pb-3 pt-1 bg-muted/20">
                  {!r.still_held
                    ? <p className="text-[11px] text-muted-foreground">已平仓 · 已实现盈亏 ¥{r.realized.toLocaleString()}(持有期曲线仅现持仓可看)</p>
                    : p ? (p.error ? <p className="text-[11px] text-muted-foreground">{p.error}</p> : <PositionPerfDetail p={p} />)
                      : <div className="text-[11px] text-muted-foreground py-2">加载中…</div>}
                </div>
              )}
            </div>
          );
        })}
      </div>
    </div>
  );
}

function PositionPerfDetail({ p }: { p: PositionPerf }) {
  const cell = (label: string, val: string, tone?: "up" | "down") => (
    <div>
      <div className="text-[9px] text-muted-foreground uppercase tracking-wide">{label}</div>
      <div className={`text-xs font-mono tabular-nums ${tone === "up" ? "text-up" : tone === "down" ? "text-down" : ""}`}>{val}</div>
    </div>
  );
  const sr = p.stock_return_pct;
  return (
    <div className="space-y-2">
      <div className="grid grid-cols-4 gap-2">
        {cell("持有天数", `${p.hold_days}天`)}
        {cell("本票收益", `${sr >= 0 ? "+" : ""}${sr}%`, sr >= 0 ? "up" : "down")}
        {cell("区间最高", `+${p.peak_pct}%`, "up")}
        {cell("从峰值回撤", `${p.drawdown_from_peak_pct}%`, p.drawdown_from_peak_pct < 0 ? "down" : undefined)}
        {p.excess_vs_csi300_pp != null && cell("超沪深300", `${p.excess_vs_csi300_pp >= 0 ? "+" : ""}${p.excess_vs_csi300_pp}pp`, p.excess_vs_csi300_pp >= 0 ? "up" : "down")}
        {p.excess_vs_pool_pp != null && cell("超自选池", `${p.excess_vs_pool_pp >= 0 ? "+" : ""}${p.excess_vs_pool_pp}pp`, p.excess_vs_pool_pp >= 0 ? "up" : "down")}
        {p.csi300_return_pct != null && cell("同期沪深300", `${p.csi300_return_pct >= 0 ? "+" : ""}${p.csi300_return_pct}%`)}
        {p.pool_return_pct != null && cell("同期自选池", `${p.pool_return_pct >= 0 ? "+" : ""}${p.pool_return_pct}%`)}
      </div>
      {p.curve.length > 1 && (
        <BetaDualChart title={`${p.name} vs 沪深300(买入=100)`}
          strategy={p.curve.map((c) => ({ date: c.date, value: c.v / 100 }))}
          benchmark={p.csi300_curve.map((c) => ({ date: c.date, value: c.v / 100 }))} />
      )}
    </div>
  );
}
