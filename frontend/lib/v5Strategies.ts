/** V5 策略选项 — 回测 / 模拟盘 / IC 共用 */
export const V5_STRATEGIES = [
  { id: "composite", label: "V5 综合" },
  { id: "fundamental", label: "基本面" },
  { id: "quality", label: "质量因子" },
  { id: "industry", label: "行业景气" },
  { id: "capital", label: "资金面" },
  { id: "valuation", label: "估值面" },
  { id: "technical", label: "技术面" },
  { id: "market_env", label: "大盘环境" },
  { id: "policy", label: "政策面" },
  { id: "news", label: "新闻面" },
  { id: "mood", label: "情绪面" },
] as const;

export const V5_IC_LABELS: Record<string, string> = {
  composite_v5: "V5 综合",
  quality_score: "质量因子",
  industry_score: "行业景气",
  market_env_score: "大盘环境",
  fundamental: "基本面",
  quality: "质量因子",
  industry: "行业景气",
  capital: "资金面",
  valuation: "估值面",
  technical: "技术面",
  market_env: "大盘环境",
  policy: "政策面",
  news: "新闻面",
  mood: "情绪面",
};

export const V5_IC_TO_STRATEGY: Record<string, string> = {
  composite_v5: "composite",
  quality_score: "quality",
  industry_score: "industry",
  market_env_score: "market_env",
  fundamental: "fundamental",
  quality: "quality",
  industry: "industry",
  capital: "capital",
  valuation: "valuation",
  technical: "technical",
  market_env: "market_env",
  policy: "policy",
  news: "news",
  mood: "mood",
};
