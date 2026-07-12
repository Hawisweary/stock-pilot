"use client";

import { useEffect, useState, useCallback } from "react";
import { TrendingUp, TrendingDown, RefreshCw, AlertCircle } from "lucide-react";
import { Card, CardHeader, CardTitle, CardContent } from "@/components/ui/card";
import { fetchBoards, type BoardsResponse, type BoardRow } from "@/lib/market";

const POLL_INTERVAL = 60_000; // 60 秒自动刷新

function formatAmount(v: number): string {
  if (v >= 1e8) return (v / 1e8).toFixed(1) + "亿";
  if (v >= 1e4) return (v / 1e4).toFixed(0) + "万";
  return v.toFixed(0);
}

function formatMarketCap(v: number): string {
  if (v >= 1e12) return (v / 1e12).toFixed(2) + "万亿";
  if (v >= 1e8) return (v / 1e8).toFixed(1) + "亿";
  return v.toFixed(0);
}

function ChangeCell({ value }: { value: number }) {
  const color = value > 0 ? "text-red-500" : value < 0 ? "text-green-500" : "text-muted-foreground";
  const Icon = value > 0 ? TrendingUp : value < 0 ? TrendingDown : () => null;
  return (
    <span className={`${color} font-mono text-sm flex items-center gap-1 justify-end`}>
      {value !== 0 && <Icon className="h-3 w-3" />}
      {value > 0 ? "+" : ""}{value.toFixed(2)}%
    </span>
  );
}

