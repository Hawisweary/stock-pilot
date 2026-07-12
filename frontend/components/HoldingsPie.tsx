"use client";

import {
  PieChart, Pie, Cell, Tooltip, Legend, ResponsiveContainer,
} from "recharts";

interface Holding {
  code: string;
  name?: string;
  weight_pct: number;
}

const COLORS = [
  "#3b82f6", "#10b981", "#f59e0b", "#ef4444", "#8b5cf6",
  "#06b6d4", "#84cc16", "#f97316", "#ec4899", "#6b7280",
];

interface Props {
  holdings: Holding[];
  title?: string;
}

export function HoldingsPie({ holdings, title = "持仓分布" }: Props) {
  if (!holdings || !holdings.length) return null;

  const data = holdings
    .filter((h) => h.weight_pct > 0)
    .map((h) => ({
      name: h.name ? `${h.code} ${h.name}` : h.code,
      value: Math.round(h.weight_pct * 10) / 10,
    }))
    .sort((a, b) => b.value - a.value);

  if (!data.length) return null;

  return (
    <div className="space-y-1">
      <p className="text-xs font-medium text-muted-foreground">{title}</p>
      <ResponsiveContainer width="100%" height={220}>
        <PieChart>
          <Pie
            data={data}
            dataKey="value"
            nameKey="name"
            cx="50%"
            cy="50%"
            outerRadius={80}
            label={({ name, value }) => `${value}%`}
            labelLine={false}
          >
            {data.map((_, i) => (
              <Cell key={i} fill={COLORS[i % COLORS.length]} />
            ))}
          </Pie>
          <Tooltip formatter={(v) => [`${v}%`, "权重"]} />
          <Legend
            formatter={(value) => <span className="text-[10px]">{value}</span>}
          />
        </PieChart>
      </ResponsiveContainer>
    </div>
  );
}
