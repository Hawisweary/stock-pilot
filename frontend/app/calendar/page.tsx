"use client";

import { useEffect, useState, useCallback, useMemo, Suspense } from "react";
import { useRouter } from "next/navigation";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { CalendarDays, AlertTriangle, ChevronLeft, ChevronRight, TrendingUp } from "lucide-react";
import { exportCsv } from "@/lib/csvExport";

interface CalEvent {
  stock_id: number;
  code: string;
  name: string;
  industry: string;
  period_end_date: string;
  report_type: string;
  report_label: string;
  disclosure_date: string;
  is_today: boolean;
  is_past: boolean;
  days_until: number;
  near_alert: boolean;
}

interface CalResponse {
  today: string;
  count: number;
  events: CalEvent[];
}

const REPORT_COLORS: Record<string, string> = {
  annual:       "bg-purple-100 text-purple-700",
  semi:         "bg-blue-100 text-blue-700",
  q1:           "bg-green-100 text-green-700",
  q3:           "bg-amber-100 text-amber-700",
  q3_quarterly: "bg-amber-100 text-amber-700",
};

function reportColor(type: string) {
  return REPORT_COLORS[type] ?? "bg-gray-100 text-gray-600";
}

function groupByDate(events: CalEvent[]): Map<string, CalEvent[]> {
  const map = new Map<string, CalEvent[]>();
  for (const e of events) {
    const list = map.get(e.disclosure_date) ?? [];
    list.push(e);
    map.set(e.disclosure_date, list);
  }
  return map;
}

