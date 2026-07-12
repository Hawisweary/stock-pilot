"use client";

import { useCallback, useEffect, useState } from "react";
import Link from "next/link";
import { AlertCircle, BarChart3, RefreshCw, TrendingDown, TrendingUp } from "lucide-react";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import {
  fetchMarketIndices,
  type MarketIndexRow,
  type MarketIndicesResponse,
} from "@/lib/market";

const POLL_MS = 90_000;

function pctClass(v: number | null | undefined): string {
  if (v == null) return "text-muted-foreground";
  if (v > 0) return "text-red-600";
  if (v < 0) return "text-green-600";
  return "text-muted-foreground";
}

function signalBadge(signal: string): string {
  if (signal === "偏多") return "bg-red-50 text-red-700 border-red-200";
  if (signal === "偏空") return "bg-green-50 text-green-700 border-green-200";
  return "bg-yellow-50 text-yellow-800 border-yellow-200";
}

function envBadge(env: string): string {
  return signalBadge(env);
}

function displayPrice(row: MarketIndexRow): number | null {
  return row.last ?? row.close;
}

function formatPct(v: number | null | undefined): string {
  if (v == null) return "--";
  return `${v > 0 ? "+" : ""}${v}%`;
}

function dayChangePct(row: MarketIndexRow): number | null | undefined {
  return row.change_1d_pct ?? row.change_pct_today;
}

function IndexTile({ row, quoteMode }: { row: MarketIndexRow; quoteMode?: string }) {
  const price = displayPrice(row);
  const ch1d = dayChangePct(row);
  return (
    <div className="rounded-lg border border-border bg-muted/20 p-3 flex flex-col gap-2 min-h-[120px]">
      <div className="flex items-center justify-between gap-2">
        <span className="text-sm font-medium truncate">{row.name}</span>
        <span
          className={`text-[10px] px-1.5 py-0.5 rounded border shrink-0 ${signalBadge(row.signal)}`}
        >
          {row.signal}
        </span>
      </div>
      <div className="font-mono text-xl font-bold tabular-nums">
        {price != null ? price.toLocaleString("zh-CN", { maximumFractionDigits: 2 }) : "--"}
      </div>
      <div className="flex flex-wrap gap-x-3 gap-y-0.5 text-[11px] tabular-nums">
        <span className={pctClass(ch1d)}>
          单日 {formatPct(ch1d)}
          {quoteMode === "realtime" && row.last != null ? (
            <span className="text-muted-foreground font-normal"> 实时</span>
          ) : null}
        </span>
        <span className={pctClass(row.change_5d_pct)}>
          5日 {formatPct(row.change_5d_pct)}
        </span>
        <span className={pctClass(row.change_20d_pct)}>
          20日 {formatPct(row.change_20d_pct)}
        </span>
      </div>
      <div className="text-[10px] text-muted-foreground flex flex-wrap gap-x-2">
        {row.rsi14 != null && <span>RSI {row.rsi14}</span>}
        {row.macd_bar != null && <span>MACD柱 {row.macd_bar}</span>}
        {row.ma5 != null && row.ma20 != null && (
          <span>
            MA5/20 {row.ma5 > row.ma20 ? "↑" : row.ma5 < row.ma20 ? "↓" : "—"}
          </span>
        )}
      </div>
    </div>
  );
}

type MarketIndexCardProps = {
  /** 首页等窄区域：单行摘要 */
  compact?: boolean;
  showLink?: boolean;
  /** 父级「全部刷新」递增时强制拉取 */
  refreshKey?: number;
};

