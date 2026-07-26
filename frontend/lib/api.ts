/**
 * API 客户端 - 封装所有后端接口调用
 */

const API_BASE = "/api";
const API_BACKEND = process.env.NEXT_PUBLIC_API_URL || "http://localhost:8800";
const API_KEY = process.env.NEXT_PUBLIC_AFR_API_KEY || "";

function buildHeaders(extra?: HeadersInit): HeadersInit {
  const h: Record<string, string> = { "Content-Type": "application/json" };
  if (API_KEY) h["X-API-Key"] = API_KEY;
  if (extra && typeof extra === "object" && !Array.isArray(extra)) {
    Object.assign(h, extra as Record<string, string>);
  }
  return h;
}

// 简单内存缓存: key → { data, ts }
const cache = new Map<string, { data: unknown; ts: number }>();
const CACHE_TTL: Record<string, number> = {
  peers: 300_000, quarterly: 300_000, margin: 300_000,
  dividends: 300_000, technical: 300_000, ranking: 120_000,
  dashboard: 120_000,
};

function cacheKey(path: string): string {
  return path.replace(/\?.*/, ""); // strip query params for cache key
}

function getCache(key: string): unknown | null {
  const entry = cache.get(key);
  if (!entry) return null;
  for (const [prefix, ttl] of Object.entries(CACHE_TTL)) {
    if (key.includes(prefix) && Date.now() - entry.ts < ttl) return entry.data;
  }
  return null; // expired or no TTL match
}

function setCache(key: string, data: unknown) {
  cache.set(key, { data, ts: Date.now() });
}

function formatErrorDetail(detail: unknown): string {
  if (typeof detail === "string") return detail;
  if (Array.isArray(detail)) {
    return detail
      .map((item) => {
        if (item && typeof item === "object" && "msg" in item) {
          return String((item as { msg: string }).msg);
        }
        return String(item);
      })
      .join("; ");
  }
  if (detail && typeof detail === "object" && "message" in detail) {
    return String((detail as { message: string }).message);
  }
  return "";
}

type ApiRequestInit = RequestInit & { timeoutMs?: number };

async function request<T>(path: string, options?: ApiRequestInit): Promise<T> {
  const { timeoutMs = 30_000, ...fetchOptions } = options ?? {};
  // GET 请求先查缓存（V5 评分始终实时拉取）
  if (
    (!fetchOptions.method || fetchOptions.method === "GET") &&
    !path.includes("/v5/")
  ) {
    const key = cacheKey(path);
    const cached = getCache(key);
    if (cached !== null) return cached as T;
  }

  const isGet = !fetchOptions.method || fetchOptions.method === "GET";

  const attempt = async (): Promise<T> => {
    const controller = new AbortController();
    const timeoutId = setTimeout(() => controller.abort(), timeoutMs);

    try {
      const res = await fetch(`${API_BASE}${path}`, {
        ...fetchOptions,
        headers: buildHeaders(fetchOptions.headers as HeadersInit),
        signal: controller.signal,
      });
      clearTimeout(timeoutId);
      if (!res.ok) {
        const err = await res.json().catch(() => ({ detail: res.statusText }));
        let message = formatErrorDetail(err.detail) || res.statusText || `HTTP ${res.status}`;
        if (res.status >= 500 && /internal server error/i.test(message) && !formatErrorDetail(err.detail)) {
          message = "后端内部错误（请查看 launch.sh 日志或重启：./launch.sh start）";
        }
        const httpErr = new Error(message) as Error & { status?: number };
        httpErr.status = res.status;
        throw httpErr;
      }
      const data = await res.json();

      // 200 但含 truthy error 字段且非降级场景 → 抛出错误（SEC-OPS P0-5）
      if (
        data &&
        typeof data === "object" &&
        (data as Record<string, unknown>).error &&
        !(data as Record<string, unknown>).degraded
      ) {
        const errMsg =
          (data as Record<string, unknown>).message ||
          (data as Record<string, unknown>).error;
        throw new Error(typeof errMsg === "string" ? errMsg : String(errMsg));
      }

      // GET 请求缓存结果（V5 实时评分除外）
      if (isGet && !path.includes("/v5/")) {
        setCache(cacheKey(path), data);
      }

      return data as T;
    } catch (e: unknown) {
      clearTimeout(timeoutId);
      if (e instanceof DOMException && e.name === "AbortError") {
        const sec = Math.round(timeoutMs / 1000);
        throw new Error(`请求超时（${sec}s），请稍后重试`);
      }
      throw e;
    }
  };

  // 仅 GET（幂等）在瞬时故障时自动重试 2 次：后端重启期间的连接拒绝
  // (TypeError: Failed to fetch) 或 502/503/504 网关错误，避免页面因一次
  // 撞上重启窗口就空白/报错。POST 等写请求绝不重试（防止重复下单）。
  if (!isGet) return attempt();

  let lastErr: unknown;
  for (let i = 0; i < 3; i++) {
    try {
      return await attempt();
    } catch (e: unknown) {
      lastErr = e;
      const status = (e as { status?: number })?.status;
      const isNetworkFail = e instanceof TypeError; // fetch 连接失败
      // 500 也重试:后端批任务持写锁时读接口会短暂 database is locked → 500,
      // 属瞬时故障;GET 幂等,退避重试基本可命中锁释放的间隙
      const isServerErr = status === 500 || status === 502 || status === 503 || status === 504;
      if (i < 2 && (isNetworkFail || isServerErr)) {
        await new Promise((r) => setTimeout(r, 800 * (i + 1)));
        continue;
      }
      throw e;
    }
  }
  throw lastErr;
}

/** V5 重算完成后派发，各页面监听并刷新评分 */
export const V5_RECALC_EVENT = "afr:v5-recalculated";

const V5_RECALC_TS_KEY = "afr:v5-recalc-ts";

export function notifyV5Recalculated() {
  if (typeof window === "undefined") return;
  const ts = String(Date.now());
  try {
    localStorage.setItem(V5_RECALC_TS_KEY, ts);
  } catch {
    /* ignore */
  }
  window.dispatchEvent(new Event(V5_RECALC_EVENT));
  try {
    new BroadcastChannel(V5_RECALC_EVENT).postMessage(ts);
  } catch {
    /* ignore */
  }
}

export function getV5RecalcTimestamp(): number {
  if (typeof window === "undefined") return 0;
  try {
    return Number(localStorage.getItem(V5_RECALC_TS_KEY) || 0);
  } catch {
    return 0;
  }
}

function fetchV5ScoresBatch(market?: string, limit?: number) {
  const q = new URLSearchParams();
  q.set("_", String(Date.now()));
  if (market && market !== "ALL") q.set("market", market);
  if (limit) q.set("limit", String(limit));
  return request<{
    calc_date?: string;
    scores: V5ScoreRow[];
    count: number;
  }>(`/v5/scores/batch?${q.toString()}`);
}

