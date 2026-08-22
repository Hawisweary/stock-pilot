"use client";
import { useEffect, useState, useCallback } from "react";
import { X } from "lucide-react";
import { KLineChart } from "@/components/KLineChart";

type Item = {
  ticker: string; name: string; last: number | null; is_yield?: boolean;
  chg_1d?: number | null; chg_1w?: number | null; chg_1m?: number | null; chg_ytd?: number | null;
  bp_1d?: number | null;
};
type Group = { group: string; items: Item[] };
type Bar = { trade_date: string; open: number; high: number; low: number; close: number; volume: number | null };
type Freq = "D" | "W" | "M" | "Y";

const US_UP = "#22c55e", US_DOWN = "#ef4444";
type SortKey = "chg_1d" | "chg_1w" | "chg_1m" | "chg_ytd";
const COLS: { key: SortKey; label: string }[] = [
  { key: "chg_1d", label: "日" }, { key: "chg_1w", label: "周" },
  { key: "chg_1m", label: "月" }, { key: "chg_ytd", label: "年初至今" },
];
const color = (v?: number | null) => v == null ? "var(--muted-foreground)" : v > 0 ? US_UP : v < 0 ? US_DOWN : "var(--muted-foreground)";
const pct = (v?: number | null) => v == null ? "—" : `${v > 0 ? "+" : ""}${v.toFixed(2)}%`;
const fmtLast = (v?: number | null) => v == null ? "—" : v.toLocaleString(undefined, { maximumFractionDigits: 2 });

export default function GlobalPage() {
  const [groups, setGroups] = useState<Group[] | null>(null);
  const [asOf, setAsOf] = useState<string | null>(null);
  const [sort, setSort] = useState<{ key: SortKey; dir: 1 | -1 } | null>(null);
  const [sel, setSel] = useState<Item | null>(null);

  useEffect(() => {
    fetch("/api/global/overview").then((r) => r.json()).then((d) => {
      setGroups(d.groups ?? []); setAsOf(d.as_of ?? null);
    }).catch(() => setGroups([]));
  }, []);

  const sortItems = useCallback((items: Item[]) => {
    if (!sort) return items;
    return [...items].sort((a, b) => {
      const av = a[sort.key], bv = b[sort.key];
      if (av == null) return 1; if (bv == null) return -1;
      return (bv - av) * sort.dir;
    });
  }, [sort]);
  const clickSort = (k: SortKey) =>
    setSort((s) => s?.key === k ? (s.dir === -1 ? { key: k, dir: 1 } : null) : { key: k, dir: -1 });

  return (
    <div className="space-y-4">
      <div>
        <h1 className="text-xl font-bold">全球市场</h1>
        <p className="text-xs text-muted-foreground mt-1">
          股指 · 汇率 · 大宗 · 美债 · 加密 · 数据 {asOf ?? "—"} · 点标的看K线 · 点表头按周期排序
        </p>
      </div>

      {groups == null ? (
        <div className="animate-pulse h-96 bg-muted rounded" />
      ) : (
        <div className="grid grid-cols-1 xl:grid-cols-2 gap-4">
          {groups.map((g) => (
            <div key={g.group} className="rounded-xl border bg-card overflow-hidden">
              <div className="px-3 py-2 text-sm font-semibold border-b bg-muted/30">{g.group}</div>
              <table className="w-full text-sm">
                <thead className="text-[11px] text-muted-foreground">
                  <tr>
                    <th className="px-3 py-1.5 text-left font-medium">名称</th>
                    <th className="px-3 py-1.5 text-right font-medium">{g.group === "美债收益率" ? "利率%" : "最新"}</th>
                    {COLS.map((c) => (
                      <th key={c.key} onClick={() => clickSort(c.key)}
                        className="px-3 py-1.5 text-right font-medium cursor-pointer select-none hover:text-foreground">
                        {c.label}{sort?.key === c.key ? (sort.dir === -1 ? " ↓" : " ↑") : ""}
                      </th>
                    ))}
                  </tr>
                </thead>
                <tbody>
                  {sortItems(g.items).map((r) => (
                    <tr key={r.ticker} onClick={() => setSel(r)}
                      className="border-t border-border/60 hover:bg-accent/40 cursor-pointer">
                      <td className="px-3 py-1.5 font-medium">{r.name}</td>
                      <td className="px-3 py-1.5 text-right tabular-nums">
                        {fmtLast(r.last)}{r.is_yield && r.last != null ? "%" : ""}</td>
                      {COLS.map((c) => (
                        <td key={c.key} className="px-3 py-1.5 text-right tabular-nums" style={{ color: color(r[c.key]) }}>
                          {pct(r[c.key])}</td>
                      ))}
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          ))}
        </div>
      )}

      {sel && <KlineModal item={sel} onClose={() => setSel(null)} />}
    </div>
  );
}

function KlineModal({ item, onClose }: { item: Item; onClose: () => void }) {
  const [bars, setBars] = useState<Bar[] | null>(null);
  const [freq, setFreq] = useState<Freq>("D");
  useEffect(() => {
    setBars(null);
    fetch(`/api/us/${encodeURIComponent(item.ticker)}/klines?freq=${freq}&adjust=false`)
      .then((r) => r.json()).then((j) => setBars(j.bars ?? [])).catch(() => setBars([]));
  }, [item.ticker, freq]);
  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/40 p-4" onClick={onClose}>
      <div className="w-full max-w-4xl rounded-xl border bg-card p-4 shadow-xl" onClick={(e) => e.stopPropagation()}>
        <div className="flex items-center justify-between mb-3">
          <div>
            <div className="text-base font-bold">{item.name}</div>
            <div className="text-xs text-muted-foreground font-mono">{item.ticker}</div>
          </div>
          <div className="flex items-center gap-2">
            {(["D", "W", "M", "Y"] as Freq[]).map((f) => (
              <button key={f} onClick={() => setFreq(f)}
                className={`text-xs px-2.5 py-1 rounded-md border ${freq === f ? "bg-primary text-primary-foreground border-primary" : "hover:bg-accent border-border"}`}>
                {f === "D" ? "日K" : f === "W" ? "周K" : f === "M" ? "月K" : "年K"}
              </button>
            ))}
            <button onClick={onClose} className="p-1 rounded hover:bg-accent"><X className="h-4 w-4" /></button>
          </div>
        </div>
        {bars == null ? (
          <div className="animate-pulse h-80 bg-muted rounded" />
        ) : bars.length > 1 ? (
          <KLineChart data={bars.map((b) => ({ date: b.trade_date, open: b.open, high: b.high, low: b.low, close: b.close, volume: b.volume ?? undefined }))}
            height={400} greenUp />
        ) : <div className="text-xs text-muted-foreground py-16 text-center">暂无K线数据</div>}
      </div>
    </div>
  );
}