export function IndustryBoardsCard({ refreshKey = 0 }: { refreshKey?: number }) {
  const [data, setData] = useState<BoardsResponse | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [sortField, setSortField] = useState<keyof BoardRow>("change_pct");
  const [sortAsc, setSortAsc] = useState(false);

  const load = useCallback(async (force = false) => {
    try {
      setLoading(true);
      setError(null);
      const d = await fetchBoards({ force });
      setData(d);
    } catch (e) {
      setError(e instanceof Error ? e.message : "获取板块数据失败");
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    load(refreshKey > 0);
    const timer = setInterval(() => load(false), POLL_INTERVAL);
    return () => clearInterval(timer);
  }, [load, refreshKey]);

  const handleSort = (field: keyof BoardRow) => {
    if (sortField === field) {
      setSortAsc(!sortAsc);
    } else {
      setSortField(field);
      setSortAsc(field === "change_pct" ? false : true);
    }
  };

  const sorted = data?.all_boards?.slice().sort((a, b) => {
    const aVal = a[sortField] as number;
    const bVal = b[sortField] as number;
    return sortAsc ? aVal - bVal : bVal - aVal;
  }) ?? [];

  const SortArrow = ({ field }: { field: keyof BoardRow }) => {
    if (sortField !== field) return <span className="ml-1 text-muted-foreground/40">↕</span>;
    return <span className="ml-1">{sortAsc ? "↑" : "↓"}</span>;
  };

  if (loading && !data) {
    return (
      <Card>
        <CardHeader><CardTitle>行业板块涨跌幅</CardTitle></CardHeader>
        <CardContent>
          <div className="animate-pulse space-y-2">
            {[1,2,3,4,5].map(i => <div key={i} className="h-6 bg-muted rounded" />)}
          </div>
        </CardContent>
      </Card>
    );
  }

  if (error && !data) {
    return (
      <Card>
        <CardHeader><CardTitle>行业板块涨跌幅</CardTitle></CardHeader>
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
        <div className="flex items-center gap-3">
          <CardTitle className="text-base">行业板块涨跌幅</CardTitle>
          {data && (
            <div className="flex items-center gap-2 text-xs text-muted-foreground">
              <span className="text-red-500">↑{data.up_count}</span>
              <span className="text-green-500">↓{data.down_count}</span>
              <span className="text-muted-foreground">均{data.avg_change_pct > 0 ? "+" : ""}{data.avg_change_pct.toFixed(2)}%</span>
            </div>
          )}
        </div>
        <button onClick={load} className="text-muted-foreground hover:text-foreground transition-colors">
          <RefreshCw className={`h-4 w-4 ${loading ? "animate-spin" : ""}`} />
        </button>
      </CardHeader>
      <CardContent className="p-0">
        <div className="overflow-x-auto">
          <table className="w-full text-xs">
            <thead>
              <tr className="border-b border-border text-muted-foreground">
                <th className="text-left py-2 px-3 font-medium">板块</th>
                <th className="text-right py-2 px-1 font-medium cursor-pointer select-none hover:text-foreground" onClick={() => handleSort("change_pct")}>
                  涨跌幅<SortArrow field="change_pct" />
                </th>
                <th className="text-right py-2 px-1 font-medium cursor-pointer select-none hover:text-foreground" onClick={() => handleSort("turnover_rate")}>
                  换手率<SortArrow field="turnover_rate" />
                </th>
                <th className="text-right py-2 px-1 font-medium cursor-pointer select-none hover:text-foreground" onClick={() => handleSort("pe_ratio")}>
                  PE<SortArrow field="pe_ratio" />
                </th>
                <th className="text-right py-2 px-1 font-medium cursor-pointer select-none hover:text-foreground" onClick={() => handleSort("pb_ratio")}>
                  PB<SortArrow field="pb_ratio" />
                </th>
                <th className="text-right py-2 px-1 font-medium cursor-pointer select-none hover:text-foreground" onClick={() => handleSort("market_cap")}>
                  总市值<SortArrow field="market_cap" />
                </th>
                <th className="text-right py-2 px-2 font-medium cursor-pointer select-none hover:text-foreground" onClick={() => handleSort("amount")}>
                  成交额<SortArrow field="amount" />
                </th>
              </tr>
            </thead>
            <tbody>
              {sorted.map((b, i) => (
                <tr key={b.code} className={`border-b border-border/50 hover:bg-muted/50 transition-colors ${i < 3 && b.change_pct > 0 ? "bg-red-50/30" : i >= sorted.length - 3 && b.change_pct < 0 ? "bg-green-50/30" : ""}`}>
                  <td className="py-1.5 px-3 font-medium text-sm">{b.name}</td>
                  <td className="py-1.5 px-1 text-right"><ChangeCell value={b.change_pct} /></td>
                  <td className="py-1.5 px-1 text-right font-mono text-sm">{b.turnover_rate.toFixed(1)}%</td>
                  <td className="py-1.5 px-1 text-right font-mono text-sm">{b.pe_ratio > 0 ? b.pe_ratio.toFixed(1) : "--"}</td>
                  <td className="py-1.5 px-1 text-right font-mono text-sm">{b.pb_ratio > 0 ? b.pb_ratio.toFixed(2) : "--"}</td>
                  <td className="py-1.5 px-1 text-right font-mono text-sm">{formatMarketCap(b.market_cap)}</td>
                  <td className="py-1.5 px-2 text-right font-mono text-sm">{formatAmount(b.amount)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </CardContent>
    </Card>
  );
}

// Used for top/bottom summary cards
export function BoardSummaryCard({ data }: { data: BoardsResponse | null }) {
  if (!data) return null;
  return (
    <div className="grid grid-cols-2 gap-4">
      <div className="rounded-lg border border-border p-3">
        <div className="text-xs text-muted-foreground mb-1">涨幅前3</div>
        {data.top_gainers.slice(0, 3).map(b => (
          <div key={b.code} className="flex justify-between text-sm py-0.5">
            <span>{b.name}</span>
            <span className="text-red-500 font-mono">+{b.change_pct.toFixed(2)}%</span>
          </div>
        ))}
      </div>
      <div className="rounded-lg border border-border p-3">
        <div className="text-xs text-muted-foreground mb-1">跌幅前3</div>
        {data.top_losers.slice(0, 3).map(b => (
          <div key={b.code} className="flex justify-between text-sm py-0.5">
            <span>{b.name}</span>
            <span className="text-green-500 font-mono">{b.change_pct.toFixed(2)}%</span>
          </div>
        ))}
      </div>
    </div>
  );
}
