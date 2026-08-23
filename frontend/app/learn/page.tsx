"use client";
import { useMemo, useState } from "react";
import { ChevronLeft, ChevronRight } from "lucide-react";
import { LEARN, type Block } from "@/lib/learnContent";

// **加粗** 内联解析
function Inline({ text }: { text: string }) {
  const parts = text.split("**");
  return <>{parts.map((s, i) => (i % 2 ? <strong key={i} className="font-semibold text-foreground">{s}</strong> : s))}</>;
}

function BlockView({ b }: { b: Block }) {
  switch (b.t) {
    case "h":
      return <h3 className="text-base font-semibold mt-5 mb-1.5">{b.text}</h3>;
    case "p":
      return <p className="text-sm leading-7 text-muted-foreground my-2"><Inline text={b.text} /></p>;
    case "ul":
      return (
        <ul className="my-2 space-y-1.5">
          {b.items.map((it, i) => (
            <li key={i} className="text-sm leading-7 text-muted-foreground flex gap-2">
              <span className="text-primary mt-0.5 shrink-0">•</span><span><Inline text={it} /></span>
            </li>
          ))}
        </ul>
      );
    case "tip":
      return (
        <div className="my-3 rounded-lg border-l-4 border-primary bg-primary/5 px-3 py-2">
          <div className="text-[11px] font-semibold text-primary mb-0.5">💡 提示</div>
          <p className="text-sm leading-6 text-foreground/90"><Inline text={b.text} /></p>
        </div>
      );
    case "table":
      return (
        <div className="my-3 overflow-x-auto rounded-lg border">
          <table className="w-full text-sm">
            <thead className="bg-muted/40 text-xs text-muted-foreground">
              <tr>{b.head.map((h, i) => <th key={i} className="px-3 py-2 text-left font-medium">{h}</th>)}</tr>
            </thead>
            <tbody>
              {b.rows.map((r, i) => (
                <tr key={i} className="border-t border-border/60">
                  {r.map((c, j) => (
                    <td key={j} className={`px-3 py-2 ${j === 0 ? "font-medium text-muted-foreground" : "text-foreground"}`}>
                      <Inline text={c} />
                    </td>
                  ))}
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      );
  }
}

export default function LearnPage() {
  // 扁平化所有 section 便于上/下一节
  const flat = useMemo(
    () => LEARN.flatMap((ch) => ch.sections.map((s) => ({ ch, s }))),
    [],
  );
  const [cur, setCur] = useState(0);
  const { ch, s } = flat[cur];
  const idx = flat.findIndex((f) => f.s.id === s.id);

  return (
    <div className="space-y-4">
      <div>
        <h1 className="text-xl font-bold">新手教程</h1>
        <p className="text-xs text-muted-foreground mt-1">从零学交易:概念 · 看懂数据 · 策略 · 怎么用本软件 · 风控。内容仅供学习,不构成投资建议。</p>
      </div>

      <div className="grid grid-cols-1 lg:grid-cols-[240px_1fr] gap-5">
        {/* 目录 */}
        <nav className="lg:sticky lg:top-4 self-start space-y-3 text-sm">
          {LEARN.map((chap) => (
            <div key={chap.id}>
              <div className="flex items-center gap-1.5 text-xs font-semibold text-muted-foreground mb-1">
                <span>{chap.icon}</span>{chap.title}{chap.stub && <span className="text-[10px] text-muted-foreground/60">(完善中)</span>}
              </div>
              <div className="space-y-0.5">
                {chap.sections.map((sec) => {
                  const active = sec.id === s.id;
                  const gi = flat.findIndex((f) => f.s.id === sec.id);
                  return (
                    <button key={sec.id} onClick={() => setCur(gi)}
                      className={`block w-full text-left px-2.5 py-1.5 rounded-md text-[13px] transition-colors ${
                        active ? "bg-primary text-primary-foreground font-medium" : "hover:bg-accent text-muted-foreground"
                      }`}>
                      {sec.title}
                    </button>
                  );
                })}
              </div>
            </div>
          ))}
        </nav>

        {/* 内容 */}
        <article className="min-w-0">
          <div className="rounded-xl border bg-card p-5 md:p-6">
            <div className="text-[11px] text-muted-foreground mb-1">{ch.icon} {ch.title}</div>
            <h2 className="text-lg font-bold mb-2">{s.title}</h2>
            {s.blocks.map((b, i) => <BlockView key={i} b={b} />)}
          </div>

          <div className="flex items-center justify-between mt-4">
            <button disabled={idx === 0} onClick={() => setCur(idx - 1)}
              className="flex items-center gap-1 text-sm px-3 py-1.5 rounded-md border hover:bg-accent disabled:opacity-40 disabled:cursor-not-allowed">
              <ChevronLeft className="h-4 w-4" />上一节
            </button>
            <span className="text-xs text-muted-foreground">{idx + 1} / {flat.length}</span>
            <button disabled={idx === flat.length - 1} onClick={() => setCur(idx + 1)}
              className="flex items-center gap-1 text-sm px-3 py-1.5 rounded-md border hover:bg-accent disabled:opacity-40 disabled:cursor-not-allowed">
              下一节<ChevronRight className="h-4 w-4" />
            </button>
          </div>
        </article>
      </div>
    </div>
  );
}
