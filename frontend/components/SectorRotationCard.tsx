"use client";

import { useEffect, useState } from "react";
import Link from "next/link";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { api } from "@/lib/api";
import {
  fetchSectorRotation,
  type SectorRotationItem,
  type SectorRotationResponse,
} from "@/lib/marketExtras";
import { ChevronDown, ChevronRight, Loader2, TrendingDown, TrendingUp } from "lucide-react";

function pctClass(v: number) {
  if (v > 0) return "text-red-500";
  if (v < 0) return "text-green-500";
  return "text-muted-foreground";
}

function SectorRow({
  item,
  tone,
  expanded,
  onToggle,
}: {
  item: SectorRotationItem;
  tone: "up" | "down";
  expanded: boolean;
  onToggle: () => void;
}) {
  const ret = item.avg_return_5d ?? item.score ?? 0;
  const rel = item.rel_strength ?? item.momentum ?? 0;

  return (
    <div className="rounded-md border border-border/60 overflow-hidden">
      <button
        type="button"
        onClick={onToggle}
        className="w-full flex items-center justify-between gap-2 px-2 py-1.5 text-xs hover:bg-muted/50 transition-colors text-left"
      >
        <span className="flex items-center gap-1 min-w-0 truncate">
          {expanded ? (
            <ChevronDown className="h-3.5 w-3.5 shrink-0 text-muted-foreground" />
          ) : (
            <ChevronRight className="h-3.5 w-3.5 shrink-0 text-muted-foreground" />
          )}
          <span className="font-medium truncate">{item.industry}</span>
          <span className="text-[10px] text-muted-foreground shrink-0">({item.stock_count}只)</span>
        </span>
        <span className="shrink-0 flex items-center gap-1.5">
          <span className={`font-mono ${pctClass(ret)}`}>
            {ret >= 0 ? "+" : ""}
            {ret.toFixed(2)}%
          </span>
          <Badge variant="outline" className="text-[10px] h-5 px-1">
            {tone === "up" ? (
              <TrendingUp className="h-3 w-3 mr-0.5" />
            ) : (
              <TrendingDown className="h-3 w-3 mr-0.5" />
            )}
            RS {rel >= 0 ? "+" : ""}
            {rel.toFixed(2)}%
          </Badge>
        </span>
      </button>
      {expanded && item.stocks?.length > 0 && (
        <div className="border-t border-border/60 bg-muted/20 max-h-40 overflow-y-auto divide-y divide-border/40">
          {item.stocks.map((s) => (
            <Link
              key={s.stock_id}
              href={`/stocks/${s.stock_id}`}
              className="flex items-center justify-between px-2.5 py-1 text-[11px] hover:bg-muted/60"
            >
              <span className="truncate min-w-0">
                <span className="font-medium">{s.name}</span>
                <span className="text-muted-foreground font-mono ml-1">{s.code}</span>
              </span>
              <span className="shrink-0 tabular-nums flex items-center gap-2">
                {(s.score ?? s.composite_v5) != null && (
                  <span className="text-muted-foreground">分 {(s.score ?? s.composite_v5)}</span>
                )}
                <span className={pctClass(s.return_5d)}>
                  {s.return_5d >= 0 ? "+" : ""}
                  {s.return_5d.toFixed(2)}%
                </span>
              </span>
            </Link>
          ))}
        </div>
      )}
    </div>
  );
}

function SectorList({
  title,
  items,
  tone,
  expandedKey,
  onToggle,
}: {
  title: string;
  items: SectorRotationItem[];
  tone: "up" | "down";
  expandedKey: string | null;
  onToggle: (industry: string) => void;
}) {
  if (!items.length) return null;
  return (
    <div>
      <div
        className={`text-xs font-medium mb-1.5 ${tone === "up" ? "text-red-600" : "text-green-600"}`}
      >
        {title}
      </div>
      <div className="space-y-1.5">
        {items.map((s) => (
          <SectorRow
            key={s.industry}
            item={s}
            tone={tone}
            expanded={expandedKey === s.industry}
            onToggle={() => onToggle(s.industry)}
          />
        ))}
      </div>
    </div>
  );
}

