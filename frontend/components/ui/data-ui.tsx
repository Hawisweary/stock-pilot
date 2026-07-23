// 共享数据 UI 原语 — 骨架 / 空态 / 错误态 / 指标块。买方工作台风格:紧凑、mono 数字、可行动。
import * as React from "react";
import { cn } from "@/lib/utils";

/** 与布局同尺寸的骨架条(不要无意义大转圈) */
export function Skeleton({ className }: { className?: string }) {
  return <div className={cn("animate-pulse rounded bg-muted/70", className)} />;
}

/** 可行动的空态:文案 + 主行动按钮 */
export function EmptyState({
  icon: Icon,
  title,
  hint,
  action,
}: {
  icon?: React.ComponentType<{ className?: string }>;
  title: string;
  hint?: string;
  action?: { label: string; onClick: () => void };
}) {
  return (
    <div className="flex flex-col items-center justify-center gap-2 rounded-md border border-dashed border-border py-10 text-center">
      {Icon && <Icon className="h-5 w-5 text-muted-foreground" />}
      <div className="text-sm font-medium text-foreground">{title}</div>
      {hint && <div className="max-w-xs text-xs text-muted-foreground">{hint}</div>}
      {action && (
        <button
          onClick={action.onClick}
          className="mt-1 rounded border border-border px-2.5 py-1 text-xs font-medium hover:bg-muted transition-colors"
        >
          {action.label}
        </button>
      )}
    </div>
  );
}

/** 可行动的错误态:红边框 + 重试 */
export function ErrorState({
  message = "加载失败",
  onRetry,
}: {
  message?: string;
  onRetry?: () => void;
}) {
  return (
    <div className="flex items-center justify-between gap-2 rounded-md border border-destructive/40 bg-destructive/5 px-3 py-2 text-xs">
      <span className="text-destructive">{message}</span>
      {onRetry && (
        <button
          onClick={onRetry}
          className="rounded border border-destructive/40 px-2 py-0.5 text-destructive hover:bg-destructive/10 transition-colors"
        >
          重试
        </button>
      )}
    </div>
  );
}

/** 指标块:标签 + mono 大数 + 可选涨跌着色 + 副标 */
export function StatTile({
  label,
  value,
  sub,
  tone = "neutral",
  className,
}: {
  label: string;
  value: React.ReactNode;
  sub?: React.ReactNode;
  tone?: "neutral" | "up" | "down" | "accent";
  className?: string;
}) {
  const toneCls =
    tone === "up"
      ? "text-up"
      : tone === "down"
        ? "text-down"
        : tone === "accent"
          ? "text-primary"
          : "text-foreground";
  return (
    <div className={cn("flex flex-col gap-0.5 px-3 py-2", className)}>
      <span className="text-[10px] uppercase tracking-wide text-muted-foreground">{label}</span>
      <span className={cn("font-mono tabular-nums text-lg leading-tight", toneCls)}>{value}</span>
      {sub != null && <span className="text-[11px] text-muted-foreground">{sub}</span>}
    </div>
  );
}
