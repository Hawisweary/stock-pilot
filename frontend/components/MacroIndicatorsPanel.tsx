"use client";

import { useEffect, useState } from "react";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { fetchMacroIndicators, type MacroIndicatorRow } from "@/lib/marketExtras";
import { Globe } from "lucide-react";

const FIELDS: { key: keyof MacroIndicatorRow; label: string; suffix?: string }[] = [
  { key: "gdp", label: "GDP" },
  { key: "gdp_yoy", label: "GDP同比", suffix: "%" },
  { key: "cpi", label: "CPI" },
  { key: "cpi_yoy", label: "CPI同比", suffix: "%" },
  { key: "pmi_manufacturing", label: "制造业PMI" },
  { key: "pmi_services", label: "服务业PMI" },
  { key: "lpr_1y", label: "LPR 1Y", suffix: "%" },
  { key: "lpr_5y", label: "LPR 5Y", suffix: "%" },
  { key: "m2", label: "M2" },
  { key: "m2_yoy", label: "M2同比", suffix: "%" },
  { key: "shibor_overnight", label: "隔夜Shibor", suffix: "%" },
  { key: "social_financing", label: "新增信贷(亿)" },
  { key: "social_financing_yoy", label: "信贷同比", suffix: "%" },
  { key: "bond_yield_10y", label: "10Y国债", suffix: "%" },
  { key: "usd_cnh", label: "USD/CNH" },
];

export function MacroIndicatorsPanel({
  compact,
  refreshKey = 0,
}: {
  compact?: boolean;
  refreshKey?: number;
}) {
  const [rows, setRows] = useState<MacroIndicatorRow[]>([]);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    setLoading(true);
    fetchMacroIndicators(refreshKey > 0)
      .then(setRows)
      .catch(() => setRows([]))
      .finally(() => setLoading(false));
  }, [refreshKey]);

  const latest = rows[0];
  if (loading) {
    return <Card><CardContent className="p-4 h-20 animate-pulse" /></Card>;
  }
  if (!latest) return null;

  if (compact) {
    return (
      <Card>
        <CardContent className="p-3">
          <div className="text-[10px] text-muted-foreground mb-1 flex items-center gap-1">
            <Globe className="h-3 w-3" /> 宏观指标 · {latest.date}
          </div>
          <div className="flex flex-wrap gap-x-3 gap-y-0.5 text-xs">
            {FIELDS.map((f) => {
              const v = latest[f.key];
              if (v == null) return null;
              return (
                <span key={String(f.key)}>
                  {f.label}: <b>{v}{f.suffix || ""}</b>
                </span>
              );
            })}
          </div>
        </CardContent>
      </Card>
    );
  }

  return (
    <Card>
      <CardHeader className="pb-2">
        <CardTitle className="text-sm flex items-center gap-1.5">
          <Globe className="h-4 w-4" />
          宏观指标历史
        </CardTitle>
      </CardHeader>
      <CardContent className="overflow-x-auto pt-0">
        <table className="w-full text-xs">
          <thead>
            <tr className="text-muted-foreground border-b">
              <th className="text-left py-1 pr-2">日期</th>
              {FIELDS.map((f) => (
                <th key={String(f.key)} className="text-right py-1 px-1 whitespace-nowrap">
                  {f.label}
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {rows.map((r) => (
              <tr key={r.date} className="border-b last:border-0">
                <td className="py-1 pr-2 text-muted-foreground">{r.date}</td>
                {FIELDS.map((f) => (
                  <td key={String(f.key)} className="text-right py-1 px-1 tabular-nums">
                    {r[f.key] != null ? `${r[f.key]}${f.suffix || ""}` : "—"}
                  </td>
                ))}
              </tr>
            ))}
          </tbody>
        </table>
      </CardContent>
    </Card>
  );
}
