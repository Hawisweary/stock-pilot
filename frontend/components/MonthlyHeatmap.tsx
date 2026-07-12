"use client";

interface DailyPoint {
  date: string;
  value: number;
}

interface Props {
  daily: DailyPoint[];
}

/** 从日净值序列计算月度收益率矩阵 */
function calcMonthlyReturns(daily: DailyPoint[]): Record<number, Record<number, number>> {
  if (!daily.length) return {};
  // 按月分组，取每月第一天和最后一天
  const byMonth: Record<string, DailyPoint[]> = {};
  for (const pt of daily) {
    const key = pt.date.slice(0, 7); // YYYY-MM
    if (!byMonth[key]) byMonth[key] = [];
    byMonth[key].push(pt);
  }
  const result: Record<number, Record<number, number>> = {};
  for (const [key, pts] of Object.entries(byMonth)) {
    const [y, m] = key.split("-").map(Number);
    const first = pts[0].value;
    const last = pts[pts.length - 1].value;
    const ret = first > 0 ? ((last / first) - 1) * 100 : 0;
    if (!result[y]) result[y] = {};
    result[y][m] = Math.round(ret * 100) / 100;
  }
  return result;
}

const MONTH_LABELS = ["1月", "2月", "3月", "4月", "5月", "6月", "7月", "8月", "9月", "10月", "11月", "12月"];

function colorForReturn(ret: number): string {
  if (ret > 8) return "bg-green-700 text-white";
  if (ret > 4) return "bg-green-500 text-white";
  if (ret > 1) return "bg-green-300 text-green-900";
  if (ret > -1) return "bg-gray-100 text-gray-600";
  if (ret > -4) return "bg-red-200 text-red-900";
  if (ret > -8) return "bg-red-400 text-white";
  return "bg-red-700 text-white";
}

export function MonthlyHeatmap({ daily }: Props) {
  const matrix = calcMonthlyReturns(daily);
  const years = Object.keys(matrix).map(Number).sort();

  if (!years.length) return null;

  return (
    <div className="space-y-2">
      <p className="text-xs font-medium text-muted-foreground">月度收益热力图</p>
      <div className="overflow-x-auto">
        <table className="text-[10px] border-separate border-spacing-0.5 w-full min-w-[640px]">
          <thead>
            <tr>
              <th className="text-left pr-2 text-muted-foreground font-normal w-12">年</th>
              {MONTH_LABELS.map((l) => (
                <th key={l} className="text-center text-muted-foreground font-normal w-14">{l}</th>
              ))}
              <th className="text-center text-muted-foreground font-normal w-14">全年</th>
            </tr>
          </thead>
          <tbody>
            {years.map((y) => {
              const row = matrix[y];
              // 全年收益 = 各月复利乘积
              const annualRet = Object.values(row).reduce(
                (acc, r) => acc * (1 + r / 100), 1
              );
              const annualPct = Math.round((annualRet - 1) * 10000) / 100;
              return (
                <tr key={y}>
                  <td className="pr-2 text-muted-foreground">{y}</td>
                  {Array.from({ length: 12 }, (_, i) => i + 1).map((m) => {
                    const ret = row[m];
                    if (ret == null) {
                      return <td key={m} className="rounded text-center bg-gray-50 text-gray-300">—</td>;
                    }
                    return (
                      <td key={m} className={`rounded text-center py-1 ${colorForReturn(ret)}`}>
                        {ret > 0 ? "+" : ""}{ret}%
                      </td>
                    );
                  })}
                  <td className={`rounded text-center py-1 font-medium ${colorForReturn(annualPct)}`}>
                    {annualPct > 0 ? "+" : ""}{annualPct}%
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
      <div className="flex gap-2 items-center text-[9px] text-muted-foreground flex-wrap">
        {[
          { label: ">+8%", cls: "bg-green-700" },
          { label: "+4~8%", cls: "bg-green-500" },
          { label: "+1~4%", cls: "bg-green-300" },
          { label: "±1%", cls: "bg-gray-100 border" },
          { label: "-1~4%", cls: "bg-red-200" },
          { label: "-4~8%", cls: "bg-red-400" },
          { label: "<-8%", cls: "bg-red-700" },
        ].map(({ label, cls }) => (
          <span key={label} className="flex items-center gap-1">
            <span className={`w-3 h-3 rounded ${cls} inline-block`} />
            {label}
          </span>
        ))}
      </div>
    </div>
  );
}
