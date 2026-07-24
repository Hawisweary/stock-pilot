// 评分质量 = 单色(强调蓝)顺序标度,不复用 涨跌红绿(功能色),避免语义歧义。
// 分越高越"实"(强调蓝加粗),越低越"淡"(muted)。
export function scoreTextClass(v: number | null | undefined): string {
  if (v == null) return "text-muted-foreground";
  if (v >= 60) return "text-primary font-semibold";
  if (v >= 40) return "text-foreground";
  return "text-muted-foreground";
}

export function scoreBgClass(v: number | null | undefined): string {
  if (v == null) return "";
  if (v >= 60) return "bg-primary/15";
  if (v >= 40) return "bg-primary/[0.06]";
  return "bg-muted/30";
}

export function scoreColor(v: number | null | undefined): string {
  if (v == null) return "hsl(var(--muted-foreground))";
  if (v >= 60) return "#dc2626";
  if (v >= 40) return "#d97706";
  return "#15803d";
}
