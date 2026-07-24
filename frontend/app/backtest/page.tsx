"use client";

import { useState, useEffect, Suspense } from "react";
import { useSearchParams } from "next/navigation";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { BetaShell, BetaTabs, BETA_TABS } from "@/components/BetaShell";
import { BetaDualChart } from "@/components/BetaDualChart";
import { api } from "@/lib/api";
import { useToast } from "@/lib/useToast";
import { BACKTEST_PRESETS, type BacktestParams, type BacktestResult } from "@/types/beta";
import { FALLBACK_STRATEGIES } from "@/lib/strategies";
import {
  BarChart, Bar, XAxis, YAxis, Tooltip, Legend, ResponsiveContainer, ReferenceLine, Cell,
} from "recharts";
import { BacktestProgress } from "@/components/BacktestProgress";
import { MonthlyHeatmap } from "@/components/MonthlyHeatmap";
import { HoldingsPie } from "@/components/HoldingsPie";
import { Filter } from "lucide-react";

type Tab = "single" | "rolling" | "scan" | "walkforward" | "history";

const EXTRA_BACKTEST = [
  { v: "ml_pred", l: "ML 预测（实验）" },
];

export default function BacktestPage() {
  return (
    <Suspense fallback={<div className="p-8 animate-pulse bg-muted rounded h-64" />}>
      <BacktestInner />
    </Suspense>
  );
}

