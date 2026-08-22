"use client";
import { useEffect, useRef, useState } from "react";
import { Plus, Minus, Maximize2 } from "lucide-react";
import { WORLD_PATHS } from "@/lib/worldmap";

type Item = { ticker: string; name: string; last: number | null; chg_1d?: number | null };

const US_UP = "#22c55e", US_DOWN = "#ef4444";
const W = 1000, H = 500;
const proj = (lng: number, lat: number): [number, number] => [((lng + 180) / 360) * W, ((90 - lat) / 180) * H];
const clamp = (v: number, lo: number, hi: number) => Math.max(lo, Math.min(hi, v));

const COORDS: Record<string, { lng: number; lat: number; label: string; dx: number; dy: number }> = {
  "^GSPC": { lng: -74, lat: 41, label: "标普500", dx: 4, dy: -13 },
  "^IXIC": { lng: -85, lat: 34, label: "纳指", dx: -2, dy: 14 },
  "^DJI": { lng: -99, lat: 46, label: "道指", dx: -8, dy: -10 },
  "^N225": { lng: 139.7, lat: 35.7, label: "日经", dx: 13, dy: -6 },
  "^HSI": { lng: 114, lat: 22, label: "恒生", dx: 9, dy: 13 },
  "000001.SS": { lng: 121.5, lat: 31, label: "上证", dx: 15, dy: -2 },
  "^FTSE": { lng: -0.1, lat: 51.5, label: "FTSE", dx: -14, dy: -11 },
  "^GDAXI": { lng: 8.7, lat: 50.1, label: "DAX", dx: 8, dy: -12 },
  "^FCHI": { lng: 2.3, lat: 48.9, label: "CAC", dx: -12, dy: 13 },
  "^STOXX50E": { lng: 12, lat: 45, label: "斯托克50", dx: 18, dy: 10 },
  "^KS11": { lng: 127.5, lat: 37.5, label: "KOSPI", dx: 13, dy: 11 },
  "^BSESN": { lng: 72.8, lat: 19, label: "Sensex", dx: 2, dy: 16 },
};

