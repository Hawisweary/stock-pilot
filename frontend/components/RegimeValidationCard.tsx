"use client";

import { useEffect, useState } from "react";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { ClipboardCheck, CheckCircle2, AlertTriangle, XCircle } from "lucide-react";
import { api } from "@/lib/api";

type BucketStat = {
  label: string;
  days: number;
  mean_daily_return_pct?: number | null;
  sample_sufficient?: boolean;
};

type StrategyResult = {
  strategy: string;
  in_regime_days?: number;
  in_regime_sharpe?: number | null;
  out_regime_sharpe?: number | null;
  sharpe_lift?: number | null;
  effective?: boolean;
  sample_sufficient?: boolean;
  error?: string;
};

type ValidationReport = {
  sample_days?: number;
  primary_index?: string;
  data_source?: string;
  overall_verdict?: string;
  csi300_csi800_agreement?: {
    label_agreement_pct?: number;
    bucket_agreement_pct?: number;
    sample_days?: number;
  };
  layer1_internal_consistency?: {
    return_anova?: { p_value?: number; significant_05?: boolean };
    dwell_time?: { overall_mean_days?: number };
    bucket_stats?: BucketStat[];
    verdict?: string;
  };
  layer2_walk_forward?: {
    bucket_match_rate_pct?: number;
    horizon5_majority_match_pct?: number;
    verdict?: string;
  };
  layer2_forward_returns?: {
    forward_returns?: Record<string, { significant_05?: boolean; anova_p_value?: number }>;
  };
  layer3_strategy_conditional?: {
    match_mode?: string;
    effective_matches?: number;
    strategies_tested?: number;
    verdict?: string;
    results?: StrategyResult[];
  };
};

function StatusIcon({ ok }: { ok: boolean | undefined }) {
  if (ok === true) return <CheckCircle2 className="h-3.5 w-3.5 text-green-600" />;
  if (ok === false) return <XCircle className="h-3.5 w-3.5 text-destructive" />;
  return <AlertTriangle className="h-3.5 w-3.5 text-amber-600" />;
}

export function RegimeValidationCard({ compact = false }: { compact?: boolean }) {
  const [data, setData] = useState<ValidationReport | null>(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    setLoading(true);
    api
      .getMarketRegimeValidation({ days: 252, includeStrategy: false })
      .then((d) => setData(d as unknown as ValidationReport))
      .catch(() => setData(null))
      .finally(() => setLoading(false));
  }, [compact]);

  if (loading) {
    return (
      <Card>
        <CardHeader className="pb-2">
          <CardTitle className="text-sm flex items-center gap-2">
            <ClipboardCheck className="h-4 w-4" /> 状态划分验证
          </CardTitle>
        </CardHeader>
        <CardContent className="text-xs text-muted-foreground">加载中…</CardContent>
      </Card>
    );
  }

  if (!data) {
    return (
      <Card>
        <CardHeader className="pb-2">
          <CardTitle className="text-sm flex items-center gap-2">
            <ClipboardCheck className="h-4 w-4" /> 状态划分验证
          </CardTitle>
        </CardHeader>
        <CardContent className="text-xs text-muted-foreground">验证报告暂不可用</CardContent>
      </Card>
    );
  }

  const l1 = data.layer1_internal_consistency;
  const l2 = data.layer2_walk_forward;
  const l3 = data.layer3_strategy_conditional;
  const agree = data.csi300_csi800_agreement;
  const h5 = data.layer2_forward_returns?.forward_returns?.h5;

  return (
    <Card>
      <CardHeader className="pb-2 flex flex-row items-center justify-between gap-2">
        <CardTitle className="text-sm flex items-center gap-2">
          <ClipboardCheck className="h-4 w-4" /> 状态划分验证
        </CardTitle>
        <span className="text-[10px] text-muted-foreground font-mono">
          {data.sample_days}天 · {data.primary_index}
        </span>
      </CardHeader>
      <CardContent className="pt-0 space-y-3 text-xs">
        {agree?.sample_days ? (
          <div className="rounded-md bg-muted/40 px-2 py-1.5 text-[10px]">
            CSI300↔800 标签一致 {agree.label_agreement_pct}% · 四格一致 {agree.bucket_agreement_pct}%
          </div>
        ) : null}

        <div className="grid gap-2">
          <Row
            label="内部一致性（收益 ANOVA）"
            detail={`p=${l1?.return_anova?.p_value ?? "—"}`}
            ok={l1?.return_anova?.significant_05}
          />
          <Row
            label="Walk-Forward 桶匹配"
            detail={`${l2?.bucket_match_rate_pct ?? "—"}%`}
            ok={(l2?.bucket_match_rate_pct ?? 0) >= 60}
          />
          <Row
            label="前瞻 5 日收益区分"
            detail={`p=${h5?.anova_p_value ?? "—"}`}
            ok={h5?.significant_05}
          />
          {!compact && l3 ? (
            <Row
              label={`策略条件有效性 (${l3.match_mode})`}
              detail={`${l3.effective_matches}/${l3.strategies_tested} 有效`}
              ok={(l3.effective_matches ?? 0) >= Math.ceil((l3.strategies_tested ?? 1) / 2)}
            />
          ) : null}
        </div>

        {!compact && l1?.bucket_stats ? (
          <div className="grid grid-cols-2 gap-1.5">
            {l1.bucket_stats.map((b) => (
              <div key={b.label} className="rounded bg-muted/30 px-2 py-1 text-[10px]">
                <span className="font-medium">{b.label}</span> {b.days}天
                {!b.sample_sufficient && <span className="text-amber-600"> ⚠</span>}
              </div>
            ))}
          </div>
        ) : null}

        {!compact && l3?.results?.length ? (
          <div className="space-y-1 border-t pt-2">
            {l3.results.map((r) => (
              <div key={r.strategy} className="flex items-center justify-between text-[10px]">
                <span className="font-mono">{r.strategy}</span>
                {r.error ? (
                  <span className="text-muted-foreground">{r.error}</span>
                ) : (
                  <span>
                    in {r.in_regime_days}d · lift {r.sharpe_lift ?? "—"}{" "}
                    {r.effective ? "✅" : r.sample_sufficient ? "❌" : "⚠"}
                  </span>
                )}
              </div>
            ))}
          </div>
        ) : null}

        <div className="text-[10px] text-muted-foreground leading-relaxed border-t pt-2">
          {data.overall_verdict}
        </div>
      </CardContent>
    </Card>
  );
}

function Row({
  label,
  detail,
  ok,
}: {
  label: string;
  detail: string;
  ok?: boolean;
}) {
  return (
    <div className="flex items-center justify-between gap-2 rounded-md bg-muted/30 px-2 py-1.5">
      <div className="flex items-center gap-1.5 min-w-0">
        <StatusIcon ok={ok} />
        <span className="truncate">{label}</span>
      </div>
      <span className="font-mono text-[10px] shrink-0">{detail}</span>
    </div>
  );
}
