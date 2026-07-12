export function scoreTextClass(v: number | null | undefined): string {
  if (v == null) return "text-muted-foreground";
  if (v >= 60) return "text-red-600";
  if (v >= 40) return "text-amber-600";
  return "text-green-700";
}

export function scoreBgClass(v: number | null | undefined): string {
  if (v == null) return "bg-muted";
  if (v >= 60) return "bg-red-50 dark:bg-red-950/30";
  if (v >= 40) return "bg-amber-50 dark:bg-amber-950/30";
  return "bg-green-50 dark:bg-green-950/30";
}

export function scoreColor(v: number | null | undefined): string {
  if (v == null) return "hsl(var(--muted-foreground))";
  if (v >= 60) return "#dc2626";
  if (v >= 40) return "#d97706";
  return "#15803d";
}
