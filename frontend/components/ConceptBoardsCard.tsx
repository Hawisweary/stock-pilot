"use client";

import { useEffect, useState } from "react";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Layers } from "lucide-react";

interface Board {
  bk_code: string;
  name: string;
  type: string;
  chg_pct: number | null;
}

interface Props { code: string }

const TYPE_COLOR: Record<string, string> = {
  行业: "bg-blue-100 text-blue-700",
  地域: "bg-green-100 text-green-700",
  概念: "bg-purple-100 text-purple-700",
};

export function ConceptBoardsCard({ code }: Props) {
  const [boards, setBoards] = useState<Board[]>([]);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    if (!code) return;
    setLoading(true);
    fetch(`/api/concept-boards/${code}`)
      .then(r => r.ok ? r.json() : null)
      .then(d => setBoards(d?.boards ?? []))
      .catch(() => {})
      .finally(() => setLoading(false));
  }, [code]);

  if (loading) return null;
  if (!boards.length) return null;

  return (
    <Card>
      <CardHeader className="pb-2">
        <CardTitle className="text-sm flex items-center gap-2">
          <Layers className="h-4 w-4" /> 所属板块
        </CardTitle>
      </CardHeader>
      <CardContent className="pt-0">
        <div className="flex flex-wrap gap-1.5">
          {boards.map((b, i) => (
            <span key={i}
              className={`inline-flex items-center gap-1 text-xs px-2 py-0.5 rounded-full font-medium
                ${TYPE_COLOR[b.type] ?? "bg-gray-100 text-gray-600"}`}>
              {b.name}
              {b.chg_pct != null && (
                <span className={b.chg_pct >= 0 ? "text-red-600" : "text-green-600"}>
                  {b.chg_pct >= 0 ? "+" : ""}{b.chg_pct.toFixed(2)}%
                </span>
              )}
            </span>
          ))}
        </div>
        <p className="text-[10px] text-muted-foreground mt-2">
          <span className="inline-block w-2 h-2 rounded-full bg-blue-400 mr-1" />行业
          <span className="inline-block w-2 h-2 rounded-full bg-green-400 mx-1 ml-2" />地域
          <span className="inline-block w-2 h-2 rounded-full bg-purple-400 mx-1 ml-2" />概念
        </p>
      </CardContent>
    </Card>
  );
}
