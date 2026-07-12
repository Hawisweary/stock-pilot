/**
 * 市场行情数据 API 调用
 */

const API_BASE = "/api/market";

export const MARKET_INDEX_OPTIONS = [
  { code: "sh000001", name: "上证指数" },
  { code: "sz399001", name: "深证成指" },
  { code: "sh000300", name: "沪深300" },
  { code: "sz399006", name: "创业板指" },
] as const;

export type MarketIndexCode = (typeof MARKET_INDEX_OPTIONS)[number]["code"];

export type IndexKlineBar = {
  date: string;
  open: number | null;
  high: number | null;
  low: number | null;
  close: number | null;
  volume?: number | null;
};

export type IndexKlineResponse = {
  code: string;
  name: string;
  period: string;
  count: number;
  kline: IndexKlineBar[];
  technical: {
    date: string;
    macd_dif?: number | null;
    macd_dea?: number | null;
    macd_bar?: number | null;
    kdj_k?: number | null;
    kdj_d?: number | null;
    kdj_j?: number | null;
    rsi14?: number | null;
    boll_upper?: number | null;
    boll_mid?: number | null;
    boll_lower?: number | null;
    atr14?: number | null;
  }[];
  updated_at?: number;
  as_of_trade_date?: string | null;
  error?: string;
};

export async function syncWatchlistQuotes(): Promise<{
  stocks: number;
  synced: number;
  failed: number;
  latest_trade_date?: string;
}> {
  const res = await fetch(`${API_BASE}/sync-quotes`, { method: "POST", cache: "no-store" });
  if (!res.ok) throw new Error(`HTTP ${res.status}`);
  return res.json();
}

export type BoardRow = {
  code: string;
  name: string;
  change_pct: number;
  price: number;
  pe_ratio: number;
  pb_ratio: number;
  turnover_rate: number;
  market_cap: number;
  volume: number;
  amount: number;
};

export type BoardsResponse = {
  date: string;
  total: number;
  up_count: number;
  down_count: number;
  flat_count: number;
  avg_change_pct: number;
  top_gainers: BoardRow[];
  top_losers: BoardRow[];
  all_boards: BoardRow[];
};

export type LimitStatStock = {
  stock_id: number;
  code: string;
  name: string;
  price: number;
  change_pct: number;
};

export type LimitStatsCategory = "limit_up" | "limit_down" | "up_over_5pct" | "down_over_5pct";

export type LimitStatsResponse = {
  limit_up: number;
  limit_down: number;
  up_over_5pct: number;
  down_over_5pct: number;
  total: number;
  limit_up_stocks?: LimitStatStock[];
  limit_down_stocks?: LimitStatStock[];
  up_over_5pct_stocks?: LimitStatStock[];
  down_over_5pct_stocks?: LimitStatStock[];
};

export type MarketIndexRow = {
  name: string;
  code: string;
  /** 日线收盘价（技术指标同源） */
  close: number | null;
  /** 腾讯实时点位（盘中/收盘后当日） */
  last?: number | null;
  /** 单日涨跌幅：优先实时，否则最近两根日 K */
  change_1d_pct?: number | null;
  change_pct_today?: number | null;
  change_amt_today?: number | null;
  change_5d_pct: number | null;
  change_20d_pct: number | null;
  rsi14: number | null;
  macd_bar: number | null;
  ma5: number | null;
  ma20: number | null;
  weekly_rsi14: number | null;
  signal: "偏多" | "偏空" | "震荡";
};

export type MarketIndicesResponse = {
  updated_at: number;
  /** 当前日历日（Asia/Shanghai） */
  calendar_date?: string | null;
  /** 行情 K 线最后一根日期（日线） */
  as_of_trade_date?: string | null;
  /** 库内个股最新交易日（对齐参考） */
  expected_trade_date?: string | null;
  /** daily=仅日线收盘；realtime=点位已叠加腾讯实时 */
  quote_mode?: "daily" | "realtime";
  /** 指数行情落后于 expected 或回退旧缓存 */
  stale?: boolean;
  environment: "偏多" | "偏空" | "震荡";
  environment_comment: string;
  indices: MarketIndexRow[];
  available: boolean;
  error?: string;
};

export type MarketFetchOpts = {
  signal?: AbortSignal;
  /** 手动刷新时传 true，跳过后端短缓存 */
  force?: boolean;
};

function withRefreshQuery(path: string, force?: boolean): string {
  const sep = path.includes("?") ? "&" : "?";
  const bust = `_t=${Date.now()}`;
  return force ? `${path}${sep}force=1&${bust}` : `${path}${sep}${bust}`;
}

async function fetchJson<T>(url: string, opts?: MarketFetchOpts): Promise<T> {
  const res = await fetch(withRefreshQuery(url, opts?.force), {
    signal: opts?.signal,
    cache: "no-store",
  });
  if (!res.ok) throw new Error(`HTTP ${res.status}: ${res.statusText}`);
  return res.json();
}

export function fetchBoards(opts?: MarketFetchOpts): Promise<BoardsResponse> {
  return fetchJson<BoardsResponse>(`${API_BASE}/boards`, opts);
}

export function fetchLimitStats(opts?: MarketFetchOpts): Promise<LimitStatsResponse> {
  return fetchJson<LimitStatsResponse>(`${API_BASE}/limit-stats`, opts);
}

export async function fetchMarketIndices(opts?: MarketFetchOpts): Promise<MarketIndicesResponse> {
  const empty: MarketIndicesResponse = {
    updated_at: Math.floor(Date.now() / 1000),
    environment: "震荡",
    environment_comment: "指数数据暂不可用",
    indices: [],
    available: false,
    error: "fetch failed",
  };
  try {
    const res = await fetch(withRefreshQuery(`${API_BASE}/indices`, opts?.force), {
      signal: opts?.signal,
      cache: "no-store",
    });
    if (!res.ok) return { ...empty, error: `HTTP ${res.status}` };
    return (await res.json()) as MarketIndicesResponse;
  } catch (e) {
    return { ...empty, error: e instanceof Error ? e.message : "network error" };
  }
}

export function fetchMarketIndexKline(
  code: string,
  period: "daily" | "weekly" = "daily",
  days = 250,
  opts?: MarketFetchOpts,
): Promise<IndexKlineResponse> {
  const q = new URLSearchParams({
    code,
    period,
    days: String(days),
  });
  return fetchJson<IndexKlineResponse>(`${API_BASE}/indices/kline?${q}`, opts);
}
