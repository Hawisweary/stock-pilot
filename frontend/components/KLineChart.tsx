"use client";

import { useEffect, useRef, useState, useCallback, useMemo } from "react";
import {
  createChart, ColorType, CandlestickSeries, LineSeries, HistogramSeries,
} from "lightweight-charts";

import {
  compactBeijingDateTimeToUnix,
  minuteChartLocalization,
} from "@/lib/chartTime";
import {
  computeTechnicalFromBars,
  macdBarColor,
  type OhlcBar,
  type TechnicalBar,
} from "@/lib/indicators";

interface KLineItem {
  date: string; open: number; high: number; low: number; close: number; volume?: number;
}

interface Props {
  data: KLineItem[];
  /** 已废弃：指标改由 K 线 OHLC 在前端同源计算，此字段仅保留兼容 */
  macdData?: unknown;
  height?: number;
  minuteBars?: boolean;
}

function parseChartTime(date: string, minuteBars?: boolean): string | number {
  const s = String(date).trim();
  if (minuteBars) {
    const unix = compactBeijingDateTimeToUnix(s.replace(/\D/g, ""));
    return unix ?? Number.NaN;
  }
  if (/^\d{8}$/.test(s)) {
    return `${s.slice(0, 4)}-${s.slice(4, 6)}-${s.slice(6, 8)}`;
  }
  if (/^\d{4}-\d{2}-\d{2}/.test(s)) return s.slice(0, 10);
  const digits = s.replace(/\D/g, "");
  if (digits.length >= 8) {
    return `${digits.slice(0, 4)}-${digits.slice(4, 6)}-${digits.slice(6, 8)}`;
  }
  return s.slice(0, 10);
}

type CandleBar = { time: string | number; open: number; high: number; low: number; close: number; volume: number | null };
type AlignedRow = { time: string | number; [key: string]: number | string | null | undefined };

const SERIES_NO_PRICE_LINE = { priceLineVisible: false } as const;

function compareChartTime(a: string | number, b: string | number): number {
  if (typeof a === "number" && typeof b === "number") return a - b;
  return String(a).localeCompare(String(b));
}

function numOrNull(v: unknown): number | null {
  if (v == null) return null;
  const n = Number(v);
  return Number.isFinite(n) ? n : null;
}

function prepareChartData(data: KLineItem[], minuteBars?: boolean) {
  const raw = data
    .filter((d) => d.open != null && d.high != null && d.low != null && d.close != null && !isNaN(d.open))
    .map((d) => ({
      time: parseChartTime(d.date, minuteBars) as string | number,
      date: String(d.date),
      open: d.open,
      high: d.high,
      low: d.low,
      close: d.close,
      volume: numOrNull(d.volume),
    }))
    .filter((d) => d.time != null && !(typeof d.time === "number" && Number.isNaN(d.time)))
    .sort((a, b) => compareChartTime(a.time, b.time));

  const byTime = new Map<string | number, typeof raw[0]>();
  for (const row of raw) byTime.set(row.time, row);
  const ordered = Array.from(byTime.values()).sort((a, b) => compareChartTime(a.time, b.time));

  const ohlc: OhlcBar[] = ordered.map((r) => ({
    date: r.date,
    open: r.open,
    high: r.high,
    low: r.low,
    close: r.close,
  }));

  const technical = minuteBars ? [] : computeTechnicalFromBars(ohlc);
  const candles: CandleBar[] = ordered.map((r) => ({
    time: r.time,
    open: r.open,
    high: r.high,
    low: r.low,
    close: r.close,
    volume: r.volume,
  }));

  return { candles, technical };
}

