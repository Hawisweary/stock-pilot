"use client";

import { useEffect, useState, useCallback } from "react";
import Link from "next/link";
import { ArrowBigUp, ArrowBigDown, RefreshCw, AlertCircle, TrendingUp, TrendingDown } from "lucide-react";
import { Card, CardHeader, CardTitle, CardContent } from "@/components/ui/card";
import {
  fetchLimitStats,
  type LimitStatsCategory,
  type LimitStatsResponse,
  type LimitStatStock,
} from "@/lib/market";

const POLL_INTERVAL = 60_000;

const CATEGORIES: {
  key: LimitStatsCategory;
  label: string;
  countKey: keyof Pick<
    LimitStatsResponse,
    "limit_up" | "limit_down" | "up_over_5pct" | "down_over_5pct"
  >;
  listKey: keyof Pick<
    LimitStatsResponse,
    | "limit_up_stocks"
    | "limit_down_stocks"
    | "up_over_5pct_stocks"
    | "down_over_5pct_stocks"
  >;
  tone: "up" | "down";
}[] = [
  { key: "limit_up", label: "涨停", countKey: "limit_up", listKey: "limit_up_stocks", tone: "up" },
  { key: "limit_down", label: "跌停", countKey: "limit_down", listKey: "limit_down_stocks", tone: "down" },
  { key: "up_over_5pct", label: "涨幅>5%", countKey: "up_over_5pct", listKey: "up_over_5pct_stocks", tone: "up" },
  { key: "down_over_5pct", label: "跌幅>5%", countKey: "down_over_5pct", listKey: "down_over_5pct_stocks", tone: "down" },
];

function pctClass(v: number, tone: "up" | "down") {
  if (tone === "up") return v >= 9.8 ? "text-red-600 font-semibold" : "text-red-500";
  return v <= -9.8 ? "text-green-600 font-semibold" : "text-green-500";
}

function StockList({ items, tone }: { items: LimitStatStock[]; tone: "up" | "down" }) {
  if (!items.length) {
    return <div className="text-xs text-muted-foreground py-3 text-center">暂无</div>;
  }
  return (
    <div className="max-h-52 overflow-y-auto divide-y divide-border/60 rounded-md border border-border/60">
      {items.map((s) => (
        <Link
          key={s.stock_id}
          href={`/stocks/${s.stock_id}`}
          className="flex items-center justify-between gap-2 px-2.5 py-1.5 text-xs hover:bg-muted/60 transition-colors"
        >
          <span className="min-w-0 flex-1">
            <div className="font-medium truncate">{s.name}</div>
            <div className="text-[10px] text-muted-foreground font-mono">{s.code}</div>
          </span>
          <span className="shrink-0 tabular-nums text-right leading-tight">
            <div className="text-muted-foreground">¥{s.price.toFixed(2)}</div>
            <div className={pctClass(s.change_pct, tone)}>
              {s.change_pct > 0 ? "+" : ""}
              {s.change_pct.toFixed(2)}%
            </div>
          </span>
        </Link>
      ))}
    </div>
  );
}

