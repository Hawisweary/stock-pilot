"use client";

import { useEffect, useState, useRef, useCallback, Suspense } from "react";
import { useRouter, usePathname, useSearchParams } from "next/navigation";
import { StockTable } from "@/components/StockTable";
import { Input } from "@/components/ui/input";
import { Button } from "@/components/ui/button";
import {
  Dialog, DialogContent, DialogDescription, DialogFooter,
  DialogHeader, DialogTitle, DialogTrigger,
} from "@/components/ui/dialog";
import { api, pollOnboardUntilDone, pollSingleFetchUntilDone, afterFetchWaitForScore, scoreStockUntilDone, V5_RECALC_EVENT, loadStocksWithV5, getV5RecalcTimestamp } from "@/lib/api"
import { useToast } from "@/lib/useToast"

import { Plus, Search, Pencil, LayoutGrid, List, Trash2, RotateCw, Filter, X, BarChart3, Upload } from "lucide-react";
import { GroupManager } from "@/components/GroupManager";
import { GroupCompareTable } from "@/components/GroupCompareTable";
import { CsvImportDialog } from "@/components/CsvImportDialog";

const FILTER_PRESETS = [
  { label: "高分", desc: "综合分≥60", filter: (r: any) => ((r.score ?? r.composite_v5) || 0) >= 60 },
  { label: "中高分", desc: "综合分 40–60", filter: (r: any) => ((r.score ?? r.composite_v5) || 0) >= 40 && ((r.score ?? r.composite_v5) || 0) < 60 },
  { label: "低分", desc: "综合分<40", filter: (r: any) => ((r.score ?? r.composite_v5) || 0) < 40 && (r.score ?? r.composite_v5) != null },
  { label: "需回避", desc: "否决", filter: (r: any) => r.veto_status === "exclude" },
  { label: "全部", desc: "", filter: () => true },
];

export default function StocksPageWrapper() {
  return (
    <Suspense fallback={<div className="p-8 animate-pulse text-muted-foreground text-sm">加载中...</div>}>
      <StocksPage />
    </Suspense>
  );
}

