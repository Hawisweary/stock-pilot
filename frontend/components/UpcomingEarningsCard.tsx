"use client";

import { useEffect, useState } from "react";
import { useRouter } from "next/navigation";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { CalendarDays } from "lucide-react";

interface UpcomingEvent {
  stock_id: number;
  code: string;
  name: string;
  report_label: string;
  disclosure_date: string;
  days_until: number;
}

export function UpcomingEarningsCard() {
  const router = useRouter();
  const [events, setEvents] = useState<UpcomingEvent[]>([]);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    fetch("/api/calendar/upcoming?limit=8")
      .then(r => r.json())
      .then(d => setEvents(d.events ?? []))
      .catch(() => {})
      .finally(() => setLoading(false));
  }, []);

  if (loading || events.length === 0) return null;

  return (
    <Card>
      <CardHeader className="pb-2 flex flex-row items-center justify-between">
        <CardTitle className="text-sm flex items-center gap-2">
          <CalendarDays className="h-4 w-4" /> 即将披露财报
        </CardTitle>
        <button onClick={() => router.push("/calendar")}
          className="text-xs text-muted-foreground hover:text-foreground">
          查看全部 →
        </button>
      </CardHeader>
      <CardContent className="p-0">
        <ul className="divide-y divide-border">
          {events.map((e, i) => (
            <li key={i}
              onClick={() => router.push(`/stocks/${e.code}`)}
              className="flex items-center gap-3 px-4 py-2 hover:bg-muted/40 cursor-pointer transition-colors text-sm">
              <div className="w-14 shrink-0">
                <div className="text-xs font-medium">{e.disclosure_date.slice(5)}</div>
                <div className="text-[10px] text-muted-foreground">
                  {e.days_until === 0 ? "今天" : `${e.days_until}天后`}
                </div>
              </div>
              <div className="flex-1 min-w-0">
                <div className="font-medium truncate">{e.name}</div>
                <div className="text-xs text-muted-foreground font-mono">{e.code}</div>
              </div>
              <span className="text-xs shrink-0 px-1.5 py-0.5 rounded bg-muted text-muted-foreground">
                {e.report_label}
              </span>
            </li>
          ))}
        </ul>
      </CardContent>
    </Card>
  );
}
