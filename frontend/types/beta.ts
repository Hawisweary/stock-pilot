export type BetaIssue = { level: string; module: string; msg: string };

export type RustBacktestStatus = {
  qars3_installed: boolean;
  qars3_version?: string | null;
  approved: boolean;
  available: boolean;
  engine_default: "python";
  message: string;
};

export type BetaHealth = {
  backtest_ready: boolean;
  ic_ready: boolean;
  portfolio_ready: boolean;
  universe_size: number;
  latest_quote_date?: string;
  latest_score_date?: string;
  score_history_days: number;
  factor_history_days: number;
  trade_days: number;
  rust_backtest?: RustBacktestStatus;
  issues: BetaIssue[];
  checked_at: string;
};

export type BetaMeta = {
  data_as_of?: string;
  universe_size?: number;
  score_history_days?: number;
  factor_history_days?: number;
  warnings?: string[];
};

export type BacktestParams = {
  days?: number;
  top_n?: number;
  lookback?: number;
  pos_style?: string;
  strategy?: string;
  min_score?: number;
  rebalance?: string;
  apply_costs?: boolean;
  apply_t1?: boolean;
  combination_id?: number;
  benchmark_mode?: "equal" | "csi300";
  sector_window?: number;
  save?: boolean;
  engine?: "python" | "rust";
};

export type BacktestResult = {
  error?: string;
  params?: Record<string, unknown>;
  total_return_pct?: number;
  annualized_return_pct?: number;
  max_drawdown_pct?: number;
  sharpe?: number;
  calmar?: number;
  win_rate_pct?: number;
  excess_return_pct?: number;
  benchmark?: { name: string; return_pct: number };
  benchmark_curve?: { date: string; value: number }[];
  daily_values?: { date: string; value: number }[];
  drawdowns?: { date: string; drawdown_pct: number }[];
  monthly_returns?: Record<string, number>;
  current_holdings?: { code: string; name: string; shares: number; weight_pct: number }[];
  recent_trades?: Record<string, unknown>[];
  elapsed_ms?: number;
  engine?: string;
  rust_fallback?: boolean;
  meta?: BetaMeta;
};

export type BacktestPreset = {
  id: string;
  label: string;
  params: BacktestParams;
};

export const BACKTEST_PRESETS: BacktestPreset[] = [
  { id: "composite-weekly", label: "V5 综合 Top5 周调仓", params: { strategy: "composite", top_n: 5, days: 90, rebalance: "weekly", min_score: 50 } },
  { id: "quality-value", label: "质量因子价值型", params: { strategy: "quality", top_n: 8, days: 120, rebalance: "monthly", min_score: 55 } },
  { id: "momentum-lab", label: "动量实验", params: { strategy: "momentum", top_n: 5, days: 60, lookback: 20, rebalance: "weekly" } },
  { id: "index-enhance", label: "指数增强 Top15", params: { strategy: "index_enhance", top_n: 15, days: 120, rebalance: "monthly", min_score: 50, benchmark_mode: "csi300" } },
  { id: "dual-ma", label: "双均线 F013", params: { strategy: "F013", top_n: 10, days: 90, rebalance: "weekly", min_score: 0 } },
  { id: "turtle", label: "海龟 20日突破", params: { strategy: "turtle", top_n: 5, days: 90, lookback: 20, rebalance: "weekly", min_score: 60 } },
  { id: "sector-rot", label: "行业轮动", params: { strategy: "sector_rotation", top_n: 10, days: 120, rebalance: "monthly", min_score: 0, sector_window: 5 } },
];

export type FactorIcRow = {
  mean_ic: number;
  mean_rank_ic?: number;
  ir: number;
  ic_positive_ratio: number;
  effectiveness: string;
  n_periods: number;
};

export type IcHeatmap = {
  period: number;
  forward_days: number[];
  matrix: Record<string, Record<string, number>>;
  meta?: BetaMeta;
};

