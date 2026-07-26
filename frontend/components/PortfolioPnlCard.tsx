"use client";

import { useEffect, useState } from "react";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Briefcase, ChevronDown, ChevronRight, Info } from "lucide-react";

interface Position {
  code: string;
  name: string;
  shares: number;
  avg_cost: number;
  price: number;
  market_value: number;
  pnl: number;
  pnl_pct: number;
  market_pnl_pct?: number;
  today_pnl_pct?: number;
  bought_today?: boolean;
}

interface Portfolio {
  id: number;
  name: string;
  position_count: number;
  total_cost: number;
  total_market_value: number;
  total_pnl: number;
  total_pnl_pct: number;
  market_pnl_pct?: number;
  today_pnl_pct?: number;
  all_bought_today?: boolean;
  positions: Position[];
}

interface Summary {
  total_cost: number;
  total_market_value: number;
  total_pnl: number;
  total_pnl_pct: number;
  market_pnl_pct?: number;
  today_pnl_pct?: number;
  stock_count?: number;
  position_count?: number;
}

interface PnlMeta {
  calendar_date?: string;
  friction_pct?: number;
  friction_note?: string;
}

function pnlColor(v: number) {
  return v > 0 ? "text-red-600" : v < 0 ? "text-green-600" : "text-muted-foreground";
}
function pnlBg(v: number) {
  return v > 0 ? "bg-red-50 text-red-700" : v < 0 ? "bg-green-50 text-green-700" : "bg-muted text-muted-foreground";
}

function fmtPct(v: number | undefined) {
  if (v == null || Number.isNaN(v)) return "—";
  return `${v >= 0 ? "+" : ""}${v.toFixed(2)}%`;
}