function buildAlignedRows(
  candles: CandleBar[],
  technical: TechnicalBar[],
  indicator: IndicatorId,
): AlignedRow[] {
  const rows: AlignedRow[] = [];
  const n = Math.min(candles.length, technical.length);
  for (let i = 0; i < n; i++) {
    const t = technical[i];
    const time = candles[i].time;
    switch (indicator) {
      case "macd":
        rows.push({
          time,
          dif: numOrNull(t.macd_dif),
          dea: numOrNull(t.macd_dea),
          bar: numOrNull(t.macd_bar),
        });
        break;
      case "kdj":
        rows.push({
          time,
          k: numOrNull(t.kdj_k),
          d: numOrNull(t.kdj_d),
          j: numOrNull(t.kdj_j),
        });
        break;
      case "rsi":
        rows.push({ time, rsi14: numOrNull(t.rsi14) });
        break;
      case "boll":
        rows.push({
          time,
          upper: numOrNull(t.boll_upper),
          mid: numOrNull(t.boll_mid),
          lower: numOrNull(t.boll_lower),
        });
        break;
      case "atr":
        rows.push({ time, atr14: numOrNull(t.atr14) });
        break;
    }
  }
  return rows;
}

function macdAutoscaleProvider(nums: number[]) {
  return () => {
    const finite = nums.filter((v) => Number.isFinite(v));
    if (finite.length === 0) return null;
    let min = Math.min(...finite, 0);
    let max = Math.max(...finite, 0);
    const pad = Math.max((max - min) * 0.12, 0.05);
    return { priceRange: { minValue: min - pad, maxValue: max + pad } };
  };
}

type IndicatorId = "macd" | "kdj" | "rsi" | "boll" | "atr";

interface IndicatorDef {
  id: IndicatorId;
  label: string;
  series: { key: string; color: string; label: string; type: "line" | "histogram" }[];
}

const INDICATORS: IndicatorDef[] = [
  {
    id: "macd",
    label: "MACD",
    series: [
      { key: "bar", color: "#ef4444", label: "MACD柱", type: "histogram" },
      { key: "dif", color: "#3b82f6", label: "DIF", type: "line" },
      { key: "dea", color: "#f59e0b", label: "DEA", type: "line" },
    ],
  },
  {
    id: "kdj",
    label: "KDJ",
    series: [
      { key: "k", color: "#3b82f6", label: "K", type: "line" },
      { key: "d", color: "#f59e0b", label: "D", type: "line" },
      { key: "j", color: "#ef4444", label: "J", type: "line" },
    ],
  },
  {
    id: "rsi",
    label: "RSI",
    series: [{ key: "rsi14", color: "#8b5cf6", label: "RSI(14)", type: "line" }],
  },
  {
    id: "boll",
    label: "布林带",
    series: [
      { key: "upper", color: "#6b7280", label: "上轨", type: "line" },
      { key: "mid", color: "#3b82f6", label: "中轨", type: "line" },
      { key: "lower", color: "#6b7280", label: "下轨", type: "line" },
    ],
  },
  {
    id: "atr",
    label: "ATR",
    series: [{ key: "atr14", color: "#f97316", label: "ATR(14)", type: "line" }],
  },
];

