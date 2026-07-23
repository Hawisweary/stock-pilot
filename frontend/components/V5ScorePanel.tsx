"use client";

import { useEffect, useState } from "react";
import { Rocket, Coins, type LucideIcon } from "lucide-react";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { V5ScoreExplainer } from "@/components/V5ScoreExplainer";
import { scoreTextClass } from "@/lib/scoreColors";
import { ValuationPercentileBar } from "@/components/ValuationPercentileBar";
type V5Tier = Record<string, number | null>;

interface V5Data {
  /** v3.0 权威分（View 别名） */
  score?: number | null;
  composite_v5?: number | null;
  veto_status?: string;
  veto_reasons?: string[];
  calc_date?: string;
  breakdown?: {
    tiers?: V5Tier;
    tier_sources?: Record<string, string>;
    shortboard_penalty?: number;
    base_score?: number;
    missing_dims?: string[];
    dims_available?: string;
    effective_weights?: Record<string, number>;
    market_beta?: number;
    veto_reasons?: string[];
  };
  missing_dims?: string[];
  dims_available?: string;
  tiers?: V5Tier;
  tier_sources?: Record<string, string>;
  labels?: Record<string, string>;
  shortboard_penalty?: number;
  base_score?: number;
}

const DIM_ORDER = [
  "fundamental",
  "quality",
  "industry",
  "capital",
  "valuation",
  "technical",
  "market_env",
  "policy",
  "news",
  "mood",
];

function tierColor(t: number): string {
  if (t >= 2) return "text-red-600 bg-red-50";
  if (t >= 1) return "text-orange-600 bg-orange-50";
  if (t <= -2) return "text-gray-600 bg-gray-200";
  if (t <= -1) return "text-blue-700 bg-blue-50";
  return "text-green-700 bg-green-50";
}

function tierLabel(t: number | null | undefined): string {
  if (t == null) return "—";
  if (t >= 2) return "+2";
  if (t >= 1) return "+1";
  if (t <= -2) return "-2";
  if (t <= -1) return "-1";
  return "0";
}

function tierColorClass(t: number | null | undefined): string {
  if (t == null) return "text-muted-foreground bg-muted/50 border border-dashed border-muted-foreground/30";
  return tierColor(t);
}

export function V5ScoreBadge({ stockId }: { stockId: number }) {
  const [score, setScore] = useState<number | null>(null);
  const [veto, setVeto] = useState<string | null>(null);

  useEffect(() => {
    if (!stockId) return;
    fetch(`/api/v5/scores/${stockId}?_=${Date.now()}`, { cache: "no-store" })
      .then((r) => (r.ok ? r.json() : null))
      .then((res) => {
        const v5 = res?.v5 as V5Data | undefined;
        setScore(v5?.score ?? v5?.composite_v5 ?? null);
        setVeto(v5?.veto_status ?? null);
      })
      .catch(() => {
        setScore(null);
        setVeto(null);
      });
  }, [stockId]);

  if (score == null) {
    return <span className="text-xs text-muted-foreground">V5: --</span>;
  }
  const color =
    score >= 60 ? "text-red-600" : score >= 40 ? "text-yellow-600" : "text-green-600";
  return (
    <span className="text-xs text-muted-foreground">
      V5: <b className={color}>{score.toFixed(1)}</b>
      {veto && veto !== "ok" && (
        <span className="ml-1 text-amber-700">
          {veto === "exclude" ? "回避" : veto === "discount" ? "折扣" : "减仓"}
        </span>
      )}
    </span>
  );
}