export type PortfolioPosition = {
  code: string;
  name: string;
  shares: number;
  avg_cost: number;
  price?: number;
  price_source?: "realtime" | "eod_close";
  quote_date?: string;
  market_value: number;
  pnl: number;
  pnl_pct: number;
  weight_pct: number;
  buy_date?: string;
  sellable_shares: number;
  t1_locked: number;
  turtle_stop_price?: number | null;
};

export type TradeJournalEntry = {
  trade_date: string;
  code: string;
  name?: string;
  action: string;
  shares: number;
  price: number;
  commission?: number;
  tax?: number;
  cash_delta?: number;
  price_source?: string;
  quote_date?: string;
  raw_price?: number;
};

export type PricingContext = {
  mode: "intraday" | "eod" | "closed";
  can_trade: boolean;
  block_reason?: string | null;
  session_label: string;
  calendar_date: string;
  latest_quote_date: string;
  trade_date: string;
  slippage_pct: number;
  rules?: Record<string, string>;
};

export type FeePreview = {
  cash_delta: number;
  commission: number;
  tax: number;
  price?: number;
  price_label?: string;
  quote_date?: string;
  price_source?: string;
  can_trade?: boolean;
  block_reason?: string | null;
};

export type PortfolioSettings = {
  owner_id?: string;
  rebalance_schedule: "none" | "weekly" | "monthly";
  last_rebalance_date?: string;
  max_weight_pct: number;
  min_cash_pct: number;
  default_strategy: string;
  default_top_n: number;
  default_min_score: number;
  default_pos_style: "equal" | "weighted";
  default_combination_id?: number | null;
  default_lookback?: number;
  default_sector_window?: number;
  default_per_sector?: number;
};

export type PortfolioMetrics = {
  portfolio_id: number;
  total_return_pct: number;
  max_drawdown_pct: number;
  days_running: number;
  win_rate_pct: number;
  avg_hold_days: number;
  realized_pnl: number;
  closed_trades: number;
  total_trades: number;
};

export type PortfolioCompare = {
  portfolio_id: number;
  simulated_curve: { date: string; value: number }[];
  backtest_curve: { date: string; value: number }[];
  benchmark_curve?: { date: string; value: number }[];
  sim_return_pct: number;
  backtest_return_pct: number;
  gap_pct: number;
  params?: Record<string, unknown>;
  backtest_metrics?: Record<string, unknown>;
  error?: string;
};

export type BuildPreview = {
  portfolio_id: number;
  cash: number;
  preview: {
    code: string;
    name: string;
    score: number;
    price: number;
    weight_pct: number;
    shares: number;
    est_cost: number;
    price_label?: string;
    quote_date?: string;
    price_source?: string;
  }[];
  strategy: string;
  pos_style: string;
  pricing?: PricingContext;
};

export type PortfolioDetail = {
  id: number;
  name: string;
  cash: number;
  initial_cash: number;
  total_value: number;
  pnl: number;
  pnl_pct: number;
  created_at?: string;
  positions: PortfolioPosition[];
  journal: TradeJournalEntry[];
  history: { snapshot_date: string; total_value: number }[];
  settings?: PortfolioSettings;
  pricing?: PricingContext;
  error?: string;
};

export type PortfolioSummary = {
  id: number;
  name: string;
  cash: number;
  total_value: number;
  initial_cash?: number;
};

export type PortfolioBuildParams = {
  top_n: number;
  min_score: number;
  strategy?: string;
  pos_style?: "equal" | "weighted";
  combination_id?: number;
  lookback?: number;
  sector_window?: number;
  per_sector?: number;
};

export type CompareParams = {
  days?: number;
  top_n?: number;
  min_score?: number;
  strategy?: string;
  pos_style?: "equal" | "weighted";
  rebalance?: "daily" | "weekly" | "monthly";
  combination_id?: number;
  lookback?: number;
  sector_window?: number;
  per_sector?: number;
};

export { V5_STRATEGIES as PORTFOLIO_STRATEGIES } from "@/lib/v5Strategies";
