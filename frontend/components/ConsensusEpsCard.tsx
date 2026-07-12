"use client";

import { useEffect, useState } from "react";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { TrendingUp } from "lucide-react";

interface Forecast {
  year: string;
  eps: number;
}

interface Props { code: string }

export function ConsensusEpsCard({ code }: Props) {
  const [forecasts, setForecasts] = useState<Forecast[]>([]);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    if (!code) return;
    setLoading(true);
    fetch(`/api/consensus-eps/${code}`)
      .then(r => r.ok ? r.json() : null)
      .then(d => setForecasts(d?.forecasts ?? []))
      .catch(() => {})
      .finally(() => setLoading(false));
  }, [code]);

  if (loading || !forecasts.length) return null;

  const maxEps = Math.max(...forecasts.map(f => Math.abs(f.eps)));

  return (
    <Card>
      <CardHeader className="pb-2">
        <CardTitle className="text-sm flex items-center gap-2">
          <TrendingUp className="h-4 w-4" /> 分析师一致预期 EPS
          <span className="text-xs font-normal text-muted-foreground ml-1">同花顺</span>
        </CardTitle>
      </CardHeader>
      <CardContent className="pt-0 space-y-2">
        {forecasts.map((f, i) => {
          const barW = maxEps > 0 ? (Math.abs(f.eps) / maxEps) * 100 : 0;
          const isPos = f.eps >= 0;
          return (
            <div key={i} className="space-y-0.5">
              <div className="flex items-center justify-between text-xs">
                <span className="text-muted-foreground">{f.year}E</span>
                <span className={`font-medium tabular-nums ${isPos ? "text-foreground" : "text-red-600"}`}>
                  {f.eps.toFixed(4)} 元
                </span>
              </div>
              <div className="h-1.5 bg-muted rounded-full overflow-hidden">
                <div
                  className={`h-full rounded-full ${isPos ? "bg-primary" : "bg-red-500"}`}
                  style={{ width: `${barW}%` }}
                />
              </div>
            </div>
          );
        })}
        <p className="text-[10px] text-muted-foreground pt-1">
          E = 分析师预测值，非实际披露数据
        </p>
      </CardContent>
    </Card>
  );
}