export function WorldIndexMap({ items, onSelect }: { items: Item[]; onSelect: (it: Item) => void }) {
  const svgRef = useRef<SVGSVGElement>(null);
  const [hover, setHover] = useState<string | null>(null);
  const [view, setView] = useState({ k: 1, tx: 0, ty: 0 });
  const drag = useRef<{ x: number; y: number; tx: number; ty: number } | null>(null);

  const toSvg = (cx: number, cy: number): [number, number] => {
    const r = svgRef.current!.getBoundingClientRect();
    return [((cx - r.left) / r.width) * W, ((cy - r.top) / r.height) * H];
  };
  const zoomAt = (px: number, py: number, factor: number) => {
    setView((v) => {
      const nk = clamp(v.k * factor, 1, 12);
      const wx = (px - v.tx) / v.k, wy = (py - v.ty) / v.k;
      return { k: nk, tx: px - wx * nk, ty: py - wy * nk };
    });
  };

  // 滚轮缩放(非 passive,才能 preventDefault)
  useEffect(() => {
    const el = svgRef.current;
    if (!el) return;
    const onWheel = (e: WheelEvent) => {
      e.preventDefault();
      const [px, py] = toSvg(e.clientX, e.clientY);
      zoomAt(px, py, e.deltaY < 0 ? 1.18 : 1 / 1.18);
    };
    el.addEventListener("wheel", onWheel, { passive: false });
    return () => el.removeEventListener("wheel", onWheel);
  }, []);

  const onDown = (e: React.MouseEvent) => { drag.current = { x: e.clientX, y: e.clientY, tx: view.tx, ty: view.ty }; };
  const onMove = (e: React.MouseEvent) => {
    if (!drag.current) return;
    const r = svgRef.current!.getBoundingClientRect();
    const dx = ((e.clientX - drag.current.x) / r.width) * W;
    const dy = ((e.clientY - drag.current.y) / r.height) * H;
    setView((v) => ({ ...v, tx: drag.current!.tx + dx, ty: drag.current!.ty + dy }));
  };
  const onUp = () => { drag.current = null; };

  const { k, tx, ty } = view;
  const grat = [];
  for (let lng = -150; lng <= 150; lng += 30) { const [x] = proj(lng, 0); grat.push(<line key={`v${lng}`} x1={x} y1={0} x2={x} y2={H} stroke="var(--border)" strokeWidth={0.5 / k} opacity={0.4} />); }
  for (let lat = -60; lat <= 60; lat += 30) { const [, y] = proj(0, lat); grat.push(<line key={`h${lat}`} x1={0} y1={y} x2={W} y2={y} stroke="var(--border)" strokeWidth={0.5 / k} opacity={0.4} />); }

  return (
    <div className="rounded-xl border bg-card p-3 relative">
      <div className="flex items-center justify-between mb-1">
        <div className="text-sm font-semibold">全球股指 · 地理分布</div>
        <div className="text-[11px] text-muted-foreground">滚轮缩放 · 拖拽平移 · 圆点颜色/大小=当日涨跌 · 点圆点看K线</div>
      </div>
      <div className="absolute right-4 top-11 z-10 flex flex-col gap-1">
        <button onClick={() => zoomAt(W / 2, H / 2, 1.4)} className="w-7 h-7 rounded-md border bg-card hover:bg-accent flex items-center justify-center"><Plus className="h-3.5 w-3.5" /></button>
        <button onClick={() => zoomAt(W / 2, H / 2, 1 / 1.4)} className="w-7 h-7 rounded-md border bg-card hover:bg-accent flex items-center justify-center"><Minus className="h-3.5 w-3.5" /></button>
        <button onClick={() => setView({ k: 1, tx: 0, ty: 0 })} className="w-7 h-7 rounded-md border bg-card hover:bg-accent flex items-center justify-center"><Maximize2 className="h-3.5 w-3.5" /></button>
      </div>
      <svg ref={svgRef} viewBox={`0 0 ${W} ${H}`} className="w-full select-none"
        style={{ maxHeight: 480, cursor: drag.current ? "grabbing" : "grab" }}
        onMouseDown={onDown} onMouseMove={onMove} onMouseUp={onUp} onMouseLeave={onUp}>
        <g transform={`translate(${tx} ${ty}) scale(${k})`}>
          {grat}
          {WORLD_PATHS.map((d, i) => (
            <path key={i} d={d} fill="var(--muted)" opacity={0.5} stroke="var(--border)" strokeWidth={0.4 / k} />
          ))}
          {items.map((it) => {
            const c = COORDS[it.ticker];
            if (!c) return null;
            const [x, y] = proj(c.lng, c.lat);
            const chg = it.chg_1d ?? 0;
            const col = chg > 0 ? US_UP : chg < 0 ? US_DOWN : "var(--muted-foreground)";
            const r = (4 + Math.min(Math.abs(chg) * 2.5, 7)) / k;
            const hl = hover === it.ticker;
            return (
              <g key={it.ticker} style={{ cursor: "pointer" }}
                onMouseEnter={() => setHover(it.ticker)} onMouseLeave={() => setHover(null)}
                onClick={(e) => { e.stopPropagation(); if (!drag.current) onSelect(it); }}>
                <circle cx={x} cy={y} r={r + 4 / k} fill={col} opacity={hl ? 0.28 : 0.12} />
                <circle cx={x} cy={y} r={r} fill={col} stroke="#fff" strokeWidth={1 / k} />
                <text x={x + c.dx / k} y={y + c.dy / k} textAnchor="middle" fontSize={12 / k} fontWeight={600}
                  fill="var(--foreground)" style={{ pointerEvents: "none" }} stroke="var(--card)" strokeWidth={3 / k} paintOrder="stroke">
                  {c.label} <tspan fill={col}>{chg > 0 ? "+" : ""}{chg.toFixed(2)}%</tspan>
                </text>
              </g>
            );
          })}
        </g>
      </svg>
    </div>
  );
}
