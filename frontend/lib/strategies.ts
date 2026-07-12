/** 策略选项 — 优先从 API 加载，失败时回退 V5 列表 */
import { V5_STRATEGIES } from "@/lib/v5Strategies";

export type StrategyOption = {
  id: string;
  label: string;
  kind?: string;
  requires_combination_id?: boolean;
  default_top_n?: number;
  default_min_score?: number;
  default_rebalance?: string;
};

export const FALLBACK_STRATEGIES: StrategyOption[] = [
  ...V5_STRATEGIES.map((s) => ({ id: s.id, label: s.label, kind: "v5" })),
  { id: "index_enhance", label: "指数增强", kind: "v5", default_top_n: 15 },
  { id: "momentum", label: "动量因子", kind: "momentum", default_top_n: 5, default_min_score: 50 },
  { id: "dual_ma", label: "双均线", kind: "factor", default_top_n: 10, default_min_score: 0 },
  { id: "turtle", label: "海龟交易", kind: "turtle", default_top_n: 5, default_min_score: 60 },
  { id: "sector_rotation", label: "行业轮动", kind: "sector", default_top_n: 10, default_min_score: 0 },
  { id: "factor_combination", label: "合成因子方案", kind: "combo", requires_combination_id: true },
];

export type PortfolioPreset = {
  id: string;
  label: string;
  strategy: string;
  top_n: number;
  min_score: number;
  lookback?: number;
  sector_window?: number;
  per_sector?: number;
  pos_style?: "equal" | "weighted";
  rebalance_schedule?: "none" | "weekly" | "monthly";
};

export const PORTFOLIO_PRESETS: PortfolioPreset[] = [
  { id: "composite", label: "V5 综合", strategy: "composite", top_n: 5, min_score: 50, rebalance_schedule: "weekly" },
  { id: "turtle", label: "海龟 20日", strategy: "turtle", top_n: 5, min_score: 60, lookback: 20, rebalance_schedule: "weekly" },
  { id: "momentum", label: "动量", strategy: "momentum", top_n: 5, min_score: 50, lookback: 20, rebalance_schedule: "weekly" },
  { id: "sector", label: "行业轮动", strategy: "sector_rotation", top_n: 10, min_score: 0, sector_window: 5, per_sector: 2, rebalance_schedule: "monthly" },
  { id: "index", label: "指数增强", strategy: "index_enhance", top_n: 15, min_score: 50, rebalance_schedule: "monthly" },
];

const PRESET_LOOKBACK: Record<string, number> = {
  turtle: 20,
  momentum: 20,
};

export function applyStrategyDefaults(
  strategyId: string,
  strategies: StrategyOption[],
): { top_n?: number; min_score?: number; lookback?: number } {
  const meta = strategies.find((s) => s.id === strategyId);
  if (!meta) return {};
  return {
    top_n: meta.default_top_n,
    min_score: meta.default_min_score,
    lookback: PRESET_LOOKBACK[strategyId],
  };
}

export function applyPortfolioPreset(preset: PortfolioPreset) {
  return {
    buildStrategy: preset.strategy,
    buildN: preset.top_n,
    buildMin: preset.min_score,
    buildLookback: preset.lookback ?? 20,
    buildSectorWindow: preset.sector_window ?? 5,
    buildPerSector: preset.per_sector ?? 2,
    buildStyle: preset.pos_style ?? ("equal" as const),
    settings: {
      default_strategy: preset.strategy,
      default_top_n: preset.top_n,
      default_min_score: preset.min_score,
      default_lookback: preset.lookback,
      default_sector_window: preset.sector_window,
      default_per_sector: preset.per_sector,
      default_pos_style: preset.pos_style ?? "equal",
      rebalance_schedule: preset.rebalance_schedule,
    },
  };
}
