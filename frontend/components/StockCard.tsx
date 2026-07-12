import { Card, CardContent } from "@/components/ui/card";
import type { V5ScoreRow } from "@/lib/api";

interface Props {
  stock: V5ScoreRow;
  onClick?: () => void;
}

export function StockCard({ stock, onClick }: Props) {
  const score = stock.score ?? stock.composite_v5 ?? 0;
  const getScoreColor = (s: number) => {
    if (s >= 60) return "text-red-600";
    if (s >= 40) return "text-yellow-600";
    return "text-green-600";
  };

  return (
    <Card
      className="cursor-pointer hover:shadow-md transition-shadow"
      onClick={onClick}
    >
      <CardContent className="p-4">
        <div className="flex items-center justify-between">
          <div>
            <div className="font-semibold text-sm">{stock.name}</div>
            <div className="text-xs text-muted-foreground">{stock.code}</div>
          </div>
          <div className="text-right">
            <div className={`text-2xl font-bold ${getScoreColor(score)}`}>
              {score.toFixed(1)}
            </div>
            <div className="text-xs text-muted-foreground">V5 综合</div>
            {stock.veto_status && stock.veto_status !== "ok" && (
              <div className="text-[10px] text-amber-700 mt-0.5">
                {stock.veto_status === "exclude" ? "回避" : "减仓"}
              </div>
            )}
          </div>
        </div>
      </CardContent>
    </Card>
  );
}
