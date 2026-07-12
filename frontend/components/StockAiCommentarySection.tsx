"use client";

import { useEffect, useState } from "react";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { AiCommentary } from "@/components/AiCommentary";
import { api, type AiAnalysis } from "@/lib/api";
import { useToast } from "@/lib/useToast";
import { Sparkles, History, ChevronLeft, ChevronRight } from "lucide-react";

function ratingDiff(cur: string, prev: string) {
  const order: Record<string, number> = { 优秀: 4, 良好: 3, 推荐: 3, 中性: 2, 一般: 2, 谨慎: 1, 需关注: 1 };
  const c = order[cur] ?? 2;
  const p = order[prev] ?? 2;
  if (c > p) return <span className="text-red-600 text-[10px]">↑ {cur}</span>;
  if (c < p) return <span className="text-green-700 text-[10px]">↓ {cur}</span>;
  return <span className="text-muted-foreground text-[10px]">= {cur}</span>;
}

export function StockAiCommentarySection({ stockId }: { stockId: number }) {
  const toast = useToast();
  const [history, setHistory] = useState<AiAnalysis[]>([]);
  const [idx, setIdx] = useState(0);          // 当前查看的历史索引（0=最新）
  const [loading, setLoading] = useState(true);
  const [running, setRunning] = useState(false);
  const [showHistory, setShowHistory] = useState(false);

  const loadHistory = () => {
    setLoading(true);
    api
      .analysisHistory(stockId)
      .then((rows) => { setHistory(rows ?? []); setIdx(0); })
      .catch(() => setHistory([]))
      .finally(() => setLoading(false));
  };

  useEffect(() => {
    if (!stockId) return;
    loadHistory();
  }, [stockId]);

  const runAnalyze = () => {
    setRunning(true);
    api
      .analyzeStock(stockId)
      .then((r) => {
        setHistory(h => [r, ...h]);
        setIdx(0);
        toast.success("AI 分析已生成");
      })
      .catch((e: unknown) => toast.error(e instanceof Error ? e.message : "分析生成失败"))
      .finally(() => setRunning(false));
  };

  const analysis = history[idx] ?? null;
  const prev = history[idx + 1] ?? null;

  return (
    <Card>
      <CardHeader className="pb-2 flex flex-row items-center justify-between">
        <CardTitle className="text-sm flex items-center gap-2">
          AI 基本面评论
          {history.length > 1 && (
            <button
              onClick={() => setShowHistory(h => !h)}
              className="text-xs text-muted-foreground hover:text-foreground flex items-center gap-0.5"
              title="查看历史版本"
            >
              <History className="h-3 w-3" />
              {history.length} 版本
            </button>
          )}
        </CardTitle>
        <Button size="sm" variant="outline" className="h-7 text-xs" onClick={runAnalyze} disabled={running}>
          <Sparkles className="h-3 w-3 mr-1" />
          {running ? "分析中…" : analysis ? "重新分析" : "生成分析"}
        </Button>
      </CardHeader>

      <CardContent className="pt-0 space-y-3">
        {/* 版本导航栏 */}
        {showHistory && history.length > 1 && (
          <div className="flex items-center gap-2 text-xs border rounded-lg p-2 bg-muted/30">
            <button
              disabled={idx >= history.length - 1}
              onClick={() => setIdx(i => i + 1)}
              className="disabled:opacity-30 hover:text-foreground"
            >
              <ChevronLeft className="h-4 w-4" />
            </button>
            <span className="flex-1 text-center text-muted-foreground">
              {idx === 0 ? "最新版本" : `历史版本 -${idx}`}
              {analysis && <span className="ml-1">· {analysis.generated_at?.slice(0, 10)}</span>}
            </span>
            <button
              disabled={idx <= 0}
              onClick={() => setIdx(i => i - 1)}
              className="disabled:opacity-30 hover:text-foreground"
            >
              <ChevronRight className="h-4 w-4" />
            </button>

            {/* 评级变化对比 */}
            {analysis && prev && (
              <span className="ml-2 px-2 py-0.5 rounded border bg-background">
                {ratingDiff(analysis.overall_rating, prev.overall_rating)}
                <span className="text-muted-foreground ml-1">vs {prev.generated_at?.slice(0, 10)}</span>
              </span>
            )}
          </div>
        )}

        {/* 当前版本差异提示（仅查看历史时） */}
        {showHistory && idx > 0 && analysis && history[0] && (
          <div className="text-[10px] text-muted-foreground px-1">
            对比最新版本：评级 {history[0].overall_rating}（{history[0].generated_at?.slice(0, 10)}）
          </div>
        )}

        <AiCommentary analysis={analysis} loading={loading || running} />

        {!loading && !running && !analysis && (
          <p className="text-xs text-muted-foreground">暂无缓存，点击上方按钮生成（可能需数十秒）。</p>
        )}
      </CardContent>
    </Card>
  );
}