/** 股票列表 + V5 分：单次合并接口，避免两次大请求 */
export async function loadStocksWithV5(market = "ALL") {
  const q = new URLSearchParams();
  q.set("_", String(Date.now()));
  if (market && market !== "ALL") q.set("market", market);
  const data = await request<{
    calc_date?: string;
    rows: (Stock & {
      composite_v5: number | null;
      veto_status: string | null;
      calc_date?: string;
    })[];
    count: number;
  }>(`/v5/stocks-with-scores?${q.toString()}`);
  const rows = (data.rows || []).map((r) => ({
    ...r,
    score: r.composite_v5,
    v5_calc_date: r.calc_date ?? data.calc_date ?? null,
  }));
  return { rows, calcDate: data.calc_date ?? null, count: data.count ?? rows.length };
}

/** 清除指定前缀的缓存（数据更新后调用） */
export function clearCache(prefix?: string) {
  if (!prefix) { cache.clear(); return; }
  for (const key of cache.keys()) {
    if (key.includes(prefix)) cache.delete(key);
  }
}

// ===== 类型定义 =====

export interface Stock {
  id: number;
  code: string;
  name: string;
  market: string;
  sector: string;
  industry: string;
  industry_sw?: string;
  industry_sw2?: string;
  industry_sw3?: string;
  concepts?: string[];
  list_date: string;
  /** v3.0 权威综合分（= composite_v5） */
  score?: number | null;
  composite_v5?: number | null;
  veto_status?: string | null;
  /** @deprecated v3.0: 读 score 字段 */
  composite_score?: number | null;
}

export interface StockDetail extends Stock {
  latest_scores: FactorScores | null;
  latest_indicators: Indicator | null;
}

export interface FactorScores {
  id: number;
  stock_id: number;
  calc_date: string;
  profitability_score: number;
  growth_score: number;
  safety_score: number;
  value_score: number;
  momentum_score?: number;
  composite_score: number;
}

export interface V5ScoreRow {
  stock_id: number;
  code: string;
  name: string;
  industry_sw?: string;
  calc_date?: string;
  /** v3.0 权威分（View 别名）*/
  score?: number | null;
  /** 兼容旧字段名（等同 score） */
  composite_v5?: number | null;
  veto_status?: string;
}

export interface ScoreRanking {
  rank: number;
  stock_id: number;
  code: string;
  name: string;
  /** v3.0 权威分（= composite_v5，来自 v_stock_scores 视图） */
  score?: number | null;
  /** @deprecated v3.0: 读 score 字段 */
  composite_score?: number | null;
  /** @deprecated v3.0: 读 score 字段 */
  composite_v5?: number | null;
  profitability_score?: number | null;
  growth_score?: number | null;
  safety_score?: number | null;
  value_score: number;
  momentum_score: number;
}

export interface FinancialReport {
  id: number;
  stock_id: number;
  period_end_date: string;
  report_type: string;
  revenue: number | null;
  net_profit: number | null;
  net_profit_parent: number | null;
  gross_profit: number | null;
  operating_profit: number | null;
  total_assets: number | null;
  total_equity: number | null;
  total_liabilities: number | null;
  eps: number | null;
  bvps: number | null;
  operating_cf: number | null;
}

export interface Indicator {
  id: number;
  stock_id: number;
  calc_date: string;
  pe_ttm: number | null;
  pb: number | null;
  roe: number | null;
  roa: number | null;
  gross_margin: number | null;
  net_margin: number | null;
  debt_to_equity: number | null;
  current_ratio: number | null;
  dividend_yield: number | null;
  market_cap: number | null;
}

export interface DailyQuote {
  id: number;
  stock_id: number;
  trade_date: string;
  open: number | null;
  high: number | null;
  low: number | null;
  close: number | null;
  volume: number | null;
  change_pct: number | null;
}

export interface DashboardOverview {
  stock_count: number;
  active_stocks: number;
  stale_stocks: number;
  stale_stock_list: { id: number; code: string; name: string; last_date: string }[];
  avg_composite_score: number;
  top_3_stocks: ScoreRanking[];
  last_update: string;
  data_quality?: {
    daily_quotes: number;
    financial_cover: number;
    scoring_cover: number;
    valuation_cover: number;
    last_sync?: string;
  } | null;
}

export interface ScoreSyncTrendPoint {
  date: string;
  sync_rate_required: number;
}

export interface ScoreSyncHealth {
  target_date: string;
  active_stocks_count: number;
  sync_rate_all: number;
  sync_rate_required: number;
  stocks_full_required: number;
  missing_total: number;
  stale_total?: number;
  gap_stale_days?: number;
  missing_by_dimension: Record<string, number>;
  gaps_by_dimension: Record<
    string,
    {
      ok: number;
      missing: number;
      no_source: number;
      stale?: number;
      required?: boolean;
      stock_ids_missing?: number[];
      stock_ids_no_source?: number[];
    }
  >;
  alert: {
    active: boolean;
    alert_key?: string;
    since?: string;
    duration_minutes?: number;
    channels_sent?: string[];
  };
  last_fill_job?: {
    job_id: string;
    status: string;
    sync_rate_required_after?: number;
    error?: string;
  } | null;
  trend_7d: ScoreSyncTrendPoint[];
  recommended_actions?: string[];
}

export interface BatchFillPlan {
  dry_run: boolean;
  mode: string;
  target_date: string;
  planned_actions?: Array<{
    priority: number;
    action: string;
    affected_stocks: number;
    estimated_ms_range?: [number, number];
    would_fetch?: number;
  }>;
  total_estimated_ms_range?: [number, number];
}

export interface BatchFillQueued {
  job_id: string;
  status: string;
  poll_url?: string;
}

export interface JobStatusResponse {
  id: string;
  status: string;
  result?: Record<string, unknown>;
  error?: string;
  heartbeat_at?: string;
}

export interface FetchResult {
  stock_id: number;
  code: string;
  name: string;
  quotes_count: number;
  financials_count: number;
  indicators_count: number;
  status: "success" | "partial" | "error" | string;
  errors?: { step: string; message: string }[];
  error?: string;
  duration_ms?: number;
  batch_fill_job_id?: string | null;
}

export interface FetchAllResponse {
  status: string;
  count?: number;
  message?: string;
  progress?: string;
  mode?: "incremental" | "full" | string;
  warning?: string;
}

export interface FetchStatusResponse {
  running: boolean;
  progress: string;
  started_at: string;
  finished: boolean;
  total?: number;
  /** 已处理只数（含 partial/error） */
  processed?: number;
  /** 完全成功只数 */
  success?: number;
  phase?: string;
  mode?: "incremental" | "full" | string;
  warning?: string;
  error?: string;
  /** 后端按股票数估算的最大等待秒数 */
  stale_after_sec?: number;
}