export function PortfolioPnlCard() {
  const [data, setData] = useState<{
    portfolios: Portfolio[];
    summary: Summary;
    meta?: PnlMeta;
    strategy_aggregate?: { portfolio_count?: number; note?: string };
  } | null>(null);
  const [loading, setLoading] = useState(true);
  const [expanded, setExpanded] = useState<Set<number>>(new Set());

  useEffect(() => {
    setLoading(true);
    fetch("/api/portfolio/pnl-summary")
      .then((r) => (r.ok ? r.json() : null))
      .then((d) => setData(d))
      .catch(() => {})
      .finally(() => setLoading(false));
  }, []);

  if (loading) return <div className="h-24 bg-muted animate-pulse rounded-lg" />;
  if (!data?.portfolios?.length) return null;

  const { summary, portfolios, meta, strategy_aggregate } = data;

  const toggleExpand = (id: number) => {
    setExpanded((prev) => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });
  };

  return (
    <Card>
      <CardHeader className="pb-2">
        <CardTitle className="text-sm flex items-center gap-2">
          <Briefcase className="h-4 w-4" /> 持仓盈亏摘要
        </CardTitle>
        <p className="text-[10px] text-muted-foreground">
          去重持仓 {summary.stock_count ?? "—"} 只
          {strategy_aggregate?.portfolio_count
            ? ` · ${strategy_aggregate.portfolio_count} 个策略组合`
            : null}
        </p>
      </CardHeader>
      <CardContent className="pt-0 space-y-3">
        <div className="grid grid-cols-3 gap-3">
          <div className="text-center">
            <div className="text-[10px] text-muted-foreground">总成本（去重）</div>
            <div className="text-sm font-bold">{(summary.total_cost / 1e4).toFixed(1)}万</div>
          </div>
          <div className="text-center">
            <div className="text-[10px] text-muted-foreground">市值</div>
            <div className="text-sm font-bold">{(summary.total_market_value / 1e4).toFixed(1)}万</div>
          </div>
          <div className="text-center">
            <div className="text-[10px] text-muted-foreground">浮盈</div>
            <div className={`text-sm font-bold ${pnlColor(summary.total_pnl)}`}>
              {summary.total_pnl >= 0 ? "+" : ""}{(summary.total_pnl / 1e4).toFixed(1)}万
            </div>
            <div className={`text-[10px] font-medium ${pnlColor(summary.total_pnl_pct ?? 0)}`}>
              累计 {fmtPct(summary.total_pnl_pct)}
            </div>
            {summary.today_pnl_pct != null && (
              <div className={`text-[10px] ${pnlColor(summary.today_pnl_pct)}`}>
                今日 {fmtPct(summary.today_pnl_pct)}
              </div>
            )}
          </div>
        </div>

        {meta?.friction_note && (
          <div className="flex gap-1.5 items-start rounded-md border border-border bg-muted/30 px-2 py-1.5 text-[10px] text-muted-foreground">
            <Info className="h-3 w-3 shrink-0 mt-0.5" />
            <span>{meta.friction_note}</span>
          </div>
        )}

        <div className="space-y-1.5 border-t pt-2">
          {portfolios.map((pf) => (
            <div key={pf.id} className="rounded-lg border overflow-hidden">
              <button
                className="w-full flex items-center gap-2 px-3 py-2 hover:bg-muted/40 text-left"
                onClick={() => toggleExpand(pf.id)}
              >
                {expanded.has(pf.id) ? (
                  <ChevronDown className="h-3.5 w-3.5 shrink-0" />
                ) : (
                  <ChevronRight className="h-3.5 w-3.5 shrink-0" />
                )}
                <span className="text-xs font-medium flex-1 truncate">{pf.name}</span>
                {pf.all_bought_today && (
                  <span className="text-[9px] px-1 py-0.5 rounded bg-amber-500/10 text-amber-700 shrink-0">
                    今日建仓
                  </span>
                )}
                <span className="text-[10px] text-muted-foreground shrink-0">{pf.position_count}只</span>
                <span className="text-[10px] text-muted-foreground shrink-0 hidden sm:inline">
                  累计 {fmtPct(pf.total_pnl_pct)}
                </span>
                <span className={`text-[10px] font-medium shrink-0 hidden sm:inline ${pnlColor(pf.today_pnl_pct ?? 0)}`}>
                  今日 {fmtPct(pf.today_pnl_pct)}
                </span>
                <span className={`text-xs font-bold ml-1 px-1.5 py-0.5 rounded shrink-0 ${pnlBg(pf.total_pnl_pct)}`}>
                  {fmtPct(pf.total_pnl_pct)}
                </span>
              </button>

              {expanded.has(pf.id) && (
                <div className="border-t bg-muted/20">
                  <div className="px-3 py-1 text-[10px] text-muted-foreground flex flex-wrap gap-x-3 gap-y-0.5">
                    <span>累计 {fmtPct(pf.total_pnl_pct)}</span>
                    <span className={pnlColor(pf.market_pnl_pct ?? 0)}>
                      市价 {fmtPct(pf.market_pnl_pct)}
                    </span>
                    <span className={pnlColor(pf.today_pnl_pct ?? 0)}>
                      今日 {fmtPct(pf.today_pnl_pct)}
                    </span>
                  </div>
                  <table className="w-full text-[10px]">
                    <thead>
                      <tr className="text-muted-foreground border-b">
                        <th className="py-1 px-2 text-left">股票</th>
                        <th className="py-1 px-2 text-right">持股</th>
                        <th className="py-1 px-2 text-right">成本</th>
                        <th className="py-1 px-2 text-right">现价</th>
                        <th className="py-1 px-2 text-right">累计</th>
                        <th className="py-1 px-2 text-right">今日</th>
                      </tr>
                    </thead>
                    <tbody>
                      {pf.positions.map((p, i) => (
                        <tr key={i} className="border-b last:border-0 hover:bg-muted/40">
                          <td className="py-1 px-2">
                            <div className="font-mono">{p.code}</div>
                            <div className="text-muted-foreground truncate max-w-[72px]">{p.name}</div>
                          </td>
                          <td className="py-1 px-2 text-right tabular-nums">{p.shares}</td>
                          <td className="py-1 px-2 text-right tabular-nums">{p.avg_cost.toFixed(2)}</td>
                          <td className="py-1 px-2 text-right tabular-nums">
                            {p.price > 0 ? p.price.toFixed(2) : "—"}
                          </td>
                          <td className={`py-1 px-2 text-right font-bold tabular-nums ${pnlColor(p.pnl)}`}>
                            {fmtPct(p.pnl_pct)}
                          </td>
                          <td className={`py-1 px-2 text-right tabular-nums ${pnlColor(p.today_pnl_pct ?? 0)}`}>
                            {fmtPct(p.today_pnl_pct)}
                          </td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                  <div className="px-3 py-1.5 flex justify-between text-[10px] text-muted-foreground border-t">
                    <span>市值 {(pf.total_market_value / 1e4).toFixed(1)}万</span>
                    <span className={`font-bold ${pnlColor(pf.total_pnl)}`}>
                      浮盈 {pf.total_pnl >= 0 ? "+" : ""}{(pf.total_pnl / 1e4).toFixed(2)}万
                    </span>
                  </div>
                </div>
              )}
            </div>
          ))}
        </div>
      </CardContent>
    </Card>
  );
}
