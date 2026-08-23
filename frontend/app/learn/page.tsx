"use client";
import { useMemo, useState } from "react";
import { ChevronLeft, ChevronRight } from "lucide-react";
import { LEARN, type Block } from "@/lib/learnContent";

// **加粗** 内联解析
function Inline({ text }: { text: string }) {
  const parts = text.split("**");
  return <>{parts.map((s, i) => (i % 2 ? <strong key={i} className="font-semibold text-foreground">{s}</strong> : s))}</>;
}

function KlineFig() {
  const RED = "#ef4444", GREEN = "#22c55e", TX = "var(--muted-foreground)";
  return (
    <svg viewBox="0 0 380 220" className="w-full max-w-md mx-auto my-3" style={{ maxHeight: 240 }}>
      {/* 阳线(涨) */}
      <line x1="110" y1="30" x2="110" y2="185" stroke={RED} strokeWidth="1.5" />
      <rect x="95" y="70" width="30" height="80" fill={RED} rx="1.5" />
      <line x1="150" y1="60" x2="130" y2="60" stroke={TX} strokeWidth="0.8" strokeDasharray="2 2" /><text x="154" y="63" fontSize="11" fill={TX}>收盘</text>
      <line x1="150" y1="150" x2="130" y2="150" stroke={TX} strokeWidth="0.8" strokeDasharray="2 2" /><text x="154" y="153" fontSize="11" fill={TX}>开盘</text>
      <line x1="90" y1="30" x2="70" y2="30" stroke={TX} strokeWidth="0.8" strokeDasharray="2 2" /><text x="26" y="33" fontSize="11" fill={TX}>最高</text>
      <line x1="90" y1="185" x2="70" y2="185" stroke={TX} strokeWidth="0.8" strokeDasharray="2 2" /><text x="26" y="188" fontSize="11" fill={TX}>最低</text>
      <text x="88" y="208" fontSize="12" fill={RED} fontWeight="600" textAnchor="middle">阳线(涨)</text>
      {/* 阴线(跌) */}
      <line x1="290" y1="45" x2="290" y2="195" stroke={GREEN} strokeWidth="1.5" />
      <rect x="275" y="80" width="30" height="70" fill={GREEN} rx="1.5" />
      <text x="322" y="83" fontSize="11" fill={TX}>开盘</text>
      <text x="322" y="153" fontSize="11" fill={TX}>收盘</text>
      <text x="290" y="212" fontSize="12" fill={GREEN} fontWeight="600" textAnchor="middle">阴线(跌)</text>
      <text x="200" y="120" fontSize="10" fill={TX} textAnchor="middle">粗柱=实体</text>
      <text x="200" y="135" fontSize="10" fill={TX} textAnchor="middle">细线=影线</text>
    </svg>
  );
}

function AdjustFig() {
  const UP = "#22c55e", RED = "#ef4444", TX = "var(--muted-foreground)", BD = "var(--border)";
  return (
    <div className="grid grid-cols-2 gap-3 my-3 max-w-lg mx-auto">
      <div className="rounded-lg border p-2">
        <svg viewBox="0 0 160 90" className="w-full">
          <polyline points="10,70 40,55 70,40 71,75 100,62 130,50 150,44" fill="none" stroke={RED} strokeWidth="2" />
          <line x1="71" y1="40" x2="71" y2="75" stroke={RED} strokeWidth="1" strokeDasharray="2 2" />
          <text x="74" y="60" fontSize="9" fill={RED}>拆股断崖</text>
        </svg>
        <div className="text-[11px] text-center text-muted-foreground">不复权:有断崖(假摔)</div>
      </div>
      <div className="rounded-lg border p-2">
        <svg viewBox="0 0 160 90" className="w-full">
          <polyline points="10,72 40,62 70,50 95,44 120,36 150,26" fill="none" stroke={UP} strokeWidth="2" />
        </svg>
        <div className="text-[11px] text-center text-muted-foreground">前复权:平滑连续</div>
      </div>
    </div>
  );
}

function BlockView({ b }: { b: Block }) {
  switch (b.t) {
    case "fig":
      return b.kind === "kline" ? <KlineFig /> : <AdjustFig />;
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
