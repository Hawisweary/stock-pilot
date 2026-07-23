'use client';
import { useEffect, useMemo, useState } from 'react';
import { useParams } from 'next/navigation';
import { StockDetailSkeleton } from '@/components/StockDetailSkeleton';
import { ScoreTrendChart } from '@/components/ScoreTrendChart';
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card';
import { KLineChart } from '@/components/KLineChart';
import { IntradayChart, type IntradayBar } from '@/components/IntradayChart';
import { ErrorBoundary } from '@/components/ErrorBoundary';
import { StockAiCommentarySection } from '@/components/StockAiCommentarySection';
import { MarketFundamentalsCard } from '@/components/MarketFundamentalsCard';
import { StockMarketSignalsPanel } from '@/components/StockMarketSignalsPanel';
import { PeerDeepPanel } from '@/components/PeerDeepPanel';
import { V5ScorePanel, V5ScoreBadge } from '@/components/V5ScorePanel';
import { ReportRagPanel } from '@/components/ReportRagPanel';
import { ConceptBoardsCard } from '@/components/ConceptBoardsCard';
import { CompanyInfoCard } from '@/components/CompanyInfoCard';
import { EarningsAlertsCard } from '@/components/EarningsAlertsCard';
import { MoneyFlowDetailCard } from '@/components/MoneyFlowDetailCard';
import { LhbPeriodStatsCard } from '@/components/LhbPeriodStatsCard';
import { AlphaFactorsCard } from '@/components/AlphaFactorsCard';
import { ConsensusEpsCard } from '@/components/ConsensusEpsCard';
import Link from 'next/link';
import { ArrowLeft, Download, Ruler, Factory, BarChart3, FileText, Newspaper } from 'lucide-react';
import { api } from '@/lib/api';
import { avgReliableYoy, formatProfitYoy, formatRevenueYoy } from '@/lib/yoyDisplay';
import { BarChart, Bar, LineChart, Line, XAxis, YAxis, CartesianGrid, Tooltip, Legend, ResponsiveContainer } from 'recharts';

const KLINE_DAYS = 250;

const LIMIT_STATUS_LABELS: Record<number, string> = {
  0: '平盘', 1: '上涨', 2: '涨停', 3: '一字涨停',
  4: '下跌', 5: '跌停', 6: '一字跌停',
};