export function MarketIndexCard({ compact = false, showLink = true, refreshKey = 0 }: MarketIndexCardProps) {
  const [data, setData] = useState<MarketIndicesResponse | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(async (force = false) => {
    try {
      setLoading(true);
      setError(null);
      const d = await fetchMarketIndices({ force });
      setData(d);
    } catch (e) {
      setError(e instanceof Error ? e.message : "获取大盘指数失败");
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    load(true);
    const t = setInterval(() => load(true), POLL_MS);
    return () => clearInterval(t);
  }, [load, refreshKey]);

  const env = data?.environment ?? "震荡";
  const EnvIcon = env === "偏多" ? TrendingUp : env === "偏空" ? TrendingDown : BarChart3;

  if (compact) {
    return (
      <Card>
        <CardContent className="p-4">
          <div className="flex flex-wrap items-center justify-between gap-3">
            <div className="flex items-center gap-2 min-w-0">
              <BarChart3 className="h-4 w-4 text-muted-foreground shrink-0" />
              <span className="text-xs text-muted-foreground">
                大盘指数
                {data?.quote_mode === "realtime" ? (
                  <span className="ml-1 text-primary">实时</span>
                ) : data?.as_of_trade_date ? (
                  <span className="ml-1">截至 {data.as_of_trade_date}</span>
                ) : null}
                {data?.stale && (
                  <span className="ml-1 text-amber-600">滞后</span>
                )}
              </span>
              <span className={`text-[10px] px-1.5 py-0.5 rounded border ${envBadge(env)}`}>
                <EnvIcon className="inline h-3 w-3 mr-0.5 -mt-0.5" />
                {env}
              </span>
              {data?.environment_comment && (
                <span className="text-[10px] text-muted-foreground truncate max-w-[200px] hidden sm:inline">
                  {data.environment_comment}
                </span>
              )}
            </div>
            {loading && !data ? (
              <div className="h-4 w-48 bg-muted animate-pulse rounded" />
            ) : error && !data?.available ? (
              <span className="text-xs text-red-500">{error}</span>
            ) : (
              <div className="flex flex-wrap gap-4 text-xs font-mono tabular-nums">
                {(data?.indices ?? []).map((row) => (
                  <span key={row.code} className="whitespace-nowrap">
                    <span className="text-muted-foreground">{row.name.replace("指数", "").replace("板", "")}</span>{" "}
                    <span className="font-medium">{displayPrice(row) ?? "--"}</span>{" "}
                    <span className={pctClass(dayChangePct(row))}>
                      {formatPct(dayChangePct(row))}
                    </span>
                  </span>
                ))}
              </div>
            )}
            <div className="flex items-center gap-2 shrink-0">
              <button
                type="button"
                onClick={() => load(true)}
                className="text-muted-foreground hover:text-foreground"
                aria-label="刷新大盘"
              >
                <RefreshCw className={`h-3.5 w-3.5 ${loading ? "animate-spin" : ""}`} />
              </button>
              {showLink && (
                <Link href="/market" className="text-[10px] text-primary hover:underline">
                  详情
                </Link>
              )}
            </div>
          </div>
        </CardContent>
      </Card>
    );
  }

  return (
    <Card className="lg:col-span-2">
      <CardHeader className="flex flex-row items-center justify-between pb-2">
        <div className="space-y-0.5">
          <CardTitle className="text-base flex items-center gap-2">
            <BarChart3 className="h-4 w-4" />
            大盘指数
          </CardTitle>
          <p className="text-[10px] text-muted-foreground font-normal">
            上证 · 深证成指 · 沪深300 · 创业板
            {data?.quote_mode === "realtime" ? (
              <span className="ml-1">
                · 点位实时
                {data.as_of_trade_date ? ` · 技术指标基于 ${data.as_of_trade_date}` : ""}
              </span>
            ) : data?.as_of_trade_date ? (
              <span className="ml-1">· 行情截至 {data.as_of_trade_date}</span>
            ) : null}
            {loading && data && <span className="ml-1 text-primary">更新中…</span>}
          </p>
        </div>
        <div className="flex items-center gap-2">
          <span className={`text-xs px-2 py-0.5 rounded border ${envBadge(env)}`}>
            <EnvIcon className="inline h-3 w-3 mr-0.5 -mt-0.5" />
            环境 {env}
          </span>
          <button
            type="button"
            onClick={() => load(true)}
            className="text-muted-foreground hover:text-foreground"
          >
            <RefreshCw className={`h-4 w-4 ${loading ? "animate-spin" : ""}`} />
          </button>
        </div>
      </CardHeader>
      <CardContent>
        {loading && !data ? (
          <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-4 gap-3">
            {[1, 2, 3, 4].map((i) => (
              <div key={i} className="h-[120px] bg-muted animate-pulse rounded-lg" />
            ))}
          </div>
        ) : error && !data?.available ? (
          <div className="flex items-center gap-2 text-red-500 text-sm">
            <AlertCircle className="h-4 w-4" />
            {error}
          </div>
        ) : (data?.indices?.length ?? 0) > 0 ? (
          <>
            {data?.environment_comment && (
              <p className="text-xs text-muted-foreground mb-3">{data.environment_comment}</p>
            )}
            <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-4 gap-3">
              {data!.indices.map((row) => (
                <IndexTile key={row.code} row={row} quoteMode={data.quote_mode} />
              ))}
            </div>
          </>
        ) : (
          <p className="text-sm text-muted-foreground">暂无指数数据（非交易时段或数据源不可用）</p>
        )}
      </CardContent>
    </Card>
  );
}

/** 个股技术面评分卡下方：大盘环境一行提示 */
export function MarketTechContext() {
  const [data, setData] = useState<MarketIndicesResponse | null>(null);

  useEffect(() => {
    fetchMarketIndices()
      .then(setData)
      .catch(() => setData(null));
  }, []);

  if (!data?.available) return null;

  return (
    <div className="rounded-md border border-dashed border-purple-200 bg-purple-50/50 dark:bg-purple-950/20 px-2.5 py-2 text-[10px] text-muted-foreground">
      <span className="font-medium text-purple-800 dark:text-purple-300">大盘环境 </span>
      <span className={`px-1 rounded ${envBadge(data.environment)}`}>{data.environment}</span>
      <span className="mx-1">·</span>
      <span>{data.environment_comment}</span>
      <span className="mx-1 hidden sm:inline">·</span>
      <span className="hidden sm:inline font-mono tabular-nums">
        {(data.indices ?? []).map((r, i) => (
          <span key={r.code}>
            {i > 0 ? " | " : ""}
            {r.name.replace("指数", "")}{" "}
            <span className={pctClass(r.change_5d_pct)}>
              {r.change_5d_pct != null ? `${r.change_5d_pct > 0 ? "+" : ""}${r.change_5d_pct}%` : "--"}
            </span>
          </span>
        ))}
      </span>
      <span className="ml-1 text-purple-600/80">（已纳入技术面 AI 评分）</span>
    </div>
  );
}
