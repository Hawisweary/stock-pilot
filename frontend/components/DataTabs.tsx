"use client";

import Link from "next/link";

const DATA_TABS = [
  { id: "data", label: "数据管理", href: "/data" },
  { id: "data-quality", label: "质量看板", href: "/data-quality" },
];

/** 数据中心内的 tab 切换条(数据管理 ↔ 质量看板),与 BetaTabs 同款。 */
export function DataTabs({ active }: { active: "data" | "data-quality" }) {
  return (
    <div className="flex gap-1 border-b border-border pb-2 flex-wrap">
      {DATA_TABS.map((t) => (
        <Link
          key={t.id}
          href={t.href}
          className={`px-3 py-1.5 text-sm rounded-t ${
            active === t.id
              ? "bg-primary/10 text-primary font-medium"
              : "text-muted-foreground hover:bg-muted"
          }`}
        >
          {t.label}
        </Link>
      ))}
    </div>
  );
}
