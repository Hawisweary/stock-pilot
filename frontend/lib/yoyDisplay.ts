/** 格式化金额变动（元 → 万/亿） */
export function formatProfitChange(amount: number | null | undefined): string {
  if (amount == null || Number.isNaN(amount)) return '';
  const abs = Math.abs(amount);
  const sign = amount >= 0 ? '+' : '-';
  if (abs >= 1e8) return `${sign}${(abs / 1e8).toFixed(2)}亿`;
  if (abs >= 1e4) return `${sign}${(abs / 1e4).toFixed(0)}万`;
  return `${sign}${abs.toFixed(0)}元`;
}

export interface YoyDisplayRow {
  profit_yoy?: number | null;
  profit_yoy_raw?: number | null;
  profit_yoy_reliable?: boolean;
  profit_yoy_note?: string | null;
  profit_yoy_change?: number | null;
  revenue_yoy?: number | null;
  revenue_yoy_reliable?: boolean;
  revenue_yoy_note?: string | null;
}

export function formatProfitYoy(row: YoyDisplayRow): {
  text: string;
  title?: string;
  warning: boolean;
  reliable: boolean;
} {
  if (row.profit_yoy_reliable && row.profit_yoy != null) {
    const prefix = row.profit_yoy >= 0 ? '+' : '';
    return {
      text: `${prefix}${row.profit_yoy.toFixed(1)}%`,
      warning: false,
      reliable: true,
    };
  }

  const changeText = row.profit_yoy_change != null ? formatProfitChange(row.profit_yoy_change) : '';
  const rawText =
    row.profit_yoy_raw != null ? `原始同比 ${row.profit_yoy_raw >= 0 ? '+' : ''}${row.profit_yoy_raw.toFixed(1)}%` : '';
  const title = [row.profit_yoy_note, changeText && `绝对变动 ${changeText}`, rawText]
    .filter(Boolean)
    .join('；');

  return {
    text: changeText ? `基期过小 (${changeText})` : '基期过小',
    title: title || '去年同期基数过小，同比百分比参考意义有限',
    warning: true,
    reliable: false,
  };
}

export function formatRevenueYoy(row: YoyDisplayRow): {
  text: string;
  title?: string;
  warning: boolean;
} {
  if (row.revenue_yoy_reliable !== false && row.revenue_yoy != null) {
    const prefix = row.revenue_yoy >= 0 ? '+' : '';
    return { text: `${prefix}${row.revenue_yoy.toFixed(1)}%`, warning: false };
  }
  return {
    text: '基期过小',
    title: row.revenue_yoy_note || '营收同比因基数过小未展示百分比',
    warning: true,
  };
}

/** 近 N 期平均：仅统计可信同比，排除失真值 */
export function avgReliableYoy(rows: YoyDisplayRow[], field: 'profit' | 'revenue'): number | null {
  const key = field === 'profit' ? 'profit_yoy' : 'revenue_yoy';
  const reliableKey = field === 'profit' ? 'profit_yoy_reliable' : 'revenue_yoy_reliable';
  const vals = rows
    .filter((r) => r[reliableKey] !== false && r[key] != null)
    .map((r) => r[key] as number);
  if (!vals.length) return null;
  return vals.reduce((s, v) => s + v, 0) / vals.length;
}
