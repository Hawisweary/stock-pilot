"use client";

import { useEffect, useState } from "react";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { api } from "@/lib/api";
import { useToast } from "@/lib/useToast";

const LABELS: Record<string, string> = {
  quality: "盈利能力",
  growth: "成长性",
  value: "估值",
  momentum: "基本面动量",
  risk: "安全性",
};

export function FactorWeightsEditor() {
  const toast = useToast();
  const [weights, setWeights] = useState({
    quality: 0.3,
    growth: 0.25,
    value: 0.2,
    momentum: 0.1,
    risk: 0.15,
  });
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);

  useEffect(() => {
    api
      .getFactorWeights()
      .then(setWeights)
      .catch(() => toast.error("加载因子权重失败"))
      .finally(() => setLoading(false));
  }, [toast]);

  const total = Object.values(weights).reduce((a, b) => a + b, 0);

  const save = async () => {
    setSaving(true);
    try {
      await api.updateFactorWeights(weights);
      toast.success("因子权重已保存，请重算评分生效");
    } catch (e: unknown) {
      toast.error(e instanceof Error ? e.message : "保存失败");
    } finally {
      setSaving(false);
    }
  };

  const recalc = async () => {
    try {
      const r = await api.recalculateScores();
      toast.success(`已重算 ${r.updated} 只股票评分`);
    } catch (e: unknown) {
      toast.error(e instanceof Error ? e.message : "重算失败");
    }
  };

  if (loading) {
    return <div className="text-xs text-muted-foreground animate-pulse">加载因子权重…</div>;
  }

  return (
    <Card>
      <CardHeader className="pb-2">
        <CardTitle className="text-sm">五因子权重配置</CardTitle>
      </CardHeader>
      <CardContent className="space-y-3">
        {(Object.keys(LABELS) as (keyof typeof weights)[]).map((key) => (
          <div key={key} className="flex items-center gap-3 text-xs">
            <span className="w-24 shrink-0">{LABELS[key]}</span>
            <input
              type="range"
              min={0}
              max={100}
              value={Math.round(weights[key] * 100)}
              onChange={(e) =>
                setWeights((w) => ({ ...w, [key]: Number(e.target.value) / 100 }))
              }
              className="flex-1"
            />
            <span className="w-10 text-right font-mono">{(weights[key] * 100).toFixed(0)}%</span>
          </div>
        ))}
        <div className="text-xs text-muted-foreground">
          合计: <span className={Math.abs(total - 1) > 0.02 ? "text-red-600 font-semibold" : ""}>
            {(total * 100).toFixed(0)}%
          </span>
          {Math.abs(total - 1) > 0.02 && "（须为 100%）"}
        </div>
        <div className="flex gap-2">
          <Button size="sm" onClick={save} disabled={saving || Math.abs(total - 1) > 0.02}>
            保存权重
          </Button>
          <Button size="sm" variant="outline" onClick={recalc}>
            保存后重算评分
          </Button>
        </div>
      </CardContent>
    </Card>
  );
}
