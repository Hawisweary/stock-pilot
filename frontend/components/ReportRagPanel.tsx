"use client";

import { useEffect, useState } from "react";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { api, RagDocument } from "@/lib/api";
import { useToast } from "@/lib/useToast";

export function ReportRagPanel({ stockId }: { stockId: number }) {
  const toast = useToast();
  const [docs, setDocs] = useState<RagDocument[]>([]);
  const [question, setQuestion] = useState("");
  const [answer, setAnswer] = useState("");
  const [loading, setLoading] = useState(false);

  const loadDocs = () => {
    api
      .listRagDocuments(stockId)
      .then((r) => setDocs(r.documents || []))
      .catch(() => setDocs([]));
  };

  useEffect(() => {
    if (stockId) loadDocs();
  }, [stockId]);

  const onUpload = async (e: React.ChangeEvent<HTMLInputElement>) => {
    const file = e.target.files?.[0];
    if (!file) return;
    setLoading(true);
    try {
      const res = await api.uploadRagPdf(stockId, file);
      toast.success(`已入库 ${res.chunks} 个文本块`);
      loadDocs();
    } catch (err: unknown) {
      toast.error(err instanceof Error ? err.message : "上传失败");
    } finally {
      setLoading(false);
      e.target.value = "";
    }
  };

  const onAsk = async () => {
    if (!question.trim()) return;
    setLoading(true);
    try {
      const res = await api.askRag(stockId, question.trim());
      setAnswer(res.answer);
    } catch (err: unknown) {
      toast.error(err instanceof Error ? err.message : "问答失败");
    } finally {
      setLoading(false);
    }
  };

  return (
    <Card>
      <CardHeader>
        <CardTitle className="text-sm">财报 PDF 问答</CardTitle>
      </CardHeader>
      <CardContent className="space-y-3 text-xs">
        <div className="flex items-center gap-2 flex-wrap">
          <label className="cursor-pointer rounded border px-2 py-1 hover:bg-muted">
            上传 PDF
            <input type="file" accept=".pdf" className="hidden" onChange={onUpload} disabled={loading} />
          </label>
          <span className="text-muted-foreground">{docs.length} 份文档</span>
        </div>
        {docs.length > 0 && (
          <ul className="text-[10px] text-muted-foreground space-y-0.5 max-h-16 overflow-y-auto">
            {docs.map((d) => (
              <li key={d.id}>
                {d.title} · {d.chunk_count} 块
              </li>
            ))}
          </ul>
        )}
        <textarea
          className="w-full min-h-[60px] rounded border bg-background p-2 text-xs"
          placeholder="例如：公司主营业务与竞争优势？未来资本开支计划？"
          value={question}
          onChange={(e) => setQuestion(e.target.value)}
        />
        <Button size="sm" onClick={onAsk} disabled={loading || !question.trim()}>
          {loading ? "分析中…" : "提问"}
        </Button>
        {answer && (
          <div className="rounded bg-muted/50 p-2 text-xs whitespace-pre-wrap leading-relaxed">
            {answer}
          </div>
        )}
      </CardContent>
    </Card>
  );
}
