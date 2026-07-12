"use client";

import { useEffect, useRef } from "react";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";

type Pt = { date: string; value: number };

function fmtDate(d: string): string {
  // "2025-01-15" → "01/15"；跨年时显示 "25/01"
  const parts = d.slice(0, 10).split("-");
  if (parts.length < 3) return d.slice(5, 10).replace("-", "/");
  return `${parts[1]}/${parts[2]}`;
}

function fmtPct(nav: number): string {
  const pct = (nav - 1) * 100;
  return (pct >= 0 ? "+" : "") + pct.toFixed(1) + "%";
}

export function BetaDualChart({
  strategy,
  benchmark,
  compare,
  compareLabel = "对比",
  title = "策略 vs 基准",
}: {
  strategy: Pt[];
  benchmark?: Pt[];
  compare?: Pt[];
  compareLabel?: string;
  title?: string;
}) {
  const ref = useRef<HTMLCanvasElement>(null);

  useEffect(() => {
    const cv = ref.current;
    if (!cv || !strategy.length) return;
    const ctx = cv.getContext("2d")!;
    const dpr = window.devicePixelRatio || 2;
    const W = cv.offsetWidth * dpr;
    const H = 240 * dpr;
    cv.width = W;
    cv.height = H;
    ctx.scale(dpr, dpr);
    const logicW = cv.offsetWidth;
    const logicH = 240;

    const pad = { t: 16, r: 12, b: 32, l: 52 };
    const pw = logicW - pad.l - pad.r;
    const ph = logicH - pad.t - pad.b;

    const all = [
      ...strategy.map((d) => d.value),
      ...(benchmark || []).map((d) => d.value),
      ...(compare || []).map((d) => d.value),
    ];
    const rawMin = Math.min(...all);
    const rawMax = Math.max(...all);
    // Y 轴以净值1.0为基准，上下各留余量
    const vMin = Math.min(rawMin, 1) * 0.97;
    const vMax = Math.max(rawMax, 1) * 1.03;

    const toX = (i: number, len: number) => pad.l + (i / Math.max(len - 1, 1)) * pw;
    const toY = (v: number) => pad.t + ph - ((v - vMin) / (vMax - vMin)) * ph;

    ctx.clearRect(0, 0, logicW, logicH);

    // --- 网格线 & Y 轴标签 ---
    const yTicks = 5;
    ctx.textAlign = "right";
    ctx.font = "10px system-ui";
    ctx.fillStyle = "#94a3b8";
    for (let i = 0; i <= yTicks; i++) {
      const v = vMin + (vMax - vMin) * (i / yTicks);
      const y = toY(v);
      // 网格
      ctx.strokeStyle = v === 1 ? "#cbd5e1" : "#f1f5f9";
      ctx.lineWidth = v === 1 ? 1 : 0.5;
      ctx.setLineDash(v === 1 ? [4, 3] : []);
      ctx.beginPath();
      ctx.moveTo(pad.l, y);
      ctx.lineTo(pad.l + pw, y);
      ctx.stroke();
      ctx.setLineDash([]);
      // Y 标签：显示为 ±x%
      ctx.fillStyle = "#64748b";
      ctx.fillText(fmtPct(v), pad.l - 4, y + 3.5);
    }

    // --- X 轴时间刻度 ---
    const nXTicks = Math.min(6, strategy.length);
    ctx.textAlign = "center";
    ctx.font = "10px system-ui";
    ctx.fillStyle = "#94a3b8";
    for (let i = 0; i < nXTicks; i++) {
      const idx = Math.round((i / (nXTicks - 1)) * (strategy.length - 1));
      const x = toX(idx, strategy.length);
      const label = fmtDate(strategy[idx].date);
      ctx.fillText(label, x, logicH - pad.b + 14);
      // 刻度线
      ctx.strokeStyle = "#e2e8f0";
      ctx.lineWidth = 0.5;
      ctx.beginPath();
      ctx.moveTo(x, pad.t + ph);
      ctx.lineTo(x, pad.t + ph + 4);
      ctx.stroke();
    }

    // --- 折线 ---
    const drawLine = (data: Pt[], color: string, dash: number[] = [], width = 1.5) => {
      if (!data.length) return;
      ctx.setLineDash(dash);
      ctx.strokeStyle = color;
      ctx.lineWidth = width;
      ctx.beginPath();
      data.forEach((d, i) => {
        const x = toX(i, data.length);
        const y = toY(d.value);
        i === 0 ? ctx.moveTo(x, y) : ctx.lineTo(x, y);
      });
      ctx.stroke();
      ctx.setLineDash([]);

      // 终点标注盈亏
      const last = data[data.length - 1];
      const lx = toX(data.length - 1, data.length);
      const ly = toY(last.value);
      const label = fmtPct(last.value);
      const isPositive = last.value >= 1;
      ctx.font = "bold 10px system-ui";
      ctx.textAlign = "left";
      ctx.fillStyle = color;
      ctx.fillText(label, lx + 3, ly + 3.5);
    };

    if (benchmark?.length) drawLine(benchmark, "#94a3b8", [], 1);
    if (compare?.length) drawLine(compare, "#f59e0b", [6, 3], 1.5);
    drawLine(strategy, "#3b82f6", [], 2);
  }, [strategy, benchmark, compare]);

  const lastNav = strategy[strategy.length - 1]?.value ?? 1;
  const totalPct = ((lastNav - 1) * 100).toFixed(2);
  const isPos = lastNav >= 1;

  const lastBench = benchmark?.length ? benchmark[benchmark.length - 1]?.value ?? 1 : null;
  const alphaPct = lastBench != null ? ((lastNav - lastBench) * 100).toFixed(2) : null;
  const alphaPos = alphaPct != null && parseFloat(alphaPct) >= 0;

  return (
    <Card>
      <CardHeader className="pb-1 flex flex-row items-center justify-between">
        <div className="flex items-center gap-3">
          <CardTitle className="text-sm">{title}</CardTitle>
          <span className={`text-sm font-bold ${isPos ? "text-green-600" : "text-red-500"}`}>
            {isPos ? "+" : ""}{totalPct}%
          </span>
          {alphaPct != null && (
            <span className={`text-xs px-1.5 py-0.5 rounded font-medium ${alphaPos ? "bg-green-50 text-green-700" : "bg-red-50 text-red-600"}`}>
              α {alphaPos ? "+" : ""}{alphaPct}%
            </span>
          )}
        </div>
        <div className="flex gap-3 text-[10px] text-muted-foreground">
          <span className="flex items-center gap-1">
            <span className="w-3 h-0.5 bg-blue-500 inline-block" />策略 A
          </span>
          {compare?.length ? (
            <span className="flex items-center gap-1">
              <span className="w-3 h-0.5 bg-amber-400 inline-block" style={{ borderTop: "2px dashed #f59e0b", height: 0 }} />
              策略 B ({compareLabel})
            </span>
          ) : null}
          {benchmark?.length ? (
            <span className="flex items-center gap-1">
              <span className="w-3 h-0.5 bg-slate-400 inline-block" />基准
            </span>
          ) : null}
        </div>
      </CardHeader>
      <CardContent className="p-0 pt-1">
        <canvas ref={ref} className="w-full" style={{ height: 240 }} />
      </CardContent>
    </Card>
  );
}