export interface SingleFetchJobStatus {
  ok?: boolean;
  running?: boolean;
  status: string;
  stock_id: number;
  quotes?: number;
  financials?: number;
  indicators?: number;
  errors?: { step: string; message: string }[];
  error?: string;
  batch_fill_job_id?: string | null;
}

export interface DeepPeersResponse {
  industry: string;
  industry_sw?: string;
  peer_count: number;
  market_cap_band?: number;
  self: Record<string, unknown>;
  peers: Record<string, unknown>[];
  percentiles: Record<string, number | null>;
  stats: Record<string, number | null>;
  summary: { strengths: string[]; weaknesses: string[] };
  error?: string;
}

export interface RagDocument {
  id: number;
  title: string;
  file_name: string;
  created_at: string;
  chunk_count: number;
}

export interface RagAnswer {
  answer: string;
  sources: Record<string, unknown>[];
  source: "llm" | "rules" | "none";
}

export interface DataStatus {
  stock_id: number;
  code: string;
  name: string;
  last_quote_date: string;
  last_report_date: string;
}

export interface AiAnalysis {
  id: number;
  stock_id: number;
  summary: string;
  strengths: string[];
  weaknesses: string[];
  factor_commentary: Record<string, string>;
  valuation_view: string;
  overall_rating: string;
  generated_at: string;
  source?: "llm" | "rules";
  mda?: Record<string, string>;
  risk?: Record<string, string>;
  financials?: Record<string, string>;
}

export interface TrendAlert {
  type: string;
  title: string;
  detail: string;
  severity: string;
  source?: "llm" | "rules";
}

export interface QuarterTrendRow {
  period_end_date?: string;
  report_type?: string;
  revenue?: number;
  net_profit?: number;
  net_profit_parent?: number;
  operating_cf?: number;
  revenue_yoy?: number | null;
  revenue_yoy_raw?: number | null;
  revenue_yoy_reliable?: boolean;
  revenue_yoy_note?: string | null;
  revenue_qoq?: number | null;
  profit_yoy?: number | null;
  profit_yoy_raw?: number | null;
  profit_yoy_reliable?: boolean;
  profit_yoy_note?: string | null;
  profit_yoy_change?: number | null;
  profit_qoq?: number | null;
}

export interface QuarterlyTrendsResponse {
  stock_id: number;
  data_granularity: "annual" | "quarterly";
  quarters: QuarterTrendRow[];
  alerts: TrendAlert[];
  alerts_source?: "llm" | "rules" | "none";
}

export interface PeerRow {
  id?: number;
  rank?: number;
  code: string;
  name: string;
  composite_score?: number;
  profitability_score?: number;
  growth_score?: number;
  safety_score?: number;
  value_score?: number;
  momentum_score?: number;
  is_current?: boolean;
  pe?: number;
  pb?: number;
  roe?: number;
  market_cap?: number;
  gross_margin?: number;
}

export interface PeersResponse {
  industry: string;
  industry_sw?: string;
  peers: PeerRow[];
}

// ===== API 方法 =====