function CalendarPage() {
  const router = useRouter();
  const [data, setData] = useState<CalResponse | null>(null);
  const [loading, setLoading] = useState(true);
  const [daysAhead, setDaysAhead] = useState(60);
  const [filterType, setFilterType] = useState("all");
  const [filterAlert, setFilterAlert] = useState(false);
  const [viewMode, setViewMode] = useState<"timeline" | "table" | "grid">("timeline");

  const load = useCallback(async (ahead: number) => {
    setLoading(true);
    try {
      const res = await fetch(`/api/calendar/events?days_ahead=${ahead}&days_behind=14`);
      if (!res.ok) {
        setData({ today: new Date().toISOString().slice(0, 10), count: 0, events: [] });
        return;
      }
      const d = await res.json();
      setData(d);
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => { load(daysAhead); }, [daysAhead]);

  const events = data?.events ?? [];
  const filtered = events.filter(e => {
    if (filterType !== "all" && e.report_type !== filterType) return false;
    if (filterAlert && !e.near_alert) return false;
    return true;
  });

  const byDate = groupByDate(filtered);
  const sortedDates = Array.from(byDate.keys()).sort();
  const today = data?.today ?? "";

  const handleExport = () => {
    exportCsv(`financial_calendar_${today}.csv`,
      ["代码", "名称", "行业", "财报类型", "报告期", "披露日", "距今天数", "分数变动"],
      filtered.map(e => [e.code, e.name, e.industry, e.report_label,
        e.period_end_date, e.disclosure_date, e.days_until, e.near_alert ? "是" : ""])
    );
  };

  const typeOptions = [
    { value: "all", label: "全部" },
    { value: "annual", label: "年报" },
    { value: "semi", label: "半年报" },
    { value: "q1", label: "一季报" },
    { value: "q3", label: "三季报" },
  ];

  return (
    <div className="space-y-4">
      {/* 标题 */}
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-xl font-bold flex items-center gap-2">
            <CalendarDays className="h-5 w-5" /> 财报日历
          </h1>
          <p className="text-sm text-muted-foreground">
            {data ? `共 ${filtered.length} 条 / 总计 ${data.count} 条` : "加载中…"}
          </p>
        </div>
        <div className="flex gap-2 items-center">
          <button onClick={handleExport}
            className="text-xs border rounded px-3 py-1.5 hover:bg-accent transition-colors">
            导出 CSV
          </button>
          <div className="flex gap-1">
            {(["timeline", "grid", "table"] as const).map((m) => (
              <button
                key={m}
                onClick={() => setViewMode(m)}
                className={`text-xs border rounded px-2.5 py-1.5 transition-colors ${viewMode === m ? "bg-primary text-primary-foreground border-primary" : "hover:bg-accent"}`}
              >
                {m === "timeline" ? "时间线" : m === "grid" ? "月历" : "表格"}
              </button>
            ))}
          </div>
        </div>
      </div>

      {/* 筛选栏 */}
      <div className="flex flex-wrap gap-3 items-center">
        {/* 时间范围 */}
        <div className="flex gap-1 items-center">
          <button onClick={() => setDaysAhead(d => Math.max(14, d - 30))}
            className="p-1 rounded border hover:bg-accent"><ChevronLeft className="h-4 w-4" /></button>
          <span className="text-sm w-24 text-center">未来 {daysAhead} 天</span>
          <button onClick={() => setDaysAhead(d => Math.min(365, d + 30))}
            className="p-1 rounded border hover:bg-accent"><ChevronRight className="h-4 w-4" /></button>
        </div>

        {/* 报告类型 */}
        <div className="flex gap-1">
          {typeOptions.map(o => (
            <button key={o.value} onClick={() => setFilterType(o.value)}
              className={`text-xs px-2.5 py-1 rounded border transition-colors ${
                filterType === o.value ? "bg-primary text-primary-foreground border-primary" : "hover:bg-accent"
              }`}>
              {o.label}
            </button>
          ))}
        </div>

        {/* 分数变动过滤 */}
        <label className="flex items-center gap-1.5 text-xs cursor-pointer">
          <input type="checkbox" checked={filterAlert} onChange={e => setFilterAlert(e.target.checked)}
            className="rounded" />
          <AlertTriangle className="h-3.5 w-3.5 text-amber-600" /> 仅显示有分数变动
        </label>
      </div>

      {loading ? (
        <div className="grid gap-3">
          {[...Array(3)].map((_, i) => (
            <div key={i} className="h-24 rounded-xl bg-muted animate-pulse" />
          ))}
        </div>
      ) : viewMode === "timeline" ? (
        /* 时间线视图 */
        <div className="space-y-4">
          {sortedDates.length === 0 && (
            <div className="py-16 text-center text-muted-foreground text-sm">该时间范围内没有财报</div>
          )}
          {sortedDates.map(dateStr => {
            const isToday = dateStr === today;
            const isPast = dateStr < today;
            const dayEvents = byDate.get(dateStr)!;

            return (
              <div key={dateStr} className="flex gap-3">
                {/* 日期轴 */}
                <div className="w-20 shrink-0 text-right">
                  <div className={`text-xs font-medium ${isToday ? "text-primary" : isPast ? "text-muted-foreground" : ""}`}>
                    {dateStr.slice(5)}
                  </div>
                  {isToday && <div className="text-[10px] text-primary font-semibold">今天</div>}
                </div>
                {/* 竖线 */}
                <div className="flex flex-col items-center">
                  <div className={`w-2.5 h-2.5 rounded-full mt-0.5 shrink-0 ${
                    isToday ? "bg-primary" : isPast ? "bg-muted-foreground" : "bg-border"}`} />
                  <div className="w-px flex-1 bg-border mt-0.5" />
                </div>
                {/* 事件卡片 */}
                <div className="flex-1 pb-4 space-y-2">
                  {dayEvents.map((e, i) => (
                    <div key={i}
                      onClick={() => router.push(`/stocks/${e.code}`)}
                      className={`flex items-center gap-2 rounded-lg border px-3 py-2 cursor-pointer hover:bg-muted/40 transition-colors text-sm ${
                        isToday ? "border-primary/40 bg-primary/5" : ""
                      }`}
                    >
                      <span className={`text-[10px] px-1.5 py-0.5 rounded font-medium whitespace-nowrap ${reportColor(e.report_type)}`}>
                        {e.report_label}
                      </span>
                      <span className="font-mono text-xs text-muted-foreground">{e.code}</span>
                      <span className="font-medium truncate">{e.name}</span>
                      {e.industry && (
                        <span className="text-xs text-muted-foreground hidden sm:block truncate max-w-[80px]">{e.industry}</span>
                      )}
                      <span className="ml-auto text-xs text-muted-foreground whitespace-nowrap">{e.period_end_date.slice(0, 7)}</span>
                      {e.near_alert && (
                        <TrendingUp className="h-3.5 w-3.5 text-amber-600 shrink-0" />
                      )}
                    </div>
                  ))}
                </div>
              </div>
            );
          })}
        </div>
      ) : viewMode === "grid" ? (
        /* 月历网格视图 */
        <MonthGrid events={filtered} today={today} onClickStock={(code) => router.push(`/stocks/${code}`)} />
      ) : (
        /* 表格视图 */
        <Card>
          <CardContent className="p-0">
            <div className="overflow-auto max-h-[65vh]">
              <table className="w-full text-xs min-w-[600px]">
                <thead className="sticky top-0 bg-card border-b">
                  <tr className="text-left text-muted-foreground">
                    <th className="py-2 px-3">披露日</th>
                    <th className="py-2 px-2">代码</th>
                    <th className="py-2 px-2">名称</th>
                    <th className="py-2 px-2">类型</th>
                    <th className="py-2 px-2 hidden sm:table-cell">报告期</th>
                    <th className="py-2 px-2 hidden md:table-cell">行业</th>
                    <th className="py-2 px-2 text-right">距今</th>
                    <th className="py-2 px-2"></th>
                  </tr>
                </thead>
                <tbody>
                  {filtered.map((e, i) => (
                    <tr key={i}
                      className={`border-b hover:bg-muted/40 cursor-pointer transition-colors ${e.is_today ? "bg-primary/5" : ""}`}
                      onClick={() => router.push(`/stocks/${e.code}`)}>
                      <td className={`py-1.5 px-3 font-medium ${e.is_today ? "text-primary" : e.is_past ? "text-muted-foreground" : ""}`}>
                        {e.disclosure_date}
                      </td>
                      <td className="py-1.5 px-2 font-mono">{e.code}</td>
                      <td className="py-1.5 px-2">{e.name}</td>
                      <td className="py-1.5 px-2">
                        <span className={`px-1.5 py-0.5 rounded text-[10px] font-medium ${reportColor(e.report_type)}`}>
                          {e.report_label}
                        </span>
                      </td>
                      <td className="py-1.5 px-2 hidden sm:table-cell text-muted-foreground">{e.period_end_date.slice(0, 7)}</td>
                      <td className="py-1.5 px-2 hidden md:table-cell text-muted-foreground truncate max-w-[80px]">{e.industry}</td>
                      <td className="py-1.5 px-2 text-right text-muted-foreground">
                        {e.days_until === 0 ? "今天" : e.days_until > 0 ? `${e.days_until}天后` : `${-e.days_until}天前`}
                      </td>
                      <td className="py-1.5 px-2">
                        {e.near_alert && <TrendingUp className="h-3.5 w-3.5 text-amber-600" />}
                      </td>
                    </tr>
                  ))}
                  {filtered.length === 0 && (
                    <tr><td colSpan={8} className="py-12 text-center text-muted-foreground">没有符合条件的财报</td></tr>
                  )}
                </tbody>
              </table>
            </div>
          </CardContent>
        </Card>
      )}
    </div>
  );
}

function MonthGrid({ events, today, onClickStock }: { events: CalEvent[]; today: string; onClickStock: (code: string) => void }) {
  const [hoveredDate, setHoveredDate] = useState<string | null>(null);

  // Build month range from events
  const months = useMemo(() => {
    const monthSet = new Set<string>();
    for (const e of events) monthSet.add(e.disclosure_date.slice(0, 7));
    return Array.from(monthSet).sort();
  }, [events]);

  const byDate = useMemo(() => {
    const map = new Map<string, CalEvent[]>();
    for (const e of events) {
      const k = e.disclosure_date;
      const arr = map.get(k) ?? [];
      arr.push(e);
      map.set(k, arr);
    }
    return map;
  }, [events]);

  if (!months.length) return (
    <div className="py-16 text-center text-muted-foreground text-sm">该时间范围内没有财报</div>
  );

  return (
    <div className="space-y-6">
      {months.map((month) => {
        const [y, m] = month.split("-").map(Number);
        const firstDay = new Date(y, m - 1, 1).getDay();
        const daysInMonth = new Date(y, m, 0).getDate();
        const weekdays = ["日", "一", "二", "三", "四", "五", "六"];
        const cells: (number | null)[] = [...Array(firstDay).fill(null), ...Array.from({ length: daysInMonth }, (_, i) => i + 1)];
        // pad to full weeks
        while (cells.length % 7 !== 0) cells.push(null);

        return (
          <div key={month}>
            <div className="text-sm font-semibold mb-2">{month}</div>
            <div className="grid grid-cols-7 gap-0.5 text-xs">
              {weekdays.map((w) => (
                <div key={w} className="text-center text-muted-foreground py-1">{w}</div>
              ))}
              {cells.map((day, i) => {
                if (!day) return <div key={i} />;
                const dateStr = `${month}-${String(day).padStart(2, "0")}`;
                const dayEvents = byDate.get(dateStr) ?? [];
                const isToday = dateStr === today;
                const isPast = dateStr < today;
                return (
                  <div
                    key={i}
                    className={`relative rounded p-1 min-h-[48px] border transition-colors cursor-default ${
                      isToday ? "border-primary bg-primary/5" : "border-transparent hover:border-border"
                    } ${isPast && !dayEvents.length ? "opacity-40" : ""}`}
                    onMouseEnter={() => dayEvents.length ? setHoveredDate(dateStr) : undefined}
                    onMouseLeave={() => setHoveredDate(null)}
                  >
                    <div className={`text-[10px] font-medium ${isToday ? "text-primary" : isPast ? "text-muted-foreground" : ""}`}>
                      {day}
                    </div>
                    {dayEvents.length > 0 && (
                      <div className="mt-0.5 space-y-0.5">
                        {dayEvents.slice(0, 2).map((e, j) => (
                          <div
                            key={j}
                            onClick={() => onClickStock(e.code)}
                            className={`text-[9px] truncate px-1 rounded cursor-pointer hover:opacity-80 ${reportColor(e.report_type)}`}
                          >
                            {e.name.slice(0, 4)}
                          </div>
                        ))}
                        {dayEvents.length > 2 && (
                          <div className="text-[9px] text-muted-foreground pl-1">+{dayEvents.length - 2}</div>
                        )}
                      </div>
                    )}
                    {/* Hover popup */}
                    {hoveredDate === dateStr && dayEvents.length > 0 && (
                      <div className="absolute z-20 top-full left-0 mt-1 w-48 bg-popover border rounded-lg shadow-lg p-2 space-y-1">
                        {dayEvents.map((e, j) => (
                          <div
                            key={j}
                            onClick={() => onClickStock(e.code)}
                            className="flex items-center gap-1.5 cursor-pointer hover:bg-accent rounded px-1 py-0.5"
                          >
                            <span className={`text-[9px] px-1 rounded ${reportColor(e.report_type)}`}>{e.report_label}</span>
                            <span className="text-[10px] font-mono">{e.code}</span>
                            <span className="text-[10px] truncate">{e.name}</span>
                          </div>
                        ))}
                      </div>
                    )}
                  </div>
                );
              })}
            </div>
          </div>
        );
      })}
    </div>
  );
}

export default function CalendarPageWrapper() {
  return (
    <Suspense fallback={<div className="p-8 animate-pulse text-sm text-muted-foreground">加载中…</div>}>
      <CalendarPage />
    </Suspense>
  );
}
