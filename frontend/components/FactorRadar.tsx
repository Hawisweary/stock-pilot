"use client";

import { useState, useEffect, useRef } from "react";
import {
  RadarChart, PolarGrid, PolarAngleAxis, PolarRadiusAxis, Radar,
  ResponsiveContainer, Legend,
} from "recharts";

interface StockData {
  name: string;
  profitability: number;
  growth: number;
  safety: number;
  value: number;
  momentum?: number;
}

interface Props {
  // 单股票模式
  profitability?: number;
  growth?: number;
  safety?: number;
  value?: number;
  momentum?: number;
  // 多股票叠加模式
  peers?: StockData[];
  size?: number;
}

const COLORS = ["#3b82f6", "#ef4444", "#10b981", "#f59e0b", "#8b5cf6", "#ec4899", "#06b6d4"];

export function FactorRadar(props: Props) {
  const { peers, size = 320 } = props;
  const [mounted, setMounted] = useState(false);
  useEffect(() => { setMounted(true); }, []);

  // 多股票叠加模式
  if (peers && peers.length > 0) {
    const data = [
      { factor: "Quality" },
      { factor: "Growth" },
      { factor: "Value" },
      { factor: "FundMom" },
      { factor: "Risk" },
    ].map(d => {
      const entry: Record<string, string | number> = { factor: d.factor };
      for (const p of peers) {
        const map: Record<string, number> = {
          Quality: p.profitability,
          Growth: p.growth,
          Value: p.value,
          FundMom: p.momentum ?? 0,
          Risk: p.safety,
        };
        entry[p.name] = map[d.factor] ?? 0;
      }
      return entry;
    });

    return (
      <div style={{ width: size, height: size + 40, minWidth: size, minHeight: size + 40 }}>
        {mounted && (
        <ResponsiveContainer width="100%" height="100%">
          <RadarChart data={data} cx="50%" cy="45%" outerRadius="65%">
            <PolarGrid stroke="#e5e7eb" />
            <PolarAngleAxis dataKey="factor" tick={{ fontSize: 11 }} />
            <PolarRadiusAxis angle={30} domain={[0, 100]} tick={{ fontSize: 9 }} />
            {peers.map((p, i) => (
              <Radar
                key={p.name}
                dataKey={p.name}
                stroke={COLORS[i % COLORS.length]}
                fill={COLORS[i % COLORS.length]}
                fillOpacity={0.08}
                strokeWidth={2}
              />
            ))}
            <Legend wrapperStyle={{ fontSize: 11, paddingTop: 8 }} />
          </RadarChart>
        </ResponsiveContainer>
        )}
      </div>
    );
  }

  // 单股票模式
  const { profitability, growth, safety, value, momentum: mom } = props;
  const data = [
    { factor: "Quality", score: profitability },
    { factor: "Growth", score: growth },
    { factor: "Value", score: value },
    { factor: "FundMom", score: mom ?? null },
    { factor: "Risk", score: safety },
  ].filter(d => d.score != null);

  return (
    <div style={{ width: size, height: size, minWidth: size, minHeight: size }}>
      <ResponsiveContainer width={size} height={size}>
        <RadarChart data={data} cx="50%" cy="50%" outerRadius="70%">
          <PolarGrid stroke="#e5e7eb" />
          <PolarAngleAxis dataKey="factor" tick={{ fontSize: 12 }} />
          <PolarRadiusAxis angle={30} domain={[0, 100]} tick={{ fontSize: 10 }} />
          <Radar dataKey="score" stroke="#3b82f6" fill="#3b82f6" fillOpacity={0.3} />
        </RadarChart>
      </ResponsiveContainer>
    </div>
  );
}
