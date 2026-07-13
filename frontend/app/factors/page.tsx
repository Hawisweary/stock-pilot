"use client";

import { useState, useEffect, Suspense } from "react";
import { useSearchParams, useRouter } from "next/navigation";
import Link from "next/link";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { BetaShell, BetaTabs, BETA_TABS } from "@/components/BetaShell";
import { api } from "@/lib/api";
import { useToast } from "@/lib/useToast";
import type { FactorIcRow, IcHeatmap } from "@/types/beta";
import {
  LineChart, Line, XAxis, YAxis, Tooltip, ReferenceLine, ResponsiveContainer, CartesianGrid,
  ScatterChart, Scatter, ZAxis,
} from "recharts";

import { V5_IC_LABELS, V5_IC_TO_STRATEGY } from "@/lib/v5Strategies";
import { factorDescription } from "@/lib/factorDescriptions";

const IC_LABELS = V5_IC_LABELS;
const SCORE_TO_STRATEGY = V5_IC_TO_STRATEGY;

function FactorsLabInner() {
  const toast = useToast();
  const router = useRouter();
  const sp = useSearchParams();
  const tab = sp.get("tab") || "library";

  const [factors, setFactors] = useState<Record<string, unknown>[]>([]);
  const [selected, setSelected] = useState<string | null>(null);
  const [analysis, setAnalysis] = useState<Record<string, unknown> | null>(null);
  const [values, setValues] = useState<Record<string, unknown>[]>([]);
  const [computing, setComputing] = useState(false);
  const [forwardDays, setForwardDays] = useState(20);
  const [icData, setIcData] = useState<{ factors: Record<string, FactorIcRow> } | null>(null);
  const [heatmap, setHeatmap] = useState<IcHeatmap | null>(null);
  const [corr, setCorr] = useState<{ matrix: Record<string, Record<string, number>>; factors: string[] } | null>(null);
  const [customName, setCustomName] = useState("");
  const [customFormula, setCustomFormula] = useState("0.5*F002 + 0.3*F008 + 0.2*F009");
  const [customList, setCustomList] = useState<{ factor_id: string; name: string; formula: string }[]>([]);
  const [decay, setDecay] = useState<Record<string, unknown> | null>(null);
  const [icReview, setIcReview] = useState<Record<string, unknown> | null>(null);
  const [merging, setMerging] = useState(false);
  const [mergeResult, setMergeResult] = useState<Record<string, unknown> | null>(null);
  const [combinations, setCombinations] = useState<Record<string, unknown>[]>([]);
  const [comboName, setComboName] = useState("my_combo");
  const [comboMethod, setComboMethod] = useState("equal");
  const [comboFactors, setComboFactors] = useState("F010,F011,F012,F013");
  const [savingCombo, setSavingCombo] = useState(false);
  const [exprValid, setExprValid] = useState<{ valid?: boolean; kind?: string; error?: string } | null>(null);
  const [gpRunning, setGpRunning] = useState(false);
  const [gpWinners, setGpWinners] = useState<Record<string, unknown>[]>([]);
  const [mlPreds, setMlPreds] = useState<Record<string, unknown>[]>([]);
  const [mlEnabled, setMlEnabled] = useState(false);
  const [mlTraining, setMlTraining] = useState(false);
  const [loadError, setLoadError] = useState(false);

  const loadFactors = () => {
    setLoadError(false);
    api.factorsList()
      .then((d) => setFactors(d.factors || []))
      .catch(() => setLoadError(true));
    api.customFactorsList().then((d) => setCustomList(d.factors || [])).catch(() => {});
  };

  useEffect(() => { loadFactors(); }, []);

  useEffect(() => {
    if (tab === "ic") {
      api.factorIc(60, 20).then(setIcData).catch(() => {});
      api.factorIcHeatmap(60).then(setHeatmap).catch(() => {});
    }
    if (tab === "correlation") api.factorCorrelation().then(setCorr).catch(() => {});
    if (tab === "decay" && selected) api.factorDecay(selected, forwardDays).then(setDecay).catch(() => setDecay(null));
    if (tab === "merge") {
      api.icReview().then(setIcReview).catch(() => setIcReview(null));
      api.factorCombinationsList().then((d) => setCombinations(d.combinations || [])).catch(() => {});
    }
    if (tab === "ml") {
      api.qlibPredictions(30).then((r) => {
        setMlEnabled(!!r.enabled);
        setMlPreds(r.predictions || []);
      }).catch(() => {});
    }
    if (tab === "gp") {
      api.factorGpRuns(10).then((r) => {
        const last = r.runs?.[0];
        if (last?.candidates) setGpWinners(last.candidates as Record<string, unknown>[]);
      }).catch(() => {});
    }
  }, [tab, selected, forwardDays]);

  const computeAll = async () => {
    setComputing(true);
    try {
      const d = await api.factorsCompute("full");
      toast.success(`因子计算完成: ${d.factors_computed} 条${d.backfill ? "（含历史）" : ""}`);
      api.factorsList().then((r) => setFactors(r.factors || []));
    } catch (e) { toast.error(e instanceof Error ? e.message : "计算失败"); }
    setComputing(false);
  };

  const computeIncremental = async () => {
    setComputing(true);
    try {
      const d = await api.factorsCompute("incremental");
      toast.success(`增量更新: ${d.cells_written ?? 0} 条 (${d.stocks_touched ?? 0} 股)`);
      api.factorsList().then((r) => setFactors(r.factors || []));
    } catch (e) { toast.error(e instanceof Error ? e.message : "增量失败"); }
    setComputing(false);
  };

  const neutralizeSelected = async () => {
    if (!selected) return;
    try {
      const d = await api.factorNeutralize(selected);
      if (d.error) throw new Error(d.error);
      toast.success(`中性化完成 → ${d.output_factor_id}`);
      api.factorsList().then((r) => setFactors(r.factors || []));
    } catch (e) { toast.error(e instanceof Error ? e.message : "中性化失败"); }
  };

  const selectFactor = async (fid: string) => {
    setSelected(fid);
    try {
      const [a, v] = await Promise.all([
        api.factorAnalysis(fid, forwardDays),
        api.factorValues(fid),
      ]);
      setAnalysis(a);
      setValues(v.values || []);
    } catch (e) {
      setAnalysis(null);
      setValues([]);
      toast.error(`${fid}: ${e instanceof Error ? e.message : "加载失败"}`);
    }
  };

  useEffect(() => { if (selected) selectFactor(selected); }, [forwardDays]);

  const createCustom = async () => {
    try {
      const isTs = customFormula.includes("$") || /Mean|Std|Ref|Delta|Rank/.test(customFormula);
      const r = isTs
        ? await api.computeFactorExpression(customName || "expr", customFormula)
        : await api.createCustomFactor(customName, customFormula);
      if (r.error) throw new Error(r.error);
      toast.success(`已创建 ${r.factor_id}`);
      api.customFactorsList().then((d) => setCustomList(d.factors || []));
      api.factorsList().then((d) => setFactors(d.factors || []));
    } catch (e) { toast.error(e instanceof Error ? e.message : "创建失败"); }
  };

  const validateExpr = async () => {
    const r = await api.validateFactorExpression(customFormula);
    setExprValid(r);
    if (r.valid) toast.success(`公式有效 (${r.kind})`);
    else toast.error(r.error || "无效");
  };

  const runGp = async () => {
    setGpRunning(true);
    try {
      const r = await api.factorGpRun({ population: 8, generations: 4 });
      if (r.error) throw new Error(r.error);
      setGpWinners((r.winners as Record<string, unknown>[]) || []);
      toast.success(`GP 完成: ${r.winners?.length ?? 0} 候选`);
      api.factorsList().then((d) => setFactors(d.factors || []));
    } catch (e) { toast.error(e instanceof Error ? e.message : "GP 失败"); }
    setGpRunning(false);
  };

  const trainMl = async () => {
    setMlTraining(true);
    try {
      const r = await api.qlibTrain();
      toast.success(`训练已提交: ${r.job_id || r.status}`);
      setTimeout(() => api.qlibPredictions(30).then((x) => setMlPreds(x.predictions || [])), 3000);
    } catch (e) { toast.error(e instanceof Error ? e.message : "训练失败"); }
    setMlTraining(false);
  };

  const goBacktest = (strategy: string) => {
    router.push(`/backtest?strategy=${strategy}`);
  };

  const runPresetMerge = async () => {
    setMerging(true);
    try {
      const r = await api.factorMergePreset();
      if (r.error) throw new Error(String(r.error));
      setMergeResult(r);
      toast.success(`合成完成 ${r.success_count}/${r.presets_run}`);
      api.factorsList().then((d) => setFactors(d.factors || []));
      api.icReview().then(setIcReview).catch(() => {});
      api.factorCombinationsList().then((d) => setCombinations(d.combinations || [])).catch(() => {});
    } catch (e) { toast.error(e instanceof Error ? e.message : "合成失败"); }
    setMerging(false);
  };

  const saveCombination = async () => {
    setSavingCombo(true);
    try {
      const factor_ids = comboFactors.split(",").map((s) => s.trim()).filter(Boolean);
      const r = await api.createFactorCombination({
        name: comboName,
        factor_ids,
        weight_method: comboMethod,
        materialize: true,
      });
      if (r.error) throw new Error(String(r.error));
      toast.success(`方案 #${r.id} → ${r.output_factor_id || (r.materialize as Record<string, unknown>)?.output_factor_id}`);
      api.factorCombinationsList().then((d) => setCombinations(d.combinations || []));
      api.factorsList().then((d) => setFactors(d.factors || []));
    } catch (e) { toast.error(e instanceof Error ? e.message : "保存失败"); }
    setSavingCombo(false);
  };

  const setTab = (t: string) => router.push(`/factors?tab=${t}`);

  const factor = factors.find((f) => f.factor_id === selected);

  return (
    <BetaShell title="因子实验室" subtitle="因子库 · IC 分析 · 自定义因子 · 相关矩阵">
      <BetaTabs active="factors" tabs={BETA_TABS} />

      <div className="flex gap-2 flex-wrap">
        {[
          ["library", "因子库"],
          ["ic", "IC 分析"],
          ["custom", "表达式"],
          ["gp", "因子挖掘"],
          ["ml", "ML/Qlib"],
          ["correlation", "相关矩阵"],
          ["decay", "IC 衰减"],
          ["merge", "因子合成"],
        ].map(([id, label]) => (
          <button key={id} onClick={() => setTab(id)}
            className={`px-3 py-1 text-xs rounded ${tab === id ? "bg-primary text-primary-foreground" : "bg-muted"}`}>
            {label}
          </button>
        ))}
        <button onClick={computeIncremental} disabled={computing}
          className="text-xs bg-muted px-3 py-1 rounded disabled:opacity-50">
          {computing ? "…" : "⚡ 增量"}
        </button>
        <button onClick={computeAll} disabled={computing}
          className="text-xs bg-primary text-primary-foreground px-3 py-1 rounded disabled:opacity-50">
          {computing ? "计算中…" : "⟳ 全量计算"}
        </button>
      </div>

      {tab === "library" && (
        <>
          {loadError && (
            <div className="rounded border border-amber-300 bg-amber-50 dark:bg-amber-950/30 px-3 py-2 text-xs flex items-center justify-between">
              <span>因子列表加载失败（后端可能正在重启）</span>
              <button onClick={loadFactors} className="border rounded px-2 py-0.5 hover:bg-muted">重试</button>
            </div>
          )}
          {!loadError && factors.length === 0 && (
            <p className="text-xs text-muted-foreground py-2">加载中…</p>
          )}
          <div className="grid grid-cols-2 md:grid-cols-5 gap-2">
            {factors.map((f) => (
              <button key={f.factor_id as string} onClick={() => selectFactor(f.factor_id as string)}
                title={factorDescription(f)}
                className={`text-left p-2 rounded border text-xs ${selected === f.factor_id ? "border-primary ring-1" : ""}`}>
                <div className="font-mono text-muted-foreground">{f.factor_id as string}</div>
                <div className="font-bold">{f.name as string}</div>
              </button>
            ))}
          </div>
          {factor && (
            <div className="rounded-md bg-muted/40 px-3 py-2 text-xs text-muted-foreground leading-relaxed">
              <span className="font-mono text-foreground">{factor.factor_id as string}</span>
              <span className="font-medium text-foreground ml-1">{factor.name as string}</span>
              <span className="ml-1">({factor.category as string})</span> — {factorDescription(factor)}
            </div>
          )}
          {analysis && factor && (
            <div className="grid md:grid-cols-2 gap-4">
              <Card>
                <CardHeader className="flex flex-row justify-between items-center">
                  <CardTitle className="text-sm">{factor.name as string} IC</CardTitle>
                  <div className="flex gap-2">
                    <button type="button" onClick={neutralizeSelected}
                      className="text-[10px] text-muted-foreground hover:text-primary">中性化</button>
                    <Link href={`/backtest?strategy=${SCORE_TO_STRATEGY[factor.name as string] || "composite"}`}
                      className="text-[10px] text-primary">→ 回测</Link>
                  </div>
                </CardHeader>
                <CardContent className="text-xs space-y-2">
                  <label>未来收益
                    <select value={forwardDays} onChange={(e) => setForwardDays(+e.target.value)} className="ml-1 border rounded">
                      {[5, 10, 20, 60].map((n) => <option key={n} value={n}>{n}日</option>)}
                    </select>
                  </label>
                  <p>
                    Mean IC {String(analysis.mean_ic)} · IR {String(analysis.ir)}
                    {analysis.survivorship_adjusted && <span className="text-muted-foreground ml-1">· 幸存者校正</span>}
                  </p>
                  {analysis.ic_significance && typeof analysis.ic_significance === "object" && (
                    <p>
                      p={String((analysis.ic_significance as Record<string, unknown>).p_value ?? "-")}
                      {(analysis.ic_significance as Record<string, unknown>).significance
                        ? ` ${(analysis.ic_significance as Record<string, unknown>).significance}`
                        : ""}
                      {(analysis.ic_significance as Record<string, unknown>).fdr_q != null && (
                        <span className="text-muted-foreground"> · FDR q={String((analysis.ic_significance as Record<string, unknown>).fdr_q)}</span>
                      )}
                    </p>
                  )}
                  {Array.isArray(analysis.ic) && (analysis.ic as { date: string; ic: number }[]).length > 0 && (
                    <div className="mt-2">
                      <p className="text-muted-foreground mb-1">IC 序列（近 {(analysis.ic as unknown[]).length} 期）</p>
                      <div className="flex items-end gap-px h-16 border rounded p-1 bg-muted/20">
                        {(analysis.ic as { ic: number }[]).slice(-30).map((pt, i) => (
                          <div key={i} title={String(pt.ic)}
                            className={`flex-1 min-w-[2px] ${pt.ic >= 0 ? "bg-red-400" : "bg-green-500"}`}
                            style={{ height: `${Math.min(100, Math.abs(pt.ic) * 200)}%` }} />
                        ))}
                      </div>
                    </div>
                  )}
                  {analysis.layer && typeof analysis.layer === "object" && (
                    <p className="mt-1">分层 Top {String((analysis.layer as Record<string, unknown>).top_avg)}% / Bottom {String((analysis.layer as Record<string, unknown>).bottom_avg)}% · 差 {(analysis.layer as Record<string, unknown>).spread as number}%</p>
                  )}
                  {analysis.turnover && typeof analysis.turnover === "object" && (analysis.turnover as Record<string, unknown>).daily_avg_turnover != null && (
                    <p>Top20% 日均换手 {((Number((analysis.turnover as Record<string, unknown>).daily_avg_turnover) * 100).toFixed(1))}%</p>
                  )}
                </CardContent>
              </Card>
              <Card>
                <CardHeader><CardTitle className="text-sm">Top 20 截面值</CardTitle></CardHeader>
                <CardContent className="max-h-48 overflow-auto text-xs">
                  {values.slice(0, 20).map((v, i) => (
                    <div key={i} className="flex justify-between border-b py-0.5">
                      <span>{v.code as string} {v.name as string}</span><span>{Number(v.value).toFixed(1)}</span>
                    </div>
                  ))}
                </CardContent>
              </Card>
              {analysis.monotonicity && typeof analysis.monotonicity === "object" && (
                <Card>
                  <CardHeader><CardTitle className="text-sm">分组单调性（5 组）</CardTitle></CardHeader>
                  <CardContent className="text-xs">
                    <p className="mb-2">
                      Spearman {(analysis.monotonicity as Record<string, unknown>).spearman != null
                        ? String((analysis.monotonicity as Record<string, unknown>).spearman)
                        : "-"}
                      {(analysis.monotonicity as Record<string, unknown>).monotonic === true && " · 单调 ✓"}
                    </p>
                    {Array.isArray((analysis.monotonicity as Record<string, unknown>).group_returns) && (
                      <div className="flex items-end gap-1 h-20 border rounded p-1 bg-muted/20">
                        {((analysis.monotonicity as Record<string, unknown>).group_returns as (number | null)[]).map((r, i) => (
                          <div key={i} className="flex-1 flex flex-col items-center justify-end h-full">
                            <div
                              className={`w-full ${(r ?? 0) >= 0 ? "bg-red-400" : "bg-green-500"}`}
                              style={{ height: `${Math.min(100, Math.abs(r ?? 0) * 8)}%`, minHeight: r != null ? 2 : 0 }}
                              title={r != null ? `${r}%` : "-"}
                            />
                            <span className="text-[9px] text-muted-foreground">G{i + 1}</span>
                          </div>
                        ))}
                      </div>
                    )}
                  </CardContent>
                </Card>
              )}
              {analysis.long_short && typeof analysis.long_short === "object" && Array.isArray((analysis.long_short as Record<string, unknown>).cumulative) && (
                <Card>
                  <CardHeader><CardTitle className="text-sm">多空净值（Top20% − Bottom20%）</CardTitle></CardHeader>
                  <CardContent className="text-xs">
                    <p className="mb-2">
                      累计 {(analysis.long_short as Record<string, unknown>).total_return_pct != null
                        ? `${(analysis.long_short as Record<string, unknown>).total_return_pct}%`
                        : "-"}
                      <span className="text-muted-foreground"> · {(analysis.long_short as Record<string, unknown>).n_periods as number} 期</span>
                    </p>
                    <div className="flex items-end gap-px h-16 border rounded p-1 bg-muted/20">
                      {((analysis.long_short as Record<string, unknown>).cumulative as { nav: number }[]).slice(-40).map((pt, i, arr) => {
                        const min = Math.min(...arr.map((x) => x.nav));
                        const max = Math.max(...arr.map((x) => x.nav));
                        const span = max - min || 0.01;
                        const h = ((pt.nav - min) / span) * 100;
                        return (
                          <div key={i} title={String(pt.nav)}
                            className="flex-1 min-w-[2px] bg-primary/70"
                            style={{ height: `${Math.max(4, h)}%` }} />
                        );
                      })}
                    </div>
                  </CardContent>
                </Card>
              )}
            </div>
          )}
        </>
      )}

      {tab === "ic" && icData && (
        <div className="space-y-4">
          <div className="grid md:grid-cols-2 gap-3">
            {Object.entries(icData.factors || {}).map(([k, v]) => (
              <Card key={k}>
                <CardContent className="p-3 text-xs">
                  <div className="flex justify-between">
                    <span className="font-medium">{IC_LABELS[k] || k}</span>
                    <button onClick={() => goBacktest(SCORE_TO_STRATEGY[k] || "composite")} className="text-primary">回测 →</button>
                  </div>
                  <p className="mt-1">IC {v.mean_ic} · IR {v.ir} · 正向率 {(v.ic_positive_ratio * 100).toFixed(0)}%</p>
                </CardContent>
              </Card>
            ))}
          </div>
          {heatmap && (
            <Card>
              <CardHeader><CardTitle className="text-sm">IC 热力矩阵（Mean IC）</CardTitle></CardHeader>
              <CardContent className="overflow-x-auto">
                <table className="text-[10px]">
                  <thead>
                    <tr>
                      <th className="p-1" />
                      {heatmap.forward_days.map((d) => <th key={d} className="p-1">{d}日</th>)}
                    </tr>
                  </thead>
                  <tbody>
                    {Object.entries(heatmap.matrix).map(([row, cols]) => (
                      <tr key={row}>
                        <td className="p-1 font-medium">{IC_LABELS[row] || row}</td>
                        {heatmap.forward_days.map((d) => {
                          const v = cols[String(d)] ?? 0;
                          const intensity = Math.min(1, Math.abs(v) * 5);
                          const bg = v >= 0 ? `rgba(239,68,68,${0.1 + intensity * 0.6})` : `rgba(34,197,94,${0.1 + intensity * 0.6})`;
                          return <td key={d} className="p-1 text-center" style={{ background: bg }}>{v.toFixed(3)}</td>;
                        })}
                      </tr>
                    ))}
                  </tbody>
                </table>
              </CardContent>
            </Card>
          )}
        </div>
      )}

      {tab === "custom" && (
        <Card>
          <CardHeader><CardTitle className="text-sm">表达式因子</CardTitle></CardHeader>
          <CardContent className="space-y-3 text-sm">
            <p className="text-xs text-muted-foreground">
              截面: F001*0.5+F002*0.5 · 时序: Mean($adj_close,20), Delta($volume,5), Rank(Delta($adj_close,1))
            </p>
            <input placeholder="名称" value={customName} onChange={(e) => setCustomName(e.target.value)} className="border rounded px-2 py-1 w-full" />
            <input placeholder="公式" value={customFormula} onChange={(e) => setCustomFormula(e.target.value)} className="border rounded px-2 py-1 w-full font-mono text-xs" />
            <div className="flex gap-2">
              <button onClick={validateExpr} className="border px-3 py-1 rounded text-xs">校验</button>
              <button onClick={createCustom} className="bg-primary text-primary-foreground px-3 py-1 rounded text-sm">创建并计算</button>
            </div>
            {exprValid && (
              <p className={`text-xs ${exprValid.valid ? "text-green-700" : "text-red-700"}`}>
                {exprValid.valid ? `✓ ${exprValid.kind}` : exprValid.error}
              </p>
            )}
            <ul className="text-xs space-y-1">{customList.map((c) => (
              <li key={c.factor_id} className="border-b py-1"><span className="font-mono">{c.factor_id}</span> {c.name}: {c.formula}</li>
            ))}</ul>
          </CardContent>
        </Card>
      )}

      {tab === "gp" && (
        <Card>
          <CardHeader><CardTitle className="text-sm">遗传规划因子挖掘</CardTitle></CardHeader>
          <CardContent className="text-xs space-y-3">
            <p className="text-muted-foreground">随机搜索时序表达式模板，按 Rank IC 筛选 Top 候选</p>
            <button onClick={runGp} disabled={gpRunning}
              className="bg-primary text-primary-foreground px-3 py-1 rounded disabled:opacity-50">
              {gpRunning ? "挖掘中…" : "运行 GP 搜索"}
            </button>
            {gpWinners.length > 0 && (
              <table className="w-full">
                <thead><tr><th className="text-left p-1">因子</th><th className="text-left p-1">公式</th><th className="text-right p-1">IC</th></tr></thead>
                <tbody>
                  {gpWinners.map((w) => (
                    <tr key={String(w.factor_id)} className="border-t">
                      <td className="p-1 font-mono">{String(w.factor_id)}</td>
                      <td className="p-1 font-mono">{String(w.formula)}</td>
                      <td className="p-1 text-right">{Number(w.mean_ic).toFixed(4)}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            )}
          </CardContent>
        </Card>
      )}

      {tab === "ml" && (
        <Card>
          <CardHeader className="flex flex-row justify-between">
            <CardTitle className="text-sm">Qlib / ML 预测</CardTitle>
            <Link href="/backtest?strategy=ml_pred" className="text-[10px] text-primary">ml_pred 回测 →</Link>
          </CardHeader>
          <CardContent className="text-xs space-y-3">
            <button onClick={trainMl} disabled={mlTraining || !mlEnabled}
              className="bg-primary text-primary-foreground px-3 py-1 rounded disabled:opacity-50">
              {mlTraining ? "提交中…" : "触发训练"}
            </button>
            {!mlEnabled && <p className="text-muted-foreground">AFR_QLIB_ENABLED=false</p>}
            <table className="w-full">
              <thead><tr><th className="text-left p-1">代码</th><th className="text-left p-1">名称</th><th className="text-right p-1">ML分</th><th className="text-right p-1">V5分</th></tr></thead>
              <tbody>
                {mlPreds.map((p) => (
                  <tr key={String(p.code)} className="border-t">
                    <td className="p-1 font-mono">{String(p.code)}</td>
                    <td className="p-1">{String(p.name)}</td>
                    <td className="p-1 text-right font-bold">{Number(p.score).toFixed(1)}</td>
                    <td className="p-1 text-right text-blue-700">{p.composite_v5 != null ? Number(p.composite_v5).toFixed(1) : "—"}</td>
                  </tr>
                ))}
              </tbody>
            </table>
            {mlPreds.length > 1 && mlPreds.some((p) => p.composite_v5 != null) && (() => {
              const scatterData = mlPreds
                .filter((p) => p.composite_v5 != null)
                .map((p) => ({ ml: Number(p.score), v5: Number(p.composite_v5), name: String(p.code) }));
              return (
                <div className="mt-3">
                  <p className="text-muted-foreground mb-1">ML分 vs V5分 散点</p>
                  <ResponsiveContainer width="100%" height={200}>
                    <ScatterChart margin={{ top: 4, right: 8, left: -20, bottom: 0 }}>
                      <XAxis dataKey="ml" name="ML分" tick={{ fontSize: 9 }} label={{ value: "ML", position: "insideBottomRight", offset: -4, fontSize: 9 }} type="number" />
                      <YAxis dataKey="v5" name="V5分" tick={{ fontSize: 9 }} label={{ value: "V5", angle: -90, position: "insideLeft", offset: 8, fontSize: 9 }} type="number" />
                      <ZAxis range={[30, 30]} />
                      <Tooltip cursor={{ strokeDasharray: "3 3" }} content={({ payload }) => {
                        const d = payload?.[0]?.payload;
                        if (!d) return null;
                        return <div className="bg-white border rounded p-1 text-[10px]">{d.name}: ML {d.ml?.toFixed(1)} / V5 {d.v5?.toFixed(1)}</div>;
                      }} />
                      <Scatter data={scatterData} fill="#6366f1" opacity={0.7} />
                    </ScatterChart>
                  </ResponsiveContainer>
                </div>
              );
            })()}
          </CardContent>
        </Card>
      )}

      {tab === "decay" && (
        <Card>
          <CardHeader><CardTitle className="text-sm">IC 衰减（lag 分析）</CardTitle></CardHeader>
          <CardContent className="text-xs space-y-3">
            <p className="text-muted-foreground">先在「因子库」选择因子；样本不足时 API 返回 insufficient_sample</p>
            <div className="flex flex-wrap gap-2">
              {factors.slice(0, 8).map((f) => (
                <button key={f.factor_id as string} onClick={() => setSelected(f.factor_id as string)}
                  className={`px-2 py-1 rounded border ${selected === f.factor_id ? "border-primary" : ""}`}>
                  {f.factor_id as string}
                </button>
              ))}
            </div>
            {decay?.error ? (
              <p className="text-amber-700">{String(decay.error)}{decay.reason ? `: ${String(decay.reason)}` : ""}</p>
            ) : decay?.lags ? (() => {
              const lagData = (decay.lags as { lag: number; mean_ic: number | null; n_periods: number }[])
                .map((r) => ({ lag: r.lag, ic: r.mean_ic != null ? Number(r.mean_ic.toFixed(4)) : null, n: r.n_periods }));
              return (
                <div>
                  <ResponsiveContainer width="100%" height={200}>
                    <LineChart data={lagData} margin={{ top: 4, right: 8, left: -20, bottom: 0 }}>
                      <CartesianGrid strokeDasharray="3 3" stroke="#e5e7eb" />
                      <XAxis dataKey="lag" tick={{ fontSize: 10 }} label={{ value: "lag(日)", position: "insideBottomRight", offset: -4, fontSize: 9 }} />
                      <YAxis tick={{ fontSize: 10 }} domain={["auto", "auto"]} />
                      <Tooltip formatter={((v: number) => [v.toFixed(4), "Mean IC"]) as any} labelFormatter={((l: unknown) => `Lag ${l}日`) as any} />
                      <ReferenceLine y={0} stroke="#888" strokeDasharray="3 3" />
                      <Line type="monotone" dataKey="ic" stroke="#6366f1" dot={{ r: 4 }} strokeWidth={2} connectNulls />
                    </LineChart>
                  </ResponsiveContainer>
                  <table className="w-full text-left mt-2">
                    <thead><tr><th className="p-1">Lag</th><th className="p-1">Mean IC</th><th className="p-1">N</th></tr></thead>
                    <tbody>
                      {lagData.map((row) => (
                        <tr key={row.lag}><td className="p-1">{row.lag}日</td><td className="p-1">{row.ic ?? "-"}</td><td className="p-1">{row.n}</td></tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              );
            })() : selected ? (
              <p>加载中…</p>
            ) : (
              <p>请选择因子</p>
            )}
          </CardContent>
        </Card>
      )}

      {tab === "merge" && (
        <Card>
          <CardHeader><CardTitle className="text-sm">预设因子合成（F010-F013 技术面）</CardTitle></CardHeader>
          <CardContent className="text-xs space-y-3">
            {icReview ? (
              <div className="rounded border p-2 bg-muted/30">
                <p>IC 审查：{icReview.ic_stable_ready ? "✅ 通过" : "⚠️ 未就绪"}
                  （稳定因子 {String(icReview.stable_count)}/{String(icReview.min_stable_required)}）</p>
                <ul className="mt-1 space-y-0.5">
                  {((icReview.factors as { factor_id: string; ir: number; stable: boolean }[]) || []).map((f) => (
                    <li key={f.factor_id} className="font-mono">{f.factor_id} IR {f.ir?.toFixed?.(2) ?? f.ir} {f.stable ? "✓" : ""}</li>
                  ))}
                </ul>
              </div>
            ) : (
              <p>加载 IC 审查…</p>
            )}
            <p className="text-muted-foreground">等权 + IC_IR 加权各生成一个合成因子（需 AFR_FACTOR_MERGE_ENABLED=true）</p>
            <button onClick={runPresetMerge} disabled={merging || !icReview?.ic_stable_ready}
              className="bg-primary text-primary-foreground px-3 py-1 rounded text-sm disabled:opacity-50">
              {merging ? "合成中…" : "运行预设合成"}
            </button>
            {mergeResult?.results && (
              <ul className="space-y-1">
                {(mergeResult.results as { preset: string; factor_id?: string; merged_count?: number; error?: string }[]).map((r) => (
                  <li key={r.preset} className="border-b py-1">
                    {r.error ? `${r.preset}: ${r.error}` : `${r.preset} → ${r.factor_id} (${r.merged_count} 只)`}
                  </li>
                ))}
              </ul>
            )}
            <div className="border-t pt-3 space-y-2">
              <p className="font-medium">保存合成方案（持久化 + 历史回填）</p>
              <input className="border rounded px-2 py-1 w-full text-xs" placeholder="方案名称" value={comboName} onChange={(e) => setComboName(e.target.value)} />
              <input className="border rounded px-2 py-1 w-full font-mono text-xs" placeholder="F010,F011,..." value={comboFactors} onChange={(e) => setComboFactors(e.target.value)} />
              <select className="border rounded px-2 py-1 text-xs" value={comboMethod} onChange={(e) => setComboMethod(e.target.value)}>
                <option value="equal">等权</option>
                <option value="ic_ir">IC_IR</option>
                <option value="rolling_optimal">滚动最优</option>
              </select>
              <button onClick={saveCombination} disabled={savingCombo}
                className="bg-primary text-primary-foreground px-3 py-1 rounded text-sm disabled:opacity-50">
                {savingCombo ? "保存中…" : "保存方案"}
              </button>
            </div>
            {combinations.length > 0 && (
              <ul className="space-y-1 border-t pt-2">
                <p className="font-medium">已保存方案</p>
                {combinations.map((c) => (
                  <li key={String(c.id)} className="flex justify-between items-center border-b py-1">
                    <span>
                      #{String(c.id)} {String(c.name)} → {String(c.output_factor_id || "-")}
                      <span className="text-muted-foreground ml-1">({String(c.weight_method)})</span>
                    </span>
                    <Link href={`/backtest?strategy=factor_combination&combination_id=${c.id}`}
                      className="text-primary text-[10px]">回测 →</Link>
                  </li>
                ))}
              </ul>
            )}
          </CardContent>
        </Card>
      )}

      {tab === "correlation" && corr && (
        <Card>
          <CardHeader><CardTitle className="text-sm">因子相关矩阵</CardTitle></CardHeader>
          <CardContent className="overflow-x-auto text-[10px]">
            <table>
              <thead><tr><th />{corr.factors.map((f) => <th key={f} className="p-1">{f}</th>)}</tr></thead>
              <tbody>
                {corr.factors.map((a) => (
                  <tr key={a}><td className="p-1 font-mono">{a}</td>
                    {corr.factors.map((b) => {
                      const v = corr.matrix[a]?.[b];
                      return <td key={b} className="p-1 text-center">{v != null ? v.toFixed(2) : "-"}</td>;
                    })}
                  </tr>
                ))}
              </tbody>
            </table>
          </CardContent>
        </Card>
      )}
    </BetaShell>
  );
}

export default function FactorsPage() {
  return (
    <Suspense fallback={<div className="p-8 animate-pulse bg-muted rounded h-64" />}>
      <FactorsLabInner />
    </Suspense>
  );
}
