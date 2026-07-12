"use client";

import { useEffect, useState } from "react";
import { Card, CardHeader, CardTitle, CardContent } from "@/components/ui/card";
import { Activity } from "lucide-react";

interface HighLowRow {
  trade_date: string;
  high20: number | null;
  low20: number | null;
  high60: number | null;
  low60: number | null;
  high120: number | null;
  low120: number | null;
}

interface SummaryRow {
  trade_date: string;
  exchange: string;
  category: string;
  count: number | null;
  turnover: number | null;
  total_mv: number | null;
  circ_mv: number | null;
  pe_avg: number | null;
}

export function MarketBreadthCard() {
  const [highLow, setHighLow] = useState<HighLowRow[]>([]);
  const [summary, setSummary] = useState<SummaryRow[]>([]);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    setLoading(true);
    fetch(`/api/market/breadth?days=1`)
      .then((r) => (r.ok ? r.json() : null))
      .then((d) => {
        setHighLow(d?.high_low ?? []);
        setSummary(d?.summary ?? []);
      })
      .catch(() => {})
      .finally(() => setLoading(false));
  }, []);

  if (loading) return null;
  const latest = highLow[highLow.length - 1];
  if (!latest && summary.length === 0) return null;

  return (
    <Card>
      <CardHeader className="pb-2">
        <CardTitle className="text-sm flex items-center gap-2">
          <Activity className="h-4 w-4" /> 市场广度 / 总貌
        </CardTitle>
      </CardHeader>
      <CardContent className="pt-0 space-y-3">
        {latest && (
          <div>
            <p className="text-xs text-muted-foreground mb-1">{latest.trade_date} 创新高/新低个股数</p>
            <div className="grid grid-cols-3 gap-2 text-xs">
              {[
                { label: "20日", high: latest.high20, low: latest.low20 },
                { label: "60日", high: latest.high60, low: latest.low60 },
                { label: "120日", high: latest.high120, low: latest.low120 },
              ].map((r) => (
                <div key={r.label} className="rounded-md bg-muted/40 px-2 py-1.5 text-center">
                  <div className="text-[10px] text-muted-foreground mb-0.5">{r.label}</div>
                  <div className="text-red-600 font-semibold">{r.high ?? "--"}</div>
                  <div className="text-green-600 font-semibold">{r.low ?? "--"}</div>
                </div>
              ))}
            </div>
            <p className="text-[10px] text-muted-foreground mt-1">上：创新高家数 · 下：创新低家数(已剔除停牌)</p>
          </div>
        )}
        {summary.length > 0 && (
          <div className="pt-2 border-t">
            <p className="text-xs text-muted-foreground mb-1.5">
              {summary[0]?.trade_date} 交易所市场总貌(亿元)
            </p>
            <table className="w-full text-xs">
              <thead>
                <tr className="text-muted-foreground text-left">
                  <th className="font-normal py-0.5">类别</th>
                  <th className="font-normal py-0.5 text-right">数量</th>
                  <th className="font-normal py-0.5 text-right">总市值</th>
                  <th className="font-normal py-0.5 text-right">流通市值</th>
                </tr>
              </thead>
              <tbody>
                {summary
                  .filter((s) => ["股票"].includes(s.category))
                  .map((s, i) => (
                    <tr key={i} className="border-t">
                      <td className="py-0.5">{s.exchange === "SSE" ? "上交所" : "深交所"}·{s.category}</td>
                      <td className="py-0.5 text-right font-mono">{s.count ?? "--"}</td>
                      <td className="py-0.5 text-right font-mono">{s.total_mv?.toFixed(0) ?? "--"}</td>
                      <td className="py-0.5 text-right font-mono">{s.circ_mv?.toFixed(0) ?? "--"}</td>
                    </tr>
                  ))}
              </tbody>
            </table>
          </div>
        )}
      </CardContent>
    </Card>
  );
}
