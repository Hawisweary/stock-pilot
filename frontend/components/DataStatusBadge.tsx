"use client";

import { Badge } from "@/components/ui/badge";

interface Props {
  status: "fresh" | "stale" | "missing";
}

export function DataStatusBadge({ status }: Props) {
  const variants: Record<string, { variant: "default" | "secondary" | "destructive" | "outline"; label: string }> = {
    fresh: { variant: "default", label: "数据新鲜" },
    stale: { variant: "secondary", label: "数据过期" },
    missing: { variant: "destructive", label: "无数据" },
  };

  const conf = variants[status] || variants.missing;

  return <Badge variant={conf.variant}>{conf.label}</Badge>;
}