function useCombinedChart(
  containerRef: React.RefObject<HTMLDivElement | null>,
  candles: CandleBar[],
  technical: TechnicalBar[],
  indicator: IndicatorId,
  height: number,
  klinePaneHeight: number,
  volumePaneHeight: number,
  indicatorPaneHeight: number,
  showIndicator: boolean,
  minuteBars?: boolean,
) {
  useEffect(() => {
    const el = containerRef.current;
    if (!el || candles.length === 0) return;

    const chart = createChart(el, {
      height,
      layout: {
        background: { type: ColorType.Solid, color: "transparent" },
        textColor: "#6b7280",
      },
      grid: { vertLines: { color: "#f3f4f6" }, horzLines: { color: "#f3f4f6" } },
      rightPriceScale: { borderColor: "#e5e7eb" },
      timeScale: {
        borderColor: "#e5e7eb",
        timeVisible: !showIndicator || !!minuteBars,
        secondsVisible: false,
      },
      localization: minuteBars ? minuteChartLocalization(true) : undefined,
      crosshair: { mode: 0 },
    });

    const candleSeries = chart.addSeries(CandlestickSeries, {
      upColor: "#ef4444", downColor: "#22c55e", borderUpColor: "#ef4444",
      borderDownColor: "#22c55e", wickUpColor: "#ef4444", wickDownColor: "#22c55e",
      ...SERIES_NO_PRICE_LINE,
    });
    candleSeries.setData(candles as any);

    if (candles.length >= 20) {
      const addMA = (period: number, color: string) => {
        const maData: { time: string | number; value: number }[] = [];
        for (let i = period - 1; i < candles.length; i++) {
          let sum = 0;
          for (let j = i - period + 1; j <= i; j++) sum += candles[j].close;
          maData.push({ time: candles[i].time, value: sum / period });
        }
        const line = chart.addSeries(LineSeries, {
          color, lineWidth: 1, lastValueVisible: false, ...SERIES_NO_PRICE_LINE,
        });
        line.setData(maData);
      };
      addMA(5, "#3b82f6");
      addMA(10, "#f59e0b");
      addMA(20, "#ef4444");
    }

    const hasVolume = candles.some((c) => c.volume != null);
    if (hasVolume) {
      const volPane = chart.addPane();
      chart.panes()[0].setStretchFactor(klinePaneHeight);
      volPane.setStretchFactor(volumePaneHeight);

      const volSeries = volPane.addSeries(HistogramSeries, {
        base: 0,
        color: "#94a3b8",
        priceFormat: { type: "volume" },
        ...SERIES_NO_PRICE_LINE,
      });
      const volData = candles
        .filter((c) => c.volume != null)
        .map((c) => ({
          time: c.time,
          value: c.volume as number,
          color: c.close >= c.open ? "#fca5a5" : "#86efac",
        }));
      volSeries.setData(volData as any);
    }

    if (showIndicator && technical.length > 0) {
      const aligned = buildAlignedRows(candles, technical, indicator);
      if (aligned.length > 0) {
        const indPane = chart.addPane();
        chart.panes()[0].setStretchFactor(klinePaneHeight);
        indPane.setStretchFactor(indicatorPaneHeight);

        const def = INDICATORS.find((d) => d.id === indicator)!;

        if (indicator === "macd") {
          const barVals = aligned.map((m) => numOrNull(m.bar));
          const scaleNums = aligned.flatMap((m) => [m.dif, m.dea, m.bar])
            .map((v) => numOrNull(v))
            .filter((v): v is number => v !== null);
          const scaleOpts = {
            autoscaleInfoProvider: macdAutoscaleProvider(scaleNums),
            ...SERIES_NO_PRICE_LINE,
          };

          const barSeries = indPane.addSeries(HistogramSeries, {
            ...scaleOpts,
            base: 0,
            color: "#ef4444",
          });
          const barData = aligned
            .map((m, idx) => {
              const bar = numOrNull(m.bar);
              if (bar === null) return null;
              return {
                time: m.time,
                value: bar,
                color: macdBarColor(barVals, idx),
              };
            })
            .filter(Boolean);
          if (barData.length > 0) {
            barSeries.setData(barData as any);
            barSeries.createPriceLine({
              price: 0,
              color: "#9ca3af",
              lineWidth: 1,
              lineStyle: 2,
              axisLabelVisible: true,
              title: "0",
            });
          }

          for (const key of ["dif", "dea"] as const) {
            const color = key === "dif" ? "#3b82f6" : "#f59e0b";
            const lineSeries = indPane.addSeries(LineSeries, {
              ...scaleOpts,
              color,
              lineWidth: 1,
              lastValueVisible: false,
            });
            lineSeries.setData(
              aligned.map((m) => {
                const v = numOrNull(m[key]);
                return v === null ? { time: m.time } : { time: m.time, value: v };
              }) as any,
            );
          }
        } else {
          for (const s of def.series) {
            const lineSeries = indPane.addSeries(LineSeries, {
              color: s.color, lineWidth: 1, lastValueVisible: false, ...SERIES_NO_PRICE_LINE,
            });
            lineSeries.setData(
              aligned.map((m) => {
                const v = numOrNull(m[s.key]);
                return v === null ? { time: m.time } : { time: m.time, value: v };
              }) as any,
            );
          }
        }
      }
    }

    chart.timeScale().fitContent();
    return () => chart.remove();
  }, [candles, technical, indicator, height, klinePaneHeight, volumePaneHeight, indicatorPaneHeight, showIndicator, minuteBars]);
}

