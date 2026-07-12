"use client";

import { useEffect, useState } from "react";
import { useRouter } from "next/navigation";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Bell, TrendingUp, TrendingDown, AlertTriangle } from "lucide-react";

interface AlertRow {
  id: number;
  stock_id: number;
  code: string;
  name: string;
  calc_date: string;
  old_score: number | null;
  new_score: number;
  delta: number;
  old_veto: string | null;
  new_veto: string | null;
  veto_changed: boolean;
  created_at: string;
}

export function ScoreAlertCard() {
  const router = useRouter();
  const [alerts, setAlerts] = useState<AlertRow[]>([]);
  const [loading, setLoading] = useState(true);
  const [count, setCount] = useState(0);

  useEffect(() => {
    fetch("/api/alerts/score-changes/unread-count?since_hours=48")
      .then(r => r.json())
      .then(d => setCount(d.count ?? 0))
      .catch(() => {});

    fetch("/api/alerts/score-changes?days=7&min_delta=5&limit=10")
      .then(r => r.json())
      .then(d => setAlerts(d.alerts ?? []))
      .catch(() => {})
      .finally(() => setLoading(false));
  }, []);

  if (loading) return null;
  if (alerts.length === 0) return null;

  return (
    <Card className="border-amber-200 bg-amber-50/30 dark:border-amber-900 dark:bg-amber-950/20">
      <CardHeader className="pb-2">
        <CardTitle className="text-sm flex items-center gap-2">
          <Bell className="h-4 w-4 text-amber-600" />
          评分变动预警
          {count > 0 && (
            <span className="ml-1 text-xs bg-amber-500 text-white rounded-full px-1.5 py-0.5 leading-none">
              {count}
            </span>
          )}
          <span className="text-xs font-normal text-muted-foreground ml-auto">最近 7 天</span>
        </CardTitle>
      </CardHeader>
      <CardContent className="p-0">
        <ul className="divide-y divide-border">
          {alerts.map(a => (
            <li
              key={a.id}
              className="flex items-center gap-3 px-4 py-2 hover:bg-muted/40 cursor-pointer transition-colors text-sm"
              onClick={() => router.push(`/stocks/${a.code}`)}
            >
              {/* 图标 */}
              <span className="shrink-0">
                {a.veto_changed ? (
                  <AlertTriangle className="h-4 w-4 text-amber-600" />
                ) : a.delta > 0 ? (
                  <TrendingUp className="h-4 w-4 text-red-600" />
                ) : (
                  <TrendingDown className="h-4 w-4 text-green-700" />
                )}
              </span>

              {/* 股票名 */}
              <span className="font-medium w-20 truncate">{a.name}</span>
              <span className="text-muted-foreground font-mono text-xs">{a.code}</span>

              {/* 分数变化 */}
              <span className="ml-auto text-xs tabular-nums">
                {a.old_score != null ? `${a.old_score.toFixed(1)} → ` : ""}
                <span className="font-semibold">{a.new_score.toFixed(1)}</span>
              </span>

              {/* delta badge */}
              <span className={`text-xs font-medium w-12 text-right tabular-nums ${a.delta > 0 ? "text-red-600" : "text-green-700"}`}>
                {a.delta > 0 ? "+" : ""}{a.delta.toFixed(1)}
              </span>

              {/* veto 变化 */}
              {a.veto_changed && (
                <span className="text-xs text-amber-700 bg-amber-100 px-1.5 rounded whitespace-nowrap">
                  {a.new_veto === "exclude" ? "→ 回避" : a.new_veto === "reduce" ? "→ 减仓" : "→ 正常"}
                </span>
              )}
            </li>
          ))}
        </ul>
      </CardContent>
    </Card>
  );
}
