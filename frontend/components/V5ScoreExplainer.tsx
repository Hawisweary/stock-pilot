"use client";

type V5Tier = Record<string, number | null | undefined>;

interface Props {
  tiers: V5Tier;
  composite: number | null;
  vetoReasons?: string[];
}

const TIER_PHRASES: Record<string, Record<number, string>> = {
  fundamental: { 2: "基本面极佳", 1: "基本面良好", 0: "基本面一般", [-1]: "基本面偏弱" },
  quality:     { 2: "质量因子极高", 1: "质量较好", 0: "质量中等", [-1]: "质量偏低" },
  valuation:   { 2: "估值极具吸引力", 1: "估值偏低", 0: "估值合理", [-1]: "估值偏贵" },
  technical:   { 2: "技术面强势", 1: "技术面偏强", 0: "技术面中性", [-1]: "技术面偏弱" },
  capital:     { 2: "主力资金大幅流入", 1: "资金面偏强", 0: "资金面中性", [-1]: "资金面偏弱" },
  policy:      { 2: "政策高度支持", 1: "政策面利好", 0: "政策面中性", [-1]: "政策面不利" },
  news:        { 2: "近期事件积极", 1: "新闻面偏正", 0: "新闻面中性", [-1]: "近期事件偏负" },
  mood:        { 2: "市场情绪亢奋", 1: "情绪偏暖", 0: "情绪中性", [-1]: "情绪偏冷" },
  industry:    { 2: "所在行业动能极强", 1: "行业景气偏好", 0: "行业动能中等", [-1]: "行业景气偏弱" },
  market_env:  { 2: "大盘环境极佳", 1: "市场环境偏好", 0: "市场环境中性", [-1]: "大盘环境偏弱" },
};

function phrase(dim: string, tier: number): string {
  return TIER_PHRASES[dim]?.[tier] ?? `${dim} tier${tier > 0 ? "+" : ""}${tier}`;
}

export function V5ScoreExplainer({ tiers, composite, vetoReasons }: Props) {
  const entries = Object.entries(tiers).filter(([, v]) => v != null) as [string, number][];

  const positives = entries.filter(([, v]) => v >= 1).sort(([, a], [, b]) => b - a).slice(0, 2);
  const negatives = entries.filter(([, v]) => v <= -1).sort(([, a], [, b]) => a - b).slice(0, 2);

  if (positives.length === 0 && negatives.length === 0) return null;

  const level =
    composite == null ? "中性"
    : composite >= 70 ? "强势"
    : composite >= 55 ? "偏强"
    : composite >= 40 ? "中性"
    : composite >= 25 ? "偏弱"
    : "弱势";

  const posPhrases = positives.map(([d, v]) => phrase(d, v));
  const negPhrases = negatives.map(([d, v]) => phrase(d, v));

  let summary = `综合评级 ${level}`;
  if (posPhrases.length > 0) summary += `，${posPhrases.join("、")}`;
  if (negPhrases.length > 0) summary += `；但${negPhrases.join("、")}`;
  summary += "。";

  return (
    <div className="rounded-lg border bg-muted/30 px-3 py-2.5 space-y-1.5">
      <p className="text-xs text-foreground leading-relaxed">{summary}</p>
      {vetoReasons && vetoReasons.length > 0 && (
        <p className="text-xs text-amber-700">
          ⚠ 一票否决：{vetoReasons.join("；")}
        </p>
      )}
    </div>
  );
}