function BacktestInner() {
  const sp = useSearchParams();
  const toast = useToast();
  const [tab, setTab] = useState<Tab>("single");
  const [loading, setLoading] = useState(false);
  const [result, setResult] = useState<BacktestResult | null>(null);
  const [rolling, setRolling] = useState<Record<string, unknown> | null>(null);
  const [scan, setScan] = useState<Record<string, unknown> | null>(null);
  const [walk, setWalk] = useState<Record<string, unknown> | null>(null);
  const [compareResult, setCompareResult] = useState<BacktestResult | null>(null);
  const [comparing, setComparing] = useState(false);
  const [history, setHistory] = useState<{ runs: Record<string, unknown>[] } | null>(null);
  const [selectedRuns, setSelectedRuns] = useState<Set<string>>(new Set());
  const [combinations, setCombinations] = useState<Record<string, unknown>[]>([]);
  const [strategies, setStrategies] = useState<{ v: string; l: string }[]>([
    ...FALLBACK_STRATEGIES.map((s) => ({ v: s.id, l: s.label })),
    ...EXTRA_BACKTEST,
  ]);
  const [portfolioId, setPortfolioId] = useState("");
  const [params, setParams] = useState<BacktestParams>({
    days: 90, top_n: 5, lookback: 20, pos_style: "equal", strategy: sp.get("strategy") || "composite",
    min_score: sp.get("min_score") ? +sp.get("min_score")! : 50,
    rebalance: "weekly", apply_costs: true, apply_t1: true, engine: "python",
    benchmark_mode: "equal", sector_window: 5,
    combination_id: sp.get("combination_id") ? +sp.get("combination_id")! : undefined,
  });
  const fromScreener = !!sp.get("from_screener");

  useEffect(() => {
    const s = sp.get("strategy");
    const cid = sp.get("combination_id");
    const ms = sp.get("min_score");
    if (s || cid || ms) {
      setParams((p) => ({
        ...p,
        ...(s ? { strategy: s } : {}),
        ...(cid ? { combination_id: +cid } : {}),
        ...(ms ? { min_score: +ms } : {}),
      }));
    }
  }, [sp]);

  useEffect(() => {
    api.factorCombinationsList().then((d) => setCombinations(d.combinations || [])).catch(() => {});
    api.strategyList(false).then((r) => {
      if (r.strategies?.length) {
        setStrategies([
          ...r.strategies.map((s) => ({ v: s.id, l: s.label })),
          ...EXTRA_BACKTEST,
        ]);
      }
    }).catch(() => {});
  }, []);

  const loadHistory = () => api.backtestHistory(10).then(setHistory).catch(() => {});

  useEffect(() => { if (tab === "history") loadHistory(); }, [tab]);

  const run = async (save = false) => {
    setLoading(true);
    setResult(null);
    try {
      const data = await api.backtestRun({ ...params, engine: "python", save });
      if (data.error) throw new Error(data.error);
      setResult(data);
      if (save) { toast.success("回测已保存"); loadHistory(); }
    } catch (e) {
      const msg = e instanceof Error ? e.message : "回测失败";
      setResult({ error: msg });
      toast.error(msg);
    }
    setLoading(false);
  };

  const runCompare = async () => {
    setComparing(true);
    setCompareResult(null);
    try {
      const data = await api.backtestRun({ ...params, engine: "python", save: false });
      if (data.error) throw new Error(data.error);
      setCompareResult(data);
    } catch (e) {
      toast.error(e instanceof Error ? e.message : "对比回测失败");
    }
    setComparing(false);
  };

  const runRolling = async () => {
    setLoading(true);
    try {
      const data = await api.backtestRolling({
        window: params.days, step: 20, top_n: params.top_n, min_score: params.min_score, strategy: params.strategy,
      });
      if (data.error) throw new Error(String(data.error));
      setRolling(data);
    } catch (e) { toast.error(e instanceof Error ? e.message : "失败"); }
    setLoading(false);
  };

  const runScan = async () => {
    setLoading(true);
    try {
      setScan(await api.backtestScan(params.days, params.strategy));
    } catch (e) { toast.error(e instanceof Error ? e.message : "失败"); }
    setLoading(false);
  };

  const runWalk = async () => {
    setLoading(true);
    try {
      const data = await api.backtestWalkForward(60, 20, params.strategy);
      if (data.error) throw new Error(String(data.error));
      setWalk(data);
    } catch (e) { toast.error(e instanceof Error ? e.message : "失败"); }
    setLoading(false);
  };

  const importPortfolio = async () => {
    const pid = +portfolioId;
    if (!pid) { toast.error("请输入组合 ID"); return; }
    try {
      const r = await api.portfolioBuildTop(pid, {
        top_n: params.top_n || 5,
        min_score: params.min_score || 50,
        strategy: params.strategy,
      });
      if (r.error) throw new Error(String(r.error));
      toast.success(`已导入组合 #${pid}，建仓 ${r.count} 只`);
    } catch (e) { toast.error(e instanceof Error ? e.message : "导入失败"); }
  };

  return (
    <BetaShell title="回测系统" subtitle="默认综合分 Top N · 含成本与 T+1">
      <BetaTabs active="backtest" tabs={BETA_TABS} />

      <div className="flex gap-2 flex-wrap">
        {(["single", "rolling", "scan", "walkforward", "history"] as Tab[]).map((t) => (
          <button key={t} onClick={() => setTab(t)}
            className={`px-3 py-1 text-xs rounded ${tab === t ? "bg-primary text-primary-foreground" : "bg-muted"}`}>
            {{ single: "策略回测", rolling: "滚动窗口", scan: "参数扫描", walkforward: "Walk-forward", history: "历史" }[t]}
          </button>
        ))}
      </div>

      {fromScreener && (
        <div className="rounded-md border border-primary/20 bg-primary/5 px-4 py-2 text-sm text-primary flex items-center gap-2">
          <Filter className="h-3.5 w-3.5 shrink-0" />
          <span>已从选股筛选导入参数：最低分 <strong className="font-mono">{params.min_score}</strong> · 策略 <strong>{params.strategy}</strong></span>
        </div>
      )}

      <Card>
        <CardHeader><CardTitle className="text-base">参数</CardTitle></CardHeader>
        <CardContent className="space-y-3">
          <div className="flex flex-wrap gap-2">
            {BACKTEST_PRESETS.map((p) => (
              <button key={p.id} onClick={() => setParams({ ...params, ...p.params })}
                className="text-[10px] px-2 py-1 rounded border hover:bg-muted">{p.label}</button>
            ))}
          </div>
          <div className="flex flex-wrap gap-3 items-end">
            <label className="text-xs">策略
              <select className="block mt-1 border rounded px-2 py-1 text-sm" value={params.strategy}
                onChange={(e) => setParams({ ...params, strategy: e.target.value })}>
                {strategies.map((s) => <option key={s.v} value={s.v}>{s.l}</option>)}
              </select>
            </label>
            {params.strategy === "factor_combination" && (
              <label className="text-xs">合成方案
                <select className="block mt-1 border rounded px-2 py-1 text-sm min-w-[140px]"
                  value={params.combination_id ?? ""}
                  onChange={(e) => setParams({ ...params, combination_id: e.target.value ? +e.target.value : undefined })}>
                  <option value="">选择方案</option>
                  {combinations.map((c) => (
                    <option key={String(c.id)} value={String(c.id)}>
                      #{String(c.id)} {String(c.name)} ({String(c.output_factor_id || "-")})
                    </option>
                  ))}
                </select>
              </label>
            )}
            {[["days", "天数", 90], ["top_n", "Top N", 5], ["min_score", "最低分", 50]].map(([k, l, d]) => (
              <label key={k} className="text-xs">{l}
                <input type="number" className="block w-20 mt-1 border rounded px-2 py-1 text-sm"
                  value={(params as Record<string, number>)[k] ?? d}
                  onChange={(e) => setParams({ ...params, [k]: +e.target.value })} />
              </label>
            ))}
            <label className="text-xs">基准
              <select className="block mt-1 border rounded px-2 py-1 text-sm" value={params.benchmark_mode || "equal"}
                onChange={(e) => setParams({ ...params, benchmark_mode: e.target.value as "equal" | "csi300" })}>
                <option value="equal">等权池</option>
                <option value="csi300">沪深300</option>
              </select>
            </label>
            {(params.strategy === "turtle" || params.strategy === "momentum") && (
              <label className="text-xs">lookback
                <input type="number" className="block w-20 mt-1 border rounded px-2 py-1 text-sm"
                  value={params.lookback ?? 20}
                  onChange={(e) => setParams({ ...params, lookback: +e.target.value })} />
              </label>
            )}
            {params.strategy === "sector_rotation" && (
              <label className="text-xs">行业窗口
                <input type="number" className="block w-16 mt-1 border rounded px-2 py-1 text-sm"
                  value={params.sector_window ?? 5}
                  onChange={(e) => setParams({ ...params, sector_window: +e.target.value })} />
              </label>
            )}
            <label className="text-xs">调仓
              <select className="block mt-1 border rounded px-2 py-1 text-sm" value={params.rebalance}
                onChange={(e) => setParams({ ...params, rebalance: e.target.value })}>
                <option value="daily">日</option><option value="weekly">周</option><option value="monthly">月</option>
              </select>
            </label>
            <span className="text-xs self-end pb-2 px-2 py-1 rounded bg-muted text-muted-foreground">
              引擎 Python（默认）
            </span>
            <button disabled={loading} onClick={() => tab === "single" ? run(false) : tab === "rolling" ? runRolling() : tab === "scan" ? runScan() : tab === "walkforward" ? runWalk() : loadHistory()}
              className="rounded bg-primary text-primary-foreground px-4 py-2 text-sm disabled:opacity-50">
              {loading ? "运行中…" : "运行"}
            </button>
            {tab === "single" && (
              <button disabled={loading} onClick={() => run(true)} className="rounded border px-4 py-2 text-sm">保存结果</button>
            )}
            {tab === "single" && result && !result.error && (
              <button disabled={comparing} onClick={runCompare}
                className="rounded border border-amber-500/50 text-amber-700 dark:text-amber-500 px-4 py-2 text-sm disabled:opacity-50">
                {comparing ? "对比中…" : "对比回测 B"}
              </button>
            )}
          </div>
          <p className="text-[10px] text-muted-foreground leading-relaxed">
            回测统一使用 Python 引擎（全策略、CSI300 基准、交易明细）。
            Rust 回测依赖非开源 qars3，当前环境未安装，已在系统状态中标记为禁用。
          </p>
        </CardContent>
      </Card>

      <BacktestProgress days={params.days ?? 90} active={loading && tab === "single"} />

      {tab === "single" && result?.error && <p className="text-destructive text-sm">{result.error}</p>}
      {tab === "single" && result && !result.error && (
        <>
          {result.meta?.warnings?.length ? (
            <div className="text-xs text-amber-700 dark:text-amber-500 bg-amber-500/10 rounded p-2">{result.meta.warnings.join(" · ")}</div>
          ) : null}
          {(result.engine || result.rust_fallback) && (
            <p className="text-xs text-muted-foreground">
              引擎: {String(result.engine ?? (typeof result.params?.engine === "string" ? result.params.engine : "python"))}
              {result.rust_fallback ? "（Rust 不可用，已 fallback Python）" : ""}
              {result.elapsed_ms != null ? ` · ${result.elapsed_ms}ms` : ""}
            </p>
          )}
          <div className="grid grid-cols-3 md:grid-cols-6 gap-2">
            {[
              ["总收益", `${result.total_return_pct}%`],
              ["夏普", String(result.sharpe)],
              ["回撤", `${result.max_drawdown_pct}%`],
              ["基准", result.benchmark ? `${result.benchmark.return_pct}%` : "-"],
              ["超额", result.excess_return_pct != null ? `${result.excess_return_pct}%` : "-"],
              ["胜率", `${result.win_rate_pct}%`],
            ].map(([l, v]) => (
              <Card key={l}><CardContent className="p-2 text-center"><div className="font-bold font-mono tabular-nums">{v}</div><div className="text-[10px] text-muted-foreground">{l}</div></CardContent></Card>
            ))}
          </div>
          {compareResult && !compareResult.error && (
            <div className="rounded-md border border-amber-500/30 bg-amber-500/10 p-3 text-xs space-y-1">
              <p className="font-medium text-amber-800 dark:text-amber-400">策略 B 对比结果</p>
              <div className="flex flex-wrap gap-4 text-amber-700 dark:text-amber-500">
                {[
                  ["总收益", `${compareResult.total_return_pct}%`],
                  ["夏普", String(compareResult.sharpe)],
                  ["回撤", `${compareResult.max_drawdown_pct}%`],
                  ["胜率", `${compareResult.win_rate_pct}%`],
                ].map(([l, v]) => (
                  <span key={l}><strong className="font-mono tabular-nums">{v}</strong> {l}</span>
                ))}
              </div>
            </div>
          )}
          {result.daily_values && (
            <BetaDualChart
              strategy={result.daily_values}
              benchmark={result.benchmark_curve}
              compare={compareResult?.daily_values}
              compareLabel={`B: ${compareResult?.total_return_pct ?? ""}%`}
            />
          )}
          {result.current_holdings && result.current_holdings.length > 0 && (
            <Card>
              <CardContent className="p-4">
                <HoldingsPie
                  holdings={result.current_holdings}
                  title="回测末期持仓分布"
                />
              </CardContent>
            </Card>
          )}
          {result.daily_values && result.daily_values.length > 20 && (
            <Card>
              <CardContent className="p-4">
                <MonthlyHeatmap daily={result.daily_values} />
              </CardContent>
            </Card>
          )}
          <Card>
            <CardHeader><CardTitle className="text-sm">导入模拟盘</CardTitle></CardHeader>
            <CardContent className="flex gap-2 items-center">
              <input placeholder="组合 ID" value={portfolioId} onChange={(e) => setPortfolioId(e.target.value)}
                className="border rounded px-2 py-1 text-sm w-24" />
              <button onClick={importPortfolio} className="text-sm bg-primary text-primary-foreground px-3 py-1 rounded hover:bg-primary/90 transition-colors">按 Top N 建仓</button>
            </CardContent>
          </Card>
        </>
      )}

      {tab === "rolling" && rolling && !rolling.error && (
        <Card><CardContent className="p-4 text-sm space-y-2">
          <p>窗口 {String(rolling.windows)} · 胜率 {String(rolling.win_rate)}% · 均收益 {String(rolling.avg_return)}%</p>
          <table className="w-full text-xs"><tbody>
            {(rolling.window_results as Record<string, unknown>[] || []).map((w, i) => (
              <tr key={i} className="border-b border-border"><td className="py-1">{String(w.period)}</td><td className="font-mono tabular-nums">{String(w.return_pct)}%</td></tr>
            ))}
          </tbody></table>
        </CardContent></Card>
      )}

      {tab === "scan" && scan && (
        <Card><CardContent className="p-4 text-xs">
          <table className="w-full"><thead><tr><th>TopN</th><th>最低分</th><th>收益</th><th>Sharpe</th></tr></thead>
            <tbody>{(scan.results as Record<string, unknown>[] || []).map((r, i) => (
              <tr key={i} className="border-b border-border font-mono tabular-nums"><td>{String(r.top_n)}</td><td>{String(r.min_score)}</td><td>{String(r.total_return_pct)}%</td><td>{String(r.sharpe)}</td></tr>
            ))}</tbody></table>
        </CardContent></Card>
      )}

      {tab === "walkforward" && walk && !walk.error && (
        <Card><CardContent className="p-4 text-sm">
          <p>样本外收益: {String(walk.test_return_pct)}% · Sharpe {String(walk.test_sharpe)}</p>
          <p className="text-xs text-muted-foreground">最优参数 Top{String((walk.best_params as Record<string, unknown>)?.top_n)} / 最低分 {(walk.best_params as Record<string, unknown>)?.min_score as number}</p>
        </CardContent></Card>
      )}

      {tab === "history" && history && (
        <div className="space-y-4">
          <Card><CardContent className="p-4 text-xs space-y-1">
            {history.runs.map((r) => {
              const rid = String(r.id);
              const strategy = (r.params as Record<string, unknown>)?.strategy as string;
              const checked = selectedRuns.has(rid);
              return (
                <div key={rid} className="border-b border-border py-1 flex items-center gap-2">
                  <input type="checkbox" checked={checked} onChange={() => {
                    setSelectedRuns((prev) => {
                      const next = new Set(prev);
                      if (next.has(rid)) next.delete(rid); else next.add(rid);
                      return next;
                    });
                  }} />
                  <span className="flex-1 font-mono text-[11px]">#{rid} {String(r.created_at).slice(0, 10)} · {strategy}</span>
                  <span className={`font-mono tabular-nums ${Number(r.total_return_pct) >= 0 ? "text-up" : "text-down"}`}>
                    {String(r.total_return_pct)}% / Sharpe {Number(r.sharpe).toFixed(2)}
                  </span>
                </div>
              );
            })}
          </CardContent></Card>
          {selectedRuns.size > 0 && (() => {
            const chartData = history.runs
              .filter((r) => selectedRuns.has(String(r.id)))
              .map((r) => ({
                name: `#${r.id} ${(r.params as Record<string, unknown>)?.strategy || ""}`,
                total_return_pct: Number(r.total_return_pct),
                sharpe: Number(r.sharpe),
              }));
            return (
              <Card>
                <CardHeader><CardTitle className="text-sm">对比：选中 {selectedRuns.size} 个回测</CardTitle></CardHeader>
                <CardContent>
                  <ResponsiveContainer width="100%" height={220}>
                    <BarChart data={chartData} margin={{ top: 4, right: 8, left: -16, bottom: 40 }}>
                      <XAxis dataKey="name" tick={{ fontSize: 10 }} angle={-20} textAnchor="end" interval={0} />
                      <YAxis tick={{ fontSize: 10 }} />
                      <Tooltip formatter={((v: number, n: string) => [n === "sharpe" ? v.toFixed(2) : `${v}%`, n === "sharpe" ? "Sharpe" : "总收益"]) as any} />
                      <Legend wrapperStyle={{ fontSize: 10 }} />
                      <ReferenceLine y={0} stroke="var(--border)" />
                      <Bar dataKey="total_return_pct" name="总收益%" radius={[3,3,0,0]}>
                        {chartData.map((d, i) => (
                          <Cell key={i} fill={d.total_return_pct >= 0 ? "var(--up)" : "var(--down)"} />
                        ))}
                      </Bar>
                      <Bar dataKey="sharpe" name="Sharpe" fill="var(--primary)" radius={[3,3,0,0]} />
                    </BarChart>
                  </ResponsiveContainer>
                </CardContent>
              </Card>
            );
          })()}
        </div>
      )}
    </BetaShell>
  );
}