function StocksPage() {
  const toast = useToast();
  const router = useRouter();
  const [stocks, setStocks] = useState<any[]>([]);
  const [loading, setLoading] = useState(true);
  const [dialogOpen, setDialogOpen] = useState(false);
  const [newCode, setNewCode] = useState("");
  const [newName, setNewName] = useState("");
  const searchParams = useSearchParams();
  const [adding, setAdding] = useState(false);
  const [batchCodes, setBatchCodes] = useState("");
  const [batchResults, setBatchResults] = useState<any[]>([]);
  const [batchAdding, setBatchAdding] = useState(false);
  const [activeFilter, setActiveFilter] = useState(() => searchParams.get("filter") ?? "全部");
  const [searchResults, setSearchResults] = useState<any[]>([]);
  const [viewMode, setViewMode] = useState<"list" | "grouped">(() =>
    searchParams.get("view") === "grouped" ? "grouped" : "list"
  );
  const [groups, setGroups] = useState<any[]>([]);
  const [editDialog, setEditDialog] = useState(false);
  const [editStock, setEditStock] = useState<any>(null);
  const [compareGroup, setCompareGroup] = useState<{ id: number; name: string; stocks: { id: number; code: string; name: string }[] } | null>(null);
  const [csvImportOpen, setCsvImportOpen] = useState(false);
  const [scoringId, setScoringId] = useState<number | null>(null);
  const [editName, setEditName] = useState("");
  const [editIndustry, setEditIndustry] = useState("");
  const [editIndustries, setEditIndustries] = useState<string[]>([]);
  const [stockGroups, setStockGroups] = useState<any[]>([]);
  const [v5CalcDate, setV5CalcDate] = useState<string | null>(null);
  const [quotes, setQuotes] = useState<Map<string, { price: number; change_pct: number }>>(new Map());
  const [percentileMap, setPercentileMap] = useState<Map<number, number>>(new Map());
  const pathname = usePathname();
  const lastV5TsRef = useRef(0);

  // 实时行情轮询（30秒）
  useEffect(() => {
    const fetchQuotes = () => {
      fetch("/api/realtime/quotes")
        .then(r => r.ok ? r.json() : [])
        .then((data: { code: string; price: number; change_pct: number }[]) => {
          const map = new Map<string, { price: number; change_pct: number }>();
          for (const q of data) map.set(q.code, { price: q.price, change_pct: q.change_pct });
          setQuotes(map);
        })
        .catch(() => {});
    };
    fetchQuotes();
    const timer = setInterval(fetchQuotes, 30000);
    return () => clearInterval(timer);
  }, []);

  const syncUrl = useCallback((filter: string, view: string) => {
    const p = new URLSearchParams();
    if (filter && filter !== "全部") p.set("filter", filter);
    if (view === "grouped") p.set("view", "grouped");
    const qs = p.toString();
    router.replace(pathname + (qs ? `?${qs}` : ""), { scroll: false });
  }, [router, pathname]);

  const handleDelete = async (stock: any) => {
    if (!confirm(`确认删除 ${stock.name}(${stock.code})？\n\n将从自选列表中移除（数据保留）。`)) return;
    try {
      await api.deleteStock(stock.id);
      await loadData();
      await loadGroups();
    } catch (e: any) { toast.error("删除失败: " + e.message); }
  };

  const fetchStockData = async (stock: { id: number; code: string; name?: string }) => {
    toast.info(`${stock.code} 抓取中...`);
    try {
      const result = await api.fetchStock(stock.id, {
        onProgress: (msg) => toast.info(`${stock.code} ${msg}`),
      });
      const msg = `${stock.code}：行情 ${result.quotes_count} 条，财报 ${result.financials_count} 条`;
      toast.info(`${stock.code} 评分中…`);
      await afterFetchWaitForScore(
        stock.id,
        result.batch_fill_job_id,
        (s) => toast.info(`${stock.code} ${s}`),
      );
      if (result.status === "partial") {
        toast.info(`部分成功 — ${msg}，评分已更新`);
      } else {
        toast.success(`完成 — ${msg}，评分已更新`);
      }
      await loadData();
    } catch (e: unknown) {
      const msg = e instanceof Error ? e.message : "未知错误";
      toast.error(`抓取失败: ${msg}`);
    }
  };

  const handleScoreStock = async (stock: { id: number; code: string; name?: string }) => {
    setScoringId(stock.id);
    try {
      toast.info(`${stock.code} V5 评分中…`);
      await scoreStockUntilDone(stock.id, (msg) => toast.info(`${stock.code} ${msg}`));
      toast.success(`${stock.name || stock.code} 评分完成`);
      await loadData();
    } catch (e: unknown) {
      toast.error("评分失败: " + (e instanceof Error ? e.message : "未知错误"));
    } finally {
      setScoringId(null);
    }
  };

  const loadData = useCallback(async () => {
    setLoading(true);
    try {
      const { rows, calcDate } = await loadStocksWithV5("ALL");
      setStocks(rows);
      setV5CalcDate(calcDate);
      lastV5TsRef.current = getV5RecalcTimestamp();
    } catch (e) { console.error(e); }
    setLoading(false);
  }, []);

  const loadGroups = async () => {
    try { const res = await fetch("/api/groups"); setGroups((await res.json()) || []); }
    catch (e) { console.error(e); }
  };

  useEffect(() => {
    loadData();
    loadGroups();
    api.scorePercentileRanks().then((res) => {
      const m = new Map<number, number>();
      for (const [sid, info] of Object.entries(res.ranks)) {
        m.set(Number(sid), info.percentile);
      }
      setPercentileMap(m);
    }).catch(() => {});
  }, [loadData, pathname]);

  useEffect(() => {
    const onV5 = () => loadData();
    const onVisible = () => {
      if (document.visibilityState !== "visible") return;
      const ts = getV5RecalcTimestamp();
      if (ts > lastV5TsRef.current) loadData();
    };
    let channel: BroadcastChannel | null = null;
    try {
      channel = new BroadcastChannel(V5_RECALC_EVENT);
      channel.onmessage = () => loadData();
    } catch {
      /* ignore */
    }
    window.addEventListener(V5_RECALC_EVENT, onV5);
    document.addEventListener("visibilitychange", onVisible);
    return () => {
      window.removeEventListener(V5_RECALC_EVENT, onV5);
      document.removeEventListener("visibilitychange", onVisible);
      channel?.close();
    };
  }, [loadData]);

  // 按名称搜索
  const searchByName = async (q: string) => {
    setNewName(q);
    if (q.length < 1) { setSearchResults([]); return; }
    try {
      const res = await fetch(`/api/stocks/search/by-name?q=${encodeURIComponent(q)}`);
      const data = await res.json();
      setSearchResults(data.results || []);
    } catch (e) { setSearchResults([]); }
  };

  const [addingMsg, setAddingMsg] = useState("");
  const [batchOnboardJobId, setBatchOnboardJobId] = useState<string | null>(null);
  const [onboardProgress, setOnboardProgress] = useState("");
  const [onboardHint, setOnboardHint] = useState<{ stockIds: number[]; codes: string[] } | null>(null);
  const addPollAbortRef = useRef<AbortController | null>(null);

  useEffect(() => {
    return () => addPollAbortRef.current?.abort();
  }, []);

  const handleAddByCode = async () => {
    if (!newCode.trim()) return;
    addPollAbortRef.current?.abort();
    const ac = new AbortController();
    addPollAbortRef.current = ac;

    setAdding(true);
    setAddingMsg("正在添加...");
    try {
      const result = await api.addStock(newCode.trim());
      const stockId = result.id;
      const stockName = result?.name || newCode.trim();

      if (result.fetch_status === "started" && stockId) {
        setAddingMsg(`${stockName} 已添加，抓取数据中…`);
        const fetchResult = await pollSingleFetchUntilDone(
          stockId,
          (msg) => setAddingMsg(`${stockName} — ${msg}`),
          2000,
          ac.signal,
        );
        setAddingMsg(`${stockName} — 评分中…`);
        await afterFetchWaitForScore(
          stockId,
          fetchResult.batch_fill_job_id,
          (msg) => setAddingMsg(`${stockName} — ${msg}`),
          ac.signal,
        );
        const msg = `行情 ${fetchResult.quotes_count} · 财报 ${fetchResult.financials_count} · 指标 ${fetchResult.indicators_count}`;
        if (fetchResult.status === "partial") {
          toast.info(`${stockName} 部分完成 — ${msg}，评分已更新`);
        } else {
          toast.success(`${stockName} 就绪 — ${msg}，评分已更新`);
        }
      } else {
        toast.success(`${stockName} 已添加`);
      }

      setDialogOpen(false);
      setNewCode("");
      setNewName("");
      setSearchResults([]);
      await loadData();
      await loadGroups();
    } catch (e: unknown) {
      if (e instanceof DOMException && e.name === "AbortError") return;
      const msg = e instanceof Error ? e.message : "添加失败";
      toast.error(msg);
    } finally {
      setAdding(false);
      setAddingMsg("");
      addPollAbortRef.current = null;
    }
  };

  const handleAddByName = async (code: string) => {
    setNewCode(code);
    handleAddByCode();
  };

  const runOnboard = async (codes: string[]) => {
    setBatchOnboardJobId(null);
    setOnboardProgress("Onboard 启动中…");
    try {
      const queued = await api.onboardStocks({ codes, auto_score: true });
      setBatchOnboardJobId(queued.job_id);
      const job = await pollOnboardUntilDone(
        queued.job_id,
        setOnboardProgress,
      );
      const scoreInfo = job.result?.score as { batch_fill_job_id?: string; skipped?: boolean } | undefined;
      if (scoreInfo?.batch_fill_job_id && !scoreInfo.skipped) {
        setOnboardProgress("V5 评分中…");
        await afterFetchWaitForScore(0, scoreInfo.batch_fill_job_id, setOnboardProgress);
      }
      const prog = job.result?.progress as { message?: string } | undefined;
      toast.success(prog?.message || "Onboard 完成");
      setOnboardHint(null);
      await loadData();
      await loadGroups();
    } catch (e: unknown) {
      toast.error("Onboard 失败: " + (e instanceof Error ? e.message : String(e)));
    } finally {
      setBatchOnboardJobId(null);
      setOnboardProgress("");
    }
  };

  const handleBatchAdd = async () => {
    const codes = batchCodes.split(/[\n,;\s]+/).map(c => c.trim()).filter(Boolean);
    if (codes.length === 0) return;
    setBatchAdding(true);
    setBatchResults([]);
    setOnboardHint(null);
    try {
      const res = await fetch("/api/stocks/batch-add", {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ codes, market: "A" }),
      });
      const data = await res.json();
      setBatchResults(data.results || []);

      if (data.onboard_job_id) {
        setOnboardProgress("自动 Onboard 中…");
        const job = await pollOnboardUntilDone(
          data.onboard_job_id,
          setOnboardProgress,
        );
        const scoreInfo = job.result?.score as { batch_fill_job_id?: string; skipped?: boolean } | undefined;
        if (scoreInfo?.batch_fill_job_id && !scoreInfo.skipped) {
          setOnboardProgress("V5 评分中…");
          await afterFetchWaitForScore(0, scoreInfo.batch_fill_job_id, setOnboardProgress);
        }
        const prog = job.result?.progress as { message?: string } | undefined;
        toast.success(prog?.message || `已 onboard ${codes.length} 只股票`);
        await loadData();
        await loadGroups();
      } else if (data.onboard_hint && (data.stock_ids?.length ?? 0) > 0) {
        setOnboardHint({ stockIds: data.stock_ids, codes });
        toast.info(`已入库 ${data.stock_ids.length} 只，超过 5 只需确认后 Onboard`);
        await loadData();
        await loadGroups();
      } else if (data.results?.some((r: { status?: string }) => r.status === "added" || r.status === "reactivated")) {
        await loadData();
        await loadGroups();
      }
    } catch (e: unknown) {
      toast.error("批量添加失败: " + (e instanceof Error ? e.message : String(e)));
    } finally {
      setBatchAdding(false);
      setOnboardProgress("");
    }
  };

  const openEdit = async (stock: any) => {
    setEditStock(stock);
    setEditName(stock.name);
    setEditIndustry(stock.industry || "");
    setEditIndustries(stock.industry_list || (stock.industry ? [stock.industry] : []));
    // 加载该股票所属分组
    try {
      const res = await fetch("/api/groups");
      const allGroups = await res.json();
      const inGroups = allGroups.filter((g: any) => g.stocks.some((s: any) => s.id === stock.id)).map((g: any) => g.id);
      setStockGroups(inGroups);
    } catch { setStockGroups([]); }
    setEditDialog(true);
  };

  const toggleGroup = async (gid: number) => {
    if (!editStock) return;
    const inGroup = stockGroups.includes(gid);
    await fetch(`/api/groups/${gid}/${inGroup ? "remove" : "add"}`, {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ stock_id: editStock.id }),
    });
    setStockGroups(inGroup ? stockGroups.filter((id: number) => id !== gid) : [...stockGroups, gid]);
    await loadGroups();
  };

  const saveEdit = async () => {
    if (!editStock) return;
    try {
      await fetch(`/api/stocks/${editStock.id}`, {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ name: editName, industries: editIndustries.filter(Boolean) }),
      });
      setEditDialog(false);
      await loadData(); await loadGroups();
    } catch (e: any) { toast.error("保存失败"); }
  };

  const columns = [
    { key: "code", header: "代码", sortable: true },
    { key: "name", header: "名称", sortable: true },
    { key: "realtime_price", header: "现价", render: (row: any) => {
      const q = quotes.get(row.code);
      if (!q || !q.price) return <span className="text-muted-foreground text-xs">—</span>;
      const isUp = q.change_pct > 0;
      const isDown = q.change_pct < 0;
      return (
        <div>
          <div className={`font-mono text-xs font-bold ${isUp ? "text-red-600" : isDown ? "text-green-600" : ""}`}>
            {q.price.toFixed(2)}
          </div>
          <div className={`text-[10px] ${isUp ? "text-red-500" : isDown ? "text-green-500" : "text-muted-foreground"}`}>
            {isUp ? "+" : ""}{q.change_pct.toFixed(2)}%
          </div>
        </div>
      );
    }},
    { key: "industry_list", header: "行业", render: (row: any) => {
      const l1 = row.industry_sw || row.industry;
      const l2 = row.industry_sw2 && row.industry_sw2 !== l1 ? row.industry_sw2 : null;
      const l3 = row.industry_sw3 && row.industry_sw3 !== l2 && row.industry_sw3 !== l1 ? row.industry_sw3 : null;
      const tags: string[] = row.industry_list && row.industry_list.length > 0
        ? row.industry_list
        : l1 ? [l1] : [];
      return (
        <div className="flex flex-wrap items-center gap-0.5">
          {tags.map((ind: string, i: number) => (
            <span key={i} className="px-1.5 py-0.5 bg-gray-100 rounded text-xs">{ind}</span>
          ))}
          {l2 && (
            <span className="px-1.5 py-0.5 bg-gray-50 text-muted-foreground rounded text-xs">{l2}</span>
          )}
          {l3 && (
            <span className="px-1.5 py-0.5 bg-gray-50/60 text-muted-foreground/80 rounded text-[11px]">{l3}</span>
          )}
        </div>
      );
    }},
    { key: "concepts", header: "概念", render: (row: any) => {
      const concepts: string[] = row.concepts || [];
      if (concepts.length === 0) return <span className="text-xs text-muted-foreground">—</span>;
      const shown = concepts.slice(0, 3);
      return (
        <div className="flex flex-wrap items-center gap-0.5" title={concepts.join("、")}>
          {shown.map((c, i) => (
            <span key={i} className="px-1.5 py-0.5 bg-blue-50 text-blue-700 rounded text-xs">{c}</span>
          ))}
          {concepts.length > shown.length && (
            <span className="text-[10px] text-muted-foreground">+{concepts.length - shown.length}</span>
          )}
        </div>
      );
    }},
    { key: "score", header: "综合分", sortable: true, render: (row: any) => {
      const v = row.score ?? row.composite_v5;
      const bg = v == null ? "" : v >= 70 ? "bg-green-100 text-green-800" : v >= 40 ? "bg-yellow-100 text-yellow-800" : "bg-red-100 text-red-800";
      const pct = v != null ? percentileMap.get(row.id) : undefined;
      return (
        <div className="flex flex-col items-start gap-0.5">
          <span className={`font-bold px-1.5 py-0.5 rounded text-xs tabular-nums ${bg}`}>
            {v != null ? Number(v).toFixed(1) : "—"}
          </span>
          {pct != null && (
            <span className="text-[10px] text-muted-foreground leading-none px-0.5">
              前 {(100 - pct).toFixed(0)}%
            </span>
          )}
        </div>
      );
    }},
    { key: "veto_status", header: "状态", sortable: true, render: (row: any) => (
      row.veto_status === "exclude" ? (
        <span className="text-xs text-gray-600">回避</span>
      ) : row.veto_status === "reduce" ? (
        <span className="text-xs text-amber-600">减仓</span>
      ) : (
        <span className="text-xs text-muted-foreground">正常</span>
      )
    )},
    { key: "actions", header: "", render: (row: any) => (
      <div className="flex items-center gap-1">
        <button
          onClick={(e) => { e.stopPropagation(); handleScoreStock(row); }}
          disabled={scoringId === row.id}
          className="p-1 hover:bg-purple-50 rounded disabled:opacity-40"
          title="V5 评分"
        >
          <BarChart3 className={`h-3.5 w-3.5 text-purple-500 hover:text-purple-700 ${scoringId === row.id ? "animate-pulse" : ""}`} />
        </button>
        <button onClick={(e) => { e.stopPropagation(); fetchStockData(row); }} className="p-1 hover:bg-blue-50 rounded" title="抓取数据">
          <RotateCw className="h-3.5 w-3.5 text-blue-400 hover:text-blue-600" />
        </button>
        <button onClick={(e) => { e.stopPropagation(); openEdit(row); }} className="p-1 hover:bg-muted rounded">
          <Pencil className="h-3.5 w-3.5" />
        </button>
        <button onClick={(e) => { e.stopPropagation(); handleDelete(row); }} className="p-1 hover:bg-red-50 rounded">
          <Trash2 className="h-3.5 w-3.5 text-red-400 hover:text-red-600" />
        </button>
      </div>
    )},
  ];

  return (
    <div className="space-y-6">
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-2xl font-bold">股票列表</h1>
          {v5CalcDate && (
            <p className="text-xs text-muted-foreground mt-0.5">
              V5 评分日 {v5CalcDate} · 与 Dashboard 排名同源
            </p>
          )}
        </div>
        <div className="flex items-center gap-2">
          <button
            type="button"
            onClick={() => loadData()}
            className="inline-flex items-center gap-1 text-sm text-muted-foreground hover:text-foreground"
          >
            <RotateCw className="h-4 w-4" /> 刷新分数
          </button>
          <button
            onClick={() => {
              const next = viewMode === "list" ? "grouped" : "list";
              setViewMode(next);
              syncUrl(activeFilter, next);
            }}
            className="inline-flex items-center gap-1 text-sm text-muted-foreground hover:text-foreground"
          >
            {viewMode === "list" ? <><LayoutGrid className="h-4 w-4" /> 分组</> : <><List className="h-4 w-4" /> 列表</>}
          </button>
          <Dialog open={dialogOpen} onOpenChange={setDialogOpen}>
            <DialogTrigger className="inline-flex items-center gap-1 rounded-md bg-primary text-primary-foreground px-3 py-1.5 text-sm font-medium hover:bg-primary/90">
              <Plus className="h-4 w-4" /> 添加股票
            </DialogTrigger>
            <DialogContent className="max-w-lg">
              <DialogHeader>
                <DialogTitle>添加股票</DialogTitle>
                <DialogDescription>输入代码直接添加，或输入名称搜索</DialogDescription>
              </DialogHeader>

              {/* 按代码添加 */}
              <div className="flex gap-2">
                <Input placeholder="代码（如 600519）" value={newCode} onChange={(e) => setNewCode(e.target.value)}
                  onKeyDown={(e) => e.key === "Enter" && handleAddByCode()} className="flex-1" />
                <button onClick={handleAddByCode} disabled={adding || !newCode.trim()}
                  className="px-3 py-1.5 bg-primary text-primary-foreground rounded-md text-sm hover:bg-primary/90 disabled:opacity-50">
                  {adding ? "..." : "添加"}
                </button>
              </div>

              <div className="text-xs text-center text-muted-foreground">— 或按名称搜索 —</div>

              {/* 按名称搜索 */}
              <div className="relative">
                <Input placeholder="名称搜索（如 茅台）" value={newName}
                  onChange={(e) => searchByName(e.target.value)} className="w-full" />
                <Search className="h-4 w-4 absolute right-3 top-2.5 text-muted-foreground" />
              </div>
              {searchResults.length > 0 && (
                <div className="border rounded-md max-h-48 overflow-y-auto">
                  {searchResults.map((r: any) => (
                    <button key={r.code} onClick={() => handleAddByName(r.code)}
                      className="w-full text-left px-3 py-2 hover:bg-muted flex justify-between items-center text-sm">
                      <span>{r.name}</span>
                      <span className="text-muted-foreground font-mono">{r.code}</span>
                    </button>
                  ))}
                </div>
              )}
              {newName.length >= 1 && searchResults.length === 0 && (
                <div className="text-sm text-muted-foreground text-center py-2">未找到匹配股票</div>
              )}

              <div className="flex items-center gap-2 mt-2">
                <div className="flex-1 border-t" />
                <span className="text-xs text-muted-foreground">或批量添加</span>
                <div className="flex-1 border-t" />
              </div>
              <button onClick={() => setCsvImportOpen(true)} className="w-full text-xs border rounded-md py-1.5 hover:bg-accent transition-colors flex items-center justify-center gap-1.5">
                <Upload className="h-3.5 w-3.5" /> 从 CSV 文件导入
              </button>
              <div className="flex flex-col gap-2">
                <textarea
                  placeholder="输入多个代码，用逗号/换行/空格/分号分隔&#10;例：600519,000858,002415"
                  value={batchCodes}
                  onChange={(e) => setBatchCodes(e.target.value)}
                  className="w-full min-h-[80px] border rounded-md px-3 py-2 text-sm bg-background resize-y"
                  disabled={batchAdding}
                />
                <div className="flex gap-2">
                  <button onClick={handleBatchAdd} disabled={batchAdding || !batchCodes.trim()}
                    className="px-3 py-1.5 bg-primary text-primary-foreground rounded-md text-sm hover:bg-primary/90 disabled:opacity-50">
                    {batchAdding ? "添加中..." : "批量添加"}
                  </button>
                  {onboardProgress && (
                    <span className="text-xs self-center text-blue-600">{onboardProgress}</span>
                  )}
                  {batchResults.length > 0 && (
                    <span className="text-xs self-center text-muted-foreground">
                      {batchResults.filter(r => r.status === "added" || r.status === "reactivated").length} 添加 / {batchResults.filter(r => r.status === "skipped").length} 跳过
                    </span>
                  )}
                </div>
                {onboardHint && (
                  <div className="rounded-md border border-amber-200 bg-amber-50 p-3 text-sm text-amber-900">
                    <p className="mb-2">已入库 {onboardHint.stockIds.length} 只，需确认后开始抓取与评分。</p>
                    <button
                      type="button"
                      disabled={!!batchOnboardJobId}
                      onClick={() => runOnboard(onboardHint.codes)}
                      className="px-3 py-1.5 bg-amber-600 text-white rounded-md text-sm hover:bg-amber-700 disabled:opacity-50"
                    >
                      {batchOnboardJobId ? "Onboard 中…" : "确认 Onboard"}
                    </button>
                  </div>
                )}
                {batchResults.length > 0 && (
                  <div className="max-h-32 overflow-y-auto border rounded-md text-xs">
                    {batchResults.map((r, i) => (
                      <div key={i} className={`px-3 py-1 border-b last:border-b-0 ${
                        r.status === "added" || r.status === "reactivated" ? "text-green-600" : r.status === "skipped" ? "text-amber-600" : "text-red-600"
                      }`}>
                        {r.code}: {r.status === "added" || r.status === "reactivated" ? "✓" : r.status === "skipped" ? "已在列表" : "✗ " + (r.reason || r.status)}
                      </div>
                    ))}
                  </div>
                )}
              </div>

              {/* 添加进度提示 */}
              {addingMsg && (
                <div className="bg-blue-50 border border-blue-200 rounded-md p-3 text-sm text-blue-800">
                  <div className="flex items-center gap-2">
                    <span className="animate-spin inline-block h-3 w-3 border-2 border-blue-600 border-t-transparent rounded-full" />
                    <span>{addingMsg}</span>
                  </div>
                </div>
              )}
            </DialogContent>
          </Dialog>
        </div>
      </div>

      {loading ? (
        <div className="animate-pulse space-y-4">
          <div className="h-10 bg-muted rounded w-48" />
          <div className="h-64 bg-muted rounded" />
        </div>
      ) : viewMode === "list" ? (
        <div className="space-y-3">
          <div className="flex flex-wrap gap-1.5">
            {FILTER_PRESETS.map(p => (
              <button key={p.label} onClick={() => { setActiveFilter(p.label); syncUrl(p.label, viewMode); }}
                className={`text-xs px-2.5 py-1 rounded-full border transition-colors
                  ${activeFilter === p.label ? "bg-primary text-primary-foreground border-primary" : "border-border hover:bg-accent"}`}>
                <Filter className="inline h-3 w-3 mr-0.5" />
                {p.label}{p.desc ? ` (${p.desc})` : ""}
              </button>
            ))}
          </div>
          <StockTable columns={columns} data={stocks.filter(FILTER_PRESETS.find(p=>p.label===activeFilter)?.filter||(()=>true))} searchKeys={["code", "name"]}
          onRowClick={(row: any) => router.push(`/stocks/${row.id}`)} />
        </div>
      ) : (
        /* 自定义分组视图 */
        <GroupManager onSelectGroup={(g) => {
          if (g.stocks.length > 0) setCompareGroup({ id: g.id, name: g.name, stocks: g.stocks });
        }} />
      )}

      {/* P2-2: CSV 导入 */}
      <CsvImportDialog
        open={csvImportOpen}
        onClose={() => setCsvImportOpen(false)}
        existingCodes={new Set(stocks.map((s: any) => s.code))}
        onImported={() => loadData()}
      />

      {/* U2-2: 分组对比视图 */}
      {compareGroup && (
        <GroupCompareTable
          open={!!compareGroup}
          onClose={() => setCompareGroup(null)}
          groupId={compareGroup.id}
          groupName={compareGroup.name}
          stocks={compareGroup.stocks}
        />
      )}

      {/* 编辑对话框 */}
      <Dialog open={editDialog} onOpenChange={setEditDialog}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>编辑 {editStock?.code}</DialogTitle>
          </DialogHeader>
          <div className="space-y-3">
            <div>
              <label className="text-sm text-muted-foreground">名称</label>
              <Input value={editName} onChange={(e) => setEditName(e.target.value)} />
            </div>
            <div>
              <label className="text-sm text-muted-foreground">行业（逗号或回车分隔多个）</label>
              <div className="flex flex-wrap gap-1 mb-1">
                {editIndustries.filter(Boolean).map((ind, i) => (
                  <span key={i} className="px-2 py-0.5 bg-blue-100 text-blue-800 rounded text-xs flex items-center gap-1">
                    {ind}
                    <button onClick={() => setEditIndustries(editIndustries.filter((_,j) => j!==i))} className="hover:text-red-500">&times;</button>
                  </span>
                ))}
              </div>
              <Input value={editIndustry} onChange={(e) => setEditIndustry(e.target.value)}
                onKeyDown={(e) => {
                  if (e.key === "Enter" || e.key === ",") {
                    e.preventDefault();
                    const val = editIndustry.replace(/,/g,"").trim();
                    if (val && !editIndustries.includes(val)) {
                      setEditIndustries([...editIndustries, val]);
                      setEditIndustry("");
                    }
                  }
                }}
                placeholder="输入行业后按回车添加" />
            </div>
            {/* 分组管理 */}
            <div>
              <label className="text-sm text-muted-foreground">所属分组（可多选）</label>
              <div className="max-h-32 overflow-y-auto border rounded-md p-2 space-y-1">
                {groups.length === 0 ? (
                  <p className="text-xs text-muted-foreground">暂无分组，先在分组视图创建</p>
                ) : groups.map((g: any) => (
                  <label key={g.id} className="flex items-center gap-2 text-sm cursor-pointer hover:bg-muted/50 px-1 rounded">
                    <input type="checkbox" checked={stockGroups.includes(g.id)} onChange={() => toggleGroup(g.id)} className="rounded" />
                    <span>{g.name}</span>
                    <span className="text-xs text-muted-foreground">({g.stock_count}只)</span>
                  </label>
                ))}
              </div>
            </div>
          </div>
          <DialogFooter>
            <Button onClick={saveEdit}>保存</Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </div>
  );
}
