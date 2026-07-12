"use client";

import { useEffect, useState } from "react";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import {
  fetchBlockTrade,
  fetchDividends,
  fetchMargin,
  fetchShareholders,
  fetchUnlock,
} from "@/lib/signals";
import { DragonTigerStockPanel } from "@/components/DragonTigerCard";
import { Activity } from "lucide-react";

type Tab = "margin" | "dragon" | "unlock" | "block" | "holders" | "dividend";

const TABS: { id: Tab; label: string }[] = [
  { id: "margin", label: "融资融券" },
  { id: "dragon", label: "龙虎榜" },
  { id: "unlock", label: "限售解禁" },
  { id: "block", label: "大宗交易" },
  { id: "holders", label: "股东户数" },
  { id: "dividend", label: "分红" },
];

function renderRows(rows: Record<string, unknown>[], keys: string[]) {
  if (!rows.length) return <p className="text-xs text-muted-foreground py-2">暂无数据</p>;
  return (
    <div className="space-y-1 max-h-48 overflow-y-auto">
      {rows.slice(0, 8).map((r, i) => (
        <div key={i} className="text-xs border-b last:border-0 py-1 flex flex-wrap gap-x-2">
          {keys.map((k) =>
            r[k] != null ? (
              <span key={k}>
                <span className="text-muted-foreground">{k}:</span> {String(r[k])}
              </span>
            ) : null,
          )}
        </div>
      ))}
    </div>
  );
}

export function StockMarketSignalsPanel({ code }: { code: string }) {
  const [tab, setTab] = useState<Tab>("margin");
  const [payload, setPayload] = useState<unknown>(null);
  const [loading, setLoading] = useState(false);

  useEffect(() => {
    if (!code) return;
    if (tab === "dragon") {
      setPayload(null);
      setLoading(false);
      return;
    }
    setLoading(true);
    const loaders: Record<Exclude<Tab, "dragon">, () => Promise<unknown>> = {
      margin: () => fetchMargin(code),
      unlock: () => fetchUnlock(code),
      block: () => fetchBlockTrade(code),
      holders: () => fetchShareholders(code),
      dividend: () => fetchDividends(code),
    };
    loaders[tab]()
      .then(setPayload)
      .catch(() => setPayload(null))
      .finally(() => setLoading(false));
  }, [code, tab]);

  let body: React.ReactNode = null;
  const p = payload as Record<string, unknown> | null;
  if (loading) body = <p className="text-xs text-muted-foreground animate-pulse py-4">加载中…</p>;
  else if (tab === "margin" && p) body = renderRows((p.history as Record<string, unknown>[]) || [], ["date", "margin_balance", "margin_buy"]);
  else if (tab === "dragon" && code) body = <DragonTigerStockPanel code={code} />;
  else if (tab === "unlock" && p) body = renderRows((p.unlocks as Record<string, unknown>[]) || [], ["date", "shares", "ratio"]);
  else if (tab === "block" && p) {
    const rows = (p.trades as Record<string, unknown>[]) || [];
    body = renderRows(rows, rows[0] ? Object.keys(rows[0]).slice(0, 5) : ["date"]);
  } else if (tab === "holders" && p) {
    const rows = (p.history as Record<string, unknown>[]) || [];
    body = renderRows(rows, rows[0] ? Object.keys(rows[0]).slice(0, 5) : ["date"]);
  } else if (tab === "dividend" && p) {
    const rows = (p.history as Record<string, unknown>[]) || [];
    body = renderRows(rows, rows[0] ? Object.keys(rows[0]).slice(0, 5) : ["date"]);
  }

  return (
    <Card>
      <CardHeader className="pb-2">
        <CardTitle className="text-sm flex items-center gap-1.5">
          <Activity className="h-4 w-4" />
          市场信号
        </CardTitle>
        <div className="flex flex-wrap gap-1 mt-2">
          {TABS.map((t) => (
            <button
              key={t.id}
              type="button"
              onClick={() => setTab(t.id)}
              className={`text-[10px] px-2 py-0.5 rounded-full border transition-colors ${
                tab === t.id ? "bg-primary text-primary-foreground border-primary" : "border-border hover:bg-muted"
              }`}
            >
              {t.label}
            </button>
          ))}
        </div>
      </CardHeader>
      <CardContent className="pt-0">{body}</CardContent>
    </Card>
  );
}
