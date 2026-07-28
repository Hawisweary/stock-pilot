"use client";

import { useEffect, useState, useRef } from "react";
import { Badge } from "@/components/ui/badge";
import {
  api,
  clearCache,
  DataStatus,
  FetchLogStep,
  FetchStepStatusEntry,
  pollFetchUntilDone,
} from "@/lib/api";
import { useToast } from "@/lib/useToast";
import { MarketOpsButtons } from "@/components/MarketOpsButtons";
import { RefreshCw, Play, RotateCw } from "lucide-react";
import { DataTabs } from "@/components/DataTabs";

const LOG_STEPS: { key: string; label: string }[] = [
  { key: "quotes", label: "行情" },
  { key: "financials", label: "财报" },
  { key: "financials_quarterly", label: "季报" },
  { key: "indicators", label: "指标" },
  { key: "valuation", label: "估值" },
];

const STEP_STATUS_HINT: Record<string, string> = {
  skipped_by_plan: "计划跳过（增量）",
  circuit_breaker: "熔断跳过",
};

function FetchStepBadges({
  logs,
  stepStatus,
}: {
  logs?: Record<string, FetchLogStep>;
  stepStatus?: Record<string, FetchStepStatusEntry>;
}) {
  if (!logs && !stepStatus) return <span className="text-[10px] text-muted-foreground">—</span>;
  return (
    <div className="flex flex-wrap gap-1 max-w-[220px]">
      {LOG_STEPS.map(({ key, label }) => {
        const st = stepStatus?.[key];
        if (st?.status === "skipped") {
          const hint = STEP_STATUS_HINT[st.message] || st.message || "已跳过";
          return (
            <span
              key={key}
              title={`${label}: ${hint}${st.updated_at ? ` (${st.updated_at})` : ""}`}
              className={`text-[9px] px-1 rounded ${
                st.message === "circuit_breaker"
                  ? "bg-amber-500/15 text-amber-700 dark:text-amber-500"
                  : "bg-muted text-muted-foreground"
              }`}
            >
              {label}跳
            </span>
          );
        }
        if (st?.status === "error") {
          return (
            <span
              key={key}
              title={`${label}: ${st.message || "失败"}${st.updated_at ? ` (${st.updated_at})` : ""}`}
              className="text-[9px] px-1 rounded bg-destructive/10 text-destructive"
            >
              {label}!
            </span>
          );
        }
        const step = logs?.[key];
        if (!step) {
          return (
            <span key={key} className="text-[9px] px-1 rounded bg-muted/50 text-muted-foreground">
              {label}?
            </span>
          );
        }
        const ok = step.status === "success";
        const err = step.status === "error";
        return (
          <span
            key={key}
            title={`${step.source ? `[${step.source}] ` : ""}${step.error_message || `${label} ${step.records_count}条`}`}
            className={`text-[9px] px-1 rounded ${
              ok ? "bg-muted text-foreground/70" : err ? "bg-destructive/10 text-destructive" : "bg-amber-500/15 text-amber-700 dark:text-amber-500"
            }`}
          >
            {label}
          </span>
        );
      })}
    </div>
  );
}