export default function StockDetailPage() {
  const params = useParams<{ code: string }>();
  const code = params.code;
  const [stockId, setStockId] = useState<number>(0);
  const [stock, setStock] = useState<any>(null);
  const [state, setState] = useState<string>('loading');
  const [klinePeriod, setKlinePeriod] = useState<string>('intraday');
  const [klineData, setKlineData] = useState<any[]>([]);
  const [intradayBars, setIntradayBars] = useState<IntradayBar[]>([]);
  const [intradayPrevClose, setIntradayPrevClose] = useState<number | null>(null);
  const [intradayTradeDate, setIntradayTradeDate] = useState<string | null>(null);

  useEffect(() => {
    if (!code) return;
    // 6位纯数字是股票代码（如 000001/920020），不能当内部 id 用
    const isNum = /^\d+$/.test(code) && code.length !== 6;
    if (isNum) {
      const id = parseInt(code); setStockId(id);
      fetch(`/api/stocks/${id}`).then(r => {
        if (!r.ok) throw new Error(String(r.status));
        return r.json();
      }).then(d => {
        if (d?.code) { setStock(d); fetchKline(id); fetchIntraday(id); }
        else setState('error');
      }).catch(() => setState('error'));
    } else {
      fetch('/api/stocks').then(r => r.json()).then(stocks => {
        const s = stocks.find((x: any) => x.code === code);
        if (s) { setStockId(s.id); setStock(s); fetchKline(s.id); fetchIntraday(s.id); }
        else setState('error');
      }).catch(() => setState('error'));
    }
  }, [code]);

  const fetchIntraday = (id: number) => {
    fetch(`/api/realtime/intraday/${id}`)
      .then((r) => r.json())
      .then((d) => {
        setIntradayBars(d.bars || []);
        setIntradayPrevClose(d.prev_close ?? null);
        setIntradayTradeDate(d.trade_date ?? null);
      })
      .catch(() => {
        setIntradayBars([]);
        setIntradayPrevClose(null);
        setIntradayTradeDate(null);
      });
  };

  const mapKlineRows = (rows: any[]) =>
    (rows || [])
      .map((b: any) => ({
        date: b.date || b.time || '',
        open: b.open,
        high: b.high,
        low: b.low,
        close: b.close,
        volume: b.volume,
      }))
      .sort((a: any, b: any) => String(a.date).localeCompare(String(b.date)));

  const fetchKline = (id: number) => {
    fetch(`/api/stocks/${id}/kline?period=daily&days=${KLINE_DAYS}`)
      .then(r => r.json())
      .then((klineRes) => {
        const arr = mapKlineRows(klineRes?.kline || klineRes?.data || (Array.isArray(klineRes) ? klineRes : []));
        setStock((prev: any) => ({ ...prev, kline: arr }));
        setState('loaded');
      })
      .catch(() => setState('error'));
  };

  // 周期切换时重新加载（分时 / 5分 / 日周月）
  useEffect(() => {
    if (!stockId || state !== 'loaded') return;
    if (klinePeriod === 'intraday') {
      fetchIntraday(stockId);
      return;
    }
    // 避免 5分→日K 切换时用旧分钟数据渲染日K（会被压成同一天导致重复 time）
    setKlineData([]);
    if (klinePeriod === '5min') {
      fetch(`/api/realtime/kline/${stockId}?period=5min`)
        .then((r) => r.json())
        .then((d) => {
          setKlineData(mapKlineRows(d.kline || []));
        })
        .catch(() => {
          setKlineData([]);
        });
      return;
    }
    fetch(`/api/stocks/${stockId}/kline?period=${klinePeriod}&days=${KLINE_DAYS}`)
      .then(r => r.json())
      .then((klineRes) => {
        setKlineData(mapKlineRows(klineRes?.kline || klineRes?.data || (Array.isArray(klineRes) ? klineRes : [])));
      })
      .catch(() => {});
  }, [klinePeriod, stockId, state]);

  /* normalize nested API response to flat stock object */
  const stockData = useMemo(() => {
    if (!stock) return null;
    const ind = stock.latest_indicators || {};
    return {
      ...stock,
      pe_ttm: stock.pe_ttm ?? ind.pe_ttm,
      pe: ind.pe,
      ps_ttm: ind.ps_ttm,
      pb: stock.pb ?? ind.pb,
      roe_ttm: stock.roe_ttm ?? (ind.roe ? ind.roe / 100 : undefined),
      total_mv: stock.total_mv ?? (ind.market_cap ? ind.market_cap * 1e8 : undefined),
      dividend_yield: stock.dividend_yield ?? (ind.dividend_yield ? ind.dividend_yield / 100 : undefined),
      dividend_yield_ttm: ind.dividend_yield_ttm,
      turnover_rate_f: ind.turnover_rate_f,
      free_share: ind.free_share,
      limit_status: ind.limit_status,
      eps: ind.eps,
      rd_exp: ind.rd_exp,
      money_cap: ind.money_cap,
      inventories: ind.inventories,
      goodwill: ind.goodwill,
      fix_assets: ind.fix_assets,
      revenue_yoy: stock.revenue_yoy ?? ind.revenue_yoy,
      revenue_yoy_reliable: stock.revenue_yoy_reliable ?? ind.revenue_yoy_reliable,
      revenue_yoy_note: stock.revenue_yoy_note ?? ind.revenue_yoy_note,
      profit_yoy: stock.profit_yoy ?? ind.profit_yoy ?? ind.net_profit_yoy,
      profit_yoy_raw: stock.profit_yoy_raw ?? ind.profit_yoy_raw,
      profit_yoy_reliable: stock.profit_yoy_reliable ?? ind.profit_yoy_reliable,
      profit_yoy_note: stock.profit_yoy_note ?? ind.profit_yoy_note,
      profit_yoy_change: stock.profit_yoy_change ?? ind.profit_yoy_change,
      profit_yoy_period: stock.profit_yoy_period ?? ind.profit_yoy_period,
      growth_granularity: stock.growth_granularity ?? ind.growth_granularity,
      close: stock.close ?? (stock.kline?.length ? stock.kline[stock.kline.length-1].close : undefined),
      volume: stock.volume,
      amount: stock.amount,
      turnover_rate: stock.turnover_rate,
      volume_ratio: ind.volume_ratio,
      total_share: ind.total_share,
      float_share: ind.float_share,
    };
  }, [stock]);

  const chartKlineData = useMemo(() => {
    if (klinePeriod === '5min') return klineData;
    if (klineData.length > 0) return klineData;
    return stockData?.kline || [];
  }, [klinePeriod, klineData, stockData?.kline]);

  if (state === 'loading') return <div className="p-8"><StockDetailSkeleton /></div>;
  if (state === 'error' || !stockData) return <div className="p-8 text-center text-muted-foreground">股票未找到: {code}</div>;

  const price = stockData.close ?? stockData.price;
  const priceStr = price != null ? `¥${Number(price).toFixed(2)}` : "--";

  return (
    <div className="max-w-7xl mx-auto px-4 py-4 space-y-4">
      {/* Hero 头部 */}
      <div className="rounded-md border border-border bg-card px-5 py-4">
        <div className="flex items-start gap-4 flex-wrap">
          <Link href="/" className="mt-1"><ArrowLeft className="w-4 h-4 text-muted-foreground" /></Link>
          <div className="flex-1 min-w-0">
            <div className="flex items-center gap-2 flex-wrap">
              <h1 className="text-xl font-semibold tracking-tight">{stockData.name}</h1>
              <span className="text-muted-foreground text-sm font-mono">{stockData.code}</span>
              {stockData.industry_sw && (
                <span className="text-[11px] bg-primary/10 text-primary px-2 py-0.5 rounded font-medium">
                  {stockData.industry_sw}
                </span>
              )}
              {stockData.industry_sw2 && stockData.industry_sw2 !== stockData.industry_sw && (
                <span className="text-[11px] bg-primary/5 text-primary/80 px-2 py-0.5 rounded font-medium">
                  {stockData.industry_sw2}
                </span>
              )}
              {stockData.industry_sw3 && stockData.industry_sw3 !== stockData.industry_sw2 && stockData.industry_sw3 !== stockData.industry_sw && (
                <span className="text-[10px] bg-primary/5 text-primary/60 px-2 py-0.5 rounded font-medium">
                  {stockData.industry_sw3}
                </span>
              )}
            </div>
            {/* 快速指标横排 */}
            <div className="flex flex-wrap gap-x-5 gap-y-1 mt-2">
              <div>
                <span className="text-2xl font-bold font-mono tabular-nums">{priceStr}</span>
              </div>
              {stockData.pe_ttm != null && (
                <div className="text-sm">
                  <span className="text-muted-foreground text-xs">PE(TTM) </span>
                  <span className={`font-semibold font-mono tabular-nums ${stockData.pe_ttm > 100 ? 'text-up' : stockData.pe_ttm < 15 ? 'text-down' : ''}`}>
                    {stockData.pe_ttm.toFixed(1)}x
                  </span>
                </div>
              )}
              {stockData.pe != null && (
                <div className="text-sm">
                  <span className="text-muted-foreground text-xs">PE </span>
                  <span className="font-semibold font-mono tabular-nums">{stockData.pe.toFixed(1)}x</span>
                </div>
              )}
              {stockData.ps_ttm != null && (
                <div className="text-sm">
                  <span className="text-muted-foreground text-xs">PS(TTM) </span>
                  <span className="font-semibold font-mono tabular-nums">{stockData.ps_ttm.toFixed(2)}x</span>
                </div>
              )}
              {stockData.pb != null && (
                <div className="text-sm">
                  <span className="text-muted-foreground text-xs">PB </span>
                  <span className="font-semibold font-mono tabular-nums">{stockData.pb.toFixed(2)}x</span>
                </div>
              )}
              {stockData.roe_ttm != null && (
                <div className="text-sm">
                  <span className="text-muted-foreground text-xs">ROE </span>
                  <span className="font-semibold font-mono tabular-nums">{(stockData.roe_ttm * 100).toFixed(1)}%</span>
                </div>
              )}
              {stockData.total_mv != null && (
                <div className="text-sm">
                  <span className="text-muted-foreground text-xs">市值 </span>
                  <span className="font-semibold font-mono tabular-nums">{(stockData.total_mv / 1e8).toFixed(0)}亿</span>
                </div>
              )}
              {stockData.turnover_rate != null && (
                <div className="text-sm">
                  <span className="text-muted-foreground text-xs">换手率 </span>
                  <span className="font-semibold font-mono tabular-nums">{Number(stockData.turnover_rate).toFixed(2)}%</span>
                </div>
              )}
              {stockData.volume != null && (
                <div className="text-sm">
                  <span className="text-muted-foreground text-xs">成交量 </span>
                  <span className="font-semibold font-mono tabular-nums">{(stockData.volume / 1e4).toFixed(1)}万股</span>
                </div>
              )}
              {stockData.amount != null && (
                <div className="text-sm">
                  <span className="text-muted-foreground text-xs">成交额 </span>
                  <span className="font-semibold font-mono tabular-nums">{(stockData.amount / 1e8).toFixed(2)}亿</span>
                </div>
              )}
              {stockData.volume_ratio != null && (
                <div className="text-sm">
                  <span className="text-muted-foreground text-xs">量比 </span>
                  <span className="font-semibold font-mono tabular-nums">{Number(stockData.volume_ratio).toFixed(2)}</span>
                </div>
              )}
              {stockData.total_share != null && (
                <div className="text-sm">
                  <span className="text-muted-foreground text-xs">总股本 </span>
                  <span className="font-semibold font-mono tabular-nums">{(stockData.total_share / 1e4).toFixed(2)}亿股</span>
                </div>
              )}
              {stockData.float_share != null && (
                <div className="text-sm">
                  <span className="text-muted-foreground text-xs">流通股本 </span>
                  <span className="font-semibold font-mono tabular-nums">{(stockData.float_share / 1e4).toFixed(2)}亿股</span>
                </div>
              )}
              {stockData.free_share != null && (
                <div className="text-sm">
                  <span className="text-muted-foreground text-xs">自由流通股本 </span>
                  <span className="font-semibold font-mono tabular-nums">{(stockData.free_share / 1e4).toFixed(2)}亿股</span>
                </div>
              )}
              {stockData.turnover_rate_f != null && (
                <div className="text-sm">
                  <span className="text-muted-foreground text-xs">自由流通换手率 </span>
                  <span className="font-semibold font-mono tabular-nums">{Number(stockData.turnover_rate_f).toFixed(2)}%</span>
                </div>
              )}
              {stockData.dividend_yield != null && (
                <div className="text-sm">
                  <span className="text-muted-foreground text-xs">股息率 </span>
                  <span className="font-semibold font-mono tabular-nums">{(stockData.dividend_yield * 100).toFixed(2)}%</span>
                </div>
              )}
              {stockData.dividend_yield_ttm != null && (
                <div className="text-sm">
                  <span className="text-muted-foreground text-xs">股息率(TTM) </span>
                  <span className="font-semibold font-mono tabular-nums">{Number(stockData.dividend_yield_ttm).toFixed(2)}%</span>
                </div>
              )}
              {stockData.limit_status != null && stockData.limit_status !== 0 && (
                <div className="text-sm" title="上一交易日收盘涨跌停状态（Tushare daily_basic，非实时）">
                  <span className="text-muted-foreground text-xs">昨日状态 </span>
                  <span className={`font-semibold font-mono tabular-nums ${[2, 3].includes(stockData.limit_status) ? 'text-up' : [5, 6].includes(stockData.limit_status) ? 'text-down' : ''}`}>
                    {LIMIT_STATUS_LABELS[stockData.limit_status] ?? '--'}
                  </span>
                </div>
              )}
              {stockData.eps != null && (
                <div className="text-sm">
                  <span className="text-muted-foreground text-xs">每股收益 </span>
                  <span className="font-semibold font-mono tabular-nums">{Number(stockData.eps).toFixed(2)}元</span>
                </div>
              )}
              {stockData.rd_exp != null && (
                <div className="text-sm">
                  <span className="text-muted-foreground text-xs">研发费用 </span>
                  <span className="font-semibold font-mono tabular-nums">{(stockData.rd_exp / 1e8).toFixed(2)}亿</span>
                </div>
              )}
              {stockData.money_cap != null && (
                <div className="text-sm">
                  <span className="text-muted-foreground text-xs">货币资金 </span>
                  <span className="font-semibold font-mono tabular-nums">{(stockData.money_cap / 1e8).toFixed(2)}亿</span>
                </div>
              )}
              {stockData.inventories != null && (
                <div className="text-sm">
                  <span className="text-muted-foreground text-xs">存货 </span>
                  <span className="font-semibold font-mono tabular-nums">{(stockData.inventories / 1e8).toFixed(2)}亿</span>
                </div>
              )}
              {stockData.goodwill != null && (
                <div className="text-sm">
                  <span className="text-muted-foreground text-xs">商誉 </span>
                  <span className="font-semibold font-mono tabular-nums">{(stockData.goodwill / 1e8).toFixed(2)}亿</span>
                </div>
              )}
              {stockData.fix_assets != null && (
                <div className="text-sm">
                  <span className="text-muted-foreground text-xs">固定资产 </span>
                  <span className="font-semibold font-mono tabular-nums">{(stockData.fix_assets / 1e8).toFixed(2)}亿</span>
                </div>
              )}
            </div>
          </div>
          <div className="flex items-center gap-2">
            {stockId > 0 && <V5ScoreBadge stockId={stockId} />}
            {stockId > 0 && (
              <a
                href={api.reportExportUrl(stockId)}
                target="_blank"
                rel="noopener noreferrer"
                className="inline-flex items-center gap-1 text-xs border rounded px-2 py-1 hover:bg-muted"
              >
                <Download className="h-3.5 w-3.5" /> 研报
              </a>
            )}
          </div>
        </div>
      </div>

      {/* 两栏主布局 */}
      <div className="grid grid-cols-1 lg:grid-cols-7 gap-4">
        {/* 左栏：行情 + K线 + 财报 */}
        <div className="lg:col-span-5 space-y-4">
          {/* 行情图：分时 + K线 */}
          {(intradayBars.length > 0 || klineData.length > 0 || stockData.kline?.length > 0) && (
            <Card>
              <CardHeader className="pb-2 flex flex-row items-center justify-between">
                <CardTitle className="text-sm">
                  {klinePeriod === 'intraday' ? '分时图' : klinePeriod === '5min' ? '5分钟K线' : 'K线图'}
                </CardTitle>
                <div className="flex gap-1 flex-wrap justify-end">
                  {([
                    ['intraday', '分时'],
                    ['5min', '5分'],
                    ['daily', '日'],
                    ['weekly', '周'],
                    ['monthly', '月'],
                  ] as const).map(([p, label]) => (
                    <button key={p} onClick={() => setKlinePeriod(p)}
                      className={`text-xs px-2 py-1 rounded ${klinePeriod === p ? 'bg-primary text-primary-foreground' : 'bg-muted'}`}>
                      {label}
                    </button>
                  ))}
                </div>
              </CardHeader>
              <CardContent className="p-0">
                <ErrorBoundary>
                  {klinePeriod === 'intraday' ? (
                    <IntradayChart
                      bars={intradayBars}
                      prevClose={intradayPrevClose}
                      tradeDate={intradayTradeDate}
                    />
                  ) : chartKlineData.length > 0 ? (
                    <KLineChart
                      key={klinePeriod}
                      data={chartKlineData}
                      minuteBars={klinePeriod === '5min'}
                    />
                  ) : (
                    <div className="flex items-center justify-center text-xs text-muted-foreground h-[220px]">
                      暂无{klinePeriod === '5min' ? '5分钟' : ''}K线数据
                    </div>
                  )}
                </ErrorBoundary>
              </CardContent>
            </Card>
          )}

          {/* 深度基本面分析 */}
          <DeepFundamental stockId={stockId} />

          {/* 财报趋势 */}
          <QuarterlySection stockId={stockId} stockName={stockData.name} />
        </div>

        {/* 右栏：Tab 分区 */}
        <div className="lg:col-span-2">
          <RightSidebarTabs
            stockId={stockId}
            stock={stockData}
            stockCode={stock?.code}
          />
        </div>
      </div>
    </div>
  );
}

