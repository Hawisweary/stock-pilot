"use client";

import { useEffect, useState } from "react";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Wallet } from "lucide-react";

interface L2Row {
  trade_date: string;
  buy_sm_amount: number | null;
  sell_sm_amount: number | null;
  buy_md_amount: number | null;
  sell_md_amount: number | null;
  buy_lg_amount: number | null;
  sell_lg_amount: number | null;
  buy_elg_amount: number | null;
  sell_elg_amount: number | null;
  net_mf_amount: number | null;
}

interface DcRow {
  trade_date: string;
  net_amount: number | null;
  net_amount_rate: number | null;
  buy_elg_amount: number | null;
  buy_lg_amount: number | null;
  buy_md_amount: number | null;
  buy_sm_amount: number | null;
}

function fmtWan(v: number | null): string {
  if (v == null) return "--";
  const wan = v / 1e4;
  return `${wan >= 0 ? "+" : ""}${wan.toFixed(0)}万`;
}

function toneClass(v: number | null): string {
  if (v == null) return "text-muted-foreground";
  return v >= 0 ? "text-red-600" : "text-green-600";
}

export function MoneyFlowDetailCard({ stockId }: { stockId: number }) {
  const [l2, setL2] = useState<L2Row[]>([]);
  const [dc, setDc] = useState<DcRow[]>([]);
  const [loading, setLoading] = useState(true);
  const [tab, setTab] = useState<"l2" | "dc">("l2");

  useEffect(() => {
    if (!stockId) return;
    setLoading(true);
    fetch(`/api/stocks/${stockId}/moneyflow-detail`)
      .then((r) => (r.ok ? r.json() : null))
      .then((d) => {
        setL2(d?.l2 ?? []);
        setDc(d?.dc ?? []);
      })
      .catch(() => {})
      .finally(() => setLoading(false));
  }, [stockId]);

  if (loading) return null;
  if (l2.length === 0 && dc.length === 0) return null;

  const latestL2 = l2[0];
  const latestDc = dc[0];

  return (
    <Card>
      <CardHeader className="pb-2">
        <CardTitle className="text-sm flex items-center justify-between">
          <span className="flex items-center gap-2">
            <Wallet className="h-4 w-4" /> 资金流明细
          </span>
          <span className="flex rounded-md border overflow-hidden text-xs">
            <button
              onClick={() => setTab("l2")}
              className={`px-2 py-1 ${tab === "l2" ? "bg-primary text-primary-foreground" : "hover:bg-muted"}`}
            >
              L2大小单
            </button>
            <button
              onClick={() => setTab("dc")}
              className={`px-2 py-1 ${tab === "dc" ? "bg-primary text-primary-foreground" : "hover:bg-muted"}`}
            >
              东财口径
            </button>
          </span>
        </CardTitle>
        <p className="text-[11px] text-muted-foreground">
          两套独立数据源（交易所逐笔委托 vs 东方财富），数值有差异属正常
        </p>
      </CardHeader>
      <CardContent className="pt-0">
        {tab === "l2" && latestL2 && (
          <div className="space-y-1.5">
            <p className="text-xs text-muted-foreground mb-1">{latestL2.trade_date}</p>
            <div className="grid grid-cols-2 gap-x-4 gap-y-1 text-xs">
              <div>
                <span className="text-muted-foreground">超大单 </span>
                <span className={toneClass((latestL2.buy_elg_amount ?? 0) - (latestL2.sell_elg_amount ?? 0))}>
                  {fmtWan((latestL2.buy_elg_amount ?? 0) - (latestL2.sell_elg_amount ?? 0))}
                </span>
              </div>
              <div>
                <span className="text-muted-foreground">大单 </span>
                <span className={toneClass((latestL2.buy_lg_amount ?? 0) - (latestL2.sell_lg_amount ?? 0))}>
                  {fmtWan((latestL2.buy_lg_amount ?? 0) - (latestL2.sell_lg_amount ?? 0))}
                </span>
              </div>
              <div>
                <span className="text-muted-foreground">中单 </span>
                <span className={toneClass((latestL2.buy_md_amount ?? 0) - (latestL2.sell_md_amount ?? 0))}>
                  {fmtWan((latestL2.buy_md_amount ?? 0) - (latestL2.sell_md_amount ?? 0))}
                </span>
              </div>
              <div>
                <span className="text-muted-foreground">小单 </span>
                <span className={toneClass((latestL2.buy_sm_amount ?? 0) - (latestL2.sell_sm_amount ?? 0))}>
                  {fmtWan((latestL2.buy_sm_amount ?? 0) - (latestL2.sell_sm_amount ?? 0))}
                </span>
              </div>
              <div className="col-span-2 pt-1 border-t">
                <span className="text-muted-foreground">净流入 </span>
                <span className={`font-semibold ${toneClass(latestL2.net_mf_amount)}`}>
                  {fmtWan(latestL2.net_mf_amount)}
                </span>
              </div>
            </div>
          </div>
        )}
        {tab === "dc" && latestDc && (
          <div className="space-y-1.5">
            <p className="text-xs text-muted-foreground mb-1">{latestDc.trade_date}</p>
            <div className="grid grid-cols-2 gap-x-4 gap-y-1 text-xs">
              <div>
                <span className="text-muted-foreground">超大单净额 </span>
                <span className={toneClass(latestDc.buy_elg_amount)}>{fmtWan(latestDc.buy_elg_amount)}</span>
              </div>
              <div>
                <span className="text-muted-foreground">大单净额 </span>
                <span className={toneClass(latestDc.buy_lg_amount)}>{fmtWan(latestDc.buy_lg_amount)}</span>
              </div>
              <div>
                <span className="text-muted-foreground">中单净额 </span>
                <span className={toneClass(latestDc.buy_md_amount)}>{fmtWan(latestDc.buy_md_amount)}</span>
              </div>
              <div>
                <span className="text-muted-foreground">小单净额 </span>
                <span className={toneClass(latestDc.buy_sm_amount)}>{fmtWan(latestDc.buy_sm_amount)}</span>
              </div>
              <div className="col-span-2 pt-1 border-t">
                <span className="text-muted-foreground">净流入 </span>
                <span className={`font-semibold ${toneClass(latestDc.net_amount)}`}>
                  {fmtWan(latestDc.net_amount)}
                  {latestDc.net_amount_rate != null && (
                    <span className="text-muted-foreground font-normal"> ({latestDc.net_amount_rate.toFixed(2)}%)</span>
                  )}
                </span>
              </div>
            </div>
          </div>
        )}
      </CardContent>
    </Card>
  );
}