export const api = {
  // 股票
  getStocks: () => request<Stock[]>("/stocks"),
  getStock: (id: number) => request<StockDetail>(`/stocks/${id}`),
  addStock: (code: string, market = "A") =>
    request<Stock & { fetch_status?: string; fetch_poll_url?: string }>(
      "/stocks",
      { method: "POST", body: JSON.stringify({ code, market }) },
    ),
  onboardStocks: (body: {
    codes: string[];
    market?: string;
    auto_score?: boolean;
    score_mode?: string;
    fetch_parallel?: number;
  }) =>
    request<{
      ok: boolean;
      job_id: string;
      poll_url: string;
      registered: { code: string; status: string; stock_id?: number }[];
      stock_ids: number[];
    }>("/stocks/onboard", {
      method: "POST",
      body: JSON.stringify(body),
    }),
  deleteStock: (id: number) =>
    request<{ ok: boolean }>(`/stocks/${id}`, { method: "DELETE" }),

  // 数据抓取（启动后台任务并轮询至完成）
  fetchStock: (
    id: number,
    opts?: { onProgress?: (msg: string) => void; signal?: AbortSignal },
  ) => startAndPollSingleFetch(id, opts),
  fetchStockStatus: (id: number) =>
    request<SingleFetchJobStatus>(`/data/fetch/${id}/status`),
  fetchAll: (mode: "incremental" | "full" = "incremental") =>
    request<FetchAllResponse>(`/data/fetch-all?mode=${mode}`, { method: "POST" }).then((r) => {
      clearCache();
      return r;
    }),
  fetchStatus: () => request<FetchStatusResponse>("/data/fetch-status"),
  dataStatus: () => request<DataStatus[]>("/data/status"),

  // 财务数据
  getFinancials: (id: number, years = 5) =>
    request<FinancialReport[]>(`/stocks/${id}/financials?years=${years}`),
  getIndicators: (id: number, days = 365) =>
    request<Indicator[]>(`/stocks/${id}/indicators?days=${days}`),
  getQuotes: (id: number, days = 365) =>
    request<DailyQuote[]>(`/stocks/${id}/quotes?days=${days}`),

  // 评分
  getRanking: async (limit = 20) => {
    const data = await request<ScoreRanking[] | { rankings: ScoreRanking[] }>(
      `/scores/ranking?limit=${limit}`,
    );
    return Array.isArray(data) ? data : data.rankings ?? [];
  },
  /** @deprecated v3.0: dual 模式已废弃，composite_v5 是唯一权威分，直接用 getRanking */
  getRankingDual: (limit = 20, _clientKey = "default") =>
    api.getRanking(limit).then((rankings) => ({ rankings, dual_mode: false })),
  getScoresBatch: (limit?: number) =>
    request<{
      comprehensive: Array<Record<string, unknown>>;
      count: number;
    }>(limit ? `/scores/batch?limit=${limit}` : "/scores/batch"),
  getScoreTrend: (id: number, days = 30) =>
    request<{ stock_id: number; metric?: string; trend: { date?: string; score?: number }[] }>(
      `/scores/trend/${id}?days=${days}`,
    ),
  postScoreSparkline: (stockIds: number[], days = 30) =>
    request<{ days: number; metric: string; series: Record<string, { date: string; score: number }[]> }>(
      "/scores/sparkline",
      { method: "POST", body: JSON.stringify({ stock_ids: stockIds, days, metric: "composite_v5" }) },
    ),
  getFeatureFlags: () => request<Record<string, boolean>>("/system/features"),
  getStockScores: (id: number) =>
    request<FactorScores[]>(`/stocks/${id}/scores`),
  recalculateScores: (benchmark: "industry" | "watchlist" = "industry") =>
    request<{ updated: number; status: string; benchmark_mode?: string }>(
      `/scores/recalculate?benchmark=${benchmark}`,
      { method: "POST" },
    ).then((r) => {
      clearCache();
      return r;
    }),

  getValuation: (stockId: number) =>
    request<{ latest: Record<string, unknown> | null; history: Record<string, unknown>[] }>(
      `/stocks/${stockId}/valuation`,
    ),

  // Dashboard
  dashboardOverview: () => request<DashboardOverview>("/dashboard/overview"),
  getScoreSyncHealth: (targetDate?: string) =>
    request<ScoreSyncHealth>(
      targetDate
        ? `/dashboard/score-sync-health?target_date=${encodeURIComponent(targetDate)}`
        : "/dashboard/score-sync-health",
    ),
  getScoreSyncTrend: (days = 7) =>
    request<{ days: number; trend: ScoreSyncTrendPoint[] }>(`/dashboard/score-sync-trend?days=${days}`),
  getScoreGaps: (targetDate?: string) =>
    request<Record<string, unknown>>(
      targetDate ? `/scores/gaps?target_date=${encodeURIComponent(targetDate)}` : "/scores/gaps",
    ),
  batchFillScores: (body: {
    mode: string;
    dry_run?: boolean;
    target_date?: string;
    stock_ids?: number[];
    skip_no_source?: boolean;
  }) =>
    request<BatchFillPlan | BatchFillQueued>("/scores/batch-fill", {
      method: "POST",
      body: JSON.stringify(body),
    }).then((r) => {
      clearCache();
      return r;
    }),
  getJobStatus: (jobId: string) => request<JobStatusResponse>(`/system/jobs/${jobId}`),
  mlTop: (limit = 10, horizon?: number) =>
    request<{
      enabled: boolean;
      horizon?: number;
      horizons_available?: number[];
      predictions: {
        code: string;
        name: string;
        score: number;
        model_version?: string;
        is_demo?: boolean;
      }[];
    }>(`/dashboard/ml-top?limit=${limit}${horizon != null ? `&horizon=${horizon}` : ""}`),
  qlibPredictions: (limit = 20) =>
    request<{
      enabled: boolean;
      validation?: Record<string, unknown>;
      predictions: {
        code: string;
        name: string;
        score: number;
        model_version?: string;
        is_demo?: boolean;
        composite_v5?: number | null;
      }[];
    }>(`/qlib/predictions?limit=${limit}`),
  qlibTrain: () => request<Record<string, unknown>>("/qlib/train", { method: "POST" }),
  topStocks: (limit = 5) => request<ScoreRanking[]>(`/dashboard/top-stocks?limit=${limit}`),

  // AI
  analyzeStock: (id: number, force = false) =>
    request<AiAnalysis>(`/ai/analyze/${id}`, {
      method: "POST",
      body: JSON.stringify({ force }),
    }),
  analysisHistory: (id: number) => request<AiAnalysis[]>(`/ai/history/${id}`),

  // 行业 / 季度
  getPeers: (id: number) => request<PeersResponse>(`/stocks/${id}/peers`),
  getQuarterlyTrends: (id: number, periods = 8) =>
    request<QuarterlyTrendsResponse>(`/stocks/${id}/quarterly?periods=${periods}`),

  getDeepPeers: (id: number, marketCapBand = 0.5) =>
    request<DeepPeersResponse>(
      `/analysis/${id}/deep-peers?market_cap_band=${marketCapBand}`,
    ),

  listRagDocuments: (stockId: number) =>
    request<{ documents: RagDocument[] }>(`/rag/stocks/${stockId}/documents`),

  uploadRagPdf: async (stockId: number, file: File) => {
    const form = new FormData();
    form.append("file", file);
    const headers: Record<string, string> = {};
    if (API_KEY) headers["X-API-Key"] = API_KEY;
    const res = await fetch(`${API_BASE}/rag/stocks/${stockId}/upload`, {
      method: "POST",
      headers,
      body: form,
    });
    if (!res.ok) {
      const err = await res.json().catch(() => ({ detail: res.statusText }));
      throw new Error(formatErrorDetail(err.detail) || res.statusText);
    }
    clearCache();
    return res.json();
  },

  askRag: (stockId: number, question: string, useLlm = true) =>
    request<RagAnswer>(`/rag/stocks/${stockId}/ask`, {
      method: "POST",
      body: JSON.stringify({ question, use_llm: useLlm }),
    }),

  fetchLogsSummary: () =>
    request<{ summary: Record<number, Record<string, FetchLogStep>> }>(
      "/data/fetch-logs-summary",
    ),

  fetchStepStatus: (stockId?: number) =>
    request<{ summary: Record<number, Record<string, FetchStepStatusEntry>> }>(
      stockId != null
        ? `/data/fetch-step-status?stock_id=${stockId}`
        : "/data/fetch-step-status",
    ),

  getFactorWeights: () =>
    request<{
      quality: number;
      growth: number;
      value: number;
      momentum: number;
      risk: number;
    }>("/scores/factor-weights"),

  updateFactorWeights: (weights: {
    quality: number;
    growth: number;
    value: number;
    momentum: number;
    risk: number;
  }) =>
    request<{ ok: boolean; weights: typeof weights }>("/scores/factor-weights", {
      method: "PUT",
      body: JSON.stringify(weights),
    }).then((r) => {
      clearCache();
      return r;
    }),

  reportExportUrl: (stockId: number) => `/api/stocks/${stockId}/report/export`,

  // ===== Beta 实验模块 =====
  getBetaHealth: () => request<import("@/types/beta").BetaHealth>("/system/beta-health"),

  backtestRun: (params: import("@/types/beta").BacktestParams) => {
    const q = new URLSearchParams();
    Object.entries(params).forEach(([k, v]) => {
      if (v !== undefined && v !== null) q.set(k, String(v));
    });
    return request<import("@/types/beta").BacktestResult>(`/backtest/run?${q}`);
  },

  backtestScan: (days = 90, strategy = "composite") =>
    request<{ results: Record<string, unknown>[]; best?: Record<string, unknown> }>(
      `/backtest/scan?days=${days}&strategy=${strategy}`,
    ),

  backtestRolling: (params: { window?: number; step?: number; top_n?: number; min_score?: number; strategy?: string }) => {
    const q = new URLSearchParams();
    Object.entries(params).forEach(([k, v]) => v != null && q.set(k, String(v)));
    return request<Record<string, unknown>>(`/backtest/rolling?${q}`);
  },

  backtestWalkForward: (trainDays = 60, testDays = 20, strategy = "composite") =>
    request<Record<string, unknown>>(
      `/backtest/walk-forward?train_days=${trainDays}&test_days=${testDays}&strategy=${strategy}`,
    ),

  backtestHistory: (limit = 10) =>
    request<{ runs: { id: number; created_at: string; params: Record<string, unknown>; total_return_pct?: number; sharpe?: number }[] }>(
      `/backtest/history?limit=${limit}`,
    ),

  factorIc: (period = 60, forwardDays = 20) =>
    request<{ factors: Record<string, import("@/types/beta").FactorIcRow>; meta?: import("@/types/beta").BetaMeta }>(
      `/strategy/factor-ic?period=${period}&forward_days=${forwardDays}`,
    ),

  factorIcHeatmap: (period = 60) =>
    request<import("@/types/beta").IcHeatmap>(`/strategy/factor-ic/heatmap?period=${period}`),

  factorsList: () => request<{ factors: Record<string, unknown>[] }>("/factors/list"),
  factorsHealth: () => request<{ factors: Record<string, { status: string; mean_ic: number | null; ir: number | null; significance: string | null }>; decayed_count: number; total: number }>("/factors/health"),

  factorAnalysis: (factorId: string, forwardDays = 20) =>
    request<Record<string, unknown>>(`/factors/${factorId}/analysis?forward_days=${forwardDays}`),

  factorDecay: (factorId: string, forwardDays = 20) =>
    request<Record<string, unknown>>(`/factors/${factorId}/decay?forward_days=${forwardDays}`),

  factorValues: (factorId: string) =>
    request<{ values: Record<string, unknown>[] }>(`/factors/values?factor_id=${factorId}`),

  factorsCompute: (mode: "full" | "incremental" = "full") =>
    request<{ factors_computed?: number; backfill?: boolean; mode?: string; cells_written?: number; stocks_touched?: number }>(
      `/factors/compute?mode=${mode}`,
      { method: "POST" },
    ).then((r) => {
      clearCache();
      return r;
    }),

  factorNeutralize: (factorId: string, outputName?: string) =>
    request<{ output_factor_id?: string; error?: string }>("/factors/neutralize", {
      method: "POST",
      body: JSON.stringify({ factor_id: factorId, output_name: outputName }),
    }).then((r) => {
      clearCache();
      return r;
    }),

  factorOrthogonalize: (factorIds: string[], namePrefix = "ortho") =>
    request<{ output_factors?: string[]; error?: string }>("/factors/orthogonalize", {
      method: "POST",
      body: JSON.stringify({ factor_ids: factorIds, name_prefix: namePrefix }),
    }).then((r) => {
      clearCache();
      return r;
    }),

  factorCorrelation: () => request<{ matrix: Record<string, Record<string, number>>; factors: string[] }>("/factors/correlation"),

  customFactorsList: () => request<{ factors: { factor_id: string; name: string; formula: string }[] }>("/factors/custom"),

  createCustomFactor: (name: string, formula: string) =>
    request<{ factor_id?: string; error?: string }>(`/factors/custom?name=${encodeURIComponent(name)}&formula=${encodeURIComponent(formula)}`, {
      method: "POST",
    }),

  validateFactorExpression: (formula: string) =>
    request<{ valid?: boolean; kind?: string; error?: string }>("/factors/expressions/validate", {
      method: "POST",
      body: JSON.stringify({ formula }),
    }),

  computeFactorExpression: (name: string, formula: string) =>
    request<{ factor_id?: string; error?: string; computed?: number }>("/factors/expressions/compute", {
      method: "POST",
      body: JSON.stringify({ name, formula }),
    }).then((r) => {
      clearCache();
      return r;
    }),

  factorGpRun: (opts?: { population?: number; generations?: number; async_mode?: boolean }) =>
    request<{ run_id?: number; winners?: Record<string, unknown>[]; job_id?: string; error?: string }>(
      "/factors/gp/run",
      {
        method: "POST",
        body: JSON.stringify({
          population: opts?.population ?? 12,
          generations: opts?.generations ?? 8,
          async_mode: opts?.async_mode ?? false,
        }),
      },
    ),

  factorGpRuns: (limit = 20) =>
    request<{ runs: Record<string, unknown>[] }>(`/factors/gp/runs?limit=${limit}`),

  factorMerge: (factorIds: string[], name: string, method: "equal" | "ic_ir" | "rolling_optimal" = "equal", saveCombination = false) =>
    request<Record<string, unknown>>("/factors/merge", {
      method: "POST",
      body: JSON.stringify({ factor_ids: factorIds, name, method, save_combination: saveCombination }),
    }),

  strategyList: (portfolioOnly = true) =>
    request<{ strategies: import("@/lib/strategies").StrategyOption[] }>(
      `/strategy/list?portfolio_only=${portfolioOnly ? "true" : "false"}`,
    ),

  factorCombinationsList: () =>
    request<{ combinations: Record<string, unknown>[] }>("/factors/combinations"),

  createFactorCombination: (body: {
    name: string;
    factor_ids: string[];
    weight_method?: string;
    materialize?: boolean;
  }) =>
    request<Record<string, unknown>>("/factors/combinations", {
      method: "POST",
      body: JSON.stringify(body),
    }),

  materializeFactorCombination: (comboId: number) =>
    request<Record<string, unknown>>(`/factors/combinations/${comboId}/materialize`, { method: "POST" }),

  icReview: () => request<Record<string, unknown>>("/system/ic-review"),

  factorMergePreset: () =>
    request<Record<string, unknown>>("/system/factor-merge/preset", { method: "POST" }).then((r) => {
      clearCache();
      return r;
    }),

  portfolioList: () => request<import("@/types/beta").PortfolioSummary[]>("/portfolio/portfolios"),

  portfolioGet: (id: number) => request<import("@/types/beta").PortfolioDetail>(`/portfolio/portfolios/${id}`),

  portfolioCreate: (name: string, cash = 100000) =>
    request<import("@/types/beta").PortfolioSummary>(`/portfolio/portfolios?name=${encodeURIComponent(name)}&cash=${cash}`, { method: "POST" }),

  portfolioDelete: (id: number) =>
    request<{ deleted: number }>(`/portfolio/portfolios/${id}`, { method: "DELETE" }),

  portfolioRename: (id: number, name: string) =>
    request<{ id: number; name: string }>(`/portfolio/portfolios/${id}`, {
      method: "PATCH",
      body: JSON.stringify({ name }),
    }),

  portfolioTrade: (id: number, body: { code: string; action: "buy" | "sell"; shares: number }) =>
    request<{ code: string; shares: number; commission?: number; tax?: number; cash_delta?: number; error?: string }>(
      `/portfolio/portfolios/${id}/trade`,
      { method: "POST", body: JSON.stringify(body) },
    ),

  portfolioBuildTop: (id: number, body: import("@/types/beta").PortfolioBuildParams) =>
    request<{ count: number; skipped?: { code: string; reason: string }[]; error?: string }>(
      `/portfolio/portfolios/${id}/build-top`,
      { method: "POST", body: JSON.stringify(body) },
    ),

  portfolioNavSeries: (id: number, days = 90) =>
    request<{ dates: string[]; nav: number[]; benchmark: (number | null)[]; base_value: number | null }>(
      `/portfolio/portfolios/${id}/nav-series?days=${days}`
    ),

  scorePercentileRanks: () =>
    request<{ pool_size: number; calc_date: string | null; ranks: Record<string, { raw: number; percentile: number; code: string; name: string }> }>(
      "/scores/percentile-ranks"
    ),

  portfolioMetrics: (id: number) =>
    request<import("@/types/beta").PortfolioMetrics>(`/portfolio/portfolios/${id}/metrics`),

  portfolioScoreAlerts: (id: number, threshold = 40) =>
    request<{
      alerts: { stock_id: number; code: string; name: string; shares: number; avg_cost: number; composite_v5: number | null; veto_status: string; veto_reasons: string; trigger: string }[];
      checked: number;
      threshold: number;
    }>(`/portfolio/portfolios/${id}/score-alerts?threshold=${threshold}`),

  portfolioRebalancePreview: (id: number) =>
    request<{
      due: boolean;
      days_left: number;
      schedule: string;
      strategy: string;
      preview_buy: { code: string; name: string; score: number }[];
      preview_sell: { code: string; name: string; shares: number }[];
      skip_next_rebalance: boolean;
      reason?: string;
    }>(`/portfolio/portfolios/${id}/rebalance-preview`),

  portfolioSkipRebalance: (id: number, skip: boolean) =>
    request<{ ok: boolean; skip_next_rebalance: boolean }>(
      `/portfolio/portfolios/${id}/skip-rebalance?skip=${skip}`,
      { method: "POST" },
    ),

  portfolioReplacePosition: (id: number, body: { sell_code: string; strategy?: string; min_score?: number }) =>
    request<{ sold: { code: string; shares: number }; bought: { code: string; name: string; shares: number; score: number }; warning?: string }>(
      `/portfolio/portfolios/${id}/replace-position`,
      { method: "POST", body: JSON.stringify(body) },
    ),

  portfolioCompare: (id: number, body: import("@/types/beta").CompareParams) =>
    request<import("@/types/beta").PortfolioCompare>(`/portfolio/portfolios/${id}/compare-backtest`, {
      method: "POST",
      body: JSON.stringify(body),
    }),

  portfolioBuildPreview: (id: number, body: import("@/types/beta").PortfolioBuildParams) =>
    request<import("@/types/beta").BuildPreview>(`/portfolio/portfolios/${id}/build-preview`, {
      method: "POST",
      body: JSON.stringify(body),
    }),

  portfolioEstimateFees: (id: number, code: string, action: string, shares: number) =>
    request<{
      price: number;
      raw_price?: number;
      quote_date?: string;
      price_source?: string;
      price_label?: string;
      commission: number;
      tax: number;
      cash_delta: number;
      can_trade?: boolean;
      block_reason?: string | null;
      error?: string;
    }>(
      `/portfolio/portfolios/${id}/estimate-fees?code=${encodeURIComponent(code)}&action=${action}&shares=${shares}`,
    ),

  portfolioUpdateSettings: (id: number, body: Partial<import("@/types/beta").PortfolioSettings> & { name?: string }) =>
    request<import("@/types/beta").PortfolioDetail>(`/portfolio/portfolios/${id}/settings`, {
      method: "PUT",
      body: JSON.stringify(body),
    }),

  portfolioTurtleExitsPreview: (id: number) =>
    request<{
      portfolio_id: number;
      lookback: number;
      exit_period: number;
      monitored: number;
      signal_count: number;
      signals: {
        code: string;
        name: string;
        shares: number;
        sellable_shares: number;
        price?: number;
        stop_price?: number;
        exit_reason?: "stop" | "channel";
      }[];
    }>(`/portfolio/portfolios/${id}/turtle-exits-preview`),

  portfolioTurtleExits: (id: number) =>
    request<{ exited: number; details: { code: string; name: string; shares: number; exit_reason?: string }[] }>(
      `/portfolio/portfolios/${id}/turtle-exits`,
      { method: "POST" },
    ),

  portfolioSyncTurtle: (
    id: number,
    opts?: { lookback?: number; top_n?: number; min_score?: number; rebalance_schedule?: string },
  ) => {
    const q = new URLSearchParams();
    if (opts?.lookback != null) q.set("lookback", String(opts.lookback));
    if (opts?.top_n != null) q.set("top_n", String(opts.top_n));
    if (opts?.min_score != null) q.set("min_score", String(opts.min_score));
    if (opts?.rebalance_schedule) q.set("rebalance_schedule", opts.rebalance_schedule);
    const suffix = q.toString() ? `?${q}` : "";
    return request<{
      portfolio_id: number;
      lookback: number;
      preview_buy: { code: string; name: string; score: number }[];
      pick_error?: string | null;
    }>(`/portfolio/portfolios/${id}/sync-turtle${suffix}`, { method: "POST" });
  },

  portfolioSyncSectorRotation: (
    id: number,
    opts?: { window_days?: number; per_sector?: number; top_n?: number; min_score?: number },
  ) => {
    const q = new URLSearchParams();
    if (opts?.window_days != null) q.set("window_days", String(opts.window_days));
    if (opts?.per_sector != null) q.set("per_sector", String(opts.per_sector));
    if (opts?.top_n != null) q.set("top_n", String(opts.top_n));
    if (opts?.min_score != null) q.set("min_score", String(opts.min_score));
    const suffix = q.toString() ? `?${q}` : "";
    return request<{
      portfolio_id: number;
      window_days: number;
      add_sectors: string[];
      reduce_sectors: string[];
      preview_buy: { code: string; name: string; score: number }[];
      preview_sell_codes: string[];
      pick_error?: string | null;
    }>(`/portfolio/portfolios/${id}/sync-sector-rotation${suffix}`, { method: "POST" });
  },

  portfolioExportUrl: (id: number) => `/api/portfolio/portfolios/${id}/export`,

  portfolioPricingContext: () =>
    request<import("@/types/beta").PricingContext>(`/portfolio/pricing-context`),

  stockSearch: (q: string) =>
    request<{ results?: { code: string; name: string; id: number }[] }>(`/stocks/search/by-name?q=${encodeURIComponent(q)}`),

  getV5Scores: (stockId: number) =>
    request<{ stock_id: number; v5: Record<string, unknown> }>(`/v5/scores/${stockId}`),

  getV5ScoresBatch: (opts?: { limit?: number; market?: string }) =>
    fetchV5ScoresBatch(opts?.market, opts?.limit),

  computeV5Scores: (body?: { stock_ids?: number[] }) =>
    request<Record<string, unknown>>("/v5/compute-scores", {
      method: "POST",
      body: JSON.stringify(body ?? {}),
    }).then((r) => {
      clearCache();
      notifyV5Recalculated();
      return r;
    }),

  getV5IcReport: (dimension = "composite_v5") =>
    request<Record<string, unknown>>(`/v5/ic-report?dimension=${encodeURIComponent(dimension)}`),

  // 数据质量 / 市场状态 / 波动率预测
  getDataQualitySummary: (tradeDate?: string) =>
    request<{
      trade_date: string;
      total_alerts: number;
      by_severity: Record<string, number>;
      top_alerts: Array<{ stock_id: number; code?: string; name?: string; anomaly_score: number; severity: string; flags: string[] }>;
    }>(
      tradeDate
        ? `/v5/data-quality/summary?trade_date=${encodeURIComponent(tradeDate)}`
        : "/v5/data-quality/summary",
    ),

  getDataQualityStock: (stockId: number, limit?: number) =>
    request<{
      stock_id: number;
      alerts: Array<{
        trade_date: string;
        anomaly_score: number;
        flags: string[];
        severity: string;
        created_at?: string;
      }>;
    }>(`/v5/data-quality/stock/${stockId}${limit ? `?limit=${limit}` : ""}`),

  detectDataQuality: (tradeDate?: string) =>
    request<{
      trade_date: string;
      total_alerts: number;
      critical: number;
      warning: number;
      info: number;
    }>(
      tradeDate
        ? `/v5/data-quality/detect?trade_date=${encodeURIComponent(tradeDate)}`
        : "/v5/data-quality/detect",
      { method: "POST" },
    ),

  getMarketRegime: (tradeDate?: string) =>
    request<Record<string, unknown>>(
      tradeDate
        ? `/v5/market-regime?trade_date=${encodeURIComponent(tradeDate)}`
        : "/v5/market-regime",
    ),

  syncMarketRegime: (tradeDate?: string) =>
    request<Record<string, unknown>>(
      tradeDate
        ? `/v5/market-regime/sync?trade_date=${encodeURIComponent(tradeDate)}`
        : "/v5/market-regime/sync",
      { method: "POST" },
    ),

  getVolatilityForecast: (tradeDate?: string) =>
    request<{
      trade_date: string;
      total_records: number;
      avg_realized_vol_20: number;
      avg_forecast_vol_20: number;
      avg_turnover_20: number;
      avg_amount_20: number;
      avg_amihud_illiq_20: number;
      top_volatility: Array<{
        stock_id: number;
        code?: string;
        name?: string;
        realized_vol_20: number;
        forecast_vol_20: number;
        avg_turnover_20: number;
      }>;
    }>(
      tradeDate
        ? `/v5/volatility-forecast?trade_date=${encodeURIComponent(tradeDate)}`
        : "/v5/volatility-forecast",
    ),

  getVolatilityForecastStock: (stockId: number, limit?: number) =>
    request<{
      stock_id: number;
      forecasts: Array<{
        trade_date: string;
        realized_vol_20: number;
        realized_vol_60: number;
        avg_turnover_20: number;
        avg_amount_20: number;
        amihud_illiq_20: number;
        forecast_vol_20: number;
        forecast_horizon: number;
        forecast_method: string;
      }>;
    }>(`/v5/volatility-forecast/${stockId}${limit ? `?limit=${limit}` : ""}`),

  syncVolatilityForecast: (tradeDate?: string) =>
    request<Record<string, unknown>>(
      tradeDate
        ? `/v5/volatility-forecast/sync?trade_date=${encodeURIComponent(tradeDate)}`
        : "/v5/volatility-forecast/sync",
      { method: "POST" },
    ),
};

