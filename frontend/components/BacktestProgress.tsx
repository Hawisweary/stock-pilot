"use client";

import { useEffect, useRef, useState } from "react";

interface Props {
  days: number;
  /** loading=true 时显示；false 时隐藏 */
  active: boolean;
}

/**
 * 回测进度条
 *
 * 根据 days 估算总耗时（每天约 2ms），用 setInterval 推进进度，
 * 确保不超过 95%（实际完成由父组件 active=false 触发消失）。
 */
export function BacktestProgress({ days, active }: Props) {
  const [pct, setPct] = useState(0);
  const [elapsed, setElapsed] = useState(0);
  const startRef = useRef<number>(0);
  const timerRef = useRef<ReturnType<typeof setInterval> | null>(null);

  useEffect(() => {
    if (!active) {
      setPct(0);
      setElapsed(0);
      if (timerRef.current) clearInterval(timerRef.current);
      return;
    }

    startRef.current = Date.now();
    setPct(0);

    // 估算总耗时：days × 2ms，最少 500ms
    const estimatedMs = Math.max(days * 2, 500);

    timerRef.current = setInterval(() => {
      const now = Date.now();
      const spent = now - startRef.current;
      setElapsed(spent);
      const progress = Math.min((spent / estimatedMs) * 95, 95);
      setPct(Math.round(progress));
    }, 100);

    return () => {
      if (timerRef.current) clearInterval(timerRef.current);
    };
  }, [active, days]);

  if (!active) return null;

  const elapsedSec = (elapsed / 1000).toFixed(1);

  return (
    <div className="space-y-1.5">
      <div className="flex items-center justify-between text-xs text-muted-foreground">
        <span>
          回测运行中… <span className="font-mono text-primary">{pct}%</span>
        </span>
        <span className="font-mono">{elapsedSec}s</span>
      </div>
      <div className="h-2 w-full rounded-full bg-muted overflow-hidden">
        <div
          className="h-full rounded-full bg-primary transition-all duration-100"
          style={{ width: `${pct}%` }}
        />
      </div>
      <p className="text-[10px] text-muted-foreground">
        模拟 {days} 个交易日 · 预计耗时约 {Math.ceil(days * 2 / 1000)}s
      </p>
    </div>
  );
}
