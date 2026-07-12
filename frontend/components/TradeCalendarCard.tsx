"use client";

import { useEffect, useState, useCallback } from "react";
import { ChevronLeft, ChevronRight, CalendarDays } from "lucide-react";
import { Card, CardHeader, CardTitle, CardContent } from "@/components/ui/card";

interface Day {
  date: string;
  is_open: boolean;
}

interface CalendarResp {
  year: number;
  month: number;
  days: Day[];
  today: string;
  today_is_open: boolean;
  next_open_date: string;
}

const WEEK_LABELS = ["一", "二", "三", "四", "五", "六", "日"];

export function TradeCalendarCard() {
  const now = new Date();
  const [year, setYear] = useState(now.getFullYear());
  const [month, setMonth] = useState(now.getMonth() + 1);
  const [data, setData] = useState<CalendarResp | null>(null);
  const [loading, setLoading] = useState(true);

  const load = useCallback((y: number, m: number) => {
    setLoading(true);
    fetch(`/api/market/trade-calendar?year=${y}&month=${m}`)
      .then((r) => (r.ok ? r.json() : null))
      .then(setData)
      .catch(() => {})
      .finally(() => setLoading(false));
  }, []);

  useEffect(() => {
    load(year, month);
  }, [year, month, load]);

  const shift = (delta: number) => {
    let m = month + delta;
    let y = year;
    if (m < 1) { m = 12; y -= 1; }
    if (m > 12) { m = 1; y += 1; }
    setMonth(m);
    setYear(y);
  };

  const leadingBlanks = data?.days.length
    ? (new Date(data.days[0].date).getDay() + 6) % 7
    : 0;

  return (
    <Card>
      <CardHeader className="pb-2">
        <CardTitle className="text-sm flex items-center justify-between">
          <span className="flex items-center gap-2">
            <CalendarDays className="h-4 w-4" /> 交易日历
          </span>
          <span className="flex items-center gap-1">
            <button onClick={() => shift(-1)} className="p-1 hover:bg-muted rounded">
              <ChevronLeft className="h-4 w-4" />
            </button>
            <span className="text-xs font-medium w-16 text-center">{year}-{String(month).padStart(2, "0")}</span>
            <button onClick={() => shift(1)} className="p-1 hover:bg-muted rounded">
              <ChevronRight className="h-4 w-4" />
            </button>
          </span>
        </CardTitle>
      </CardHeader>
      <CardContent className="pt-0">
        {data && (
          <p className="text-xs text-muted-foreground mb-2">
            {data.today_is_open
              ? "今日为交易日"
              : `今日休市 · 下一交易日 ${data.next_open_date}`}
          </p>
        )}
        <div className="grid grid-cols-7 gap-1 text-[10px] text-muted-foreground mb-1">
          {WEEK_LABELS.map((w) => (
            <div key={w} className="text-center">{w}</div>
          ))}
        </div>
        <div className="grid grid-cols-7 gap-1">
          {Array.from({ length: leadingBlanks }).map((_, i) => (
            <div key={`b${i}`} />
          ))}
          {data?.days.map((d) => {
            const isToday = d.date === data.today;
            return (
              <div
                key={d.date}
                title={d.date}
                className={`text-center text-xs rounded py-1 ${
                  d.is_open
                    ? isToday
                      ? "bg-primary text-primary-foreground font-semibold"
                      : "bg-green-50 text-green-700"
                    : "bg-gray-50 text-gray-400"
                }`}
              >
                {Number(d.date.slice(-2))}
              </div>
            );
          })}
        </div>
        {loading && <p className="text-xs text-muted-foreground mt-2">加载中...</p>}
        <p className="text-[10px] text-muted-foreground mt-2">
          <span className="inline-block w-2 h-2 rounded-full bg-green-400 mr-1" />交易日
          <span className="inline-block w-2 h-2 rounded-full bg-gray-300 mx-1 ml-3" />休市
        </p>
      </CardContent>
    </Card>
  );
}
