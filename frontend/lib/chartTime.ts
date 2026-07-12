/**
 * A 股分钟级图表时间 — 数据源为北京时间墙钟，lightweight-charts 按 UTC 分量渲染刻度。
 * 须用 Date.UTC 编码，勿用 new Date(y,m,d,h,mi)（会随浏览器时区偏移）。
 */

export const CHART_TZ = "Asia/Shanghai";

export interface YmdParts {
  y: number;
  mo: number;
  d: number;
}

/** 解析交易日 YYYYMMDD / YYYY-MM-DD */
export function parseTradeDateParts(tradeDate?: string | null): YmdParts {
  const s = String(tradeDate ?? "").trim();
  if (/^\d{8}$/.test(s)) {
    return {
      y: parseInt(s.slice(0, 4), 10),
      mo: parseInt(s.slice(4, 6), 10) - 1,
      d: parseInt(s.slice(6, 8), 10),
    };
  }
  if (/^\d{4}-\d{2}-\d{2}$/.test(s)) {
    const [y, mo, d] = s.split("-").map(Number);
    return { y, mo: mo - 1, d };
  }
  const now = new Date();
  return { y: now.getFullYear(), mo: now.getMonth(), d: now.getDate() };
}

/** 北京时间墙钟 → chart unix（UTC 分量 = 北京时分） */
export function beijingWallClockToUnix(
  y: number,
  mo: number,
  d: number,
  h: number,
  mi: number,
): number {
  return Math.floor(Date.UTC(y, mo, d, h, mi, 0) / 1000);
}

/** 分时 HHmm + 交易日 → chart unix */
export function beijingHHmmToUnix(hhmm: string, tradeDate?: string | null): number | null {
  const t = String(hhmm).replace(/\D/g, "").padStart(4, "0");
  if (t.length < 4) return null;
  const h = parseInt(t.slice(0, 2), 10);
  const mi = parseInt(t.slice(2, 4), 10);
  if (Number.isNaN(h) || Number.isNaN(mi)) return null;
  const { y, mo, d } = parseTradeDateParts(tradeDate);
  return beijingWallClockToUnix(y, mo, d, h, mi);
}

/** YYYYMMDDHH[mm] 紧凑串 → chart unix */
export function compactBeijingDateTimeToUnix(digits: string): number | null {
  const d = digits.replace(/\D/g, "");
  if (d.length < 8) return null;
  const y = parseInt(d.slice(0, 4), 10);
  const mo = parseInt(d.slice(4, 6), 10) - 1;
  const day = parseInt(d.slice(6, 8), 10);
  const h = d.length >= 10 ? parseInt(d.slice(8, 10), 10) : 0;
  const mi = d.length >= 12 ? parseInt(d.slice(10, 12), 10) : 0;
  if ([y, mo, day, h, mi].some(Number.isNaN)) return null;
  return beijingWallClockToUnix(y, mo, day, h, mi);
}

/** 从 chart unix 格式化为北京时间 HH:mm */
export function formatChartUnixAsBeijingTime(unix: number, withDate = false): string {
  const dt = new Date(unix * 1000);
  const hh = String(dt.getUTCHours()).padStart(2, "0");
  const mm = String(dt.getUTCMinutes()).padStart(2, "0");
  if (!withDate) return `${hh}:${mm}`;
  const y = dt.getUTCFullYear();
  const mo = String(dt.getUTCMonth() + 1).padStart(2, "0");
  const d = String(dt.getUTCDate()).padStart(2, "0");
  return `${y}-${mo}-${d} ${hh}:${mm}`;
}

/** lightweight-charts 分钟轴 localization */
export function minuteChartLocalization(withDate = false) {
  return {
    locale: "zh-CN",
    timeFormatter: (time: number) => formatChartUnixAsBeijingTime(time, withDate),
  };
}
