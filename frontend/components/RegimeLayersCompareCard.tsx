"use client";

import { useEffect, useState } from "react";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { CheckCircle2, GitCompare, AlertTriangle } from "lucide-react";
import { api } from "@/lib/api";

type LayerInfo = {
  bucket?: string;
  bucket_label?: string;
  regime_label?: string;
  jump_penalty?: number;
  model_version?: string;
  hmm_state?: number;
  role?: string;
};

type LayersPayload = {
  trade_date?: string;
  layers?: {
    rules?: LayerInfo;
    jump?: LayerInfo;
    hmm?: LayerInfo;
  };
  all_aligned?: boolean;
  diverged_layers?: string[];
  layer_count?: number;
};

type RecPayload = {
  recommendation?: { confidence?: number };
};

const LAYER_META: Record<string, { label: string; sub: string }> = {
  rules: { label: "规则（生产）", sub: "L1 主基准" },
  jump: { label: "Jump Model", sub: "动态 λ" },
  hmm: { label: "HMM（对照）", sub: "研究层" },
};

function LayerRow({
  id,
  layer,
  diverged,
  confidence,
}: {
  id: string;
  layer?: LayerInfo;
  diverged?: boolean;
  confidence?: number;
}) {
  const meta = LAYER_META[id] ?? { label: id, sub: "" };
  if (!layer?.bucket_label) {
    return (
      <div className="flex items-center gap-2 text-[11px] text-muted-foreground py-0.5">
        <span className="w-28 shrink-0">{meta.label}</span>
        <span>— 暂无数据</span>
      </div>
    );
  }

  let extra = "";
  if (id === "jump" && layer.jump_penalty != null) {
    extra = `(λ=${layer.jump_penalty})`;
  }
  if (id === "rules" && confidence != null) {
    extra = `置信度 ${Math.round(confidence * 100)}%`;
  }

  return (
    <div
      className={`flex items-start gap-2 text-[11px] py-0.5 ${
        diverged ? "text-amber-800 dark:text-amber-300" : ""
      }`}
    >
      <span className="w-28 shrink-0 font-medium">{meta.label}</span>
      <span className="flex-1">
        → {layer.bucket_label}
        {extra && <span className="text-muted-foreground ml-1.5">{extra}</span>}
        {diverged && (
          <AlertTriangle className="inline h-3 w-3 ml-1 -mt-0.5 text-amber-600" aria-hidden />
        )}
      </span>
    </div>
  );
}

export function RegimeLayersCompareCard() {
  const [layers, setLayers] = useState<LayersPayload | null>(null);
  const [confidence, setConfidence] = useState<number | null>(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    setLoading(true);
    Promise.all([
      api.getMarketRegimeLayers().catch(() => null),
      api.getCurrentRecommendation().catch(() => null),
    ])
      .then(([l, rec]) => {
        setLayers(l as LayersPayload | null);
        setConfidence((rec as RecPayload | null)?.recommendation?.confidence ?? null);
      })
      .finally(() => setLoading(false));
  }, []);

  if (loading) {
    return (
      <Card className="border-dashed">
        <CardHeader className="pb-2">
          <CardTitle className="text-sm flex items-center gap-2">
            <GitCompare className="h-4 w-4" /> 多模型状态对照
          </CardTitle>
        </CardHeader>
        <CardContent className="text-xs text-muted-foreground">加载中…</CardContent>
      </Card>
    );
  }

  if (!layers?.layers?.rules) {
    return null;
  }

  const diverged = new Set(layers.diverged_layers ?? []);
  const order: Array<keyof NonNullable<LayersPayload["layers"]>> = ["rules", "jump", "hmm"];

  return (
    <Card className="border-dashed bg-muted/20">
      <CardHeader className="pb-2">
        <CardTitle className="text-sm flex items-center gap-2">
          <GitCompare className="h-4 w-4 text-primary" />
          多模型状态对照
          {layers.trade_date && (
            <span className="text-[10px] font-normal text-muted-foreground font-mono ml-auto">
              {layers.trade_date}
            </span>
          )}
        </CardTitle>
      </CardHeader>
      <CardContent className="pt-0 space-y-2 text-xs">
        <div className="rounded-md border bg-background/70 px-2.5 py-2 space-y-0.5">
          {order.map((key) => (
            <LayerRow
              key={key}
              id={key}
              layer={layers.layers?.[key]}
              diverged={diverged.has(key)}
              confidence={key === "rules" ? confidence ?? undefined : undefined}
            />
          ))}
        </div>
        <div className="flex items-center gap-1.5 text-[10px] text-muted-foreground">
          {layers.all_aligned ? (
            <>
              <CheckCircle2 className="h-3.5 w-3.5 text-emerald-600" />
              三方一致
            </>
          ) : diverged.size > 0 ? (
            <>
              <AlertTriangle className="h-3.5 w-3.5 text-amber-600" />
              分歧：{Array.from(diverged).map((d) => LAYER_META[d]?.label ?? d).join(" · ")}
              <span className="text-muted-foreground/80">（L3 仍以规则为准）</span>
            </>
          ) : (
            "部分模型暂无数据"
          )}
        </div>
      </CardContent>
    </Card>
  );
}
