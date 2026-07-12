"use client";

import { useEffect, useState } from "react";
import Link from "next/link";
import { AlertTriangle, CheckCircle2, ExternalLink } from "lucide-react";
import { api } from "@/lib/api";
import type { BetaHealth } from "@/types/beta";

type Props = {
  title: string;
  subtitle?: string;
  children: React.ReactNode;
};

export function BetaShell({ title, subtitle, children }: Props) {
  const [health, setHealth] = useState<BetaHealth | null>(null);

  useEffect(() => {
    api.getBetaHealth().then(setHealth).catch(() => {});
  }, []);

  return (
    <div className="space-y-4">
      <div className="rounded-lg border border-amber-500/30 bg-amber-500/5 px-4 py-2 text-xs text-amber-800 dark:text-amber-200">
        实验模块 · 基于自选股池 · 结果仅供参考，非投资建议
      </div>

      <div className="flex items-start justify-between gap-4 flex-wrap">
        <div>
          <h1 className="text-2xl font-bold">{title}</h1>
          {subtitle && <p className="text-sm text-muted-foreground mt-1">{subtitle}</p>}
        </div>
        <Link href="/data" className="text-xs text-primary flex items-center gap-1 hover:underline">
          数据管理 <ExternalLink className="h-3 w-3" />
        </Link>
      </div>

      {health && <BetaHealthPanel health={health} />}

      {children}
    </div>
  );
}

function BetaHealthPanel({ health }: { health: BetaHealth }) {
  const rust = health.rust_backtest;
  const items = [
    { ok: health.backtest_ready, label: `回测 ${health.trade_days}交易日` },
    { ok: health.ic_ready, label: `评分 ${health.score_history_days}天` },
    { ok: health.factor_history_days >= 10, label: `因子 ${health.factor_history_days}天` },
    { ok: health.portfolio_ready, label: `股票池 ${health.universe_size}只` },
    {
      ok: rust?.available ?? false,
      label: rust?.available ? "Rust 回测" : "Rust 回测已禁用",
    },
  ];

  return (
    <div className="rounded-lg border bg-card p-3 space-y-2">
      <div className="text-xs font-medium text-muted-foreground">数据前置检查</div>
      <div className="flex flex-wrap gap-2">
        {items.map((it) => (
          <span
            key={it.label}
            className={`inline-flex items-center gap-1 text-[10px] px-2 py-1 rounded-full ${
              it.ok ? "bg-emerald-100 text-emerald-800 dark:bg-emerald-900/30 dark:text-emerald-300" : "bg-amber-100 text-amber-800"
            }`}
          >
            {it.ok ? <CheckCircle2 className="h-3 w-3" /> : <AlertTriangle className="h-3 w-3" />}
            {it.label}
          </span>
        ))}
        {health.latest_score_date && (
          <span className="text-[10px] text-muted-foreground self-center">评分日 {health.latest_score_date}</span>
        )}
      </div>
      {rust && !rust.available && (
        <p className="text-[10px] text-muted-foreground">{rust.message}</p>
      )}
      {health.issues.length > 0 && (
        <ul className="text-[10px] text-amber-700 dark:text-amber-300 space-y-0.5">
          {health.issues.filter((i) => i.level !== "info").slice(0, 4).map((i, idx) => (
            <li key={idx}>· [{i.module}] {i.msg}</li>
          ))}
        </ul>
      )}
    </div>
  );
}

export function BetaTabs({ active, tabs }: { active: string; tabs: { id: string; label: string; href: string }[] }) {
  return (
    <div className="flex gap-1 border-b pb-2 flex-wrap">
      {tabs.map((t) => (
        <Link
          key={t.id}
          href={t.href}
          className={`px-3 py-1.5 text-sm rounded-t ${active === t.id ? "bg-primary/10 text-primary font-medium" : "text-muted-foreground hover:bg-muted"}`}
        >
          {t.label}
        </Link>
      ))}
    </div>
  );
}

export const BETA_TABS = [
  { id: "backtest", label: "回测", href: "/backtest" },
  { id: "portfolio", label: "模拟交易", href: "/portfolio" },
  { id: "factors", label: "因子实验室", href: "/factors" },
];