const API = typeof window !== "undefined" ? window.location.origin : "";
export default function DataPage() {
  const toast = useToast();
  const [statusMap, setStatusMap] = useState<Record<number, DataStatus>>({});
  const [stocks, setStocks] = useState<DataStatus[]>([]);
  const [loading, setLoading] = useState(true);
  const [refreshMsg, setRefreshMsg] = useState("");
  const [fetchAllLoading, setFetchAllLoading] = useState(false);
  const [fetchingIds, setFetchingIds] = useState<Set<number>>(new Set());
  const [logSummary, setLogSummary] = useState<Record<number, Record<string, FetchLogStep>>>({});
  const [stepStatusMap, setStepStatusMap] = useState<
    Record<number, Record<string, FetchStepStatusEntry>>
  >({});
  const pollAbortRef = useRef<AbortController | null>(null);

  useEffect(() => {
    loadData();
    return () => pollAbortRef.current?.abort();
  }, []);

  const loadData = async () => {
    setLoading(true);
    try {
      const [data, logsRes, stepRes] = await Promise.all([
        api.dataStatus(),
        api.fetchLogsSummary().catch(() => ({ summary: {} })),
        api.fetchStepStatus().catch(() => ({ summary: {} })),
      ]);
      setStocks(data);
      setLogSummary(logsRes.summary || {});
      setStepStatusMap(stepRes.summary || {});
      const map: Record<number, DataStatus> = {};
      for (const s of data) {
        map[s.stock_id] = s;
      }
      setStatusMap(map);
    } catch (e) {
      console.error(e);
      toast.error("加载数据状态失败");
    }
    setLoading(false);
  };

  const getAge = (status: DataStatus): string => {
    if (!status?.last_quote_date) return "无数据";
    try {
      const d = new Date(status.last_quote_date + "T15:00:00+08:00");
      const n = new Date();
      const h = Math.floor((n.getTime() - d.getTime()) / 36e5);
      if (h < 1) return "刚刚";
      if (h < 24) return `${h}小时前`;
      return `${Math.floor(h / 24)}天前`;
    } catch {
      return "?";
    }
  };

  const getLabel = (status: DataStatus): string => {
    if (!status?.last_quote_date) return "无数据";
    try {
      const d = new Date(status.last_quote_date + "T15:00:00+08:00");
      const n = new Date();
      const h = Math.floor((n.getTime() - d.getTime()) / 36e5);
      return h < 24 ? "fresh" : "stale";
    } catch {
      return "stale";
    }
  };

  const fetchStock = async (s: DataStatus) => {
    if (fetchingIds.has(s.stock_id)) return;
    setFetchingIds((prev) => new Set(prev).add(s.stock_id));
    setRefreshMsg(`正在抓取 ${s.code}...`);
    try {
      const result = await api.fetchStock(s.stock_id, {
        onProgress: () => setRefreshMsg(`正在抓取 ${s.code}...`),
      });
      const msg = `${s.code}：行情 ${result.quotes_count} 条，财报 ${result.financials_count} 条`;
      if (result.status === "partial") {
        const detail = (result.errors || []).map((e) => e.step).join(", ");
        toast.info(`部分成功 — ${msg}${detail ? `（失败: ${detail}）` : ""}`);
      } else {
        toast.success(`更新成功 — ${msg}`);
      }
      await loadData();
    } catch (e: unknown) {
      const msg = e instanceof Error ? e.message : String(e);
      toast.error(`抓取失败: ${msg}`);
      setRefreshMsg(`抓取异常: ${msg}`);
    } finally {
      setFetchingIds((prev) => {
        const next = new Set(prev);
        next.delete(s.stock_id);
        return next;
      });
      setTimeout(() => setRefreshMsg(""), 3000);
    }
  };

  const handleFetchAll = async (mode: "incremental" | "full") => {
    pollAbortRef.current?.abort();
    const ac = new AbortController();
    pollAbortRef.current = ac;

    const label = mode === "full" ? "深度全量" : "增量抓取";
    setFetchAllLoading(true);
    setRefreshMsg(`${label}启动中...`);
    try {
      const result = await api.fetchAll(mode);
      if (result.warning) {
        toast.info(result.warning);
      }
      if (result.status === "already_running") {
        setRefreshMsg(`抓取进行中 ${result.progress || ""}`);
      } else if (result.count === 0) {
        toast.error(result.message || "没有可抓取的股票");
        return;
      }

      const finalStatus = await pollFetchUntilDone(
        (p) => setRefreshMsg(`${label} ${p}`),
        2000,
        ac.signal,
      );
      const progressStr = finalStatus?.progress || "";
      const m = progressStr.match(/(\d+)\/(\d+)/);
      const parsedDone = m ? parseInt(m[1], 10) : 0;
      const total =
        finalStatus?.total ?? (m ? parseInt(m[2], 10) : 0);
      const processed = finalStatus?.processed ?? parsedDone;
      const success = finalStatus?.success ?? parsedDone;
      const batchError = finalStatus?.error?.trim();

      if (batchError === "poll_timeout_background_running") {
        const prog = finalStatus.progress || `${processed}/${total}`;
        toast.info(
          `抓取仍在后台进行（${prog}），页面轮询已结束。请稍后刷新本页查看进度，勿重复点击。`,
        );
        setRefreshMsg(`后台抓取中 ${prog}`);
      } else if (batchError) {
        toast.error(batchError);
        setRefreshMsg(batchError);
      } else if (total > 0 && processed < total) {
        toast.error(`批量抓取未完成：仅处理 ${processed}/${total}`);
        setRefreshMsg(`仅处理 ${processed}/${total}，${total - processed} 只未跑完`);
      } else if (total > 0 && success < total) {
        toast.info(
          `${mode === "full" ? "深度全量" : "增量"}抓取结束：${success}/${total} 完全成功，${total - success} 只有步骤失败或部分成功。请点「重算 V5」更新评分。`,
        );
        setRefreshMsg("");
      } else {
        toast.success(
          mode === "incremental"
            ? `增量抓取完成（${success}/${total}），请点「重算 V5」更新评分`
            : `深度全量完成（${success}/${total}），请点「重算 V5」更新评分`,
        );
        setRefreshMsg("");
      }
      await loadData();
    } catch (e: unknown) {
      if (e instanceof DOMException && e.name === "AbortError") return;
      const msg = e instanceof Error ? e.message : String(e);
      toast.error(`批量抓取失败: ${msg}`);
      setRefreshMsg(`批量抓取失败: ${msg}`);
    } finally {
      setFetchAllLoading(false);
      pollAbortRef.current = null;
      setTimeout(() => setRefreshMsg(""), 3000);
    }
  };

  const handleRecalculate = async () => {
    setRefreshMsg("V5 评分计算中...");
    try {
      const data = await api.computeV5Scores();
      const n = (data as { computed?: number }).computed ?? (data as { updated?: number }).updated;
      toast.success(`V5 重算完成${n != null ? `：${n} 只` : ""}`);
      await loadData();
    } catch (e: unknown) {
      const msg = e instanceof Error ? e.message : String(e);
      toast.error(`V5 重算失败: ${msg}`);
    } finally {
      setRefreshMsg("");
    }
  };

  const issueCount = stocks.filter((s) => {
    const st = stepStatusMap[s.stock_id];
    if (!st) return false;
    return Object.values(st).some(
      (v) => v.status === "error" || (v.status === "skipped" && v.message === "circuit_breaker"),
    );
  }).length;

  return (
    <div className="space-y-6">
      <DataTabs active="data" />
      <div>
        <h1 className="text-xl font-semibold tracking-tight">数据管理</h1>
        <p className="text-xs text-muted-foreground mt-0.5">行情 / 财报抓取与 V5 评分重算</p>
      </div>
      <div className="flex items-center gap-2 flex-wrap">
        <button
          type="button"
          onClick={handleRecalculate}
          className="inline-flex items-center gap-1 rounded-md border border-border bg-background px-2.5 h-7 text-xs hover:bg-muted transition-colors"
        >
          <RefreshCw className="h-3.5 w-3.5" /> 重算 V5
        </button>
        <button
          type="button"
          onClick={() => handleFetchAll("incremental")}
          disabled={fetchAllLoading}
          className="inline-flex items-center gap-1 rounded-md bg-primary text-primary-foreground px-2.5 h-7 text-xs hover:bg-primary/90 transition-colors disabled:opacity-50"
        >
          <Play className="h-3.5 w-3.5" />
          {fetchAllLoading ? "抓取中..." : "增量抓取"}
        </button>
        <button
          type="button"
          onClick={() => handleFetchAll("full")}
          disabled={fetchAllLoading}
          title="拉全量财报与长历史行情，耗时更长"
          className="inline-flex items-center gap-1 rounded-md border border-border bg-background px-2.5 h-7 text-xs hover:bg-muted transition-colors disabled:opacity-50"
        >
          <RotateCw className="h-3.5 w-3.5" />
          深度全量
        </button>
        <MarketOpsButtons
          ops={["macro", "v5", "fusion", "ths", "review"]}
          onMacroSynced={() => setRefreshMsg("宏观指标已同步")}
          onV5Synced={() => setRefreshMsg("V5 数据源已同步")}
        />
        {refreshMsg && (
          <span className="text-xs text-primary">{refreshMsg}</span>
        )}
      </div>
      <p className="text-xs pl-6 text-muted-foreground">
        日常用「增量抓取」；财报季或数据修复时用「深度全量」。抓完后请点「重算 V5」更新评分。
        {issueCount > 0 && (
          <span className="text-amber-700 dark:text-amber-500">
            {" "}
            · {issueCount} 只股票有步骤异常或熔断跳过（见「抓取步骤」列）
          </span>
        )}
      </p>

      {loading ? (
        <div className="animate-pulse space-y-4">
          <div className="h-10 bg-muted rounded w-48" />
          <div className="h-64 bg-muted rounded" />
        </div>
      ) : (
        <div className="overflow-x-auto">
          <table className="w-full text-sm">
            <thead>
              <tr className="border-b border-border text-left text-muted-foreground">
                <th className="py-2 px-2">代码</th>
                <th className="py-2 px-2">名称</th>
                <th className="py-2 px-2">最新行情</th>
                <th className="py-2 px-2">数据年龄</th>
                <th className="py-2 px-2">抓取步骤</th>
                <th className="py-2 px-2">操作</th>
              </tr>
            </thead>
            <tbody>
              {stocks.map((s) => {
                const status = statusMap[s.stock_id] || s;
                const busy = fetchingIds.has(s.stock_id);
                return (
                  <tr key={s.stock_id} className="border-b border-border hover:bg-muted/50">
                    <td className="py-1.5 px-2 font-mono">{s.code}</td>
                    <td className="py-1.5 px-2">{s.name}</td>
                    <td className="py-1.5 px-2 text-xs">
                      {status.last_quote_date || "无"}
                    </td>
                    <td className="py-1.5 px-2">
                      <Badge
                        variant={getLabel(status) === "fresh" ? "default" : "secondary"}
                        className="text-xs"
                      >
                        {getAge(status)}
                      </Badge>
                    </td>
                    <td className="py-1.5 px-2">
                      <FetchStepBadges
                        logs={logSummary[s.stock_id]}
                        stepStatus={stepStatusMap[s.stock_id]}
                      />
                    </td>
                    <td className="py-1.5 px-2">
                      <button
                        type="button"
                        onClick={() => fetchStock(s)}
                        disabled={busy || fetchAllLoading}
                        className="p-1 hover:bg-muted rounded text-xs disabled:opacity-40"
                        title="抓取行情与财报"
                      >
                        <RotateCw
                          className={`h-3.5 w-3.5 text-muted-foreground hover:text-foreground ${busy ? "animate-spin" : ""}`}
                        />
                      </button>
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}
