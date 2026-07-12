"use client";

import { useEffect, useMemo, useRef, useState } from "react";
import { ColorType, LineSeries, createChart, type IChartApi, type ISeriesApi } from "lightweight-charts";
import { beijingHHmmToUnix, minuteChartLocalization } from "@/lib/chartTime";

export interface IntradayBar {
  time: string;
  price: number;
  volume?: number;
}

interface Props {
  bars: IntradayBar[];
  prevClose?: number | null;
  tradeDate?: string | null;
  height?: number;
}

interface HoverInfo {
  time: string;
  price: number;
  changePct: number | null;
  changeAmt: number | null;
}

function formatTimeLabel(hhmm: string): string {
  const t = hhmm.replace(":", "").padStart(4, "0");
  return `${t.slice(0, 2)}:${t.slice(2, 4)}`;
}

function calcChange(price: number, prevClose: number | null | undefined) {
  if (prevClose == null || prevClose === 0 || Number.isNaN(prevClose)) {
    return { changePct: null, changeAmt: null };
  }
  const changeAmt = price - prevClose;
  const changePct = (changeAmt / prevClose) * 100;
  return { changePct, changeAmt };
}

function formatPct(pct: number | null): string {
  if (pct == null || Number.isNaN(pct)) return "--";
  const sign = pct > 0 ? "+" : "";
  return `${sign}${pct.toFixed(2)}%`;
}

function formatAmt(amt: number | null): string {
  if (amt == null || Number.isNaN(amt)) return "";
  const sign = amt > 0 ? "+" : "";
  return `${sign}${amt.toFixed(2)}`;
}

function upDownColor(value: number, baseline: number | null | undefined): string {
  if (baseline == null || Number.isNaN(baseline)) return "#3b82f6";
  if (value > baseline) return "#ef4444";
  if (value < baseline) return "#22c55e";
  return "#6b7280";
}

