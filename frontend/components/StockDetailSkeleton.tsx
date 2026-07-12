export function StockDetailSkeleton() {
  return (
    <div className="space-y-6 animate-pulse">
      {/* 返回链接占位 */}
      <div className="h-4 w-20 bg-muted rounded" />

      {/* 头部：代码 / 名称 / 综合分 */}
      <div className="flex items-start justify-between gap-4">
        <div className="space-y-2">
          <div className="h-8 w-48 bg-muted rounded" />
          <div className="h-4 w-32 bg-muted rounded" />
        </div>
        <div className="h-12 w-20 bg-muted rounded" />
      </div>

      {/* 卡片行 1 */}
      <div className="grid grid-cols-1 md:grid-cols-3 gap-4">
        {[...Array(3)].map((_, i) => (
          <div key={i} className="rounded-xl border bg-card p-4 space-y-3">
            <div className="h-4 w-24 bg-muted rounded" />
            <div className="h-6 w-16 bg-muted rounded" />
            <div className="h-3 w-full bg-muted rounded" />
            <div className="h-3 w-3/4 bg-muted rounded" />
          </div>
        ))}
      </div>

      {/* K 线区域占位 */}
      <div className="rounded-xl border bg-card p-4 space-y-3">
        <div className="h-4 w-32 bg-muted rounded" />
        <div className="h-64 w-full bg-muted rounded" />
      </div>

      {/* 卡片行 2 */}
      <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
        {[...Array(2)].map((_, i) => (
          <div key={i} className="rounded-xl border bg-card p-4 space-y-3">
            <div className="h-4 w-28 bg-muted rounded" />
            <div className="h-32 w-full bg-muted rounded" />
          </div>
        ))}
      </div>
    </div>
  );
}
