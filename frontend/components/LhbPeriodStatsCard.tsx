"use client";

import { useEffect, useState } from "react";
import { Card, CardHeader, CardTitle, CardContent } from "@/components/ui/card";
import { Trophy } from "lucide-react";

interface PeriodRow {
  period: string;
  last_lhb_date: string;
  lhb_count: number | null;
  lhb_net_amount: number | null;
  inst_net_amount: number | null;
  chg_1m: number | null;
  chg_3m: number | null;
  chg_6m: number | null;
  chg_1y: number | null;
}

const PERIOD_LABEL: Record<string, string> = { "1m": "近1月", "3m": "近3月", "6m": "近6月", "1y": "近1年" };
const CHG_KEY: Record<string, keyof PeriodRow> = { "1m": "chg_1m", "3m": "chg_3m", "6m": "chg_6m", "1y": "chg_1y" };

function fmtWan(v: number | null): string {
  if (v == null) return "--";
  const wan = v / 1e4;
  return `${wan >= 0 ? "+" : ""}${wan.toFixed(0)}万`;
}

function fmtPct(v: number | null | undefined): string {
  if (v == null) return "--";
  return `${v >= 0 ? "+" : ""}${v.toFixed(1)}%`;
}

export function LhbPeriodStatsCard({ stockId }: { stockId: number }) {
  const [periods, setPeriods] = useState<PeriodRow[]>([]);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    if (!stockId) return;
    setLoading(true);
    fetch(`/api/stocks/${stockId}/lhb-period-stats`)
      .then((r) => (r.ok ? r.json() : null))
      .then((d) => setPeriods(d?.periods ?? []))
      .catch(() => {})
      .finally(() => setLoading(false));
  }, [stockId]);

  if (loading || periods.length === 0) return null;

  return (
    <Card>
      <CardHeader className="pb-2">
        <CardTitle className="text-sm flex items-center gap-2">
          <Trophy className="h-4 w-4" /> 龙虎榜多周期统计
        </CardTitle>
      </CardHeader>
      <CardContent className="pt-0">
        <table className="w-full text-xs">
          <thead>
            <tr className="text-muted-foreground text-left">
              <th className="font-normal py-0.5">周期</th>
              <th className="font-normal py-0.5 text-right">上榜次数</th>
              <th className="font-normal py-0.5 text-right">龙虎榜净买额</th>
              <th className="font-normal py-0.5 text-right">机构净买额</th>
              <th className="font-normal py-0.5 text-right">期间涨跌幅</th>
            </tr>
          </thead>
          <tbody>
            {periods.map((p) => (
              <tr key={p.period} className="border-t">
                <td className="py-1">{PERIOD_LABEL[p.period] ?? p.period}</td>
                <td className="py-1 text-right font-mono">{p.lhb_count ?? "--"}</td>
                <td className={`py-1 text-right font-mono ${(p.lhb_net_amount ?? 0) >= 0 ? "text-red-600" : "text-green-600"}`}>
                  {fmtWan(p.lhb_net_amount)}
                </td>
                <td className={`py-1 text-right font-mono ${(p.inst_net_amount ?? 0) >= 0 ? "text-red-600" : "text-green-600"}`}>
                  {fmtWan(p.inst_net_amount)}
                </td>
                <td className={`py-1 text-right font-mono ${(p[CHG_KEY[p.period]] as number ?? 0) >= 0 ? "text-red-600" : "text-green-600"}`}>
                  {fmtPct(p[CHG_KEY[p.period]] as number | null)}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
        <p className="text-[10px] text-muted-foreground mt-1.5">最近上榜日: {periods[0]?.last_lhb_date}</p>
      </CardContent>
    </Card>
  );
}