function DeepFundamental({ stockId }: { stockId: number }) {
  const [quarters, setQuarters] = useState<any>(null);
  useEffect(() => {
    fetch(`/api/stocks/${stockId}/quarterly`)
      .then((r) => r.json())
      .then(setQuarters)
      .catch(() => {});
  }, [stockId]);
  if (!quarters?.quarters?.length) return null;

  const recentQ = quarters?.quarters?.slice(-4) || [];
  const avgRevGrowth = avgReliableYoy(recentQ, 'revenue');
  const avgProfitGrowth = avgReliableYoy(recentQ, 'profit');

  return (
    <Card>
      <CardHeader className="pb-2"><CardTitle className="text-sm flex items-center gap-1.5"><Ruler className="w-3.5 h-3.5 text-muted-foreground" /> 深度基本面</CardTitle></CardHeader>
      <CardContent>
        <div className="grid grid-cols-2 gap-3 text-center">
          <div className="border border-border rounded p-2">
            <div className="text-[10px] text-muted-foreground uppercase tracking-wide">近4Q平均营收增速</div>
            <div className={"text-lg font-bold font-mono tabular-nums "+ ((avgRevGrowth ?? 0)>10?'text-up':(avgRevGrowth ?? 0)>0?'text-down':'text-muted-foreground')}>
              {avgRevGrowth != null ? `${avgRevGrowth.toFixed(1)}%` : '基期过小'}
            </div>
          </div>
          <div className="border border-border rounded p-2">
            <div className="text-[10px] text-muted-foreground uppercase tracking-wide">近4Q平均利润增速</div>
            <div className={"text-lg font-bold font-mono tabular-nums "+ ((avgProfitGrowth ?? 0)>15?'text-up':(avgProfitGrowth ?? 0)>0?'text-down':'text-muted-foreground')}>
              {avgProfitGrowth != null ? `${avgProfitGrowth.toFixed(1)}%` : '基期过小'}
            </div>
          </div>
        </div>

        {recentQ.length > 0 && (
          <div className="mt-3 border-t border-border pt-3">
            <div className="text-[11px] text-muted-foreground mb-2">近4季度趋势</div>
            <div className="grid grid-cols-4 gap-1">
              {recentQ.map((q: any, i: number) => {
                const rev = formatRevenueYoy(q);
                const prof = formatProfitYoy(q);
                return (
                <div key={i} className="text-center text-[10px]">
                  <div className="text-muted-foreground font-mono">{q.period_end_date?.slice(5)}</div>
                  <div
                    className={'font-mono tabular-nums ' + (rev.warning ? 'text-orange-600 bg-orange-500/10 rounded px-0.5 ' : '') + (q.revenue_yoy>=15?"text-up font-bold":q.revenue_yoy>=0?"text-down":"text-muted-foreground")}
                    title={rev.title}
                  >
                    营收{rev.text}
                  </div>
                  <div
                    className={'font-mono tabular-nums ' + (prof.warning ? 'text-orange-600 bg-orange-500/10 rounded px-0.5 font-medium ' : '') + (q.profit_yoy>=20?"text-up font-bold":q.profit_yoy>=0?"text-down":"text-up")}
                    title={prof.title}
                  >
                    利润{prof.text.startsWith('基期') ? prof.text : (q.profit_yoy != null && q.profit_yoy >= 0 ? '+' : '') + prof.text}
                  </div>
                </div>
              );})}
            </div>
          </div>
        )}
      </CardContent>
    </Card>
  );
}

