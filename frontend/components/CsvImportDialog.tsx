"use client";

import { useRef, useState } from "react";
import { Dialog, DialogContent, DialogHeader, DialogTitle } from "@/components/ui/dialog";
import { useToast } from "@/lib/useToast";
import { Upload, Download, X, CheckCircle, AlertTriangle } from "lucide-react";

interface PreviewRow {
  code: string;
  market: string;
  status: "new" | "exists" | "invalid";
  reason?: string;
}

interface Props {
  open: boolean;
  onClose: () => void;
  existingCodes?: Set<string>;
  onImported?: (codes: string[]) => void;
}

const TEMPLATE_CSV = "代码,市场\n000333,SZ\n600519,SH\n";
const CODE_RE = /^\d{6}$/;

function parseMarket(raw: string): string {
  const m = (raw ?? "").trim().toUpperCase();
  if (m === "SH" || m === "上交所" || m === "上海") return "SH";
  if (m === "SZ" || m === "深交所" || m === "深圳") return "SZ";
  // 自动推断
  return "";
}

function inferMarket(code: string): string {
  if (code.startsWith("6") || code.startsWith("5") || code.startsWith("9")) return "SH";
  return "SZ";
}

function parseCsv(text: string, existingCodes: Set<string>): PreviewRow[] {
  const lines = text.trim().split(/\r?\n/);
  const rows: PreviewRow[] = [];
  const seen = new Set<string>();

  for (const line of lines) {
    const cols = line.split(",").map(c => c.trim().replace(/^["']|["']$/g, ""));
    // 跳过标题行
    if (cols[0] === "代码" || cols[0] === "code" || cols[0] === "CODE") continue;
    if (!cols[0]) continue;

    const code = cols[0].padStart(6, "0");
    const rawMarket = cols[1] ?? "";
    const market = parseMarket(rawMarket) || inferMarket(code);

    if (!CODE_RE.test(code)) {
      rows.push({ code: cols[0], market, status: "invalid", reason: "代码格式错误（需6位数字）" });
      continue;
    }
    if (seen.has(code)) {
      rows.push({ code, market, status: "invalid", reason: "文件内重复" });
      continue;
    }
    seen.add(code);

    if (existingCodes.has(code)) {
      rows.push({ code, market, status: "exists" });
    } else {
      rows.push({ code, market, status: "new" });
    }
  }

  return rows;
}

export function CsvImportDialog({ open, onClose, existingCodes = new Set(), onImported }: Props) {
  const toast = useToast();
  const fileRef = useRef<HTMLInputElement>(null);
  const [preview, setPreview] = useState<PreviewRow[]>([]);
  const [importing, setImporting] = useState(false);
  const [filename, setFilename] = useState("");

  const handleFile = (file: File) => {
    setFilename(file.name);
    const reader = new FileReader();
    reader.onload = (e) => {
      const text = (e.target?.result as string) ?? "";
      setPreview(parseCsv(text, existingCodes));
    };
    reader.readAsText(file, "UTF-8");
  };

  const handleDrop = (e: React.DragEvent) => {
    e.preventDefault();
    const file = e.dataTransfer.files[0];
    if (file) handleFile(file);
  };

  const downloadTemplate = () => {
    const blob = new Blob(["﻿" + TEMPLATE_CSV], { type: "text/csv;charset=utf-8" });
    const a = document.createElement("a");
    a.href = URL.createObjectURL(blob);
    a.download = "import_template.csv";
    a.click();
    URL.revokeObjectURL(a.href);
  };

  const newRows = preview.filter(r => r.status === "new");
  const existsRows = preview.filter(r => r.status === "exists");
  const invalidRows = preview.filter(r => r.status === "invalid");

  const handleImport = async () => {
    if (newRows.length === 0) return;
    setImporting(true);
    try {
      const res = await fetch("/api/stocks/batch-add", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ codes: newRows.map(r => r.code) }),
      });
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      const data = await res.json();
      const added = (data.results ?? []).filter((r: { status: string }) =>
        r.status === "added" || r.status === "reactivated"
      ).length;
      toast.success(`成功导入 ${added} 只股票`);
      onImported?.(newRows.map(r => r.code));
      onClose();
    } catch (e) {
      toast.error(e instanceof Error ? e.message : "导入失败");
    } finally {
      setImporting(false);
    }
  };

  const reset = () => {
    setPreview([]);
    setFilename("");
    if (fileRef.current) fileRef.current.value = "";
  };

  return (
    <Dialog open={open} onOpenChange={v => { if (!v) { reset(); onClose(); } }}>
      <DialogContent className="sm:max-w-xl max-h-[85vh] flex flex-col gap-0 p-0">
        <DialogHeader className="px-5 pt-4 pb-3 border-b shrink-0">
          <DialogTitle className="text-base flex items-center gap-2">
            <Upload className="h-4 w-4" /> CSV 批量导入
          </DialogTitle>
        </DialogHeader>

        <div className="flex-1 overflow-auto p-5 space-y-4">
          {/* 模板下载 */}
          <button onClick={downloadTemplate}
            className="flex items-center gap-1.5 text-xs text-muted-foreground hover:text-foreground">
            <Download className="h-3.5 w-3.5" /> 下载 CSV 模板
          </button>

          {/* 拖拽区 */}
          {preview.length === 0 ? (
            <div
              onDrop={handleDrop}
              onDragOver={e => e.preventDefault()}
              onClick={() => fileRef.current?.click()}
              className="border-2 border-dashed rounded-xl p-10 text-center cursor-pointer hover:bg-muted/40 transition-colors space-y-2"
            >
              <Upload className="h-8 w-8 mx-auto text-muted-foreground" />
              <p className="text-sm text-muted-foreground">拖拽 CSV 文件到此处，或点击选择</p>
              <p className="text-xs text-muted-foreground">格式：代码,市场（市场可省略，自动推断）</p>
              <input
                ref={fileRef}
                type="file"
                accept=".csv,.txt"
                className="hidden"
                onChange={e => { const f = e.target.files?.[0]; if (f) handleFile(f); }}
              />
            </div>
          ) : (
            <>
              {/* 文件名 + 清除 */}
              <div className="flex items-center justify-between text-xs">
                <span className="text-muted-foreground">{filename}</span>
                <button onClick={reset} className="flex items-center gap-1 hover:text-foreground">
                  <X className="h-3.5 w-3.5" /> 重新选择
                </button>
              </div>

              {/* 统计摘要 */}
              <div className="grid grid-cols-3 gap-3 text-center text-xs">
                <div className="rounded-lg border p-2">
                  <p className="text-lg font-bold text-green-700">{newRows.length}</p>
                  <p className="text-muted-foreground">待导入</p>
                </div>
                <div className="rounded-lg border p-2">
                  <p className="text-lg font-bold text-muted-foreground">{existsRows.length}</p>
                  <p className="text-muted-foreground">已存在（跳过）</p>
                </div>
                <div className="rounded-lg border p-2">
                  <p className="text-lg font-bold text-red-600">{invalidRows.length}</p>
                  <p className="text-muted-foreground">格式错误</p>
                </div>
              </div>

              {/* 预览表 */}
              <div className="border rounded-lg overflow-auto max-h-52">
                <table className="w-full text-xs">
                  <thead className="sticky top-0 bg-card border-b">
                    <tr className="text-muted-foreground text-left">
                      <th className="py-1.5 px-3">代码</th>
                      <th className="py-1.5 px-2">市场</th>
                      <th className="py-1.5 px-2">状态</th>
                    </tr>
                  </thead>
                  <tbody>
                    {preview.map((r, i) => (
                      <tr key={i} className="border-b last:border-0">
                        <td className="py-1.5 px-3 font-mono">{r.code}</td>
                        <td className="py-1.5 px-2 text-muted-foreground">{r.market}</td>
                        <td className="py-1.5 px-2">
                          {r.status === "new" && (
                            <span className="flex items-center gap-1 text-green-700">
                              <CheckCircle className="h-3 w-3" /> 待导入
                            </span>
                          )}
                          {r.status === "exists" && (
                            <span className="text-muted-foreground">已存在</span>
                          )}
                          {r.status === "invalid" && (
                            <span className="flex items-center gap-1 text-red-600">
                              <AlertTriangle className="h-3 w-3" /> {r.reason}
                            </span>
                          )}
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </>
          )}
        </div>

        {/* 底部操作 */}
        <div className="px-5 py-3 border-t shrink-0 flex justify-end gap-2">
          <button onClick={() => { reset(); onClose(); }}
            className="text-sm px-4 py-1.5 border rounded hover:bg-accent transition-colors">
            取消
          </button>
          <button
            onClick={handleImport}
            disabled={newRows.length === 0 || importing}
            className="text-sm px-4 py-1.5 bg-primary text-primary-foreground rounded hover:opacity-90 disabled:opacity-40 transition-opacity"
          >
            {importing ? "导入中…" : `导入 ${newRows.length} 只`}
          </button>
        </div>
      </DialogContent>
    </Dialog>
  );
}