export interface FetchLogStep {
  data_type: string;
  status: string;
  records_count: number;
  error_message: string;
  fetch_time: string;
  source?: string;
}

export interface FetchStepStatusEntry {
  status: "success" | "skipped" | "error" | string;
  message: string;
  updated_at?: string;
}

/** 单股 V5 评分 */
export async function scoreStockUntilDone(
  stockId: number,
  onProgress?: (msg: string) => void,
  _signal?: AbortSignal,
): Promise<void> {
  onProgress?.("V5 评分中…");
  await api.computeV5Scores({ stock_ids: [stockId] });
}

/** 轮询单股 batch-fill（抓取完成后自动入队的 job） */
export async function pollFetchScoreJob(
  batchFillJobId: string,
  onProgress?: (msg: string) => void,
  signal?: AbortSignal,
): Promise<void> {
  await pollBatchFillUntilDone(batchFillJobId, onProgress, 2000, signal);
}

/** 抓取完成后触发 V5 评分（忽略旧版八维 batch-fill job） */
export async function afterFetchWaitForScore(
  stockId: number,
  _batchFillJobId: string | null | undefined,
  onProgress?: (msg: string) => void,
  signal?: AbortSignal,
): Promise<void> {
  if (signal?.aborted) throw new DOMException("Aborted", "AbortError");
  await scoreStockUntilDone(stockId, onProgress, signal);
}