export function SectorRotationCard({ refreshKey = 0 }: { refreshKey?: number }) {
  const [data, setData] = useState<SectorRotationResponse | null>(null);
  const [loading, setLoading] = useState(true);
  const [expanded, setExpanded] = useState<string | null>(null);
  const [syncing, setSyncing] = useState(false);
  const [syncMsg, setSyncMsg] = useState<string | null>(null);
  const [portfolios, setPortfolios] = useState<{ id: number; name: string }[]>([]);
  const [syncPfId, setSyncPfId] = useState<number | "">("");

  useEffect(() => {
    setLoading(true);
    fetchSectorRotation(refreshKey > 0)
      .then(setData)
      .catch(() => setData(null))
      .finally(() => setLoading(false));
  }, [refreshKey]);

  useEffect(() => {
    api.portfolioList()
      .then((rows) => {
        const list = rows.map((p) => ({ id: p.id, name: p.name }));
        setPortfolios(list);
        setSyncPfId((prev) => (typeof prev === "number" ? prev : list[0]?.id ?? ""));
      })
      .catch(() => setPortfolios([]));
  }, []);

  const toggle = (industry: string) => {
    setExpanded((prev) => (prev === industry ? null : industry));
  };

  const syncToPortfolio = async () => {
    setSyncing(true);
    setSyncMsg(null);
    try {
      if (!portfolios.length) {
        setSyncMsg("请先在模拟盘创建组合");
        return;
      }
      const pfId = typeof syncPfId === "number" ? syncPfId : portfolios[0].id;
      const pf = portfolios.find((p) => p.id === pfId) ?? portfolios[0];
      const res = await api.portfolioSyncSectorRotation(pf.id, {
        window_days: data?.window_trading_days ?? 5,
        per_sector: 2,
        top_n: 10,
      });
      const buyN = res.preview_buy?.length ?? 0;
      const sellN = res.preview_sell_codes?.length ?? 0;
      setSyncMsg(
        `已同步到「${pf.name}」：策略=行业轮动，窗口 ${res.window_days} 日，预览买 ${buyN} / 卖 ${sellN}`,
      );
    } catch (e) {
      setSyncMsg(e instanceof Error ? e.message : "同步失败");
    } finally {
      setSyncing(false);
    }
  };

  if (loading) return <Card><CardContent className="p-4 h-32 animate-pulse" /></Card>;
  if (!data?.all?.length) return null;

  const range =
    data.as_of_trade_date && data.base_trade_date
      ? `${data.base_trade_date} → ${data.as_of_trade_date}`
      : data.date;

  return (
    <Card>
      <CardHeader className="pb-2">
        <CardTitle className="text-sm flex flex-wrap items-center gap-2">
          <span>行业轮动信号</span>
          <span className="text-[10px] font-normal text-muted-foreground">{range}</span>
          <Link href="/portfolio" className="text-[10px] font-normal text-primary hover:underline">
            去模拟盘调仓 →
          </Link>
          {portfolios.length > 0 && (
            <select
              className="h-6 text-[10px] border rounded px-1 ml-auto max-w-[120px]"
              value={typeof syncPfId === "number" ? syncPfId : portfolios[0]?.id ?? ""}
              onChange={(e) => setSyncPfId(Number(e.target.value))}
            >
              {portfolios.map((p) => (
                <option key={p.id} value={p.id}>
                  {p.name}
                </option>
              ))}
            </select>
          )}
          <Button
            type="button"
            variant="outline"
            size="sm"
            className="h-6 text-[10px] px-2"
            disabled={syncing || !portfolios.length}
            onClick={syncToPortfolio}
          >
            {syncing ? <Loader2 className="h-3 w-3 animate-spin" /> : "同步到模拟盘"}
          </Button>
        </CardTitle>
        {syncMsg && (
          <p className="text-[10px] text-muted-foreground mt-1 leading-relaxed">{syncMsg}</p>
        )}
        {data.pool_avg_return_5d != null && (
          <p className="text-[10px] text-muted-foreground mt-1 leading-relaxed">
            近 {data.window_trading_days ?? 5} 个交易日 · 跟踪池均涨{" "}
            <span className={pctClass(data.pool_avg_return_5d)}>
              {data.pool_avg_return_5d >= 0 ? "+" : ""}
              {data.pool_avg_return_5d.toFixed(2)}%
            </span>
            · RS=行业均值-池均值 · 点击行业展开成分股
          </p>
        )}
      </CardHeader>
      <CardContent className="grid grid-cols-1 md:grid-cols-2 gap-4 pt-0">
        <SectorList
          title="建议加仓"
          items={data.add}
          tone="up"
          expandedKey={expanded}
          onToggle={toggle}
        />
        <SectorList
          title="建议减仓"
          items={data.reduce}
          tone="down"
          expandedKey={expanded}
          onToggle={toggle}
        />
      </CardContent>
    </Card>
  );
}
