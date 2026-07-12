"use client";

import { useEffect, useState } from "react";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Megaphone } from "lucide-react";

interface Forecast {
  period_end_date: string;
  ann_date: string;
  type: string;
  p_change_min: number | null;
  p_change_max: number | null;
  net_profit_min: number | null;
  net_profit_max: number | null;
  summary: string;
  change_reason: string;
}

interface Express {
  period_end_date: string;
  ann_date: string;
  revenue: number | null;
  n_income: number | null;
  diluted_eps: number | null;
  diluted_roe: number | null;
  yoy_sales: number | null;
  yoy_dedu_np: number | null;
  perf_summary: string;
}

const TYPE_TONE: Record<string, string> = {
  预增: "bg-red-50 text-red-700", 略增: "bg-red-50 text-red-600", 扭亏: "bg-red-50 text-red-700",
  续盈: "bg-gray-50 text-gray-600",
  预减: "bg-green-50 text-green-700", 略减: "bg-green-50 text-green-600",
  首亏: "bg-green-50 text-green-700", 续亏: "bg-green-50 text-green-700",
};

function fmtYi(v: number | null): string {
  if (v == null) return "--";
  return `${(v / 1e8).toFixed(2)}亿`;
}

function fmtPct(v: number | null): string {
  if (v == null) return "--";
  return `${v >= 0 ? "+" : ""}${v.toFixed(1)}%`;
}

export function EarningsAlertsCard({ stockId }: { stockId: number }) {
  const [forecast, setForecast] = useState<Forecast[]>([]);
  const [express, setExpress] = useState<Express[]>([]);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    if (!stockId) return;
    setLoading(true);
    fetch(`/api/stocks/${stockId}/earnings-alerts`)
      .then((r) => (r.ok ? r.json() : null))
      .then((d) => {
        setForecast(d?.forecast ?? []);
        setExpress(d?.express ?? []);
      })
      .catch(() => {})
      .finally(() => setLoading(false));
  }, [stockId]);

  if (loading) return null;
  if (forecast.length === 0 && express.length === 0) return null;

  return (
    <Card>
      <CardHeader className="pb-2">
        <CardTitle className="text-sm flex items-center gap-2">
          <Megaphone className="h-4 w-4" /> 业绩预告 / 快报
        </CardTitle>
        <p className="text-[11px] text-muted-foreground">
          公司自愿或条件披露，非正式审计财报，仅供参考
        </p>
      </CardHeader>
      <CardContent className="pt-0 space-y-3">
        {forecast.map((f, i) => (
          <div key={`f${i}`} className="border-l-2 border-orange-300 pl-2">
            <div className="flex items-center gap-2 text-xs">
              <span className="font-medium">{f.period_end_date} 预告</span>
              <span className={`px-1.5 py-0.5 rounded text-[11px] ${TYPE_TONE[f.type] ?? "bg-gray-50 text-gray-600"}`}>
                {f.type}
              </span>
              <span className="text-muted-foreground">公告于 {f.ann_date}</span>
            </div>
            <p className="text-xs text-muted-foreground mt-0.5">
              净利润变动 {fmtPct(f.p_change_min)} ~ {fmtPct(f.p_change_max)}
              {f.net_profit_min != null && (
                <> · 预计净利润 {fmtYi(f.net_profit_min! * 1e4)} ~ {fmtYi(f.net_profit_max! * 1e4)}</>
              )}
            </p>
            {f.change_reason && (
              <p className="text-[11px] text-muted-foreground mt-0.5 line-clamp-2">{f.change_reason}</p>
            )}
          </div>
        ))}
        {express.map((e, i) => (
          <div key={`e${i}`} className="border-l-2 border-blue-300 pl-2">
            <div className="flex items-center gap-2 text-xs">
              <span className="font-medium">{e.period_end_date} 快报</span>
              <span className="text-muted-foreground">公告于 {e.ann_date}</span>
            </div>
            <p className="text-xs text-muted-foreground mt-0.5">
              营收 {fmtYi(e.revenue)}(同比 {fmtPct(e.yoy_sales)}) · 净利润 {fmtYi(e.n_income)}(同比 {fmtPct(e.yoy_dedu_np)})
              {e.diluted_roe != null && <> · ROE {e.diluted_roe.toFixed(1)}%</>}
            </p>
          </div>
        ))}
      </CardContent>
    </Card>
  );
}
