"use client";
import { useEffect, useRef, useState } from "react";
import { usePathname } from "next/navigation";
import { MessageCircle, X, Send, Sparkles } from "lucide-react";

type Msg = { role: "user" | "bot"; text: string };
const SUGGESTIONS = ["什么是市盈率?", "K线怎么看?", "什么是复权?", "选股筛选怎么用?", "定投适合新手吗?"];

const PAGE_NAMES: Record<string, string> = {
  global: "全球市场", learn: "新手教程", market: "市场行情", screener: "选股筛选",
  heatmap: "V5热力图", etf: "ETF列表", stocks: "股票列表", calendar: "财报日历", data: "数据中心",
};
function pageContext(pathname: string): string {
  const seg = pathname.split("/").filter(Boolean);
  if (seg[0] === "us" && seg[1]) return `美股 ${seg[1].toUpperCase()} 的详情页`;
  if (seg[0] === "us-etf" && seg[1]) return `美股ETF ${seg[1].toUpperCase()} 的详情页`;
  if ((seg[0] === "stock" || seg[0] === "stocks") && seg[1]) return `A股 ${seg[1]} 的详情页`;
  if (seg[0] === "us") return "美股列表页";
  if (seg[0] === "us-etf") return "美股ETF列表页";
  return PAGE_NAMES[seg[0]] ? `${PAGE_NAMES[seg[0]]}页` : "首页";
}

export function AssistantWidget() {
  const [open, setOpen] = useState(false);
  const [msgs, setMsgs] = useState<Msg[]>([]);
  const [input, setInput] = useState("");
  const [loading, setLoading] = useState(false);
  const [available, setAvailable] = useState(true);
  const pathname = usePathname();
  const scrollRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    fetch("/api/assistant/status").then((r) => r.json()).then((d) => setAvailable(d.available ?? false)).catch(() => {});
  }, []);
  useEffect(() => { scrollRef.current?.scrollTo(0, scrollRef.current.scrollHeight); }, [msgs, loading]);

  const send = async (q: string) => {
    const question = q.trim();
    if (!question || loading) return;
    setMsgs((m) => [...m, { role: "user", text: question }]);
    setInput("");
    setLoading(true);
    try {
      const r = await fetch("/api/assistant/ask", {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ question, context: pageContext(pathname) }),
      });
      const d = await r.json();
      setMsgs((m) => [...m, { role: "bot", text: d.answer || d.error || "抱歉,暂时无法回答。" }]);
    } catch {
      setMsgs((m) => [...m, { role: "bot", text: "网络错误,请稍后再试。" }]);
    } finally {
      setLoading(false);
    }
  };

  return (
    <>
      {!open && (
        <button onClick={() => setOpen(true)} aria-label="学习助手"
          className="fixed bottom-5 right-5 z-40 flex h-12 w-12 items-center justify-center rounded-full bg-primary text-primary-foreground shadow-lg hover:scale-105 transition-transform">
          <MessageCircle className="h-5 w-5" />
        </button>
      )}
      {open && (
        <div className="fixed bottom-5 right-5 z-50 flex h-[520px] w-[min(380px,calc(100vw-2rem))] flex-col rounded-2xl border bg-card shadow-2xl">
          <div className="flex items-center justify-between border-b px-4 py-2.5">
            <div className="flex items-center gap-1.5 text-sm font-semibold">
              <Sparkles className="h-4 w-4 text-primary" /> 学习助手
            </div>
            <button onClick={() => setOpen(false)} className="p-1 rounded hover:bg-accent"><X className="h-4 w-4" /></button>
          </div>

          <div ref={scrollRef} className="flex-1 overflow-y-auto px-3 py-3 space-y-3">
            {msgs.length === 0 && (
              <div className="text-xs text-muted-foreground space-y-3">
                <p>我是投资学习助手,可以解释概念、指标、策略,或教你用这个软件。<b>我不会推荐买卖或预测涨跌。</b></p>
                <div className="flex flex-wrap gap-1.5">
                  {SUGGESTIONS.map((s) => (
                    <button key={s} onClick={() => send(s)}
                      className="rounded-full border px-2.5 py-1 text-[11px] hover:bg-accent">{s}</button>
                  ))}
                </div>
              </div>
            )}
            {msgs.map((m, i) => (
              <div key={i} className={`flex ${m.role === "user" ? "justify-end" : "justify-start"}`}>
                <div className={`max-w-[85%] rounded-2xl px-3 py-2 text-[13px] leading-relaxed whitespace-pre-wrap ${
                  m.role === "user" ? "bg-primary text-primary-foreground" : "bg-muted text-foreground"
                }`}>{m.text}</div>
              </div>
            ))}
            {loading && <div className="text-xs text-muted-foreground px-1">正在思考…</div>}
          </div>

          <div className="border-t p-2.5">
            {!available && <div className="text-[10px] text-muted-foreground mb-1">助手未配置,暂不可用</div>}
            <div className="flex items-center gap-2">
              <input value={input} onChange={(e) => setInput(e.target.value)}
                onKeyDown={(e) => e.key === "Enter" && send(input)}
                placeholder="问点什么…(仅投教,不荐股)"
                className="flex-1 h-9 rounded-lg border bg-background px-3 text-sm outline-none focus:ring-1 focus:ring-ring" />
              <button onClick={() => send(input)} disabled={loading || !input.trim()}
                className="flex h-9 w-9 items-center justify-center rounded-lg bg-primary text-primary-foreground disabled:opacity-40">
                <Send className="h-4 w-4" />
              </button>
            </div>
            <div className="text-[10px] text-muted-foreground/70 mt-1 text-center">AI 生成,仅供学习,不构成投资建议</div>
          </div>
        </div>
      )}
    </>
  );
}
