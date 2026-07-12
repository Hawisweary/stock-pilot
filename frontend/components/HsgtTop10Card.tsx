"use client";

import { useEffect, useState, useCallback } from "react";
import Link from "next/link";
import { Card, CardHeader, CardTitle, CardContent } from "@/components/ui/card";
import { ArrowRightLeft } from "lucide-react";

interface HsgtRow {
  code: string;
  name: string;
  close: number | null;
  change: number | null;
  rank: number;
  amount: number | null;
  net_amount: number | null;
  buy: number | null;
  sell: number | null;
}

interface HsgtResp {
  trade_date: string | null;
  sh: HsgtRow[];
  sz: HsgtRow[];
}

function fmtYi(v: number | null): string {
  if (v == null) return "--";
  return `${(v / 1e8).toFixed(2)}亿`;
}

function List({ rows }: { rows: HsgtRow[] }) {
  if (rows.length === 0) return <p className="text-xs text-muted-foreground py-2">暂无数据</p>;
  return (
    <table className="w-full text-xs">
      <thead>
        <tr className="text-muted-foreground text-left">
          <th className="py-1 font-normal">名次</th>
          <th className="py-1 font-normal">股票</th>
          <th className="py-1 font-normal text-right">收盘价</th>
          <th className="py-1 font-normal text-right">涨跌额</th>
          <th className="py-1 font-normal text-right">成交额</th>
          <th className="py-1 font-normal text-right">净买入</th>
        </tr>
      </thead>
      <tbody>
        {rows.map((r) => (
          <tr key={r.code} className="border-t">
            <td className="py-1">{r.rank}</td>
            <td className="py-1">
              <Link href={`/stocks/${r.code}`} className="hover:underline">
                {r.name} <span className="text-muted-foreground font-mono">{r.code}</span>
              </Link>
            </td>
            <td className="py-1 text-right font-mono">{r.close?.toFixed(2) ?? "--"}</td>
            <td className={`py-1 text-right font-mono ${(r.change ?? 0) >= 0 ? "text-red-600" : "text-green-600"}`}>
              {r.change != null ? (r.change >= 0 ? "+" : "") + r.change.toFixed(2) : "--"}
            </td>
            <td className="py-1 text-right font-mono">{fmtYi(r.amount)}</td>
            <td className={`py-1 text-right font-mono ${(r.net_amount ?? 0) >= 0 ? "text-red-600" : "text-green-600"}`}>
              {fmtYi(r.net_amount)}
            </td>
          </tr>
        ))}
      </tbody>
    </table>
  );
}

export function HsgtTop10Card() {
  const [data, setData] = useState<HsgtResp | null>(null);
  const [tab, setTab] = useState<"sh" | "sz">("sh");
  const [loading, setLoading] = useState(true);

  const load = useCallback(() => {
    setLoading(true);
    fetch(`/api/market/hsgt-top10`)
      .then((r) => (r.ok ? r.json() : null))
      .then(setData)
      .catch(() => {})
      .finally(() => setLoading(false));
  }, []);

  useEffect(() => { load(); }, [load]);

  return (
    <Card>
      <CardHeader className="pb-2">
        <CardTitle className="text-sm flex items-center justify-between">
          <span className="flex items-center gap-2">
            <ArrowRightLeft className="h-4 w-4" /> 沪深股通十大成交股
            {data?.trade_date && (
              <span className="text-xs font-normal text-muted-foreground">· {data.trade_date}</span>
            )}
          </span>
          <span className="flex rounded-md border overflow-hidden text-xs">
            <button
              onClick={() => setTab("sh")}
              className={`px-2 py-1 ${tab === "sh" ? "bg-primary text-primary-foreground" : "hover:bg-muted"}`}
            >
              沪股通
            </button>
            <button
              onClick={() => setTab("sz")}
              className={`px-2 py-1 ${tab === "sz" ? "bg-primary text-primary-foreground" : "hover:bg-muted"}`}
            >
              深股通
            </button>
          </span>
        </CardTitle>
      </CardHeader>
      <CardContent className="pt-0">
        {loading ? (
          <p className="text-xs text-muted-foreground py-2">加载中...</p>
        ) : (
          <List rows={tab === "sh" ? data?.sh ?? [] : data?.sz ?? []} />
        )}
      </CardContent>
    </Card>
  );
}
