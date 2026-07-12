"use client";

import { useEffect, useState } from "react";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { fetchThsHotspots } from "@/lib/marketExtras";
import Link from "next/link";
import { Flame } from "lucide-react";

type Hot = { date?: string; code: string; name: string; reason?: string; change_pct?: number };

function formatPct(v: number | null | undefined): string {
  if (v == null || Number.isNaN(Number(v))) return "—";
  const n = Number(v);
  const sign = n > 0 ? "+" : "";
  return `${sign}${n.toFixed(2)}%`;
}

interface Props {
  /** 父组件刷新键（如同步热点后递增） */
  refreshKey?: number;
}

export function ThsHotspotsCard({ refreshKey = 0 }: Props) {
  const [items, setItems] = useState<Hot[]>([]);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    setLoading(true);
    fetchThsHotspots(refreshKey > 0)
      .then(setItems)
      .catch(() => setItems([]))
      .finally(() => setLoading(false));
  }, [refreshKey]);

  if (loading) {
    return (
      <Card>
        <CardContent className="p-4 animate-pulse h-24" />
      </Card>
    );
  }
  if (!items.length) return null;

  return (
    <Card>
      <CardHeader className="pb-2">
        <CardTitle className="text-sm flex items-center gap-1.5">
          <Flame className="h-4 w-4 text-orange-500" />
          同花顺热点
          {items[0]?.date && (
            <span className="text-[10px] font-normal text-muted-foreground ml-1">{items[0].date}</span>
          )}
        </CardTitle>
      </CardHeader>
      <CardContent className="space-y-1.5 pt-0">
        {items.slice(0, 8).map((h, i) => (
          <div key={`${h.code}-${i}`} className="flex items-start justify-between text-xs gap-2 border-b last:border-0 py-1">
            <div className="min-w-0">
              <Link href={`/stocks/${h.code}`} className="font-mono text-primary hover:underline">
                {h.code}
              </Link>{" "}
              <span>{h.name}</span>
              {h.reason && <p className="text-[10px] text-muted-foreground truncate">{h.reason}</p>}
            </div>
            <span
              className={`shrink-0 font-medium tabular-nums ${
                (h.change_pct ?? 0) > 0 ? "text-red-600" : (h.change_pct ?? 0) < 0 ? "text-green-600" : "text-muted-foreground"
              }`}
            >
              {formatPct(h.change_pct)}
            </span>
          </div>
        ))}
      </CardContent>
    </Card>
  );
}
