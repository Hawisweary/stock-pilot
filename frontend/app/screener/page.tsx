"use client";

import { useEffect, useState, useCallback, Suspense } from "react";
import { useRouter } from "next/navigation";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { scoreTextClass, scoreBgClass } from "@/lib/scoreColors";
import { exportCsv } from "@/lib/csvExport";
import { Filter, RotateCcw, Download, ChevronUp, ChevronDown, PlayCircle } from "lucide-react";

// ── 类型 ────────────────────────────────────────────────
interface RangeFilter { min?: number; max?: number }

interface ScreenerQuery {
  score?: RangeFilter;
  fundamental_score?: RangeFilter;
  technical_score?: RangeFilter;
  sentiment_score?: RangeFilter;
  capital_score?: RangeFilter;
  policy_score?: RangeFilter;
  mood_score?: RangeFilter;
  val_score?: RangeFilter;
  quality_score?: RangeFilter;
  industry_score?: RangeFilter;
  market_env_score?: RangeFilter;
  industries?: string[];
  veto_status?: string[];
  exclude_veto?: boolean;
  sort_by?: string;
  sort_dir?: "asc" | "desc";
  limit?: number;
  offset?: number;
}

interface StockRow {
  stock_id: number; code: string; name: string;
  market: string; industry: string; industry_sw: string;
  calc_date: string; score: number | null;
  fundamental_score: number | null; technical_score: number | null;
  sentiment_score: number | null; capital_score: number | null;
  policy_score: number | null; mood_score: number | null;
  val_score: number | null; quality_score: number | null;
  industry_score: number | null; market_env_score: number | null;
  veto_status: string | null;
}

interface Preset {
  id: string; label: string; desc: string; query: ScreenerQuery; profile?: string;
}

const DIM_LABELS: Record<string, string> = {
  score: "综合分", fundamental_score: "基本面", technical_score: "技术面",
  sentiment_score: "新闻面", capital_score: "资金面", policy_score: "政策面",
  mood_score: "情绪面", val_score: "估值面", quality_score: "质量",
  industry_score: "行业", market_env_score: "市场环境",
};

const SCORE_COLS = Object.keys(DIM_LABELS);

// 去掉后端预设标签里的装饰 emoji(纯展示层清洗,不改 API 数据)
const stripEmoji = (s: string) =>
  s.replace(/[\u{1F000}-\u{1FAFF}\u{2600}-\u{27BF}\u{FE0F}\u{200D}]/gu, "").trim();

const DEFAULT_QUERY: ScreenerQuery = {
  sort_by: "score", sort_dir: "desc", limit: 100, offset: 0,
};

function scoreCell(v: number | null) {
  if (v == null) return <span className="text-muted-foreground text-xs">—</span>;
  return <span className={`font-mono tabular-nums ${scoreTextClass(v)}`}>{v.toFixed(1)}</span>;
}

function vetoLabel(v: string | null) {
  if (!v || v === "ok") return null;
  return v === "exclude"
    ? <span className="text-xs text-up font-medium bg-muted px-1 rounded">回避</span>
    : <span className="text-xs text-amber-700 dark:text-amber-500 bg-amber-500/10 px-1 rounded">减仓</span>;
}

// ── Range Slider 组件 ────────────────────────────────────
function RangeSlider({ label, value, onChange }: {
  label: string;
  value?: RangeFilter;
  onChange: (v: RangeFilter | undefined) => void;
}) {
  const lo = value?.min ?? 0;
  const hi = value?.max ?? 100;
  const active = value?.min != null || value?.max != null;

  const updateMin = (raw: string) => {
    const num = Number(raw);
    const next = { min: num, max: hi };
    if (num === 0 && hi === 100) onChange(undefined);
    else onChange(next);
  };
  const updateMax = (raw: string) => {
    const num = Number(raw);
    const next = { min: lo, max: num };
    if (lo === 0 && num === 100) onChange(undefined);
    else onChange(next);
  };

  return (
    <div className="space-y-1.5">
      <div className="flex items-center justify-between">
        <label className={`text-xs font-medium ${active ? "text-primary" : "text-muted-foreground"}`}>{label}</label>
        <span className="text-[10px] tabular-nums text-muted-foreground">
          {active ? `${lo}–${hi}` : "全部"}
        </span>
      </div>
      <div className="relative h-5 flex items-center">
        <div className="absolute inset-x-0 h-1.5 bg-muted rounded" />
        <div
          className="absolute h-1.5 bg-primary rounded"
          style={{ left: `${lo}%`, right: `${100 - hi}%` }}
        />
        <input type="range" min={0} max={100} value={lo}
          onChange={e => updateMin(e.target.value)}
          className="absolute w-full h-1.5 opacity-0 cursor-pointer z-10" />
        <input type="range" min={0} max={100} value={hi}
          onChange={e => updateMax(e.target.value)}
          className="absolute w-full h-1.5 opacity-0 cursor-pointer z-20" />
      </div>
    </div>
  );
}

