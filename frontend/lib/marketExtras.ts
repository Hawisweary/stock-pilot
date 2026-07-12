/** 宏观、轮动、基本面摘要、运维类 API */

export interface MacroIndicatorRow {
  date: string;
  gdp?: number | null;
  gdp_yoy?: number | null;
  cpi?: number | null;
  cpi_yoy?: number | null;
  pmi_manufacturing?: number | null;
  pmi_services?: number | null;
  lpr_1y?: number | null;
  lpr_5y?: number | null;
  m2?: number | null;
  m2_yoy?: number | null;
  shibor_overnight?: number | null;
  social_financing?: number | null;
  social_financing_yoy?: number | null;
  social_financing_mom?: number | null;
  bond_yield_10y?: number | null;
  usd_cnh?: number | null;
}

export interface SectorRotationStock {
  stock_id: number;
  code: string;
  name: string;
  return_5d: number;
  /** v3.0 权威分 */
  score?: number | null;
  composite_v5?: number | null;
  price?: number;
}

export interface SectorRotationItem {
  industry: string;
  signal: string;
  avg_return_5d: number;
  rel_strength: number;
  stock_count: number;
  stocks: SectorRotationStock[];
  /** @deprecated 兼容旧 UI，等同 avg_return_5d */
  score?: number;
  /** @deprecated 兼容旧 UI，等同 rel_strength */
  momentum?: number;
}

export interface SectorRotationResponse {
  date: string;
  as_of_trade_date?: string;
  base_trade_date?: string;
  window_trading_days?: number;
  pool_avg_return_5d?: number;
  method?: string;
  signal_rule?: string;
  add: SectorRotationItem[];
  reduce: SectorRotationItem[];
  all: SectorRotationItem[];
  error?: string;
}

export interface MarketFundamentals {
  stock_id: number;
  code: string;
  roe?: number | null;
  gm?: number | null;
  nm?: number | null;
  revenue_cagr_3y?: number | null;
  profit_cagr_3y?: number | null;
  error?: string;
}

async function json<T>(url: string, init?: RequestInit): Promise<T> {
  const res = await fetch(url, init);
  if (!res.ok) throw new Error(`${res.status}`);
  return res.json();
}

export async function fetchMacroIndicators(force?: boolean): Promise<MacroIndicatorRow[]> {
  const q = force ? `?force=1&_t=${Date.now()}` : `?_t=${Date.now()}`;
  const data = await json<{ indicators: MacroIndicatorRow[] }>(`/api/macro/indicators${q}`, {
    cache: "no-store",
  });
  return data.indicators || [];
}

export async function syncMacro(): Promise<Record<string, unknown>> {
  return json("/api/macro/sync", { method: "POST" });
}

export async function syncV5Data(): Promise<Record<string, unknown>> {
  return json("/api/v5/sync", { method: "POST", headers: { "Content-Type": "application/json" }, body: "{}" });
}

export async function fetchSectorRotation(force?: boolean): Promise<SectorRotationResponse> {
  const q = force ? `?force=1&_t=${Date.now()}` : `?_t=${Date.now()}`;
  return json(`/api/strategy/sector-rotation-signals${q}`, { cache: "no-store" });
}

export async function fetchMarketFundamentals(stockId: number): Promise<MarketFundamentals> {
  return json(`/api/market/fundamentals/${stockId}`);
}

export async function runFusionSync(): Promise<{ status: string }> {
  return json("/api/fusion/sync", { method: "POST" });
}

export async function generateDailyReview(): Promise<Record<string, unknown>> {
  return json("/api/review/generate", { method: "POST" });
}

export async function syncThsHotspots(): Promise<Record<string, unknown>> {
  return json("/api/signals/hotspots/sync", { method: "POST" });
}

export async function fetchThsHotspots(force?: boolean): Promise<
  { date?: string; code: string; name: string; reason?: string; change_pct?: number }[]
> {
  const q = force ? `?force=1&_t=${Date.now()}` : `?_t=${Date.now()}`;
  const data = await json<{ hotspots: { date?: string; code: string; name: string; reason?: string; change_pct?: number }[] }>(
    `/api/signals/hotspots${q}`,
    { cache: "no-store" },
  );
  return data.hotspots || [];
}

export async function fetchDimensionDetail(
  stockId: number,
  dim: "capital" | "policy" | "sentiment",
): Promise<Record<string, unknown>> {
  return json(`/api/stocks/${stockId}/${dim}`);
}

export async function fetchTechnicalCache(stockId: number): Promise<{
  cached: boolean;
  analysis: Record<string, unknown> | null;
  error?: string;
}> {
  return json(`/api/stocks/${stockId}/technical/cache`);
}

export async function fetchTechnicalWeekly(stockId: number): Promise<{ data: Record<string, unknown>[] }> {
  return json(`/api/stocks/${stockId}/technical/weekly?weeks=12`);
}
