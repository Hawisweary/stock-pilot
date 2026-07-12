"use client";

interface SparkPoint { date: string; score: number }

interface Props {
  data: SparkPoint[] | null | undefined;
  width?: number;
  height?: number;
}

export function ScoreSparkline({ data, width = 56, height = 20 }: Props) {
  if (!data || data.length < 2) {
    return <span className="text-xs text-muted-foreground">—</span>;
  }

  const scores = data.map((d) => d.score);
  const min = Math.min(...scores);
  const max = Math.max(...scores);
  const range = max - min || 1;

  const pts = scores.map((s, i) => {
    const x = (i / (scores.length - 1)) * width;
    const y = height - ((s - min) / range) * height;
    return `${x.toFixed(1)},${y.toFixed(1)}`;
  });

  const first = scores[0];
  const last = scores[scores.length - 1];
  const color = last > first + 0.5 ? "#16a34a" : last < first - 0.5 ? "#dc2626" : "#6b7280";

  return (
    <svg width={width} height={height} className="overflow-visible">
      <polyline
        points={pts.join(" ")}
        fill="none"
        stroke={color}
        strokeWidth="1.5"
        strokeLinejoin="round"
        strokeLinecap="round"
      />
      {/* 末点圆点 */}
      <circle
        cx={width}
        cy={parseFloat(pts[pts.length - 1].split(",")[1])}
        r="2"
        fill={color}
      />
    </svg>
  );
}