// ========== 同业对比 ==========
function PeerSection({ stockId, stock }: { stockId: number; stock: any }) {
  const [peers, setPeers] = useState<any[]>([]);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    const ind = stock.industry_sw || stock.industry_list?.[0] || '';
    if (!ind) {
      setLoading(false);
      return;
    }
    fetch('/api/stocks/search/by-name?q=' + encodeURIComponent(ind.split(',')[0]))
      .then((r) => r.json())
      .then((d) => {
        if (Array.isArray(d)) setPeers(d.filter((p) => p.id !== stockId).slice(0, 5));
      })
      .catch(() => {})
      .finally(() => setLoading(false));
  }, [stockId, stock.industry_sw]);

  if (loading || !peers.length) return null;

  return (
    <Card>
      <CardHeader className="pb-2">
        <CardTitle className="text-sm flex items-center gap-1.5"><Factory className="w-3.5 h-3.5 text-muted-foreground" /> 同业</CardTitle>
      </CardHeader>
      <CardContent>
        <table className="w-full text-[11px]">
          <thead>
            <tr className="text-left text-muted-foreground border-b border-border">
              <th className="font-normal">股票</th>
              <th className="text-right font-normal">PE</th>
              <th className="text-right font-normal">V5</th>
            </tr>
          </thead>
          <tbody>
            {[{ ...stock, is_current: true }, ...peers].map((p, i) => (
              <tr key={i} className={'border-b border-border ' + (p.is_current ? 'bg-primary/5 font-semibold' : '')}>
                <td className="py-0.5">
                  {p.name?.slice(0, 6)}
                  {p.is_current ? <span className="text-primary">·当前</span> : ''}
                </td>
                <td className="py-0.5 text-right font-mono tabular-nums">{p.pe_ttm?.toFixed(1) || '--'}</td>
                <td className="py-0.5 text-right font-mono tabular-nums font-semibold">
                  {p.composite_v5?.toFixed(0) || '--'}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </CardContent>
    </Card>
  );
}

// ========== 财报趋势 ==========
function QuarterlySection({ stockId, stockName }: { stockId: number; stockName: string }) {
  const [data, setData] = useState<any>(null);

  useEffect(() => {
    fetch(`/api/stocks/${stockId}/quarterly`)
      .then((r) => r.json())
      .then((d) => setData(d))
      .catch(() => {});
  }, [stockId]);

  if (!data?.quarters?.length) return null;

  const quarters = data.quarters.slice(-8);
  const chartData = quarters.map((q: any) => ({
    period: q.period_end_date?.slice(0, 7) || '',
    营收: +(q.revenue / 1e8).toFixed(2),
    净利润: +((q.net_profit_parent ?? q.net_profit ?? 0) / 1e8).toFixed(2),
    营收同比: q.revenue_yoy ?? null,
    利润同比: q.profit_yoy ?? null,
  }));
  const formatYi = (v: number) => (v >= 100 ? (v / 100).toFixed(1) + '百亿' : v + '亿');

  return (
    <Card>
      <CardHeader className="pb-2">
        <CardTitle className="text-sm flex items-center gap-1.5"><BarChart3 className="w-3.5 h-3.5 text-muted-foreground" /> 财报趋势 · {stockName}</CardTitle>
        {data.yoy_note && (
          <p className="text-[10px] text-muted-foreground mt-1 leading-relaxed">{data.yoy_note}</p>
        )}
      </CardHeader>
      <CardContent>
        <div className="h-64 w-full min-w-0">
          <ResponsiveContainer width="100%" height={256} minWidth={0}>
            <BarChart data={chartData} margin={{ top: 8, right: 8, left: 8, bottom: 0 }}>
              <CartesianGrid strokeDasharray="3 3" stroke="var(--border)" />
              <XAxis dataKey="period" tick={{ fontSize: 11 }} tickLine={false} />
              <YAxis tickFormatter={formatYi} tick={{ fontSize: 10 }} width={52} />
              <Tooltip
                formatter={(value: number, name: string) =>
                  name === '营收' || name === '净利润' ? [`${value}亿`, name] : [`${value}%`, name]
                }
                contentStyle={{ fontSize: 12 }}
              />
              <Legend wrapperStyle={{ fontSize: 11 }} />
              <Bar dataKey="营收" name="营收(亿)" fill="var(--color-chart-1)" radius={[2, 2, 0, 0]} />
              <Bar dataKey="净利润" name="净利润(亿)" fill="var(--color-chart-2)" radius={[2, 2, 0, 0]} />
            </BarChart>
          </ResponsiveContainer>
        </div>
        {chartData.some((d) => d.营收同比 !== null) && (
          <div className="h-48 w-full mt-2 min-w-0">
            <ResponsiveContainer width="100%" height={192} minWidth={0}>
              <LineChart data={chartData} margin={{ top: 8, right: 8, left: 8, bottom: 0 }}>
                <CartesianGrid strokeDasharray="3 3" stroke="var(--border)" />
                <XAxis dataKey="period" tick={{ fontSize: 11 }} tickLine={false} />
                <YAxis tickFormatter={(v) => v + '%'} tick={{ fontSize: 10 }} width={44} domain={['auto', 'auto']} />
                <Tooltip formatter={(value: number) => [`${value}%`, '']} contentStyle={{ fontSize: 12 }} />
                <Legend wrapperStyle={{ fontSize: 11 }} />
                <Line type="monotone" dataKey="营收同比" name="营收同比" stroke="var(--color-chart-1)" strokeWidth={2} dot={{ r: 3 }} connectNulls />
                <Line type="monotone" dataKey="利润同比" name="利润同比" stroke="var(--color-chart-2)" strokeWidth={2} dot={{ r: 3 }} connectNulls />
              </LineChart>
            </ResponsiveContainer>
          </div>
        )}
      </CardContent>
    </Card>
  );
}

// ========== 公告 ==========
function AnnouncementsSection({ stockId }: { stockId: number }) {
  const [items, setItems] = useState<any[]>([]);

  useEffect(() => {
    fetch(`/api/stocks/${stockId}/announcements`)
      .then((r) => r.json())
      .then((d) => {
        const rows = d?.announcements || [];
        if (rows.length) setItems(rows.slice(0, 8));
        else {
          fetch(`/api/stocks/${stockId}/announcements/fetch`, { method: 'POST' })
            .then((r2) => r2.json())
            .then(() =>
              fetch(`/api/stocks/${stockId}/announcements`)
                .then((r3) => r3.json())
                .then((d3) => setItems((d3?.announcements || []).slice(0, 8)))
            )
            .catch(() => {});
        }
      })
      .catch(() => {});
  }, [stockId]);

  if (!items.length) return null;

  return (
    <Card>
      <CardHeader className="pb-2">
        <CardTitle className="text-sm flex items-center gap-1.5"><FileText className="w-3.5 h-3.5 text-muted-foreground" /> 公告</CardTitle>
      </CardHeader>
      <CardContent className="space-y-1.5">
        {items.map((a, i) => (
          <a
            key={a.art_code || i}
            href={a.pdf_url || a.url || '#'}
            target="_blank"
            rel="noopener"
            className="block hover:bg-muted p-1.5 rounded -mx-1.5"
          >
            <div className="text-xs leading-relaxed line-clamp-2">{a.title}</div>
            <div className="text-[10px] text-muted-foreground flex gap-2 mt-0.5 flex-wrap">
              {a.ann_type && <span>{a.ann_type}</span>}
              <span>{a.source}</span>
              <span>{a.pub_date}</span>
            </div>
          </a>
        ))}
      </CardContent>
    </Card>
  );
}

// ========== 右侧 Tab 分区 ==========
function RightSidebarTabs({
  stockId, stock, stockCode,
}: { stockId: number; stock: any; stockCode?: string }) {
  const [tab, setTab] = useState<"评分" | "基本面" | "信号" | "AI" | "资讯">("评分");
  const tabs = ["评分", "基本面", "信号", "AI", "资讯"] as const;

  return (
    <div className="space-y-3">
      {/* Tab 切换条 */}
      <div className="flex rounded-md border border-border overflow-hidden divide-x divide-border">
        {tabs.map((t) => (
          <button
            key={t}
            onClick={() => setTab(t)}
            className={`flex-1 text-xs py-2 font-medium transition-colors ${
              tab === t ? "bg-primary text-primary-foreground" : "hover:bg-muted"
            }`}
          >
            {t}
          </button>
        ))}
      </div>

      {tab === "评分" && (
        <div className="space-y-3">
          <V5ScorePanel stockId={stockId} />
          <div className="rounded-md border border-border bg-card p-4 space-y-2">
            <h3 className="text-sm font-semibold">评分历史</h3>
            <ScoreTrendChart stockId={stockId} />
          </div>
          <PeerSection stockId={stockId} stock={stock} />
          <PeerDeepPanel stockId={stockId} />
        </div>
      )}

      {tab === "基本面" && (
        <div className="space-y-3">
          <MarketFundamentalsCard stockId={stockId} />
          <EarningsAlertsCard stockId={stockId} />
          <MoneyFlowDetailCard stockId={stockId} />
          <LhbPeriodStatsCard stockId={stockId} />
          <AlphaFactorsCard stockId={stockId} />
          {stockCode && <ConceptBoardsCard code={stockCode} />}
          <CompanyInfoCard stockId={stockId} />
          {stockCode && <ConsensusEpsCard code={stockCode} />}
          <ReportRagPanel stockId={stockId} />
        </div>
      )}

      {tab === "信号" && (
        <div className="space-y-3">
          {stockCode && <StockMarketSignalsPanel code={stockCode} />}
        </div>
      )}

      {tab === "AI" && (
        <div className="space-y-3">
          <StockAiCommentarySection stockId={stockId} />
        </div>
      )}

      {tab === "资讯" && (
        <div className="space-y-3">
          <NewsSection stockId={stockId} stockName={stock.name} />
          <AnnouncementsSection stockId={stockId} />
        </div>
      )}
    </div>
  );
}

// ========== 新闻 ==========
function NewsSection({ stockId, stockName }: { stockId: number; stockName: string }) {
  const [news, setNews] = useState<any[]>([]);

  useEffect(() => {
    fetch(`/api/stocks/${stockId}/news`)
      .then((r) => r.json())
      .then((d) => {
        if (Array.isArray(d)) setNews(d.slice(0, 5));
        else if (d?.news) setNews(d.news.slice(0, 5));
      })
      .catch(() => {});
  }, [stockId]);

  if (!news.length) return null;

  return (
    <Card>
      <CardHeader className="pb-2">
        <CardTitle className="text-sm flex items-center gap-1.5"><Newspaper className="w-3.5 h-3.5 text-muted-foreground" /> 新闻 · {stockName}</CardTitle>
      </CardHeader>
      <CardContent className="space-y-1.5">
        {news.map((n, i) => (
          <a
            key={i}
            href={n.url || '#'}
            target="_blank"
            rel="noopener"
            className="block hover:bg-muted p-1.5 rounded -mx-1.5"
          >
            <div className="text-xs leading-relaxed line-clamp-2">{n.title}</div>
            <div className="text-[10px] text-muted-foreground flex gap-2 mt-0.5">
              <span>{n.source}</span>
              <span>{n.pub_date || n.date}</span>
            </div>
          </a>
        ))}
      </CardContent>
    </Card>
  );
}
