"use client";

import { useEffect, useState } from "react";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { fetchMarketFundamentals, type MarketFundamentals } from "@/lib/marketExtras";

export function MarketFundamentalsCard({ stockId }: { stockId: number }) {
  const [data, setData] = useState<MarketFundamentals | null>(null);

  useEffect(() => {
    if (!stockId) return;
    fetchMarketFundamentals(stockId).then(setData).catch(() => setData(null));
  }, [stockId]);

  if (!data || data.error) return null;
  const items = [
    { label: "ROE", value: data.roe, suffix: "%" },
    { label: "毛利率", value: data.gm, suffix: "%" },
    { label: "净利率", value: data.nm, suffix: "%" },
    { label: "营收3年CAGR", value: data.revenue_cagr_3y, suffix: "%" },
    { label: "净利3年CAGR", value: data.profit_cagr_3y, suffix: "%" },
  ].filter((x) => x.value != null);

  if (!items.length) return null;

  return (
    <Card>
      <CardHeader className="pb-2">
        <CardTitle className="text-sm">市场面基本面摘要</CardTitle>
      </CardHeader>
      <CardContent className="grid grid-cols-2 sm:grid-cols-3 gap-2 pt-0">
        {items.map((it) => (
          <div key={it.label} className="rounded-md bg-muted/40 px-2 py-1.5">
            <div className="text-[10px] text-muted-foreground">{it.label}</div>
            <div className="text-sm font-semibold tabular-nums">
              {it.value}
              {it.suffix}
            </div>
          </div>
        ))}
      </CardContent>
    </Card>
  );
}