/** 轮询单股抓取直到完成 */
export async function pollSingleFetchUntilDone(
  stockId: number,
  onProgress?: (msg: string) => void,
  intervalMs = 2000,
  signal?: AbortSignal,
  maxPolls = 120,
): Promise<FetchResult> {
  for (let i = 0; i < maxPolls; i++) {
    if (signal?.aborted) {
      throw new DOMException("Aborted", "AbortError");
    }
    const job = await request<SingleFetchJobStatus>(`/data/fetch/${stockId}/status`);
    if (job.running || job.status === "pending") {
      onProgress?.("抓取中...");
      await new Promise((r) => setTimeout(r, intervalMs));
      continue;
    }
    if (job.status === "success" || job.status === "partial") {
      clearCache();
      return {
        stock_id: stockId,
        code: "",
        name: "",
        quotes_count: job.quotes ?? 0,
        financials_count: job.financials ?? 0,
        indicators_count: job.indicators ?? 0,
        status: job.status,
        errors: job.errors,
        batch_fill_job_id: job.batch_fill_job_id,
      };
    }
    if (job.status === "error") {
      throw new Error(job.error || "抓取失败");
    }
    if (job.status === "not_started" && i > 5) {
      throw new Error("未检测到抓取任务，请重试");
    }
    await new Promise((r) => setTimeout(r, intervalMs));
  }
  throw new Error("单股抓取超时（约4分钟），请稍后重试");
}

