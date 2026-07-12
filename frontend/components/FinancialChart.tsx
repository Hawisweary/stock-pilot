"use client";

import { useRef, useState, useEffect, useLayoutEffect } from "react";
import {
  LineChart,
  Line,
  XAxis,
  YAxis,
  CartesianGrid,
  Tooltip,
  ResponsiveContainer,
  Legend,
  BarChart,
  Bar,
} from "recharts";

interface Props {
  data: Record<string, unknown>[];
  xKey: string;
  lines?: { key: string; name: string; color: string }[];
  bars?: { key: string; name: string; color: string }[];
  type?: "line" | "bar";
  height?: number;
}

export function FinancialChart({
  data,
  xKey,
  lines = [],
  bars = [],
  type = "line",
  height = 300,
}: Props) {
  const containerRef = useRef<HTMLDivElement>(null);
  const [w, setW] = useState(0);
  const [h, setH] = useState(0);

  // 使用 useLayoutEffect 在浏览器布局完成后测量，避免闪烁
  useLayoutEffect(() => {
    const el = containerRef.current;
    if (!el) return;

    const measure = () => {
      const cw = el.offsetWidth;
      const ch = el.offsetHeight;
      if (cw > 0 && ch > 0 && (cw !== w || ch !== h)) {
        setW(cw);
        setH(ch);
      }
    };

    measure();
    const ro = new ResizeObserver(measure);
    ro.observe(el);
    return () => ro.disconnect();
  }, []);

  const formatValue = (value: number) => {
    if (Math.abs(value) >= 1e8) return `${(value / 1e8).toFixed(2)}亿`;
    if (Math.abs(value) >= 1e4) return `${(value / 1e4).toFixed(1)}万`;
    return value.toFixed(2);
  };

  if (w === 0 || h === 0) {
    return <div ref={containerRef} className="w-full" style={{ height, minHeight: height, minWidth: 200 }} />;
  }

  if (type === "bar") {
    return (
      <div ref={containerRef} className="w-full" style={{ height, minHeight: height }}>
        <ResponsiveContainer width="100%" height={height} minWidth={w} minHeight={height}>
          <BarChart data={data}>
            <CartesianGrid strokeDasharray="3 3" />
            <XAxis dataKey={xKey} tick={{ fontSize: 12 }} />
            <YAxis tickFormatter={formatValue} tick={{ fontSize: 11 }} />
            <Tooltip formatter={(value: any) => [formatValue(Number(value)), ""] as any} />
            <Legend />
            {bars.map((bar) => (
              <Bar key={bar.key} dataKey={bar.key} name={bar.name} fill={bar.color} />
            ))}
          </BarChart>
        </ResponsiveContainer>
      </div>
    );
  }

  return (
    <div ref={containerRef} className="w-full" style={{ height, minHeight: height }}>
      <ResponsiveContainer width="100%" height={height} minWidth={w} minHeight={height}>
        <LineChart data={data}>
          <CartesianGrid strokeDasharray="3 3" />
          <XAxis dataKey={xKey} tick={{ fontSize: 12 }} />
          <YAxis yAxisId="left" tickFormatter={formatValue} tick={{ fontSize: 11 }} />
          <YAxis yAxisId="right" orientation="right" tickFormatter={formatValue} tick={{ fontSize: 11 }} />
          <Tooltip formatter={(value: any) => [formatValue(Number(value)), ""] as any} />
          <Legend />
          {lines.map((line, i) => (
            <Line
              key={line.key}
              yAxisId={i < 2 ? "left" : "right"}
              type="monotone"
              dataKey={line.key}
              name={line.name}
              stroke={line.color}
              strokeWidth={2}
              dot={false}
            />
          ))}
        </LineChart>
      </ResponsiveContainer>
    </div>
  );
}
