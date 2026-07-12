/** 市场信号 API */

export type LhbRecord = {
  date: string;
  code?: string;
  name?: string;
  close?: number | null;
  change_pct?: number | null;
  turnover_pct?: number | null;
  net_buy?: number | null;
  buy_amount?: number | null;
  sell_amount?: number | null;
  deal_amount?: number | null;
  reason?: string;
  seats?: LhbSeat[];
  source?: string;
};

export type LhbSeat = {
  side: "buy" | "sell";
  name: string;
  net_amount?: number | null;
  buy_amount?: number | null;
  sell_amount?: number | null;
  reason?: string;
};

export type LhbDailyResponse = {
  date: string;
  requested_date?: string;
  count: number;
  items: LhbRecord[];
  source?: string | null;
  error?: string | null;
  note?: string | null;
};

export type LhbStockResponse = {
  code: string;
  date?: string;
  records: LhbRecord[];
  source?: string | null;
  error?: string | null;
};

export async function fetchDragonTigerDaily(
  date?: string,
  limit = 80,
  force?: boolean,
): Promise<LhbDailyResponse> {
  const q = new URLSearchParams({ limit: String(limit), _t: String(Date.now()) });
  if (date) q.set("date", date);
  if (force) q.set("force", "1");
  return json(`/api/signals/dragon-tiger?${q}`, { cache: "no-store" });
}

export async function fetchDragonTiger(code: string, opts?: { date?: string; days?: number; limit?: number }) {
  const q = new URLSearchParams();
  if (opts?.date) q.set("date", opts.date);
  if (opts?.days != null) q.set("days", String(opts.days));
  if (opts?.limit != null) q.set("limit", String(opts.limit));
  const qs = q.toString();
  return json<LhbStockResponse>(`/api/signals/dragon-tiger/${code}${qs ? `?${qs}` : ""}`);
}

export async function fetchUnlock(code: string, days = 90) {
  return json<{ code: string; unlocks: { date: string; shares: number; ratio: number }[] }>(
    `/api/signals/unlock/${code}?days=${days}`,
  );
}

export async function fetchMargin(code: string) {
  return json<{ code: string; history: { date: string; margin_balance?: number; margin_buy?: number }[] }>(
    `/api/signals/margin/${code}`,
  );
}

export async function fetchBlockTrade(code: string) {
  return json<{ code: string; trades: Record<string, unknown>[] }>(`/api/signals/block-trade/${code}`);
}

export async function fetchShareholders(code: string) {
  return json<{ code: string; history: Record<string, unknown>[] }>(`/api/signals/shareholders/${code}`);
}

export async function fetchDividends(code: string) {
  return json<{ code: string; history: Record<string, unknown>[] }>(`/api/signals/dividends/${code}`);
}

async function json<T>(url: string): Promise<T> {
  const res = await fetch(url, { cache: "no-store" });
  if (!res.ok) throw new Error(`${res.status}`);
  return res.json();
}