export function IntradayChart({ bars, prevClose, tradeDate, height = 220 }: Props) {
  const ref = useRef<HTMLDivElement>(null);
  const chartRef = useRef<IChartApi | null>(null);
  const priceSeriesRef = useRef<ISeriesApi<"Line"> | null>(null);
  const timeByUnixRef = useRef<Map<number, string>>(new Map());

  const lastBar = useMemo((): HoverInfo | null => {
    if (bars.length === 0) return null;
    const b = bars[bars.length - 1];
    const { changePct, changeAmt } = calcChange(b.price, prevClose);
    return { time: formatTimeLabel(String(b.time)), price: b.price, changePct, changeAmt };
  }, [bars, prevClose]);

  const [hover, setHover] = useState<HoverInfo | null>(null);
  const display = hover ?? lastBar;

  useEffect(() => {
    const el = ref.current;
    if (!el || bars.length === 0) return;

    const timeByUnix = new Map<number, string>();
    const pricePoints: { time: number; value: number }[] = [];
    const pctPoints: { time: number; value: number }[] = [];

    for (const b of bars) {
      if (b.price == null || Number.isNaN(b.price)) continue;
      const unix = beijingHHmmToUnix(String(b.time), tradeDate ?? undefined);
      if (unix == null) continue;
      timeByUnix.set(unix, String(b.time));
      pricePoints.push({ time: unix, value: b.price });
      if (prevClose != null && prevClose !== 0 && !Number.isNaN(prevClose)) {
        pctPoints.push({ time: unix, value: ((b.price - prevClose) / prevClose) * 100 });
      }
    }

    timeByUnixRef.current = timeByUnix;
    if (pricePoints.length === 0) return;

    const lastPrice = pricePoints[pricePoints.length - 1].value;
    const lineColor = upDownColor(lastPrice, prevClose);

    const chart = createChart(el, {
      height,
      layout: {
        background: { type: ColorType.Solid, color: "transparent" },
        textColor: "#6b7280",
      },
      grid: { vertLines: { color: "#f3f4f6" }, horzLines: { color: "#f3f4f6" } },
      leftPriceScale: {
        visible: pctPoints.length > 0,
        borderColor: "#e5e7eb",
      },
      rightPriceScale: { borderColor: "#e5e7eb" },
      timeScale: { borderColor: "#e5e7eb", timeVisible: true, secondsVisible: false },
      localization: minuteChartLocalization(false),
      crosshair: { mode: 0 },
    });

    chartRef.current = chart;

    if (pctPoints.length > 0) {
      const pctSeries = chart.addSeries(LineSeries, {
        priceScaleId: "left",
        color: "transparent",
        lineWidth: 0,
        lastValueVisible: false,
        priceLineVisible: false,
        crosshairMarkerVisible: false,
        priceFormat: {
          type: "custom",
          formatter: (pct: number) => formatPct(pct),
        },
      });
      pctSeries.setData(pctPoints as any);

      pctSeries.createPriceLine({
        price: 0,
        color: "#9ca3af",
        lineWidth: 1,
        lineStyle: 2,
        axisLabelVisible: true,
        title: "0%",
      });
    }

    const priceSeries = chart.addSeries(LineSeries, {
      priceScaleId: "right",
      color: lineColor,
      lineWidth: 2,
      priceLineVisible: false,
      lastValueVisible: true,
      priceFormat: { type: "price", precision: 2, minMove: 0.01 },
    });
    priceSeriesRef.current = priceSeries;
    priceSeries.setData(pricePoints as any);

    if (prevClose != null && !Number.isNaN(prevClose)) {
      priceSeries.createPriceLine({
        price: prevClose,
        color: "#9ca3af",
        lineWidth: 1,
        lineStyle: 2,
        axisLabelVisible: true,
        title: "昨收",
      });
    }

    chart.subscribeCrosshairMove((param) => {
      if (!param.time || !priceSeriesRef.current) {
        setHover(null);
        return;
      }
      const raw = param.seriesData.get(priceSeriesRef.current);
      const price = raw && typeof raw === "object" && "value" in raw ? Number(raw.value) : null;
      if (price == null || Number.isNaN(price)) {
        setHover(null);
        return;
      }
      const hhmm = timeByUnixRef.current.get(param.time as number) ?? "";
      const { changePct, changeAmt } = calcChange(price, prevClose);
      setHover({
        time: formatTimeLabel(hhmm),
        price,
        changePct,
        changeAmt,
      });
    });

    chart.timeScale().fitContent();

    return () => {
      chartRef.current = null;
      priceSeriesRef.current = null;
      chart.remove();
      setHover(null);
    };
  }, [bars, prevClose, tradeDate, height]);

  if (bars.length === 0) {
    return (
      <div className="flex items-center justify-center text-xs text-muted-foreground" style={{ height }}>
        暂无分时数据（非交易时段或数据源不可用）
      </div>
    );
  }

  const pctColor = upDownColor(display?.price ?? 0, prevClose);
  const amtText = formatAmt(display?.changeAmt ?? null);

  return (
    <div>
      <div className="flex flex-wrap items-baseline gap-x-3 gap-y-1 px-3 py-2 text-xs border-b border-border/60">
        <span className="text-muted-foreground font-mono">{display?.time ?? "--:--"}</span>
        <span className="font-semibold font-mono" style={{ color: pctColor }}>
          ¥{display?.price?.toFixed(2) ?? "--"}
        </span>
        <span className="font-mono font-medium" style={{ color: pctColor }}>
          {formatPct(display?.changePct ?? null)}
        </span>
        {amtText && (
          <span className="font-mono text-muted-foreground">
            ({amtText})
          </span>
        )}
        {prevClose != null && !Number.isNaN(prevClose) && (
          <span className="text-muted-foreground ml-auto">
            昨收 ¥{prevClose.toFixed(2)}
          </span>
        )}
      </div>
      <div ref={ref} />
    </div>
  );
}