async function startAndPollSingleFetch(
  id: number,
  opts?: { onProgress?: (msg: string) => void; signal?: AbortSignal },
): Promise<FetchResult> {
  await request<{ ok: boolean; status: string; stock_id: number }>(
    `/data/fetch/${id}`,
    { method: "POST" },
  );
  clearCache();
  return pollSingleFetchUntilDone(id, opts?.onProgress, 2000, opts?.signal);
}

function parseProgressTotal(progress: string): number | null {
  const m = progress.match(/\/(\d+)\s*$/);
  return m ? parseInt(m[1], 10) : null;
}

/** 按股票数估算批量抓取轮询上限（与后端 stale_after_sec 公式一致） */
export function estimateFetchMaxPolls(
  stockCount: number,
  intervalMs = 2000,
  staleAfterSec?: number,
): number {
  const sec =
    staleAfterSec ??
    (() => {
      const n = Math.max(stockCount, 1);
      const parallel = 2;
      const estimated = Math.ceil(n / parallel) * 55 + 300;
      return Math.min(7200, Math.max(900, estimated));
    })();
  return Math.ceil((sec * 1000) / intervalMs);
}

/** 轮询批量抓取直到完成（等待上限随本次任务股票数自动伸缩） */
export async function pollFetchUntilDone(
  onProgress?: (progress: string) => void,
  intervalMs = 2000,
  signal?: AbortSignal,
  maxPolls?: number,
): Promise<FetchStatusResponse> {
  let limit = maxPolls;
  for (let i = 0; ; i++) {
    if (signal?.aborted) {
      throw new DOMException("Aborted", "AbortError");
    }
    const status = await api.fetchStatus();
    if (limit == null) {
      const total =
        status.total ??
        parseProgressTotal(status.progress) ??
        1;
      limit = estimateFetchMaxPolls(total, intervalMs, status.stale_after_sec);
    }
    const label = status.phase ? `${status.phase} ${status.progress}` : status.progress;
    onProgress?.(label.trim());
    if (!status.running) return status;

    if (i >= limit) {
      const last = await api.fetchStatus();
      if (last.running) {
        return {
          ...last,
          error: "poll_timeout_background_running",
        };
      }
      break;
    }
    await new Promise((r) => setTimeout(r, intervalMs));
  }
  throw new Error(
    "数据抓取超时（后台可能仍在进行），请稍后刷新数据页查看进度，勿重复点击批量抓取",
  );
}

