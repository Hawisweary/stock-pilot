"use client";

import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { AiAnalysis } from "@/lib/api";
import { Sparkles, ThumbsUp, AlertTriangle, TrendingUp, Shield, FileText, BarChart3 } from "lucide-react";

interface StructuredSummary {
  overall: string;
  highlights?: string[];
  risks?: string[];
  catalysts?: string[];
  valuation?: string;
}

function detectAnalysisFormat(summary: string): StructuredSummary | null {
  if (!summary.trim().startsWith("{")) return null;
  try {
    const parsed = JSON.parse(summary);
    if (typeof parsed?.overall === "string") return parsed as StructuredSummary;
  } catch {
    // not json
  }
  return null;
}

interface Props { analysis: AiAnalysis | null; loading?: boolean; }

export function AiCommentary({ analysis, loading }: Props) {
  if (loading) return (
    <div className="animate-pulse space-y-3 p-4">
      <div className="h-4 bg-muted rounded w-3/4" />
      <div className="h-4 bg-muted rounded w-1/2" />
      <div className="h-4 bg-muted rounded w-2/3" />
    </div>
  );
  if (!analysis) return null;

  const ratingColor: Record<string, string> = {
    "优秀": "bg-green-100 text-green-700", "良好": "bg-blue-100 text-blue-700",
    "一般": "bg-yellow-100 text-yellow-700", "需关注": "bg-red-100 text-red-700",
    "推荐": "bg-green-100 text-green-700", "中性": "bg-yellow-100 text-yellow-700",
    "谨慎": "bg-red-100 text-red-700",
  };

  // U3-5: 检测结构化 JSON 摘要
  const structured = analysis.summary ? detectAnalysisFormat(analysis.summary) : null;

  // 分块数据 (v4 chunked analysis)
  const mda = (analysis as any).mda || {};
  const risk = (analysis as any).risk || {};
  const financials = (analysis as any).financials || {};

  return (
    <div className="space-y-3">
      {/* 源 + 评级 */}
      <div className="flex items-center justify-between text-xs text-muted-foreground">
        <span>{analysis.source === "llm" ? "🤖 DeepSeek AI" : "📋 规则引擎"} · {analysis.generated_at}</span>
        <Badge className={ratingColor[analysis.overall_rating] || "bg-gray-100"}>{analysis.overall_rating}</Badge>
      </div>

      {/* 总结 — 结构化路径 or 纯文本 */}
      {structured ? (
        <div className="space-y-2">
          <p className="text-sm leading-relaxed">{structured.overall}</p>
          {(structured.highlights ?? []).length > 0 && (
            <div>
              <p className="text-xs font-medium text-green-700 mb-0.5">亮点</p>
              <ul className="space-y-0.5">{(structured.highlights ?? []).map((h, i) => <li key={i} className="text-xs text-muted-foreground">• {h}</li>)}</ul>
            </div>
          )}
          {(structured.risks ?? []).length > 0 && (
            <div>
              <p className="text-xs font-medium text-red-700 mb-0.5">风险</p>
              <ul className="space-y-0.5">{(structured.risks ?? []).map((r, i) => <li key={i} className="text-xs text-muted-foreground">• {r}</li>)}</ul>
            </div>
          )}
          {structured.valuation && (
            <p className="text-xs text-muted-foreground"><TrendingUp className="h-3 w-3 inline mr-1"/>{structured.valuation}</p>
          )}
        </div>
      ) : (
        analysis.summary && <p className="text-sm leading-relaxed">{analysis.summary}</p>
      )}

      {/* 三块分析 */}
      <div className="grid grid-cols-3 gap-2">
        {mda.strategy && (
          <Card className="border-purple-100">
            <CardHeader className="p-2 pb-0"><CardTitle className="text-xs flex items-center gap-1"><FileText className="h-3 w-3" />管理层</CardTitle></CardHeader>
            <CardContent className="p-2 text-xs space-y-1">
              <p><span className="text-muted-foreground">战略:</span> {mda.strategy}</p>
              {mda.tone && <p><span className="text-muted-foreground">语气:</span> {mda.tone}</p>}
              {mda.capital_allocation && <p><span className="text-muted-foreground">资本配置:</span> {mda.capital_allocation}</p>}
            </CardContent>
          </Card>
        )}
        {risk.top_risk && (
          <Card className="border-red-100">
            <CardHeader className="p-2 pb-0"><CardTitle className="text-xs flex items-center gap-1"><Shield className="h-3 w-3" />风险</CardTitle></CardHeader>
            <CardContent className="p-2 text-xs space-y-1">
              <p className="text-red-700 font-medium">最大风险: {risk.top_risk}</p>
              {risk.competition && <p><span className="text-muted-foreground">竞争:</span> {risk.competition}</p>}
              {risk.financial && <p><span className="text-muted-foreground">财务:</span> {risk.financial}</p>}
            </CardContent>
          </Card>
        )}
        {financials.margin_trend && (
          <Card className="border-blue-100">
            <CardHeader className="p-2 pb-0"><CardTitle className="text-xs flex items-center gap-1"><BarChart3 className="h-3 w-3" />财务</CardTitle></CardHeader>
            <CardContent className="p-2 text-xs space-y-1">
              <p><span className="text-muted-foreground">利润率:</span> {financials.margin_trend}</p>
              {financials.cashflow_quality && <p><span className="text-muted-foreground">现金流:</span> {financials.cashflow_quality}</p>}
              {financials.balance_sheet_health && <p><span className="text-muted-foreground">资产负债:</span> {financials.balance_sheet_health}</p>}
            </CardContent>
          </Card>
        )}
      </div>

      {/* 优势/风险 (经典视图) */}
      <div className="grid grid-cols-2 gap-3">
        {analysis.strengths.length > 0 && (
          <div>
            <h4 className="flex items-center gap-1 text-xs font-medium text-green-700 mb-1"><ThumbsUp className="h-3 w-3"/> 优势</h4>
            <ul className="space-y-0.5">{analysis.strengths.map((s,i)=><li key={i} className="text-xs text-muted-foreground">• {s}</li>)}</ul>
          </div>
        )}
        {analysis.weaknesses.length > 0 && (
          <div>
            <h4 className="flex items-center gap-1 text-xs font-medium text-red-700 mb-1"><AlertTriangle className="h-3 w-3"/> 风险</h4>
            <ul className="space-y-0.5">{analysis.weaknesses.map((w,i)=><li key={i} className="text-xs text-muted-foreground">• {w}</li>)}</ul>
          </div>
        )}
      </div>

      {/* 估值观点 */}
      {analysis.valuation_view && (
        <div className="text-xs text-muted-foreground"><TrendingUp className="h-3 w-3 inline mr-1"/>{analysis.valuation_view}</div>
      )}
    </div>
  );
}
