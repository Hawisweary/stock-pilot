"use client";

import { useEffect, useState } from "react";
import Link from "next/link";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Sparkles, Trophy, ChevronRight, AlertTriangle, TrendingUp, Activity } from "lucide-react";
import { api } from "@/lib/api";

type Pick = { code?: string; name?: string; score?: number };
type StrategyRec = {
  strategy?: string;
  label?: string;
  sharpe?: number;
  sample_days?: number;
  ann_return_pct?: number;
  top_picks?: Pick[];
  sim_portfolio?: { portfolio_id?: number; name?: string; positions?: Pick[] };
};

type HitRateBlock = {
  horizon_days?: number;
  sample_count?: number;
  hit_rate_pct?: number | null;
  avg_excess_return_pct?: number | null;
};

type SwitchEvent = {
  trade_date?: string;
  note?: string;
  new_bucket_label?: string;
  new_strategy?: string;
};

type MonitoringPayload = {
  hit_rate_h5?: HitRateBlock;
  hit_rate_h20?: HitRateBlock;
  recent_switches?: SwitchEvent[];
  evaluated_outcomes?: number;
};

type JumpOpinion = {
  available?: boolean;
  aligned?: boolean;
  jump_bucket_label?: string;
  jump_penalty?: number;
  bucket_diverged?: boolean;
  strategy_diverged?: boolean;
  rationale?: string;
  note?: string;
  primary?: StrategyRec;
  alternatives?: StrategyRec[];
};

type RecommendationPayload = {
  trade_date?: string;
  market?: {
    regime_bucket_label?: string;
    regime_csi800_label?: string;
    volatility_20?: number;
    guidance?: { max_position?: number; note?: string };
    dual_track_diverged?: boolean;
  };
  recommendation?: {
    confidence?: number;
    rationale?: string;
    hard_rule_match?: boolean;
    primary?: StrategyRec;
    alternatives?: StrategyRec[];
    avoid?: StrategyRec;
    jump_opinion?: JumpOpinion;
  };
};

function fmtVol(v?: number) {
  if (v == null) return "—";
  return `${(v * 100).toFixed(1)}%`;
}

function fmtPct(v?: number | null) {
  if (v == null) return "—";
  const sign = v > 0 ? "+" : "";
  return `${sign}${v.toFixed(2)}%`;
}