// ── 主页面 ───────────────────────────────────────────────
function ScreenerPage() {
  const router = useRouter();
  const [query, setQuery] = useState<ScreenerQuery>(DEFAULT_QUERY);
  const [results, setResults] = useState<StockRow[]>([]);
  const [total, setTotal] = useState(0);
  const [loading, setLoading] = useState(false);
  const [industries, setIndustries] = useState<string[]>([]);
  const [presets, setPresets] = useState<Preset[]>([]);
  const [showFilters, setShowFilters] = useState(true);

  // 加载行业列表和预设
  useEffect(() => {
    fetch("/api/screener/industries").then(r => r.json()).then(d => setIndustries(d.industries ?? [])).catch(() => {});
    fetch("/api/screener/presets").then(r => r.json()).then(d => setPresets(d.presets ?? [])).catch(() => {});
  }, []);

  const runQuery = useCallback(async (q: ScreenerQuery) => {
    setLoading(true);
    try {
      const res = await fetch("/api/screener/query", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(q),
      });
      const data = await res.json();
      setResults(data.results ?? []);
      setTotal(data.total ?? 0);
    } finally {
      setLoading(false);
    }
  }, []);

  // 初始查询
  useEffect(() => { runQuery(query); }, []);

  const applyPreset = (preset: Preset) => {
    const next = { ...DEFAULT_QUERY, ...preset.query };
    setQuery(next);
    runQuery(next);
  };

  const applyFilters = () => {
    const next = { ...query, offset: 0 };
    setQuery(next);
    runQuery(next);
  };

  const resetFilters = () => {
    setQuery(DEFAULT_QUERY);
    runQuery(DEFAULT_QUERY);
  };

  const toggleSort = (col: string) => {
    const next: ScreenerQuery = {
      ...query,
      sort_by: col,
      sort_dir: query.sort_by === col && query.sort_dir === "desc" ? "asc" : "desc",
    };
    setQuery(next);
    runQuery(next);
  };

  const sortIcon = (col: string) => {
    if (query.sort_by !== col) return null;
    return query.sort_dir === "desc"
      ? <ChevronDown className="h-3 w-3 inline ml-0.5 text-primary" />
      : <ChevronUp className="h-3 w-3 inline ml-0.5 text-primary" />;
  };

  const updateRange = (key: keyof ScreenerQuery, v: RangeFilter | undefined) =>
    setQuery(q => ({ ...q, [key]: v }));

  const handleExport = () => {
    const headers = ["代码", "名称", "市场", "行业", ...Object.values(DIM_LABELS), "状态"];
    const rows = results.map(r => [
      r.code, r.name, r.market, r.industry ?? r.industry_sw ?? "",
      ...SCORE_COLS.map(k => (r[k as keyof StockRow] as number | null)?.toFixed(1) ?? ""),
      r.veto_status ?? "ok",
    ]);
    exportCsv(`screener_${new Date().toISOString().slice(0, 10)}.csv`, headers, rows);
  };

  return (
    <div className="space-y-3">
      {/* 标题行 */}
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-xl font-semibold tracking-tight flex items-center gap-2">
            <Filter className="h-5 w-5 text-muted-foreground" /> 选股筛选
          </h1>
          <p className="text-sm text-muted-foreground">基于 V5 十维评分的多条件筛选</p>
        </div>
        <div className="flex gap-2">
          <button
            onClick={() => {
              const minScore = (query.score as any)?.min ?? 0;
              const url = `/backtest?strategy=composite&min_score=${minScore}&from_screener=1`;
              router.push(url);
            }}
            className="flex items-center gap-1 text-xs border rounded px-3 py-1.5 hover:bg-primary hover:text-primary-foreground transition-colors border-primary/30 text-primary"
          >
            <PlayCircle className="h-3.5 w-3.5" /> 回测此筛选
          </button>
          <button onClick={handleExport} className="flex items-center gap-1 text-xs border rounded px-3 py-1.5 hover:bg-accent transition-colors">
            <Download className="h-3.5 w-3.5" /> 导出
          </button>
          <button onClick={resetFilters} className="flex items-center gap-1 text-xs border rounded px-3 py-1.5 hover:bg-accent transition-colors">
            <RotateCcw className="h-3.5 w-3.5" /> 重置
          </button>
        </div>
      </div>

      {/* 预设策略 */}
      <div className="space-y-1.5">
        {/* M0 三画像预设 */}
        <div className="flex flex-wrap gap-1.5">
          {presets.filter(p => p.profile).map(p => (
            <button key={p.id} onClick={() => applyPreset(p)} title={p.desc}
              className="text-xs border rounded px-3 py-1.5 transition-colors font-semibold border-primary/40 text-primary hover:bg-primary hover:text-primary-foreground">
              {stripEmoji(p.label)}
            </button>
          ))}
        </div>
        {/* 原有预设 */}
        <div className="flex flex-wrap gap-1.5">
          {presets.filter(p => !p.profile).map(p => (
            <button key={p.id} onClick={() => applyPreset(p)} title={p.desc}
              className="text-xs border rounded px-3 py-1.5 hover:bg-primary hover:text-primary-foreground transition-colors font-medium">
              {stripEmoji(p.label)}
            </button>
          ))}
        </div>
      </div>

      {/* 双栏布局：左=筛选面板 右=结果 */}
      <div className="flex gap-4 items-start">
        {/* 左栏：筛选条件（固定宽） */}
        <div className="w-56 shrink-0 space-y-4">
          <Card>
            <CardHeader className="py-3 px-4 cursor-pointer flex flex-row items-center justify-between"
              onClick={() => setShowFilters(f => !f)}>
              <CardTitle className="text-xs">评分筛选</CardTitle>
              {showFilters ? <ChevronUp className="h-3.5 w-3.5 text-muted-foreground" /> : <ChevronDown className="h-3.5 w-3.5 text-muted-foreground" />}
            </CardHeader>
            {showFilters && (
              <CardContent className="pb-4 px-4 space-y-4">
                {SCORE_COLS.map(k => (
                  <RangeSlider
                    key={k}
                    label={DIM_LABELS[k]}
                    value={query[k as keyof ScreenerQuery] as RangeFilter | undefined}
                    onChange={v => updateRange(k as keyof ScreenerQuery, v)}
                  />
                ))}
              </CardContent>
            )}
          </Card>

          <Card>
            <CardContent className="p-4 space-y-4">
              {/* 行业多选 */}
              <div className="space-y-1.5">
                <label className="text-xs font-medium text-muted-foreground">行业</label>
                <select
                  multiple
                  size={5}
                  value={query.industries ?? []}
                  onChange={e => {
                    const sel = Array.from(e.target.selectedOptions, o => o.value);
                    setQuery(q => ({ ...q, industries: sel.length ? sel : undefined }));
                  }}
                  className="w-full text-xs border rounded p-1 bg-background"
                >
                  {industries.map(i => <option key={i} value={i}>{i}</option>)}
                </select>
              </div>

              {/* 否决状态 */}
              <div className="space-y-1.5">
                <label className="text-xs font-medium text-muted-foreground">否决状态</label>
                {[["排除所有否决", "exclude_veto"], ["仅减仓", "reduce"], ["仅回避", "exclude"]].map(([label, val]) => (
                  <label key={val} className="flex items-center gap-1.5 text-xs cursor-pointer">
                    <input type="checkbox"
                      checked={val === "exclude_veto" ? !!query.exclude_veto : (query.veto_status ?? []).includes(val)}
                      onChange={e => {
                        if (val === "exclude_veto") {
                          setQuery(q => ({ ...q, exclude_veto: e.target.checked, veto_status: undefined }));
                        } else {
                          const cur = query.veto_status ?? [];
                          const next = e.target.checked ? [...cur, val] : cur.filter(v => v !== val);
                          setQuery(q => ({ ...q, veto_status: next.length ? next : undefined, exclude_veto: false }));
                        }
                      }}
                      className="rounded"
                    />
                    {label}
                  </label>
                ))}
              </div>

              <button onClick={applyFilters}
                className="w-full bg-primary text-primary-foreground text-xs rounded px-3 py-2 hover:opacity-90 transition-opacity font-medium">
                应用筛选
              </button>
            </CardContent>
          </Card>
        </div>

        {/* 右栏：结果 */}
        <div className="flex-1 min-w-0">
          <Card>
            <CardHeader className="py-3 px-4 flex flex-row items-center justify-between">
              <CardTitle className="text-sm">
                筛选结果
                <span className="text-xs font-normal text-muted-foreground ml-2">
                  {loading ? "加载中…" : `共 ${total} 只`}
                </span>
              </CardTitle>
            </CardHeader>
            <CardContent className="p-0">
              <div className="overflow-auto max-h-[75vh]">
                <table className="w-full text-xs min-w-[700px]">
                  <thead className="sticky top-0 bg-card z-10 border-b">
                    <tr className="text-left text-muted-foreground">
                      <th className="py-2 px-3 w-8">#</th>
                      <th className="py-2 px-2">代码</th>
                      <th className="py-2 px-2">名称</th>
                      <th className="py-2 px-2 hidden md:table-cell">行业</th>
                      {SCORE_COLS.map(k => (
                        <th key={k}
                          className="py-2 px-2 text-right cursor-pointer hover:text-foreground select-none whitespace-nowrap"
                          onClick={() => toggleSort(k)}>
                          {DIM_LABELS[k]}{sortIcon(k)}
                        </th>
                      ))}
                      <th className="py-2 px-2 text-right">状态</th>
                    </tr>
                  </thead>
                  <tbody>
                    {results.map((r, i) => (
                      <tr key={r.stock_id}
                        className="border-b hover:bg-muted/40 transition-colors cursor-pointer"
                        onClick={() => router.push(`/stocks/${r.code}`)}>
                        <td className="py-1.5 px-3 text-muted-foreground">{i + 1}</td>
                        <td className="py-1.5 px-2 font-mono">{r.code}</td>
                        <td className="py-1.5 px-2 font-medium max-w-[6rem] truncate">{r.name}</td>
                        <td className="py-1.5 px-2 text-muted-foreground hidden md:table-cell max-w-[5rem] truncate">
                          {r.industry ?? r.industry_sw ?? "—"}
                        </td>
                        {SCORE_COLS.map(k => {
                          const v = r[k as keyof StockRow] as number | null;
                          return (
                            <td key={k} className={`py-1.5 px-2 text-right tabular-nums ${scoreBgClass(v)}`}>
                              {scoreCell(v)}
                            </td>
                          );
                        })}
                        <td className="py-1.5 px-2 text-right">{vetoLabel(r.veto_status)}</td>
                      </tr>
                    ))}
                    {!loading && results.length === 0 && (
                      <tr>
                        <td colSpan={SCORE_COLS.length + 5}
                          className="py-12 text-center text-muted-foreground">
                          没有符合条件的股票
                        </td>
                      </tr>
                    )}
                  </tbody>
                </table>
              </div>
              {/* 分页 */}
              {total > (query.limit ?? 100) && (
                <div className="flex items-center justify-between px-4 py-2 border-t text-xs text-muted-foreground">
                  <span>显示 {(query.offset ?? 0) + 1}–{Math.min((query.offset ?? 0) + (query.limit ?? 100), total)} / 共 {total} 只</span>
                  <div className="flex gap-2">
                    <button
                      className="px-2 py-1 rounded border disabled:opacity-40"
                      disabled={(query.offset ?? 0) === 0}
                      onClick={() => {
                        const next = { ...query, offset: Math.max(0, (query.offset ?? 0) - (query.limit ?? 100)) };
                        setQuery(next); runQuery(next);
                      }}
                    >上一页</button>
                    <button
                      className="px-2 py-1 rounded border disabled:opacity-40"
                      disabled={(query.offset ?? 0) + (query.limit ?? 100) >= total}
                      onClick={() => {
                        const next = { ...query, offset: (query.offset ?? 0) + (query.limit ?? 100) };
                        setQuery(next); runQuery(next);
                      }}
                    >下一页</button>
                  </div>
                </div>
              )}
            </CardContent>
          </Card>
        </div>
      </div>
    </div>
  );
}

export default function ScreenerPageWrapper() {
  return (
    <Suspense fallback={<div className="p-8 animate-pulse text-sm text-muted-foreground">加载中…</div>}>
      <ScreenerPage />
    </Suspense>
  );
}
