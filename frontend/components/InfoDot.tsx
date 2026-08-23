"use client";
import { useState } from "react";
import { GLOSSARY } from "@/lib/glossary";

// 就地术语答疑:放在任意标签后面 <InfoDot k="pe" />。悬停/点击弹出解释。
export function InfoDot({ k }: { k: string }) {
  const g = GLOSSARY[k];
  const [open, setOpen] = useState(false);
  if (!g) return null;
  return (
    <span className="relative inline-flex align-middle"
      onMouseEnter={() => setOpen(true)} onMouseLeave={() => setOpen(false)}>
      <button
        type="button"
        onClick={(e) => { e.stopPropagation(); setOpen((o) => !o); }}
        aria-label={`${g.title} 说明`}
        className="ml-0.5 inline-flex h-3.5 w-3.5 items-center justify-center rounded-full border border-muted-foreground/40 text-[9px] leading-none text-muted-foreground hover:bg-accent hover:text-foreground"
      >?</button>
      {open && (
        <span className="absolute left-1/2 top-full z-50 mt-1 w-60 -translate-x-1/2 rounded-lg border bg-card p-2.5 text-left shadow-xl"
          onClick={(e) => e.stopPropagation()}>
          <span className="block text-xs font-semibold mb-1">{g.title}</span>
          <span className="block text-[11px] leading-relaxed text-muted-foreground font-normal">{g.desc}</span>
        </span>
      )}
    </span>
  );
}