export function StrategyRecommendationCard() {
  const [data, setData] = useState<RecommendationPayload | null>(null);
  const [monitoring, setMonitoring] = useState<MonitoringPayload | null>(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    setLoading(true);
    Promise.all([
      api.getCurrentRecommendation().catch(() => null),
      api.getRecommendationMonitoring(365).catch(() => null),
    ])
      .then(([rec, mon]) => {
        setData(rec as RecommendationPayload | null);
        setMonitoring(mon as MonitoringPayload | null);
      })
      .finally(() => setLoading(false));
  }, []);

  if (loading) {
    return (
      <Card className="border-primary/20">
        <CardHeader className="pb-2">
          <CardTitle className="text-sm flex items-center gap-2">
            <Sparkles className="h-4 w-4 text-primary" /> 策略推荐
          </CardTitle>
        </CardHeader>
        <CardContent className="text-xs text-muted-foreground">生成推荐中…</CardContent>
      </Card>
    );
  }

  const rec = data?.recommendation;
  const market = data?.market;
  const primary = rec?.primary;

  if (!data || !primary) {
    return (
      <Card>
        <CardHeader className="pb-2">
          <CardTitle className="text-sm flex items-center gap-2">
            <Sparkles className="h-4 w-4" /> 策略推荐
          </CardTitle>
        </CardHeader>
        <CardContent className="text-xs text-muted-foreground">暂无推荐（请先刷新 L2 矩阵）</CardContent>
      </Card>
    );
  }

  const holdings = primary.sim_portfolio?.positions?.length
    ? primary.sim_portfolio.positions
    : primary.top_picks;

  return (
    <Card className="border-primary/25 bg-gradient-to-br from-primary/5 to-transparent">
      <CardHeader className="pb-2 flex flex-row items-start justify-between gap-2">
        <CardTitle className="text-sm flex items-center gap-2">
          <Sparkles className="h-4 w-4 text-primary" /> 策略推荐
          <span className="text-[10px] font-normal text-muted-foreground">L3</span>
        </CardTitle>
        {data.trade_date && (
          <span className="text-[10px] text-muted-foreground font-mono shrink-0">{data.trade_date}</span>
        )}
      </CardHeader>
      <CardContent className="pt-0 space-y-3 text-xs">
        <div className="rounded-md bg-background/80 border px-2.5 py-2 space-y-1">
          <div className="flex items-center gap-1.5 text-[10px] text-muted-foreground">
            <TrendingUp className="h-3 w-3" />
            当前市场 · {market?.regime_csi800_label ?? market?.regime_bucket_label}
            {market?.volatility_20 != null && (
              <span>· 波动 {fmtVol(market.volatility_20)}</span>
            )}
            <span className="ml-auto">置信 {Math.round((rec?.confidence ?? 0) * 100)}%</span>
          </div>
          {market?.guidance?.note && (
            <div className="text-[10px] text-muted-foreground">
              仓位建议 ≤ {Math.round((market.guidance.max_position ?? 0.7) * 100)}% · {market.guidance.note}
            </div>
          )}
          {market?.dual_track_diverged && (
            <div className="text-[10px] text-amber-700 dark:text-amber-400 flex items-center gap-1">
              <AlertTriangle className="h-3 w-3" /> 沪深300与中证800判断不一致，推荐以 CSI800 为准
            </div>
          )}
        </div>

        <div className="flex items-start gap-2">
          <div className="rounded-md bg-primary/10 p-2">
            <Trophy className="h-5 w-5 text-primary" />
          </div>
          <div className="flex-1 min-w-0">
            <div className="font-semibold text-base">{primary.label ?? primary.strategy}</div>
            <div className="text-[11px] text-muted-foreground mt-0.5">
              同类状态历史夏普 {primary.sharpe ?? "—"} · {primary.sample_days ?? 0} 天样本
              {primary.ann_return_pct != null && ` · 年化 ${primary.ann_return_pct}%`}
              {rec?.hard_rule_match && " · 硬规则一致 ✓"}
            </div>
            {rec?.rationale && (
              <p className="text-[10px] text-muted-foreground mt-1 leading-relaxed">{rec.rationale}</p>
            )}
          </div>
        </div>

        {holdings && holdings.length > 0 && (
          <div className="rounded-md border bg-card/50 px-2 py-1.5">
            <div className="text-[10px] text-muted-foreground mb-1">
              {primary.sim_portfolio ? "模拟盘持仓" : "选股预览 Top"}
            </div>
            <div className="flex flex-wrap gap-1">
              {holdings.slice(0, 6).map((h) => (
                <span
                  key={h.code}
                  className="inline-flex items-center rounded bg-muted/60 px-1.5 py-0.5 text-[10px] font-mono"
                >
                  {h.name ?? h.code}
                </span>
              ))}
            </div>
          </div>
        )}

        {rec?.alternatives && rec.alternatives.length > 0 && (
          <div className="text-[10px] text-muted-foreground">
            备选：{rec.alternatives.map((a) => a.label ?? a.strategy).join(" · ")}
          </div>
        )}
        {rec?.avoid && (
          <div className="text-[10px] text-amber-700 dark:text-amber-400">
            回避：{rec.avoid.label ?? rec.avoid.strategy}
            {rec.avoid.sharpe != null && `（夏普 ${rec.avoid.sharpe}）`}
          </div>
        )}

        {rec?.jump_opinion?.available && (
          <div
            className={`rounded-md border px-2 py-1.5 space-y-1 ${
              rec.jump_opinion.aligned
                ? "bg-muted/30 border-border"
                : "bg-violet-500/5 border-violet-500/25"
            }`}
          >
            <div className="text-[10px] font-medium text-violet-800 dark:text-violet-300">
              Jump Model 第二意见
              {rec.jump_opinion.jump_penalty != null && (
                <span className="font-normal text-muted-foreground ml-1">
                  λ={rec.jump_opinion.jump_penalty}
                </span>
              )}
            </div>
            {rec.jump_opinion.aligned ? (
              <div className="text-[10px] text-muted-foreground">
                {rec.jump_opinion.note ?? "与规则 L1/L3 一致"}
              </div>
            ) : (
              <>
                {rec.jump_opinion.rationale && (
                  <p className="text-[10px] text-muted-foreground leading-relaxed">
                    {rec.jump_opinion.rationale}
                  </p>
                )}
                {rec.jump_opinion.primary && (
                  <div className="text-[10px]">
                    Jump 推荐：{" "}
                    <span className="font-medium">
                      {rec.jump_opinion.primary.label ?? rec.jump_opinion.primary.strategy}
                    </span>
                    {rec.jump_opinion.primary.sharpe != null && (
                      <span className="text-muted-foreground">
                        {" "}
                        · 夏普 {rec.jump_opinion.primary.sharpe}
                      </span>
                    )}
                  </div>
                )}
              </>
            )}
          </div>
        )}

        {monitoring && (monitoring.hit_rate_h5?.sample_count ?? 0) > 0 && (
          <div className="rounded-md border bg-muted/30 px-2 py-1.5 space-y-1">
            <div className="text-[10px] text-muted-foreground flex items-center gap-1">
              <Activity className="h-3 w-3" /> 推荐质量监控（相对 CSI800 超额）
            </div>
            <div className="flex flex-wrap gap-x-3 gap-y-0.5 text-[10px]">
              <span>
                5日命中 {monitoring.hit_rate_h5?.hit_rate_pct ?? "—"}%
                <span className="text-muted-foreground">
                  {" "}
                  (n={monitoring.hit_rate_h5?.sample_count}, 均超额{" "}
                  {fmtPct(monitoring.hit_rate_h5?.avg_excess_return_pct)})
                </span>
              </span>
              {(monitoring.hit_rate_h20?.sample_count ?? 0) > 0 && (
                <span>
                  20日命中 {monitoring.hit_rate_h20?.hit_rate_pct ?? "—"}%
                  <span className="text-muted-foreground">
                    {" "}
                    (n={monitoring.hit_rate_h20?.sample_count})
                  </span>
                </span>
              )}
            </div>
          </div>
        )}

        {monitoring?.recent_switches && monitoring.recent_switches.length > 0 && (
          <div className="text-[10px] text-muted-foreground space-y-0.5">
            <div>近期切换</div>
            {monitoring.recent_switches.slice(0, 3).map((sw) => (
              <div key={`${sw.trade_date}-${sw.note}`} className="font-mono truncate">
                {sw.trade_date} · {sw.note ?? `${sw.new_bucket_label} → ${sw.new_strategy}`}
              </div>
            ))}
          </div>
        )}

        <div className="flex justify-between items-center pt-1 border-t">
          <Link href="/market" className="text-[10px] text-muted-foreground hover:underline">
            矩阵详情 →
          </Link>
          <Link
            href={
              primary.sim_portfolio?.portfolio_id
                ? `/portfolio?id=${primary.sim_portfolio.portfolio_id}`
                : `/portfolio?strategy=${encodeURIComponent(primary.strategy ?? "")}`
            }
            className="text-[10px] text-primary flex items-center gap-0.5 hover:underline font-medium"
          >
            去模拟盘 <ChevronRight className="h-3 w-3" />
          </Link>
        </div>
      </CardContent>
    </Card>
  );
}
