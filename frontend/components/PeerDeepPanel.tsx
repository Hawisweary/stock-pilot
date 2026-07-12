"use client";

import { useEffect, useState } from "react";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { api, DeepPeersResponse } from "@/lib/api";

export function PeerDeepPanel({ stockId }: { stockId: number }) {
  const [data, setData] = useState<DeepPeersResponse | null>(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    if (!stockId) return;
    setLoading(true);
    api
      .getDeepPeers(stockId)
      .then(setData)
      .catch(() => setData(null))
      .finally(() => setLoading(false));
  }, [stockId]);

  if (loading) {
    return (
      <Card>
        <CardHeader>
          <CardTitle className="text-sm">深度同业对比</CardTitle>
        </CardHeader>
        <CardContent className="text-xs text-muted-foreground animate-pulse">
          加载同业数据…
        </CardContent>
      </Card>
    );
  }

  if (!data || data.error) {
    return (
      <Card>
        <CardHeader>
          <CardTitle className="text-sm">深度同业对比</CardTitle>
        </CardHeader>
        <CardContent className="text-xs text-muted-foreground">
          {data?.error || "暂无同业数据（请确认行业分类与估值快照）"}
        </CardContent>
      </Card>
    );
  }

  const pct = data.percentiles || {};
  const summary = data.summary || { strengths: [], weaknesses: [] };

  return (
    <Card>
      <CardHeader>
        <CardTitle className="text-sm">
          深度同业 · {data.industry}（{data.peer_count} 家，市值±
          {Math.round((data.market_cap_band ?? 0.5) * 100)}%）
        </CardTitle>
      </CardHeader>
      <CardContent className="space-y-3 text-xs">
        <div className="grid grid-cols-3 gap-2">
          {[
            ["PE 分位", pct.pe],
            ["ROE 分位", pct.roe],
            ["综合分位", pct.score ?? pct.composite_v5],
            ["成长分位", pct.growth_score],
            ["估值分位", pct.value_score],
            ["毛利率分位", pct.gross_margin],
          ].map(([label, val]) => (
            <div key={label as string} className="rounded border p-2 text-center">
              <div className="text-muted-foreground">{label}</div>
              <div className="font-mono text-sm font-semibold">
                {val != null ? `${val}%` : "—"}
              </div>
            </div>
          ))}
        </div>
        {summary.strengths?.length > 0 && (
          <div>
            <div className="font-medium text-emerald-600 mb-1">相对优势</div>
            <ul className="list-disc pl-4 space-y-0.5">
              {summary.strengths.map((s) => (
                <li key={s}>{s}</li>
              ))}
            </ul>
          </div>
        )}
        {summary.weaknesses?.length > 0 && (
          <div>
            <div className="font-medium text-amber-600 mb-1">相对短板</div>
            <ul className="list-disc pl-4 space-y-0.5">
              {summary.weaknesses.map((s) => (
                <li key={s}>{s}</li>
              ))}
            </ul>
          </div>
        )}
        {data.peers.length > 0 && (
          <div className="overflow-x-auto max-h-40">
            <table className="w-full text-[10px]">
              <thead>
                <tr className="text-muted-foreground border-b">
                  <th className="text-left py-1">代码</th>
                  <th>PE</th>
                  <th>ROE</th>
                  <th>V5</th>
                </tr>
              </thead>
              <tbody>
                {data.peers.slice(0, 8).map((p) => (
                  <tr key={String(p.code)} className="border-b border-border/50">
                    <td className="py-0.5 font-mono">{String(p.code)}</td>
                    <td className="text-center">{p.pe_ttm != null ? Number(p.pe_ttm).toFixed(1) : "—"}</td>
                    <td className="text-center">{p.roe != null ? Number(p.roe).toFixed(1) : "—"}</td>
                    <td className="text-center">{(p.score ?? p.composite_v5) != null ? Number(p.score ?? p.composite_v5).toFixed(1) : "—"}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </CardContent>
    </Card>
  );
}
