"use client";

import { useEffect, useState } from "react";
import Link from "next/link";
import { Card, CardHeader, CardTitle, CardContent } from "@/components/ui/card";
import { Zap, Info } from "lucide-react";

interface ResonanceItem {
  code: string;
  name: string;
  resonance_count: number;
  l2_net_amount: number | null;
  lhb_net_buy: number | null;
  hsgt_net_amount: number | null;
}

function fmtWan(v: number | null): string {
  if (v == null) return "--";
  return `${(v / 1e4).toFixed(0)}万`;
}

export function CapitalResonanceCard() {
  const [items, setItems] = useState<ResonanceItem[]>([]);
  const [tradeDate, setTradeDate] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [showInfo, setShowInfo] = useState(false);

  useEffect(() => {
    setLoading(true);
    fetch(`/api/market/capital-resonance`)
      .then((r) => (r.ok ? r.json() : null))
      .then((d) => {
        setItems(d?.items ?? []);
        setTradeDate(d?.trade_date ?? null);
      })
      .catch(() => {})
      .finally(() => setLoading(false));
  }, []);

  if (loading) return null;

  return (
    <Card>
      <CardHeader className="pb-2">
        <CardTitle className="text-sm flex items-center justify-between">
          <span className="flex items-center gap-2">
            <Zap className="h-4 w-4" /> 三方资金共振(Alpha因子v1)
            {tradeDate && <span className="text-xs font-normal text-muted-foreground">· {tradeDate}</span>}
          </span>
          <button
            onClick={() => setShowInfo(!showInfo)}
            className="text-muted-foreground hover:text-foreground"
            title="因子说明"
          >
            <Info className="h-3.5 w-3.5" />
          </button>
        </CardTitle>
        <p className="text-[11px] text-muted-foreground">
          L2大单+龙虎榜+沪深股通≥2路同日同向净买入，稀疏高确信信号，非日常全市场因子
        </p>
      </CardHeader>
      <CardContent className="pt-0">
        {showInfo && (
          <div className="rounded-md bg-muted/40 px-2.5 py-2 mb-2 space-y-1.5 text-[11px] text-muted-foreground leading-relaxed">
            <p>三路资金来源相互独立：<span className="font-medium text-foreground">L2大单</span>是交易所逐笔委托统计的当日主力净流入；
            <span className="font-medium text-foreground">龙虎榜</span>是上榜个股的席位净买入(游资+机构混合)；
            <span className="font-medium text-foreground">沪深股通</span>只覆盖当日成交额前十的个股，天然稀疏。</p>
            <p>只有≥2路同日同向净买入才计入(单路positive几乎每天覆盖近半个市场，不算真正的"共振")。
            2路记+1档，3路记+2档，是设计上罕见出现的高确信确认信号，不是日常可用的全市场因子。</p>
          </div>
        )}
        {items.length === 0 ? (
          <p className="text-xs text-muted-foreground py-2">今日无≥2路资金共振个股</p>
        ) : (
          <table className="w-full text-xs">
            <thead>
              <tr className="text-muted-foreground text-left">
                <th className="font-normal py-0.5">股票</th>
                <th className="font-normal py-0.5 text-center">共振路数</th>
                <th className="font-normal py-0.5 text-right">L2大单</th>
                <th className="font-normal py-0.5 text-right">龙虎榜</th>
                <th className="font-normal py-0.5 text-right">沪深股通</th>
              </tr>
            </thead>
            <tbody>
              {items.map((it) => (
                <tr key={it.code} className="border-t">
                  <td className="py-1">
                    <Link href={`/stocks/${it.code}`} className="hover:underline">
                      {it.name} <span className="text-muted-foreground font-mono">{it.code}</span>
                    </Link>
                  </td>
                  <td className="py-1 text-center font-semibold text-red-600">{it.resonance_count}</td>
                  <td className="py-1 text-right font-mono">{fmtWan(it.l2_net_amount)}</td>
                  <td className="py-1 text-right font-mono">{fmtWan(it.lhb_net_buy)}</td>
                  <td className="py-1 text-right font-mono">{fmtWan(it.hsgt_net_amount)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </CardContent>
    </Card>
  );
}