/** 可取消的轮询（组件卸载时 abort） */
export function startFetchPoll(
  onProgress: (progress: string) => void,
  onDone: () => void,
  onError: (err: Error) => void
): () => void {
  const ac = new AbortController();
  pollFetchUntilDone(onProgress, 2000, ac.signal)
    .then(() => onDone())
    .catch((e) => onError(e instanceof Error ? e : new Error(String(e))));
  return () => ac.abort();
}

/** 通用 job 轮询 */
export async function pollJobUntilDone(
  jobId: string,
  options?: {
    onProgress?: (msg: string) => void;
    intervalMs?: number;
    signal?: AbortSignal;
    maxPolls?: number;
    formatProgress?: (job: JobStatusResponse) => string | null;
    timeoutMessage?: string;
    doneMessage?: string;
  },
): Promise<JobStatusResponse> {
  const intervalMs = options?.intervalMs ?? 2000;
  const maxPolls = options?.maxPolls ?? 300;
  const timeoutMessage = options?.timeoutMessage ?? "任务超时，请稍后在 Dashboard 查看 job 状态";

  for (let i = 0; i < maxPolls; i++) {
    if (options?.signal?.aborted) {
      throw new DOMException("Aborted", "AbortError");
    }
    const job = await api.getJobStatus(jobId);
    if (job.status === "running" || job.status === "pending" || job.status === "queued") {
      const custom = options?.formatProgress?.(job);
      if (custom) {
        options?.onProgress?.(custom);
      } else {
        options?.onProgress?.(
          job.status === "running"
            ? `进行中…${job.heartbeat_at ? ` (心跳 ${job.heartbeat_at.slice(11, 19)})` : ""}`
            : "排队等待…",
        );
      }
      await new Promise((r) => setTimeout(r, intervalMs));
      continue;
    }
    if (job.status === "done") {
      clearCache();
      return job;
    }
    if (job.status === "failed" || job.status === "cancelled") {
      throw new Error(job.error || `任务 ${job.status}`);
    }
    await new Promise((r) => setTimeout(r, intervalMs));
  }
  throw new Error(timeoutMessage);
}

/** 格式化 onboard job 进度文案 */
export function formatOnboardProgress(job: JobStatusResponse): string | null {
  const progress = job.result?.progress as
    | { phase?: string; message?: string; done?: number; total?: number }
    | undefined;
  if (progress?.phase && progress.phase !== "done") {
    const frac =
      progress.total && progress.done != null
        ? ` ${progress.done}/${progress.total}`
        : "";
    return `${progress.message || progress.phase}${frac}`;
  }
  if (job.status === "running") return "Onboard 进行中…";
  if (job.status === "pending") return "Onboard 排队中…";
  return null;
}

/** 轮询 onboard job 直到完成 */
export async function pollOnboardUntilDone(
  jobId: string,
  onProgress?: (msg: string) => void,
  signal?: AbortSignal,
): Promise<JobStatusResponse> {
  return pollJobUntilDone(jobId, {
    onProgress,
    signal,
    maxPolls: 300,
    timeoutMessage: "Onboard 超时，请稍后在系统任务中查看",
    formatProgress: formatOnboardProgress,
  });
}

/** 轮询 batch-fill job 直到完成 */
export async function pollBatchFillUntilDone(
  jobId: string,
  onProgress?: (msg: string) => void,
  intervalMs = 2000,
  signal?: AbortSignal,
  maxPolls = 180,
): Promise<JobStatusResponse> {
  return pollJobUntilDone(jobId, {
    onProgress,
    intervalMs,
    signal,
    maxPolls,
    timeoutMessage: "补算任务超时，请稍后在 Dashboard 查看 job 状态",
    formatProgress: (job) =>
      job.status === "running"
        ? `补算进行中…${job.heartbeat_at ? ` (心跳 ${job.heartbeat_at.slice(11, 19)})` : ""}`
        : "排队等待…",
  });
}

// ===== U1-3: 批量 Sparkline =====

export interface SparklinePoint { date: string; score: number }
export type SparklineSeries = Record<string, SparklinePoint[]>;

export async function postSparkline(
  stockIds: number[],
  days = 30,
): Promise<SparklineSeries> {
  const data = await request<{ series: SparklineSeries }>("/scores/sparkline", {
    method: "POST",
    body: JSON.stringify({ stock_ids: stockIds, days, metric: "composite_v5" }),
  });
  return data.series ?? {};
}
