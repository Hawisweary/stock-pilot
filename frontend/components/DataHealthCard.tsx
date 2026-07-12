"use client";

import { useState } from "react";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { useToast } from "@/lib/useToast";
import { api } from "@/lib/api";
import { AlertTriangle, CheckCircle2, RefreshCw, ExternalLink } from "lucide-react";
import Link from "next/link";

interface DataQuality {
  daily_quotes: number;
  financial_cover: number;
  scoring_cover: number;
  valuation_cover: number;
  last_sync?: string;
}

interface StaleStock {
  id: number;
  code: string;
  name: string;
  last_date: string;
}

interface Props {
  staleCount: number;
  staleList: StaleStock[];
  dataQuality?: DataQuality | null;
  onRefreshed?: () => void;
}

export function DataHealthCard({ staleCount, staleList, dataQuality, onRefreshed }: Props) {
  const toast = useToast();
  const [fetchingIds, setFetchingIds] = useState<Set<number>>(new Set());

  const fetchSingle = async (s: StaleStock) => {
    if (fetchingIds.has(s.id)) return;
    setFetchingIds((prev) => new Set(prev).add(s.id));
    try {
      await api.fetchStock(s.id);
      toast.success(`${s.name}(${s.code}) 数据已更新`);
      onRefreshed?.();
    } catch (e: unknown) {
      toast.error(e instanceof Error ? e.message : `${s.code} 更新失败`);
    } finally {
      setFetchingIds((prev) => { const n = new Set(prev); n.delete(s.id); return n; });
    }
  };

  const qualityItems = dataQuality
    ? [
        { label: "日线行情", value: dataQuality.daily_quotes },
        { label: "财报覆盖", value: dataQuality.financial_cover },
        { label: "评分覆盖", value: dataQuality.scoring_cover },
        { label: "估值覆盖", value: dataQuality.valuation_cover },
      ]
    : [];

  const healthy = staleCount === 0;

  return (
    <Card>
      <CardHeader className="pb-2 flex flex-row items-center justify-between">
        <CardTitle className="text-sm flex items-center gap-2">
          {healthy ? (
            <CheckCircle2 className="h-4 w-4 text-emerald-500" />
          ) : (
            <AlertTriangle className="h-4 w-4 text-amber-500" />
          )}
          数据健康
        </CardTitle>
        <Link
          href="/data"
          className="text-xs text-muted-foreground hover:text-foreground flex items-center gap-1"
        >
          数据管理 <ExternalLink className="h-3 w-3" />
        </Link>
      </CardHeader>
      <CardContent className="pt-0 space-y-3">
        {/* 覆盖率数字 */}
        {qualityItems.length > 0 && (
          <div className="grid grid-cols-2 gap-x-4 gap-y-1">
            {qualityItems.map((item) => (
              <div key={item.label} className="flex items-center justify-between text-xs">
                <span className="text-muted-foreground">{item.label}</span>
                <span className="font-mono font-medium">{item.value}</span>
              </div>
            ))}
            {dataQuality?.last_sync && (
              <div className="col-span-2 text-[10px] text-muted-foreground mt-0.5">
                上次同步：{dataQuality.last_sync.slice(0, 16)}
              </div>
            )}
          </div>
        )}

        {/* Stale 列表 */}
        {staleCount === 0 ? (
          <p className="text-xs text-emerald-600">所有股票数据均在3天内更新</p>
        ) : (
          <div className="space-y-1.5">
            <p className="text-xs text-amber-700 font-medium">
              {staleCount} 只股票数据逾期（&gt;3天）：
            </p>
            {staleList.slice(0, 5).map((s) => (
              <div
                key={s.id}
                className="flex items-center justify-between text-xs bg-amber-50 rounded px-2 py-1"
              >
                <div className="flex items-center gap-2">
                  <span className="font-mono text-muted-foreground">{s.code}</span>
                  <span className="font-medium">{s.name}</span>
                  <span className="text-muted-foreground">{s.last_date}</span>
                </div>
                <button
                  onClick={() => fetchSingle(s)}
                  disabled={fetchingIds.has(s.id)}
                  className="p-0.5 rounded hover:bg-amber-200 disabled:opacity-40 transition-colors"
                  title="更新此股数据"
                >
                  <RefreshCw
                    className={`h-3 w-3 text-amber-700 ${fetchingIds.has(s.id) ? "animate-spin" : ""}`}
                  />
                </button>
              </div>
            ))}
            {staleCount > 5 && (
              <p className="text-[10px] text-muted-foreground">
                另有 {staleCount - 5} 只…
                <Link href="/data" className="underline ml-1">查看全部</Link>
              </p>
            )}
          </div>
        )}
      </CardContent>
    </Card>
  );
}