export function KLineChart({ data, height = 400, minuteBars = false }: Props) {
  const chartRef = useRef<HTMLDivElement>(null);
  const [indicator, setIndicator] = useState<IndicatorId>("macd");
  const indicatorHeight = 140;

  const { candles, technical } = useMemo(
    () => prepareChartData(data, minuteBars),
    [data, minuteBars],
  );

  const hasIndicatorData = useCallback((id: IndicatorId): boolean => {
    if (technical.length === 0) return false;
    switch (id) {
      case "macd": return technical.some((m) => m.macd_bar != null);
      case "kdj": return technical.some((m) => m.kdj_k != null);
      case "rsi": return technical.some((m) => m.rsi14 != null);
      case "boll": return technical.some((m) => m.boll_mid != null);
      case "atr": return technical.some((m) => m.atr14 != null);
      default: return false;
    }
  }, [technical]);

  const hasInd = !minuteBars && hasIndicatorData(indicator);
  const hasVolume = candles.some((c) => c.volume != null);
  const volumeHeight = hasVolume ? 60 : 0;
  const klinePaneHeight = height - volumeHeight - (hasInd ? indicatorHeight + 12 : 0) - (hasVolume ? 8 : 0);

  useCombinedChart(
    chartRef,
    candles,
    technical,
    indicator,
    height,
    klinePaneHeight,
    volumeHeight,
    indicatorHeight,
    hasInd,
    minuteBars,
  );

  return (
    <div>
      <div className="flex items-center gap-1 mb-2 flex-wrap">
        {INDICATORS.map((ind) => {
          const hasData = hasIndicatorData(ind.id);
          const active = indicator === ind.id;
          return (
            <button
              key={ind.id}
              type="button"
              onClick={() => hasData && setIndicator(ind.id)}
              disabled={!hasData}
              className={`px-2.5 py-1 text-xs rounded-md font-medium transition-colors ${
                active
                  ? "bg-primary text-primary-foreground"
                  : hasData
                    ? "bg-muted text-muted-foreground hover:bg-accent hover:text-accent-foreground"
                    : "bg-muted/50 text-muted-foreground/40 cursor-not-allowed"
              }`}
            >
              {ind.label}
            </button>
          );
        })}
      </div>

      <div ref={chartRef} />

      <div className="flex items-center justify-center gap-4 mt-2 text-xs text-muted-foreground flex-wrap">
        <span className="flex items-center gap-1">
          <span className="w-3 h-0.5 inline-block rounded" style={{ backgroundColor: "#3b82f6" }} />
          MA5
        </span>
        <span className="flex items-center gap-1">
          <span className="w-3 h-0.5 inline-block rounded" style={{ backgroundColor: "#f59e0b" }} />
          MA10
        </span>
        <span className="flex items-center gap-1">
          <span className="w-3 h-0.5 inline-block rounded" style={{ backgroundColor: "#ef4444" }} />
          MA20
        </span>
        {hasInd && indicator === "macd" && (
          <>
            <span className="w-px h-3 bg-border mx-1" />
            <span className="flex items-center gap-1">
              <span className="w-2 h-2 inline-block rounded" style={{ backgroundColor: "#ef4444" }} />
              柱增
            </span>
            <span className="flex items-center gap-1">
              <span className="w-2 h-2 inline-block rounded" style={{ backgroundColor: "#22c55e" }} />
              柱减
            </span>
            <span className="flex items-center gap-1">
              <span className="w-3 h-0.5 inline-block rounded" style={{ backgroundColor: "#3b82f6" }} />
              DIF
            </span>
            <span className="flex items-center gap-1">
              <span className="w-3 h-0.5 inline-block rounded" style={{ backgroundColor: "#f59e0b" }} />
              DEA
            </span>
          </>
        )}
        {hasInd && indicator !== "macd" && (
          <>
            <span className="w-px h-3 bg-border mx-1" />
            {INDICATORS.find((d) => d.id === indicator)!.series.map((s) => (
              <span key={s.key} className="flex items-center gap-1">
                <span
                  className={`inline-block rounded ${s.type === "histogram" ? "w-2 h-2" : "w-3 h-0.5"}`}
                  style={{ backgroundColor: s.color }}
                />
                {s.label}
              </span>
            ))}
          </>
        )}
      </div>
    </div>
  );
}
