/**
 * 与 backend MyTT 对齐的技术指标（用于 K 线图副图，保证与主图同源同序）
 */

export interface OhlcBar {
  date: string;
  open: number;
  high: number;
  low: number;
  close: number;
}

export interface TechnicalBar {
  date: string;
  macd_dif: number | null;
  macd_dea: number | null;
  macd_bar: number | null;
  kdj_k: number | null;
  kdj_d: number | null;
  kdj_j: number | null;
  rsi14: number | null;
  boll_upper: number | null;
  boll_mid: number | null;
  boll_lower: number | null;
  atr14: number | null;
}

function round4(v: number): number {
  return Math.round(v * 10000) / 10000;
}

function ema(values: number[], period: number): number[] {
  const out = new Array<number>(values.length);
  if (values.length === 0) return out;
  const alpha = 2 / (period + 1);
  out[0] = values[0];
  for (let i = 1; i < values.length; i++) {
    out[i] = alpha * values[i] + (1 - alpha) * out[i - 1];
  }
  return out;
}

/** 中国式 SMA：ewm(alpha=1/N) */
function sma(values: number[], period: number): number[] {
  const out = new Array<number>(values.length);
  if (values.length === 0) return out;
  const alpha = 1 / period;
  out[0] = values[0];
  for (let i = 1; i < values.length; i++) {
    out[i] = alpha * values[i] + (1 - alpha) * out[i - 1];
  }
  return out;
}

function ma(values: number[], period: number): number[] {
  const out = new Array<number>(values.length).fill(NaN);
  for (let i = period - 1; i < values.length; i++) {
    let sum = 0;
    for (let j = i - period + 1; j <= i; j++) sum += values[j];
    out[i] = sum / period;
  }
  return out;
}

function std(values: number[], period: number): number[] {
  const out = new Array<number>(values.length).fill(NaN);
  for (let i = period - 1; i < values.length; i++) {
    let sum = 0;
    for (let j = i - period + 1; j <= i; j++) sum += values[j];
    const mean = sum / period;
    let sq = 0;
    for (let j = i - period + 1; j <= i; j++) sq += (values[j] - mean) ** 2;
    out[i] = Math.sqrt(sq / period);
  }
  return out;
}

function rollingMin(values: number[], period: number): number[] {
  const out = new Array<number>(values.length).fill(NaN);
  for (let i = period - 1; i < values.length; i++) {
    out[i] = Math.min(...values.slice(i - period + 1, i + 1));
  }
  return out;
}

function rollingMax(values: number[], period: number): number[] {
  const out = new Array<number>(values.length).fill(NaN);
  for (let i = period - 1; i < values.length; i++) {
    out[i] = Math.max(...values.slice(i - period + 1, i + 1));
  }
  return out;
}

function calcMacd(closes: number[]) {
  const ema12 = ema(closes, 12);
  const ema26 = ema(closes, 26);
  const dif = closes.map((_, i) => ema12[i] - ema26[i]);
  const dea = ema(dif, 9);
  const bar = dif.map((v, i) => 2 * (v - dea[i]));
  return { dif, dea, bar };
}

function calcKdj(closes: number[], highs: number[], lows: number[]) {
  const llv = rollingMin(lows, 9);
  const hhv = rollingMax(highs, 9);
  const rsv = closes.map((c, i) => {
    const span = hhv[i] - llv[i];
    if (!Number.isFinite(span) || span === 0) return 50;
    return ((c - llv[i]) / span) * 100;
  });
  const k = ema(rsv, 5);
  const d = ema(k, 5);
  const j = k.map((kv, i) => kv * 3 - d[i] * 2);
  return { k, d, j };
}

function calcRsi(closes: number[], period = 14) {
  const diff = closes.map((c, i) => (i === 0 ? 0 : c - closes[i - 1]));
  const up = diff.map((v) => Math.max(v, 0));
  const abs = diff.map((v) => Math.abs(v));
  const upSma = sma(up, period);
  const absSma = sma(abs, period);
  return closes.map((_, i) => {
    const denom = absSma[i];
    if (!Number.isFinite(denom) || denom === 0) return 50;
    return (upSma[i] / denom) * 100;
  });
}

function calcBoll(closes: number[]) {
  const mid = ma(closes, 20);
  const s = std(closes, 20);
  const upper = mid.map((m, i) => (Number.isFinite(m) && Number.isFinite(s[i]) ? m + 2 * s[i] : NaN));
  const lower = mid.map((m, i) => (Number.isFinite(m) && Number.isFinite(s[i]) ? m - 2 * s[i] : NaN));
  return { upper, mid, lower };
}

function calcAtr(closes: number[], highs: number[], lows: number[], period = 14) {
  const tr = closes.map((c, i) => {
    if (i === 0) return highs[i] - lows[i];
    return Math.max(
      highs[i] - lows[i],
      Math.abs(highs[i] - closes[i - 1]),
      Math.abs(lows[i] - closes[i - 1]),
    );
  });
  return ma(tr, period);
}

function safeNum(v: number): number | null {
  return Number.isFinite(v) ? round4(v) : null;
}

export function computeTechnicalFromBars(bars: OhlcBar[]): TechnicalBar[] {
  if (bars.length < 2) return [];

  const closes = bars.map((b) => b.close);
  const highs = bars.map((b) => b.high);
  const lows = bars.map((b) => b.low);

  const { dif, dea, bar } = calcMacd(closes);
  const { k, d, j } = calcKdj(closes, highs, lows);
  const rsi = calcRsi(closes, 14);
  const { upper, mid, lower } = calcBoll(closes);
  const atr = calcAtr(closes, highs, lows, 14);

  return bars.map((b, i) => ({
    date: String(b.date).slice(0, 10),
    macd_dif: safeNum(dif[i]),
    macd_dea: safeNum(dea[i]),
    macd_bar: safeNum(bar[i]),
    kdj_k: safeNum(k[i]),
    kdj_d: safeNum(d[i]),
    kdj_j: safeNum(j[i]),
    rsi14: safeNum(rsi[i]),
    boll_upper: safeNum(upper[i]),
    boll_mid: safeNum(mid[i]),
    boll_lower: safeNum(lower[i]),
    atr14: safeNum(atr[i]),
  }));
}

/** 通达信/同花顺常用：柱增红、柱减绿（零上零下均适用） */
export function macdBarColor(bars: Array<number | null>, index: number): string {
  const cur = bars[index];
  if (cur == null || !Number.isFinite(cur)) return "#ef4444";
  const prev = index > 0 ? bars[index - 1] : cur;
  const p = prev == null || !Number.isFinite(prev) ? cur : prev;
  if (cur >= p) return "#ef4444";
  return "#22c55e";
}
