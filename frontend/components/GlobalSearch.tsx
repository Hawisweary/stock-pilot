"use client";

import { useEffect, useRef, useState } from "react";
import { useRouter } from "next/navigation";
import { Dialog, DialogContent } from "@/components/ui/dialog";
import { Search } from "lucide-react";

interface StockItem {
  id: number;
  code: string;
  name: string;
}

let cachedStocks: StockItem[] = [];

function fuzzyMatch(item: StockItem, q: string): boolean {
  const lq = q.toLowerCase();
  return item.code.toLowerCase().includes(lq) || item.name.includes(lq);
}

export function GlobalSearch() {
  const [open, setOpen] = useState(false);
  const [query, setQuery] = useState("");
  const [stocks, setStocks] = useState<StockItem[]>([]);
  const [selected, setSelected] = useState(0);
  const router = useRouter();
  const inputRef = useRef<HTMLInputElement>(null);
  const listRef = useRef<HTMLDivElement>(null);

  // ⌘K / Ctrl+K
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if ((e.metaKey || e.ctrlKey) && e.key === "k") {
        e.preventDefault();
        setOpen((v) => !v);
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, []);

  // 懒加载股票列表
  useEffect(() => {
    if (!open) return;
    if (cachedStocks.length > 0) { setStocks(cachedStocks); return; }
    fetch("/api/stocks")
      .then((r) => r.json())
      .then((data: StockItem[]) => {
        cachedStocks = data;
        setStocks(data);
      })
      .catch(() => {});
  }, [open]);

  useEffect(() => {
    if (open) {
      setQuery("");
      setSelected(0);
      setTimeout(() => inputRef.current?.focus(), 50);
    }
  }, [open]);

  const results = query.trim()
    ? stocks.filter((s) => fuzzyMatch(s, query.trim())).slice(0, 8)
    : stocks.slice(0, 8);

  const confirm = (item: StockItem) => {
    setOpen(false);
    router.push(`/stocks/${item.code}`);
  };

  const onKeyDown = (e: React.KeyboardEvent) => {
    if (e.key === "ArrowDown") {
      e.preventDefault();
      setSelected((v) => Math.min(v + 1, results.length - 1));
    } else if (e.key === "ArrowUp") {
      e.preventDefault();
      setSelected((v) => Math.max(v - 1, 0));
    } else if (e.key === "Enter" && results[selected]) {
      confirm(results[selected]);
    }
  };

  // 滚动选中项到可视区
  useEffect(() => {
    const el = listRef.current?.querySelector(`[data-idx="${selected}"]`);
    el?.scrollIntoView({ block: "nearest" });
  }, [selected]);

  return (
    <Dialog open={open} onOpenChange={setOpen}>
      <DialogContent showCloseButton={false} className="sm:max-w-md p-0 gap-0 overflow-hidden">
        <div className="flex items-center gap-2 px-4 py-3 border-b">
          <Search className="h-4 w-4 text-muted-foreground shrink-0" />
          <input
            ref={inputRef}
            value={query}
            onChange={(e) => { setQuery(e.target.value); setSelected(0); }}
            onKeyDown={onKeyDown}
            placeholder="搜索股票代码或名称…"
            className="flex-1 bg-transparent text-sm outline-none placeholder:text-muted-foreground"
          />
          <kbd className="hidden sm:inline-flex h-5 items-center gap-1 rounded border bg-muted px-1.5 font-mono text-[10px] text-muted-foreground">
            ESC
          </kbd>
        </div>
        <div ref={listRef} className="max-h-72 overflow-y-auto py-1">
          {results.length === 0 ? (
            <p className="py-6 text-center text-sm text-muted-foreground">未找到股票</p>
          ) : (
            results.map((item, i) => (
              <div
                key={item.id}
                data-idx={i}
                onClick={() => confirm(item)}
                className={`flex items-center gap-3 px-4 py-2.5 cursor-pointer text-sm transition-colors ${
                  i === selected ? "bg-accent text-accent-foreground" : "hover:bg-accent/60"
                }`}
              >
                <span className="font-mono text-xs text-muted-foreground w-16 shrink-0">{item.code}</span>
                <span className="font-medium truncate">{item.name}</span>
              </div>
            ))
          )}
        </div>
        <div className="border-t px-4 py-2 flex items-center gap-4 text-[10px] text-muted-foreground">
          <span>↑↓ 选择</span><span>↵ 进入</span><span>ESC 关闭</span>
        </div>
      </DialogContent>
    </Dialog>
  );
}
