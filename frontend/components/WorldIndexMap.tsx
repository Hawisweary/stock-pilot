"use client";
import { useState } from "react";

type Item = { ticker: string; name: string; last: number | null; chg_1d?: number | null };

const US_UP = "#22c55e", US_DOWN = "#ef4444";
const W = 1000, H = 500;
const proj = (lng: number, lat: number): [number, number] => [((lng + 180) / 360) * W, ((90 - lat) / 180) * H];

// 简化大陆轮廓([lng,lat] 顶点,等距投影)—— 提供地理背景,精度非重点
const CONTINENTS: [number, number][][] = [
  [[-165, 60], [-140, 68], [-100, 71], [-80, 62], [-60, 55], [-52, 47], [-66, 45], [-72, 40], [-80, 31], [-82, 25], [-97, 16], [-105, 20], [-114, 28], [-124, 40], [-140, 58]], // 北美
  [[-80, 9], [-68, 10], [-58, 4], [-50, 0], [-40, -6], [-38, -14], [-48, -24], [-58, -34], [-66, -44], [-73, -52], [-72, -40], [-70, -20], [-78, -4]], // 南美
  [[-10, 58], [-4, 61], [10, 60], [26, 60], [40, 58], [38, 48], [26, 45], [14, 44], [2, 44], [-8, 48]], // 欧洲
  [[-16, 30], [10, 34], [25, 32], [33, 30], [43, 12], [51, 11], [42, -2], [40, -16], [33, -26], [24, -34], [16, -34], [10, -18], [8, 4], [-8, 5], [-16, 18]], // 非洲
  [[42, 58], [60, 66], [100, 72], [140, 72], [165, 66], [178, 62], [150, 44], [140, 34], [122, 30], [120, 22], [107, 9], [95, 6], [80, 8], [74, 22], [60, 26], [46, 30], [42, 42]], // 亚洲
  [[113, -22], [122, -17], [135, -12], [142, -11], [150, -22], [153, -28], [146, -38], [136, -35], [126, -32], [115, -34]], // 澳洲
];

// 指数经纬度 + 标签偏移(避让重叠)
const COORDS: Record<string, { lng: number; lat: number; label: string; dx: number; dy: number }> = {
  "^GSPC": { lng: -74, lat: 41, label: "标普500", dx: 2, dy: -14 },
  "^IXIC": { lng: -85, lat: 35, label: "纳指", dx: -2, dy: 14 },
  "^DJI": { lng: -99, lat: 46, label: "道指", dx: -8, dy: -10 },
  "^N225": { lng: 139.7, lat: 35.7, label: "日经", dx: 12, dy: -6 },
  "^HSI": { lng: 114, lat: 22, label: "恒生", dx: 8, dy: 14 },
  "000001.SS": { lng: 121.5, lat: 31, label: "上证", dx: 14, dy: 0 },
  "^FTSE": { lng: -3, lat: 54, label: "FTSE", dx: -12, dy: -12 },
  "^GDAXI": { lng: 10.5, lat: 51, label: "DAX", dx: 10, dy: -12 },
  "^FCHI": { lng: 2, lat: 46, label: "CAC", dx: -8, dy: 14 },
  "^STOXX50E": { lng: 15, lat: 43, label: "斯托克50", dx: 16, dy: 8 },
  "^KS11": { lng: 127.5, lat: 37.5, label: "KOSPI", dx: 12, dy: 12 },
  "^BSESN": { lng: 72.8, lat: 19, label: "Sensex", dx: 0, dy: 16 },
};

export function WorldIndexMap({ items, onSelect }: { items: Item[]; onSelect: (it: Item) => void }) {
  const [hover, setHover] = useState<string | null>(null);
  const graticule = [];
  for (let lng = -150; lng <= 150; lng += 30) { const [x] = proj(lng, 0); graticule.push(<line key={`v${lng}`} x1={x} y1={0} x2={x} y2={H} stroke="var(--border)" strokeWidth={0.5} opacity={0.4} />); }
  for (let lat = -60; lat <= 60; lat += 30) { const [, y] = proj(0, lat); graticule.push(<line key={`h${lat}`} x1={0} y1={y} x2={W} y2={y} stroke="var(--border)" strokeWidth={0.5} opacity={0.4} />); }

  return (
    <div className="rounded-xl border bg-card p-3">
      <div className="flex items-center justify-between mb-1">
        <div className="text-sm font-semibold">全球股指 · 地理分布</div>
        <div className="text-[11px] text-muted-foreground">圆点颜色/大小 = 当日涨跌 · 点圆点看K线</div>
      </div>
      <svg viewBox={`0 0 ${W} ${H}`} className="w-full" style={{ maxHeight: 460 }}>
        {graticule}
        {CONTINENTS.map((poly, i) => (
          <polygon key={i} points={poly.map(([lng, lat]) => proj(lng, lat).join(",")).join(" ")}
            fill="var(--muted)" opacity={0.55} stroke="var(--border)" strokeWidth={0.8} />
        ))}
        {items.map((it) => {
          const c = COORDS[it.ticker];
          if (!c) return null;
          const [x, y] = proj(c.lng, c.lat);
          const chg = it.chg_1d ?? 0;
          const col = chg > 0 ? US_UP : chg < 0 ? US_DOWN : "var(--muted-foreground)";
          const r = 4 + Math.min(Math.abs(chg) * 2.5, 7);
          const hl = hover === it.ticker;
          return (
            <g key={it.ticker} style={{ cursor: "pointer" }}
              onMouseEnter={() => setHover(it.ticker)} onMouseLeave={() => setHover(null)}
              onClick={() => onSelect(it)}>
              <circle cx={x} cy={y} r={r + 4} fill={col} opacity={hl ? 0.25 : 0.12} />
              <circle cx={x} cy={y} r={r} fill={col} stroke="#fff" strokeWidth={1} />
              <text x={x + c.dx} y={y + c.dy} textAnchor="middle" fontSize={12} fontWeight={600}
                fill="var(--foreground)" style={{ pointerEvents: "none" }}>
                {c.label} <tspan fill={col}>{chg > 0 ? "+" : ""}{chg.toFixed(2)}%</tspan>
              </text>
            </g>
          );
        })}
      </svg>
    </div>
  );
}