export function LimitStatsCard({ refreshKey = 0 }: { refreshKey?: number }) {
  const [data, setData] = useState<LimitStatsResponse | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [active, setActive] = useState<LimitStatsCategory>("limit_up");

  const load = useCallback(async (force = false) => {
    try {
      setLoading(true);
      setError(null);
      const d = await fetchLimitStats({ force });
      setData(d);
      setActive((prev) => {
        const counts = {
          limit_up: d.limit_up,
          limit_down: d.limit_down,
          up_over_5pct: d.up_over_5pct,
          down_over_5pct: d.down_over_5pct,
        };
        if (counts[prev] > 0) return prev;
        const first = CATEGORIES.find((c) => counts[c.key] > 0);
        return first?.key ?? "limit_up";
      });
    } catch (e) {
      setError(e instanceof Error ? e.message : "获取涨跌停统计失败");
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    load(refreshKey > 0);
    const timer = setInterval(() => load(false), POLL_INTERVAL);
    return () => clearInterval(timer);
  }, [load, refreshKey]);

  const activeMeta = CATEGORIES.find((c) => c.key === active)!;
  const activeList = (data?.[activeMeta.listKey] as LimitStatStock[] | undefined) ?? [];

  if (loading && !data) {
    return (
      <Card>
        <CardHeader><CardTitle className="text-base">涨跌停统计</CardTitle></CardHeader>
        <CardContent>
          <div className="animate-pulse space-y-3">
            <div className="grid grid-cols-2 gap-3">
              <div className="h-16 bg-muted rounded" />
              <div className="h-16 bg-muted rounded" />
            </div>
            <div className="h-4 bg-muted rounded w-2/3" />
          </div>
        </CardContent>
      </Card>
    );
  }

  if (error && !data) {
    return (
      <Card>
        <CardHeader><CardTitle className="text-base">涨跌停统计</CardTitle></CardHeader>
        <CardContent>
          <div className="flex items-center gap-2 text-red-500 text-sm">
            <AlertCircle className="h-4 w-4" />
            {error}
          </div>
        </CardContent>
      </Card>
    );
  }

  return (
    <Card>
      <CardHeader className="flex flex-row items-center justify-between pb-2">
        <CardTitle className="text-base">涨跌停统计</CardTitle>
        <button onClick={load} className="text-muted-foreground hover:text-foreground transition-colors">
          <RefreshCw className={`h-4 w-4 ${loading ? "animate-spin" : ""}`} />
        </button>
      </CardHeader>
      <CardContent>
        {data && (
          <div className="space-y-4">
            <div className="grid grid-cols-2 gap-3">
              <button
                type="button"
                onClick={() => setActive("limit_up")}
                className={`rounded-lg border p-3 text-center transition-colors ${
                  active === "limit_up"
                    ? "border-red-300 bg-red-50 ring-1 ring-red-200"
                    : "border-red-200 bg-red-50/50 hover:bg-red-50"
                }`}
              >
                <ArrowBigUp className="h-5 w-5 text-red-500 mx-auto mb-1" />
                <div className="text-2xl font-bold text-red-500 font-mono">{data.limit_up}</div>
                <div className="text-xs text-muted-foreground">涨停</div>
              </button>
              <button
                type="button"
                onClick={() => setActive("limit_down")}
                className={`rounded-lg border p-3 text-center transition-colors ${
                  active === "limit_down"
                    ? "border-green-300 bg-green-50 ring-1 ring-green-200"
                    : "border-green-200 bg-green-50/50 hover:bg-green-50"
                }`}
              >
                <ArrowBigDown className="h-5 w-5 text-green-500 mx-auto mb-1" />
                <div className="text-2xl font-bold text-green-500 font-mono">{data.limit_down}</div>
                <div className="text-xs text-muted-foreground">跌停</div>
              </button>
            </div>

            <div className="grid grid-cols-2 gap-3">
              <button
                type="button"
                onClick={() => setActive("up_over_5pct")}
                className={`flex items-center gap-2 rounded-lg border p-2.5 text-left transition-colors ${
                  active === "up_over_5pct" ? "border-red-200 bg-red-50/80 ring-1 ring-red-100" : "hover:bg-muted/40"
                }`}
              >
                <TrendingUp className="h-4 w-4 text-red-400 flex-shrink-0" />
                <div>
                  <div className="text-lg font-bold font-mono text-red-500">{data.up_over_5pct}</div>
                  <div className="text-[10px] text-muted-foreground">涨幅&gt;5%</div>
                </div>
              </button>
              <button
                type="button"
                onClick={() => setActive("down_over_5pct")}
                className={`flex items-center gap-2 rounded-lg border p-2.5 text-left transition-colors ${
                  active === "down_over_5pct" ? "border-green-200 bg-green-50/80 ring-1 ring-green-100" : "hover:bg-muted/40"
                }`}
              >
                <TrendingDown className="h-4 w-4 text-green-400 flex-shrink-0" />
                <div>
                  <div className="text-lg font-bold font-mono text-green-500">{data.down_over_5pct}</div>
                  <div className="text-[10px] text-muted-foreground">跌幅&gt;5%</div>
                </div>
              </button>
            </div>

            <div>
              <div className="text-xs font-medium text-muted-foreground mb-2">
                {activeMeta.label}明细（{activeList.length} 只）
              </div>
              <StockList items={activeList} tone={activeMeta.tone} />
            </div>

            <div className="text-[10px] text-muted-foreground text-center leading-relaxed">
              基于跟踪池 {data.total} 只 · 涨停/跌停按交易所涨跌停价判定（非固定 10%）
              <br />
              点击分类切换明细
            </div>
          </div>
        )}
      </CardContent>
    </Card>
  );
}
