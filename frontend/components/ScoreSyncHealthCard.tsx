"use client";

import { useCallback, useEffect, useState } from "react";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import {
  api,
  pollBatchFillUntilDone,
  ScoreSyncHealth,
  BatchFillPlan,
} from "@/lib/api";
import { useToast } from "@/lib/useToast";
import { AlertTriangle, CheckCircle2, Loader2, Play, Search } from "lucide-react";

const DIM_LABELS: Record<string, string> = {
  fundamental_score: "基本面",
  technical_score: "技术面",
  sentiment_score: "新闻面",
  capital_score: "资金面",
  policy_score: "政策面",
  mood_score: "情绪面",
  val_score: "估值面",
};

type FillMode = "sync_only" | "compute_and_sync";

export function ScoreSyncHealthCard() {
  const toast = useToast();
  const [health, setHealth] = useState<ScoreSyncHealth | null>(null);
  const [loading, setLoading] = useState(true);
  const [preview, setPreview] = useState<BatchFillPlan | null>(null);
  const [previewOpen, setPreviewOpen] = useState(false);
  const [running, setRunning] = useState(false);
  const [progress, setProgress] = useState("");
  const [mode, setMode] = useState<FillMode>("sync_only");

  const load = useCallback(async () => {
    try {
      const data = await api.getScoreSyncHealth();
      setHealth(data);
    } catch (e) {
      console.error(e);
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    load();
    const t = setInterval(load, 60_000);
    return () => clearInterval(t);
  }, [load]);

  const handlePreview = async () => {
    setRunning(true);
    setProgress("生成预览计划...");
    try {
      const plan = await api.batchFillScores({
        mode,
        dry_run: true,
        target_date: health?.target_date,
      });
      setPreview(plan as BatchFillPlan);
      setPreviewOpen(true);
    } catch (e: unknown) {
      toast.error(e instanceof Error ? e.message : String(e));
    } finally {
      setRunning(false);
      setProgress("");
    }
  };

  const handleFill = async () => {
    setRunning(true);
    setProgress("提交补算任务...");
    try {
      const queued = await api.batchFillScores({
        mode,
        dry_run: false,
        target_date: health?.target_date,
      });
      setProgress(`任务 ${queued.job_id} 排队中...`);
      await pollBatchFillUntilDone(queued.job_id, (msg) => setProgress(msg));
      toast.success("维度补算完成");
      await load();
    } catch (e: unknown) {
      toast.error(e instanceof Error ? e.message : String(e));
    } finally {
      setRunning(false);
      setProgress("");
    }
  };

  if (loading) {
    return (
      <Card>
        <CardContent className="p-4 text-sm text-muted-foreground">加载评分同步状态...</CardContent>
      </Card>
    );
  }

  if (!health) return null;

  const reqPct = Math.round((health.sync_rate_required ?? 0) * 100);
  const allPct = Math.round((health.sync_rate_all ?? 0) * 100);
  const trend = health.trend_7d ?? [];
  const maxTrend = Math.max(...trend.map((t) => t.sync_rate_required ?? 0), 0.01);

  return (
    <Card>
      <CardHeader className="pb-2 flex flex-row items-center justify-between gap-2">
        <CardTitle className="text-base flex items-center gap-2">
          📊 评分同步健康
          <Badge variant={reqPct >= 100 ? "outline" : "destructive"} className="text-xs font-normal">
            {health.target_date}
          </Badge>
        </CardTitle>
        <div className="flex items-center gap-1">
          <select
            className="text-xs border rounded px-2 py-1 bg-background"
            value={mode}
            onChange={(e) => setMode(e.target.value as FillMode)}
            disabled={running}
          >
            <option value="sync_only">sync_only</option>
            <option value="compute_and_sync">compute_and_sync</option>
          </select>
          <Button size="sm" variant="outline" onClick={handlePreview} disabled={running} className="h-7 text-xs gap-1">
            {running && !previewOpen ? <Loader2 className="h-3 w-3 animate-spin" /> : <Search className="h-3 w-3" />}
            预览
          </Button>
          <Button size="sm" onClick={handleFill} disabled={running} className="h-7 text-xs gap-1">
            {running ? <Loader2 className="h-3 w-3 animate-spin" /> : <Play className="h-3 w-3" />}
            补算
          </Button>
        </div>
      </CardHeader>
      <CardContent className="space-y-3">
        {health.alert?.active && (
          <div className="flex items-start gap-2 rounded-md border border-red-200 bg-red-50 px-3 py-2 text-xs text-red-800">
            <AlertTriangle className="h-4 w-4 shrink-0 mt-0.5" />
            <div>
              必需维度同步率 {reqPct}% 已持续 {health.alert.duration_minutes?.toFixed(0)} 分钟
              {health.alert.channels_sent?.length ? `（已通知: ${health.alert.channels_sent.join(", ")}）` : ""}
            </div>
          </div>
        )}

        {progress && (
          <div className="text-xs text-blue-700 bg-blue-50 border border-blue-100 rounded px-2 py-1.5 flex items-center gap-2">
            <Loader2 className="h-3 w-3 animate-spin" />
            {progress}
          </div>
        )}

        <div className="grid grid-cols-3 gap-3">
          <div>
            <div className={`text-2xl font-bold ${reqPct >= 100 ? "text-green-600" : reqPct >= 80 ? "text-yellow-600" : "text-red-600"}`}>
              {reqPct}%
            </div>
            <div className="text-xs text-muted-foreground">必需 5 维齐全率</div>
            <div className="text-[10px] text-muted-foreground">
              {health.stocks_full_required}/{health.active_stocks_count} 股
            </div>
          </div>
          <div>
            <div className="text-2xl font-bold text-purple-600">{allPct}%</div>
            <div className="text-xs text-muted-foreground">7 维全齐全率</div>
          </div>
          <div>
            <div className="text-2xl font-bold text-orange-600">{health.missing_total + (health.stale_total ?? 0)}</div>
            <div className="text-xs text-muted-foreground">待修复（缺+旧）</div>
          </div>
        </div>

        {trend.length > 0 && (
          <div>
            <div className="text-xs text-muted-foreground mb-1">必需维度同步率 — 近 7 天</div>
            <div className="flex items-end gap-1 h-12">
              {trend.map((pt) => {
                const v = pt.sync_rate_required ?? 0;
                return (
                  <div key={pt.date} className="flex-1 flex flex-col items-center gap-0.5">
                    <div
                      className={`w-full rounded-t ${v >= 1 ? "bg-green-500" : v >= 0.8 ? "bg-yellow-500" : "bg-red-400"}`}
                      style={{ height: `${Math.max(4, (v / maxTrend) * 40)}px` }}
                      title={`${pt.date}: ${(v * 100).toFixed(0)}%`}
                    />
                    <span className="text-[9px] text-muted-foreground truncate w-full text-center">
                      {pt.date.slice(5)}
                    </span>
                  </div>
                );
              })}
            </div>
          </div>
        )}

        <div className="grid grid-cols-2 md:grid-cols-4 gap-1.5">
          {Object.entries(health.gaps_by_dimension ?? {}).map(([dim, g]) => {
            const missing = g.missing ?? 0;
            const noSrc = g.no_source ?? 0;
            const stale = g.stale ?? 0;
            const ok = !missing && !noSrc && !stale;
            return (
              <div
                key={dim}
                className={`text-xs rounded border px-2 py-1 ${
                  missing > 0 ? "border-red-200 bg-red-50" : stale > 0 ? "border-orange-200 bg-orange-50" : noSrc > 0 ? "border-yellow-200 bg-yellow-50" : "border-green-200 bg-green-50"
                }`}
              >
                <div className="font-medium truncate">{DIM_LABELS[dim] ?? dim}</div>
                <div className="text-muted-foreground flex items-center gap-1 flex-wrap">
                  {ok ? <CheckCircle2 className="h-3 w-3 text-green-600" /> : null}
                  {g.ok ?? 0} ok
                  {missing > 0 && <span className="text-red-600">·缺 {missing}</span>}
                  {stale > 0 && <span className="text-orange-600">·旧 {stale}</span>}
                  {noSrc > 0 && <span className="text-yellow-700">·无源 {noSrc}</span>}
                </div>
              </div>
            );
          })}
        </div>

        {health.last_fill_job && (
          <div className="text-[10px] text-muted-foreground border-t pt-2">
            最近补算: {health.last_fill_job.job_id} — {health.last_fill_job.status}
            {health.last_fill_job.sync_rate_required_after != null &&
              ` → ${Math.round(health.last_fill_job.sync_rate_required_after * 100)}%`}
          </div>
        )}

        {previewOpen && preview && (
          <div className="border rounded-md p-3 bg-muted/30 text-xs space-y-2">
            <div className="flex justify-between items-center">
              <span className="font-medium">dry-run 预览 ({preview.mode})</span>
              <button type="button" onClick={() => setPreviewOpen(false)} className="text-muted-foreground hover:text-foreground">
                ×
              </button>
            </div>
            <div>
              估时: {preview.total_estimated_ms_range?.[0]}–{preview.total_estimated_ms_range?.[1]} ms
            </div>
            <ul className="space-y-0.5 max-h-32 overflow-y-auto">
              {(preview.planned_actions ?? []).map((a) => (
                <li key={a.action} className="font-mono">
                  P{a.priority} {a.action}: {a.affected_stocks} 股
                  {a.estimated_ms_range ? ` (~${a.estimated_ms_range[0]}-${a.estimated_ms_range[1]}ms)` : ""}
                </li>
              ))}
            </ul>
          </div>
        )}
      </CardContent>
    </Card>
  );
}