export function V5ScorePanel({ stockId }: { stockId: number }) {
  const [data, setData] = useState<V5Data | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [percentile, setPercentile] = useState<number | null>(null);

  const load = () => {
    if (!stockId) return;
    setLoading(true);
    setError(null);
    // 绕过 Next 代理与浏览器缓存，确保拿到最新 V5
    fetch(`/api/v5/scores/${stockId}?_=${Date.now()}`, { cache: "no-store" })
      .then(async (res) => {
        if (!res.ok) {
          const err = await res.json().catch(() => ({ detail: res.statusText }));
          throw new Error(
            typeof err.detail === "string" ? err.detail : res.statusText || "V5 接口错误"
          );
        }
        return res.json() as Promise<{ v5?: V5Data }>;
      })
      .then((res) => {
        setData(res.v5 as V5Data);
        if (!res.v5) setError("暂无 V5 评分数据");
      })
      .catch((e: unknown) => {
        setData(null);
        setError(e instanceof Error ? e.message : "V5 接口不可用，请重启后端");
      })
      .finally(() => setLoading(false));
  };

  useEffect(() => {
    load();
    fetch("/api/scores/percentile-ranks", { cache: "no-store" })
      .then((r) => r.ok ? r.json() : null)
      .then((res) => {
        if (!res?.ranks) return;
        const entry = res.ranks[String(stockId)];
        if (entry != null) setPercentile(entry.percentile);
      })
      .catch(() => {});
  }, [stockId]);

  if (loading) {
    return (
      <Card>
        <CardHeader>
          <CardTitle className="text-sm">V5 十维评分</CardTitle>
        </CardHeader>
        <CardContent className="text-xs text-muted-foreground">加载中…</CardContent>
      </Card>
    );
  }

  if (!data) {
    return (
      <Card>
        <CardHeader>
          <CardTitle className="text-sm">V5 十维评分</CardTitle>
        </CardHeader>
        <CardContent className="text-xs text-muted-foreground space-y-2">
          <p>{error || "暂无数据"}</p>
          <button
            type="button"
            className="text-[11px] text-blue-600 underline"
            onClick={load}
          >
            重试
          </button>
        </CardContent>
      </Card>
    );
  }

  const tiers = data.breakdown?.tiers ?? data.tiers ?? {};
  const tierSources = data.breakdown?.tier_sources ?? data.tier_sources ?? {};
  const labels = data.labels ?? {};
  const dimsAvailable =
    data.dims_available ?? data.breakdown?.dims_available ?? null;

  // M0：画像提示——基于 tier 分布推断最适合的视角
  const techTier = (tiers.technical as number | null) ?? null;
  const capTier = (tiers.capital as number | null) ?? null;
  const valTier = (tiers.valuation as number | null) ?? null;
  const qualTier = (tiers.quality as number | null) ?? null;
  const profileHint: { label: string; color: string; tip: string; icon: LucideIcon } | null = (() => {
    const momScore = (techTier ?? 0) + (capTier ?? 0);
    const valScore = (valTier ?? 0) + (qualTier ?? 0);
    if (momScore >= 2 && momScore > valScore)
      return { label: "动量视角", color: "text-primary bg-primary/10", tip: "技术+资金偏强，适合动量榜筛选", icon: Rocket };
    if (valScore >= 2 && valScore >= momScore && qualTier != null && qualTier >= 0)
      return { label: "红利/价值", color: "text-down bg-[var(--down)]/10", tip: "估值低+质量稳，适合价值/红利榜", icon: Coins };
    return null;
  })();
  const missingDims =
    data.missing_dims ?? data.breakdown?.missing_dims ?? [];
  const missingCount =
    missingDims.length > 0
      ? missingDims.length
      : DIM_ORDER.filter((k) => tiers[k] == null).length;
  const composite = data.score ?? data.composite_v5;
  const veto = data.veto_status ?? "ok";
  const reasons = data.veto_reasons ?? data.breakdown?.veto_reasons ?? [];

  return (
    <Card>
      <CardHeader className="pb-2 flex flex-row items-center justify-between">
        <CardTitle className="text-sm">V5 十维评分</CardTitle>
        <div className="flex items-center gap-2 text-xs">
          <span className="text-muted-foreground">
            综合分
            {dimsAvailable ? ` · ${dimsAvailable}维` : ""}
          </span>
          <span className={`font-bold text-base ${scoreTextClass(composite)}`}>
            {composite?.toFixed(1) ?? "--"}
          </span>
          {percentile != null && (
            <span className="text-[10px] text-muted-foreground">
              前 {(100 - percentile).toFixed(0)}%
            </span>
          )}
          {veto !== "ok" && (
            <span
              className={
                "px-1.5 py-0.5 rounded text-[10px] font-medium " +
                (veto === "exclude" ? "bg-gray-800 text-white" : "bg-yellow-100 text-yellow-800")
              }
            >
              {veto === "exclude"
                ? "回避"
                : veto === "discount"
                  ? "折扣"
                  : "减仓"}
            </span>
          )}
        </div>
      </CardHeader>
      <CardContent className="space-y-3">
        {/* U3-6: 规则引擎解释 */}
        <V5ScoreExplainer tiers={tiers} composite={composite ?? null} vetoReasons={reasons} />
        <div className="grid grid-cols-2 md:grid-cols-5 gap-2">
          {DIM_ORDER.map((key) => {
            const t = tiers[key] as number | null | undefined;
            const missing = t == null;
            return (
              <div
                key={key}
                className={"rounded p-2 text-center " + tierColorClass(t)}
                title={
                  missing
                    ? "数据不足，未计入加权"
                    : tierSources[key] || undefined
                }
              >
                <div className="text-[10px] opacity-80">{labels[key] || key}</div>
                <div className="text-sm font-bold">
                  {missing ? "不足" : tierLabel(t)}
                </div>
              </div>
            );
          })}
        </div>
        {(data.base_score != null || data.breakdown?.base_score != null) && (
          <div className="mt-2 text-[10px] text-muted-foreground flex flex-wrap gap-x-3 gap-y-1">
            <span>加权 {data.base_score ?? data.breakdown?.base_score}</span>
            <span>
              短板扣{" "}
              {data.shortboard_penalty ?? data.breakdown?.shortboard_penalty ?? 0}
            </span>
            {missingCount > 0 && (
              <span>
                基于 {dimsAvailable ?? `${10 - missingCount}/10`} 维
                {missingDims.length > 0
                  ? `，缺：${missingDims.map((k) => labels[k] || k).join("、")}`
                  : ""}
              </span>
            )}
          </div>
        )}
        {reasons.length > 0 && (
          <div className="mt-2 text-[10px] text-orange-700">
            否决/警示：{reasons.join("；")}
          </div>
        )}
        {/* M0: 画像提示 */}
        {profileHint && (
          <div className={`text-[10px] px-2 py-1 rounded flex items-center gap-1 ${profileHint.color}`} title={profileHint.tip}>
            <profileHint.icon className="w-3 h-3 shrink-0" />
            <span className="font-medium">{profileHint.label}</span>
            <span className="opacity-70">{profileHint.tip}</span>
          </div>
        )}
        {/* P1-2: 估值历史百分位 */}
        <ValuationPercentileBar stockId={stockId} />
      </CardContent>
    </Card>
  );
}
