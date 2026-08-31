# Stock Pilot

[中文](README.md) · **English**

A personal **quant research + paper-trading platform** for China A-shares (and now US equities): a complete loop from multi-source data ingestion, a ten-dimension composite scoring system, factor mining and backtesting, to strategy-driven paper portfolios and automated scheduling.

> The desktop app (Tauri) is branded **Stock Pilot**; the codebase / database keep the historical codename `afr`.

---

## Core Capabilities

### 📊 V5 Ten-Dimension Scoring
Every listed A-share is graded across ten dimensions (−2…+2), weighted into `composite_v5`, with **weak-link penalty** and **veto** rules:

| Dimension | What it captures |
|------|------|
| Fundamentals | Market-wide percentiles of profit / growth / solvency / cash flow |
| Quality | Accruals, cash quality, ROE stability |
| Industry momentum | Sector money flow and relative strength |
| Capital flow | Main-force net inflow, turnover, volume, shareholder count |
| Valuation | PE/PB/PS vs. industry and own historical percentile |
| Technical | MA / MACD / RSI / price-volume rule engine |
| Market regime | Index trend and market breadth |
| Policy | Industry policy-event impact |
| News | Per-stock news sentiment (LLM + keywords) |
| Sentiment | Cross-sectional percentile of volatility + momentum |

### 🧪 Factor Lab
- 70+ factors: scoring-dimension factors, technical factors (momentum / low-vol / price-volume / reversal / trend), composite factors, GP genetic-programming mining, custom time-series expressions
- IC / IR analysis, layered monotonicity, long-short NAV, IC decay, correlation matrix
- Factor composition (equal-weight / IC_IR-weighted / rolling-optimal)
- ML / Qlib prediction integration

### 📈 Backtest Engine
T+1 open-price fill model, configurable slippage and fees, price-limit constraints, trading-calendar rebalancing; outputs cumulative return, max drawdown, Sharpe (with risk-free rate), Information Ratio, monthly heatmap, holdings distribution.

### 💼 Paper Trading
Multi-strategy portfolios (composite / momentum / technical / valuation / quality / capital / sector-rotation / turtle …), automated rebalancing, turtle-channel exits, live / EOD pricing, slippage and commissions, FIFO realized P&L.

### 🔬 Alpha Factors v1
Earnings surprise, three-way money resonance (L2 block orders + dragon-tiger list + Stock Connect, ≥2 aligned), industry-neutral valuation — kept as standalone high-conviction signals, not merged into the V5 composite.

### 🇺🇸 US Stocks & US ETFs
- **Whole US market** (dual-track Tiingo + yfinance; auto-fallback to Tiingo when yfinance is throttled): list, search, sort by dollar volume
- **Detail page**: market cap / PE / PB / PEG, analyst price targets & rating distribution, firm-by-firm actions, **analyst-accuracy backtest** (historical price targets ≥12 months old vs. split-adjusted realized price — shows whether analysts have historically over- or under-estimated this stock), **financials & earnings** (revenue / net income / EPS trend + earnings surprise + next earnings date), **forward consensus / institutional & insider holdings / dividends**, **AI Chinese summary**
- **Candlesticks**: daily/weekly/monthly/yearly + adjusted/raw + MACD/KDJ/RSI/BOLL/ATR, green-up-red-down (US convention)
- **US ETFs** (curated majors): AUM / expense ratio / returns, top-10 holdings, sector weights, stock/bond/cash allocation, AI Chinese summary

### 🌍 Global Markets
One-screen overview of global indices / FX / commodities / US Treasury yields / crypto, with multi-period changes (1d / 1w / 1m / YTD) and sortable columns; plus a **world map** placing each country's index by lat/long, colored by the day's move, click-through to candlesticks.

### 🎓 Beginner Tutorial & AI Assistant
- **Learning Center**: trading basics / reading the data / trading strategies / using this software / risk & discipline, from scratch (with candlestick and adjustment diagrams)
- **Inline glossary**: a hover "?" next to jargon on detail pages
- **AI learning assistant**: a global floating Q&A (DeepSeek), strictly scoped to education — no stock picks, no price predictions

### 🔎 Interface Health Checks
A-share live endpoints (EastMoney / akshare) tend to fail silently when upstream changes; a built-in health-check script periodically probes key endpoints and flags empties, guarding against the "feature is there but returns empty" class of bugs.

---

## Data Sources

- **Tushare Pro** (primary for A-shares): daily quotes, financials, valuation, money flow, dragon-tiger list, indices, macro, halts, industry classification
- **AKShare / EastMoney** (A-share supplements): chip distribution, new highs/lows, multi-period dragon-tiger, shareholder counts, share unlocks, block trades, market overview, per-stock news
- **Tiingo + yfinance** (US / global): US daily & adjusted prices, financial statements / earnings surprise / forward consensus / institutional & insider holdings / dividends (yfinance, free); global indices / FX / commodities / Treasuries / crypto (yfinance); US ETF deep fundamentals (Tiingo)
- **DeepSeek LLM**: news sentiment, announcement classification, US/ETF Chinese summaries, education assistant

The A-share primary quote source is Tushare's daily batch pull (fast, stable, immune to anti-scraping); on failure it falls back to per-stock tencent / eastmoney. US quotes use yfinance batch primarily with per-ticker Tiingo backfill (self-healing for throttled megacaps like NVDA).

---

## Architecture

```
┌─ Desktop shell (Tauri / Rust)  →  loads http://127.0.0.1:3002
├─ Frontend (Next.js 16, :3002)  →  proxies /api/* to the backend
└─ Backend (FastAPI, :8800, venv-quant / Python 3.11)
     ├─ SQLite main afr.db (WAL) + cache.db (decouples high-freq log/health writes) + separate us.db (US/ETF/global, isolates lock contention)
     ├─ Scheduler: daily (15:30) / nightly (02:00) / weekly auto pipelines
     │    · persisted state + catch-up after missed windows
     │    · heavy jobs isolated in subprocesses, never block the API
     │    · WAL watchdog prevents unbounded growth
     └─ launch.sh keep-alive daemon
```

---

## Quick Start

### Requirements
- Python 3.11 (bundled `venv-quant`)
- Node.js (frontend)
- Tushare Pro token, DeepSeek API key (Tiingo token optional, for US ETF fundamentals)

### Configure secrets
Copy `.env.example` to `.env` and fill in (**never commit `.env` / `.envrc` — gitignored**):
```bash
DEEPSEEK_API_KEY=sk-...
TUSHARE_TOKEN=...
TIINGO_TOKEN=...   # optional
```

### Run
```bash
# One-shot: backend + frontend + keep-alive daemon (recommended)
./launch.sh daemon

# Status / stop
./launch.sh status
./launch.sh stop
```
- Frontend: http://localhost:3002
- Backend API docs: http://localhost:8800/docs

Desktop: after building, run `/Applications/Stock Pilot.app` (Tauri auto-starts `launch.sh daemon` and navigates once the frontend is ready).

---

## Layout

```
backend/      FastAPI backend (api/ routes, services/ logic, scripts/ batch jobs, migrations.py)
frontend/     Next.js frontend (app/ pages, components/, lib/api.ts)
src-tauri/    Tauri desktop shell (Rust)
data/         SQLite databases and backups (gitignored)
launch.sh     one-shot launch / keep-alive script
```

---

## Notes

For personal research only — **not investment advice**. Data depends on the availability and quotas of third-party interfaces.
