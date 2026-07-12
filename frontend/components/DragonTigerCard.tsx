"use client";

import { useCallback, useEffect, useState } from "react";
import Link from "next/link";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { fetchDragonTigerDaily, type LhbRecord } from "@/lib/signals";
import { RefreshCw, Trophy } from "lucide-react";

function formatPct(v: number | null | undefined): string {
  if (v == null) return "—";
  const n = Number(v);
  if (Number.isNaN(n)) return "—";
  const sign = n > 0 ? "+" : "";
  return `${sign}${n.toFixed(2)}%`;
}

function pctClass(v: number | null | undefined) {
  if (v == null) return "text-muted-foreground";
  if (v > 0) return "text-red-600";
  if (v < 0) return "text-green-600";
  return "text-muted-foreground";
}

export function DragonTigerCard({ refreshKey = 0 }: { refreshKey?: number }) {
  const [tradeDate, setTradeDate] = useState(() => new Date().toISOString().slice(0, 10));
  const [items, setItems] = useState<LhbRecord[]>([]);
  const [meta, setMeta] = useState<{ source?: string | null; error?: string | null; count?: number; note?: string | null }>({});
  const [loading, setLoading] = useState(true);

  const load = useCallback(async (force = false) => {
    setLoading(true);
    try {
      const res = await fetchDragonTigerDaily(tradeDate, 60, force);
      setItems(res.items || []);
      setMeta({ source: res.source, error: res.error, count: res.count, note: res.note });
      if (res.date) setTradeDate(res.date);
    } catch (e) {
      setItems([]);
      setMeta({ error: e instanceof Error ? e.message : "加载失败" });
    } finally {
      setLoading(false);
    }
  }, [tradeDate]);

  useEffect(() => {
    load(refreshKey > 0);
  }, [load, refreshKey]);

  return (
    <Card>
      <CardHeader className="pb-2 flex flex-row items-center justify-between gap-2">
        <CardTitle className="text-sm flex items-center gap-1.5">
          <Trophy className="h-4 w-4 text-amber-600" />
          龙虎榜
          {meta.source && (
            <span className="text-[10px] font-normal text-muted-foreground ml-1">
              {meta.source === "eastmoney" || meta.source.startsWith("eastmoney")
                ? "东财直连"
                : meta.source === "adata"
                  ? "ADATA"
                  : meta.source}
            </span>
          )}
        </CardTitle>
        <div className="flex items-center gap-2">
          <input
            type="date"
            value={tradeDate}
            onChange={(e) => setTradeDate(e.target.value)}
            className="text-xs border border-border rounded px-2 py-1 h-7"
          />
          <button
            type="button"
            onClick={load}
            className="p-1.5 rounded border border-border text-muted-foreground hover:text-foreground"
            aria-label="刷新龙虎榜"
          >
            <RefreshCw className={`h-3.5 w-3.5 ${loading ? "animate-spin" : ""}`} />
          </button>
        </div>
      </CardHeader>
      <CardContent className="pt-0">
        {loading && !items.length ? (
          <div className="h-32 animate-pulse bg-muted/40 rounded" />
        ) : meta.error && !items.length ? (
          <p className="text-xs text-red-500 py-4 text-center">{meta.error}</p>
        ) : !items.length ? (
          <p className="text-xs text-muted-foreground py-4 text-center">该日暂无龙虎榜数据（非交易日或未披露）</p>
        ) : (
          <>
          {meta.note && (
            <p className="text-[10px] text-amber-700 bg-amber-50 dark:bg-amber-950/30 rounded px-2 py-1 mb-2">{meta.note}</p>
          )}
          <div className="overflow-x-auto max-h-[360px] overflow-y-auto">
            <table className="w-full text-xs">
              <thead className="sticky top-0 bg-card">
                <tr className="text-muted-foreground border-b">
                  <th className="text-left py-1 pr-2">代码</th>
                  <th className="text-left py-1 pr-2">名称</th>
                  <th className="text-right py-1 px-1">涨跌幅</th>
                  <th className="text-right py-1 px-1">净买(万)</th>
                  <th className="text-left py-1 pl-2">上榜原因</th>
                </tr>
              </thead>
              <tbody>
                {items.map((row, i) => (
                  <tr
                    key={`${row.code}-${row.date}-${i}-${(row.reason || "").slice(0, 24)}`}
                    className="border-b last:border-0 hover:bg-muted/30"
                  >
                    <td className="py-1.5 pr-2 font-mono">
                      {row.code ? (
                        <Link href={`/stocks/${row.code}`} className="text-primary hover:underline">
                          {row.code}
                        </Link>
                      ) : (
                        "—"
                      )}
                    </td>
                    <td className="py-1.5 pr-2 max-w-[72px] truncate">{row.name || "—"}</td>
                    <td className={`py-1.5 px-1 text-right tabular-nums ${pctClass(row.change_pct)}`}>
                      {formatPct(row.change_pct)}
                    </td>
                    <td className={`py-1.5 px-1 text-right tabular-nums font-medium ${pctClass(row.net_buy)}`}>
                      {row.net_buy != null ? row.net_buy.toLocaleString() : "—"}
                    </td>
                    <td className="py-1.5 pl-2 text-muted-foreground max-w-[200px] truncate" title={row.reason}>
                      {row.reason || "—"}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          </>
        )}
        {meta.count != null && items.length > 0 && (
          <p className="text-[10px] text-muted-foreground mt-2">
            共 {meta.count} 条上榜记录，展示 {items.length} 条（同日同股可能因不同原因多次上榜）
          </p>
        )}
      </CardContent>
    </Card>
  );
}

/** 个股页龙虎榜区块 */
export function DragonTigerStockPanel({ code }: { code: string }) {
  const [pickDate, setPickDate] = useState("");
  const [records, setRecords] = useState<LhbRecord[]>([]);
  const [source, setSource] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);

  const load = useCallback(async () => {
    if (!code) return;
    setLoading(true);
    setError(null);
    try {
      const { fetchDragonTiger } = await import("@/lib/signals");
      const res = await fetchDragonTiger(code, {
        date: pickDate || undefined,
        days: pickDate ? undefined : 60,
        limit: 10,
      });
      setRecords(res.records || []);
      setSource(res.source ?? null);
      setError(res.error ?? null);
    } catch (e) {
      setRecords([]);
      setError(e instanceof Error ? e.message : "加载失败");
    } finally {
      setLoading(false);
    }
  }, [code, pickDate]);

  useEffect(() => {
    load();
  }, [load]);

  const detail = records[0];

  return (
    <div className="space-y-2">
      <div className="flex items-center gap-2 flex-wrap">
        <input
          type="date"
          value={pickDate}
          onChange={(e) => setPickDate(e.target.value)}
          className="text-[10px] border border-border rounded px-2 py-0.5 h-6"
          placeholder="指定日期"
        />
        <button
          type="button"
          onClick={() => setPickDate("")}
          className="text-[10px] text-muted-foreground hover:text-foreground"
        >
          历史上榜
        </button>
        {source && <span className="text-[10px] text-muted-foreground">{source}</span>}
      </div>
      {loading ? (
        <p className="text-xs text-muted-foreground animate-pulse py-2">加载中…</p>
      ) : error && !records.length ? (
        <p className="text-xs text-muted-foreground py-2">{error}</p>
      ) : !records.length ? (
        <p className="text-xs text-muted-foreground py-2">暂无龙虎榜记录</p>
      ) : (
        <>
          {records.map((r, i) => (
            <div key={`${r.date}-${i}`} className="text-xs border-b last:border-0 py-1.5 space-y-0.5">
              <div className="flex flex-wrap gap-x-2">
                <span className="font-medium">{r.date}</span>
                {r.change_pct != null && (
                  <span className={pctClass(r.change_pct)}>{formatPct(r.change_pct)}</span>
                )}
                {r.net_buy != null && (
                  <span className={pctClass(r.net_buy)}>净买 {r.net_buy} 万</span>
                )}
              </div>
              {r.reason && <p className="text-muted-foreground line-clamp-2">{r.reason}</p>}
            </div>
          ))}
          {detail?.seats && detail.seats.length > 0 && (
            <div className="mt-2 border-t pt-2">
              <p className="text-[10px] font-medium text-muted-foreground mb-1">营业部席位（{detail.date}）</p>
              <div className="space-y-1 max-h-36 overflow-y-auto">
                {detail.seats.map((s, i) => (
                  <div key={i} className="flex justify-between gap-2 text-[10px]">
                    <span className="truncate">
                      <span className={s.side === "buy" ? "text-red-600" : "text-green-600"}>
                        {s.side === "buy" ? "买" : "卖"}
                      </span>{" "}
                      {s.name}
                    </span>
                    <span className="shrink-0 tabular-nums">
                      {s.net_amount != null ? `${s.net_amount}万` : "—"}
                    </span>
                  </div>
                ))}
              </div>
            </div>
          )}
        </>
      )}
    </div>
  );
}
