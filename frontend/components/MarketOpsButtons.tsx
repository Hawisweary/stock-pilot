"use client";

import { useState } from "react";
import { RefreshCw } from "lucide-react";
import { generateDailyReview, runFusionSync, syncMacro, syncThsHotspots, syncV5Data } from "@/lib/marketExtras";
import { useToast } from "@/lib/useToast";

type Op = "macro" | "v5" | "fusion" | "ths" | "review";

const OPS: { id: Op; label: string; run: () => Promise<unknown> }[] = [
  { id: "macro", label: "同步宏观", run: syncMacro },
  { id: "v5", label: "V5数据源", run: syncV5Data },
  { id: "fusion", label: "数据融合", run: runFusionSync },
  { id: "ths", label: "同步同花顺热点", run: syncThsHotspots },
  { id: "review", label: "生成每日综述", run: generateDailyReview },
];

export function MarketOpsButtons({
  ops = ["macro", "fusion", "ths", "review"],
  onThsSynced,
  onMacroSynced,
  onV5Synced,
}: {
  ops?: Op[];
  /** 同花顺热点同步成功后回调（用于刷新热点卡片） */
  onThsSynced?: () => void;
  /** 宏观同步成功后回调（用于刷新宏观指标面板） */
  onMacroSynced?: () => void;
  /** V5 数据源同步成功后回调 */
  onV5Synced?: () => void;
}) {
  const toast = useToast();
  const [busy, setBusy] = useState<Op | null>(null);

  const run = async (op: Op) => {
    const cfg = OPS.find((o) => o.id === op);
    if (!cfg) return;
    setBusy(op);
    try {
      const res = await cfg.run();
      const r = res as {
        message?: string;
        status?: string;
        count?: number;
        nonzero_pct?: number;
        source?: string;
        indicators?: Record<string, number>;
      };
      let msg = r.message || r.status || "已提交";
      if (op === "ths" && r.count != null) {
        msg = `写入 ${r.count} 条，有效涨幅 ${r.nonzero_pct ?? "?"} 条`;
      }
      if (op === "macro" && r.source) {
        const n = r.indicators ? Object.keys(r.indicators).length : 0;
        msg = `来源 ${r.source}${n ? `，${n} 项指标` : ""}`;
      }
      if (op === "v5") {
        const steps = (res as { steps?: Record<string, unknown> }).steps || {};
        const names = Object.keys(steps);
        msg = names.length ? `完成 ${names.length} 步` : (r.ok ? "OK" : "部分失败");
      }
      toast.success(`${cfg.label}: ${msg}`);
      if (op === "ths") onThsSynced?.();
      if (op === "macro") onMacroSynced?.();
      if (op === "v5") {
        onMacroSynced?.();
        onV5Synced?.();
      }
    } catch (e: unknown) {
      toast.error(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(null);
    }
  };

  return (
    <div className="flex flex-wrap gap-2">
      {OPS.filter((o) => ops.includes(o.id)).map((o) => (
        <button
          key={o.id}
          type="button"
          disabled={busy !== null}
          onClick={() => run(o.id)}
          className="inline-flex items-center gap-1 rounded-lg border border-border bg-background px-2.5 h-7 text-xs hover:bg-muted transition-colors disabled:opacity-50"
        >
          <RefreshCw className={`h-3.5 w-3.5 ${busy === o.id ? "animate-spin" : ""}`} />
          {busy === o.id ? "处理中…" : o.label}
        </button>
      ))}
    </div>
  );
}
