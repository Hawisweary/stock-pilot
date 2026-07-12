# AI 基本面研究员 — 项目文档

> **AI Fundamental Researcher v4.0**  
> 自动化财务分析 + 八维度评分 + AI 辅助投研

---

## 一、项目定位

不做股价预测，只做**基本面分析**。强调**数据准确性**和**可解释性**。支持 A 股，架构可扩展。

核心价值：
- 多源数据抓取（腾讯 / yfinance / akshare）与任务持久化
- 五因子基本面引擎 + 八维度综合评分（资金/政策/情绪/技术/估值/新闻等）
- AI 辩论、深度同业、PDF RAG、研报导出
- 可视化 Dashboard 与实验模块（回测 / 组合 / 因子实验室，可开关）

---

## 技术栈（摘要）

| 层级 | 技术 |
|------|------|
| 前端 | Next.js + TypeScript + Tailwind + shadcn/ui |
| 后端 | FastAPI v4 + SQLite (WAL) + schema migrations |
| 评分 | `comprehensive_store` 统一 upsert / 回退 / 综合分重算 |
| 模块 | `AFR_ENABLE_*` 功能开关 + `/api/system/features` |

完整模块说明见 [docs/MODULES.md](docs/MODULES.md)。

---

## 二、系统架构

```
┌──────────────────────────────────────────────────────────┐
│                   前端 Dashboard                          │
│        Next.js 16 + Tailwind CSS + shadcn/ui             │
│                  (Port 3000)                             │
└──────────────────────┬───────────────────────────────────┘
                       │ REST API (rewrites proxy)
┌──────────────────────▼───────────────────────────────────┐
│                  FastAPI Backend                         │
│                (Port 8800, Python 3.11 venv-quant)       │
│  ┌──────────┬──────────┬──────────┬──────────────────┐  │
│  │ stocks   │  data    │ scores   │  dashboard       │  │
│  │ 股票CRUD │ 数据抓取  │ 因子评分  │  概览数据        │  │
│  ├──────────┼──────────┼──────────┼──────────────────┤  │
│  │ financials│   ai    │ analysis │                  │  │
│  │ 财务查询  │ AI分析  │ 行业/季度 │                  │  │
│  └──────────┴──────────┴──────────┴──────────────────┘  │
│                                                          │
│  ┌────────────────────────────────────────────────────┐  │
│  │              Services 服务层                        │  │
│  │  DataFetcher · FactorEngine · AiAnalyzer           │  │
│  │  DataProcessor · DataSources                       │  │
│  └────────────────────────────────────────────────────┘  │
└──────────────────────┬───────────────────────────────────┘
                       │
┌──────────────────────▼───────────────────────────────────┐
│                    数据层                                 │
│  ┌─────────────┐  ┌──────────┐  ┌───────────────────┐   │
│  │ 腾讯财经API  │  │  yfinance│  │  东财(akshare)    │   │
│  │ PE/PB/市值  │  │ 历史行情  │  │  财报/指标/分红    │   │
│  └─────────────┘  └──────────┘  └───────────────────┘   │
│                         │                                │
│               ┌─────────▼─────────┐                      │
│               │   SQLite 数据库   │                      │
│               │   7张数据表       │                      │
│               └───────────────────┘                      │
└──────────────────────────────────────────────────────────┘
```

---

## 三、技术栈

| 层级 | 技术 | 说明 |
|------|------|------|
| 前端 | Next.js 16 + TypeScript | App Router, Turbopack |
| UI | Tailwind CSS + shadcn/ui v4 | Card, Badge, Table, Tabs, Dialog |
| 图表 | recharts v2 | RadarChart, LineChart, BarChart |
| 图标 | lucide-react | 2000+ 图标 |
| 后端 | FastAPI + uvicorn | 异步API, 自动文档 |
| 数据库 | SQLite + WAL模式 | 零配置, 支持并发读 |
| 数据源 | yfinance | A股日线行情(.SS/.SZ) |
| 数据源 | 腾讯财经 (qt.gtimg.cn) | PE/PB/市值/中文名 |
| 数据源 | akshare + 东财API | 三表/指标/分红/融资融券 |
| AI | OpenAI/Claude API + 规则引擎 | 基本面分析报告 |

---

## 四、目录结构

```
ai-fundamental-researcher/
├── launch.sh                    # 一键启动+进程保活
├── backend/
│   ├── app.py                   # FastAPI 入口
│   ├── config.py                # 环境配置
│   ├── database.py              # SQLite 7表初始化
│   ├── api_models.py            # Pydantic 数据模型
│   ├── api_utils.py             # 数据库工具函数
│   ├── requirements.txt
│   ├── api/
│   │   ├── __init__.py          # 路由注册
│   │   ├── stocks.py            # 股票CRUD
│   │   ├── data.py              # 数据抓取(后台非阻塞)
│   │   ├── financials.py        # 财务数据查询
│   │   ├── scores.py            # 因子评分排名
│   │   ├── dashboard.py         # Dashboard概览
│   │   ├── ai.py                # AI基本面分析
│   │   └── analysis.py          # 行业对比+季度趋势
│   └── services/
│       ├── data_fetcher.py      # 多源数据抓取器
│       ├── data_processor.py    # 数据清洗转换
│       ├── data_sources.py      # 腾讯/东财直连API
│       ├── factor_engine.py     # 五因子评分引擎
│       └── ai_analyzer.py       # AI分析(OpenAI/规则)
├── frontend/
│   ├── app/
│   │   ├── layout.tsx           # 根布局+侧边栏
│   │   ├── page.tsx             # Dashboard
│   │   ├── stocks/
│   │   │   ├── page.tsx         # 股票列表
│   │   │   └── [code]/page.tsx  # 股票详情(4 Tab)
│   │   └── data/page.tsx        # 数据管理
│   ├── components/
│   │   ├── Layout.tsx           # 侧边栏导航
│   │   ├── StockCard.tsx        # 股票评分卡片
│   │   ├── FactorRadar.tsx      # 因子雷达图
│   │   ├── FinancialChart.tsx   # 通用趋势图
│   │   ├── StockTable.tsx       # 可搜索排序表格
│   │   ├── AiCommentary.tsx     # AI分析展示
│   │   ├── DataStatusBadge.tsx  # 数据新鲜度标识
│   │   └── ui/                  # shadcn/ui组件
│   └── lib/
│       ├── api.ts               # API客户端(类型安全)
│       └── utils.ts             # 工具函数
├── scripts/
│   ├── setup.sh                 # 一键安装脚本
│   └── seed_test_stocks.py      # 种子数据
└── data/afr.db                  # SQLite数据库文件
```

---

## 五、数据库设计（7张表）

### stocks — 股票主表
| 字段 | 类型 | 说明 |
|------|------|------|
| id | INTEGER PK | 主键 |
| code | TEXT UNIQUE | 股票代码(600519) |
| name | TEXT | 中文名称 |
| market | TEXT | A/HK/US |
| sector | TEXT | 板块 |
| industry | TEXT | 行业分类 |

### stock_daily_quotes — 每日行情
| 字段 | 类型 | 说明 |
|------|------|------|
| trade_date | TEXT | 交易日期 |
| open/high/low/close | REAL | OHLC |
| volume | REAL | 成交量 |
| amount | REAL | 成交额 |
| change_pct | REAL | 涨跌幅 |

### financial_reports — 财务报表
| 字段 | 类型 | 说明 |
|------|------|------|
| period_end_date | TEXT | 报告期末 |
| report_type | TEXT | annual/q1/q2/q3 |
| revenue/net_profit/eps | REAL | 利润表关键字段 |
| total_assets/equity/liabilities | REAL | 资产负债表 |
| operating_cf/investing_cf/financing_cf | REAL | 现金流量 |

### financial_indicators — 财务指标
| 字段 | 类型 | 说明 |
|------|------|------|
| pe_ttm / pb | REAL | 估值指标(腾讯财经) |
| roe / roa | REAL | 盈利能力(akshare) |
| gross_margin / net_margin | REAL | 利润率 |
| debt_to_equity / current_ratio | REAL | 偿债能力 |
| dividend_yield | REAL | 股息率(东财) |
| market_cap | REAL | 总市值(亿) |

### factor_scores — 因子评分
| 字段 | 类型 | 说明 |
|------|------|------|
| profitability_score | REAL | 盈利能力(0-100) |
| growth_score | REAL | 成长性(0-100) |
| safety_score | REAL | 安全性(0-100) |
| value_score | REAL | 估值(0-100) |
| composite_score | REAL | 综合加权评分 |

### data_fetch_log — 抓取日志
| 字段 | 说明 |
|------|------|
| data_type | quote/financial/indicator |
| status | success/error |
| records_count | 抓取行数 |
| duration_ms | 耗时(毫秒) |

### ai_analyses — AI分析记录
| 字段 | 说明 |
|------|------|
| summary | 一句话总结 |
| strengths_json | 优势列表(JSON) |
| weaknesses_json | 风险列表(JSON) |
| factor_commentary_json | 因子解读(JSON) |
| overall_rating | 推荐/中性/谨慎 |

---

## 六、因子评分模型（v2.0）

五因子加权评分（0-100分），默认在**申万一级同行业**内做百分位排名（不足 3 只时回退全跟踪池）：

| 因子 | 默认权重 | 子指标 |
|------|------|--------|
| **盈利能力 (Quality)** | 30% | ROE + 毛利率 + 净利率 + ROIC |
| **成长性 (Growth)** | 25% | 3年营收CAGR + 净利CAGR + EPS增长 |
| **估值 (Value)** | 20% | PE/PB 倒数 + 股息率（来自 `valuation_snapshots`） |
| **基本面动量 (Momentum)** | 10% | 营收/净利/EPS 同比改善（**不用股价**） |
| **安全性 (Risk)** | 15% | 负债率倒数 + 流动比率 + 盈利波动 |

标准化方法：**百分位排名** → 0-100；反向指标(PE/PB/负债)取 `100 - 百分位`。  
重算 API：`POST /api/scores/recalculate?benchmark=industry|watchlist`

---

## 七、API接口列表

### 股票管理
| 方法 | 路径 | 说明 |
|------|------|------|
| GET | `/api/stocks` | 股票列表 |
| POST | `/api/stocks` | 添加股票（自动后台抓数据） |
| GET | `/api/stocks/{id}` | 股票详情+评分+指标 |
| DELETE | `/api/stocks/{id}` | 删除(软删除) |

### 数据抓取
| 方法 | 路径 | 说明 |
|------|------|------|
| POST | `/api/data/fetch/{id}` | 启动单股抓取（立即返回） |
| GET | `/api/data/fetch/{id}/status` | 单股进度（`success`/`partial`/`error`） |
| POST | `/api/stocks/{id}/fetch` | 同上（兼容入口） |
| POST | `/api/data/fetch-all?mode=incremental` | 后台增量批量抓取（默认，日常） |
| POST | `/api/data/fetch-all?mode=full` | 深度全量抓取（财报季/数据修复） |
| GET | `/api/data/fetch-status` | 查询批量抓取进度（含 `mode` / `warning`） |
| GET | `/api/data/fetch-step-status` | 每股步骤状态（跳过/熔断/待修复） |
| POST | `/api/data/fetch-reset` | 重置僵死批量任务 |
| GET | `/api/data/status` | 数据新鲜度 |
| GET | `/api/data/logs` | 抓取日志 |

### 财务数据
| 方法 | 路径 | 说明 |
|------|------|------|
| GET | `/api/stocks/{id}/financials` | 年报数据 |
| GET | `/api/stocks/{id}/indicators` | 历史指标 |
| GET | `/api/stocks/{id}/quotes` | 每日行情 |

### 因子评分
| 方法 | 路径 | 说明 |
|------|------|------|
| GET | `/api/scores/ranking` | 综合排名 |
| GET | `/api/stocks/{id}/scores` | 历史评分 |
| POST | `/api/scores/recalculate` | 重算全部评分 |

### AI分析
| 方法 | 路径 | 说明 |
|------|------|------|
| POST | `/api/ai/analyze/{id}` | 生成AI分析报告 |
| GET | `/api/ai/history/{id}` | 历史分析记录 |

### 行业分析
| 方法 | 路径 | 说明 |
|------|------|------|
| GET | `/api/stocks/{id}/peers` | 同行业对比排名 |
| GET | `/api/analysis/{id}/deep-peers` | 深度同业（分位+市值过滤） |
| GET | `/api/stocks/{id}/quarterly` | 季度趋势+AI变化检测 |

### 财报 RAG
| 方法 | 路径 | 说明 |
|------|------|------|
| POST | `/api/rag/stocks/{id}/upload` | 上传 PDF 年报 |
| GET | `/api/rag/stocks/{id}/documents` | 已入库文档 |
| POST | `/api/rag/stocks/{id}/ask` | 财报问答 |

模块说明见 [docs/MODULES.md](docs/MODULES.md)。

### Dashboard
| 方法 | 路径 | 说明 |
|------|------|------|
| GET | `/api/dashboard/overview` | 概览数据 |
| GET | `/api/dashboard/top-stocks` | Top N排名 |

---

## 八、前端页面

### Dashboard (`/`)
- 概览卡片：跟踪股票数、数据正常数、逾期数、平均评分
- 数据逾期黄色警告条
- 一键刷新按钮（重算评分+后台数据更新）
- Top 3 股票卡片（含迷你评分条）
- 因子对比柱状图

### 股票列表 (`/stocks`)
- 可搜索排序的股票表格（代码/名称/行业/五因子+综合评分）
- 添加股票对话框（输入代码→自动抓取数据）
- 点击行跳转详情页

### 股票详情 (`/stocks/[code]`)
4个Tab：

**总览**
- 因子雷达图
- AI基本面分析（支持OpenAI/Claude/规则引擎fallback）
- 年度营收/利润趋势图

**财务数据**
- 年度营收/净利润柱状图
- 年度财务明细表（营收/净利/EPS/总资产/净资产）

**行业对比** (v2.0)
- 同行业股票排名表
- 五因子+综合横向对比
- 当前股票蓝色高亮

**季度趋势** (v2.0)
- 营收/净利润/现金流季度柱状图
- AI自动趋势检测：营收下滑、毛利率压缩、现金流恶化
- 季度明细表含环比变化百分比

### 数据管理 (`/data`)
- 数据新鲜度表格（代码/名称/行情日期/财报日期/状态）
- 全部抓取按钮（后台非阻塞）
- 单股抓取按钮
- 评分重算按钮
- 抓取结果日志

---

## 九、数据源汇总

| 数据 | 来源 | API/库 |
|------|------|--------|
| 日线行情 | Yahoo Finance | yfinance (免费) |
| PE/PB/市值/中文名 | 腾讯财经 | qt.gtimg.cn (免费HTTP) |
| 利润表/资产负债表/现金流 | 东财 | akshare → EastMoney API |
| 财务指标(ROE/毛利率等) | 东财 | akshare |
| 分红历史/融资融券 | 东财datacenter | datacenter-web API |
| AI分析 | OpenAI/Claude | GPT-4o-mini (可选) |

---

## 十、Python 运行环境（必读）

后端与所有 `backend/scripts/` 脚本要求 **Python ≥ 3.10**。项目统一使用根目录下的 **`venv-quant`（推荐 3.11）**，不要用 macOS Command Line Tools 自带的 **Python 3.9**（会因 `float | None` 等 3.10+ 语法在 import 阶段报错）。

```bash
# 首次：创建量化子环境（LightGBM / Qlib worker）
bash scripts/setup_venv_quant.sh
venv-quant/bin/pip install -r backend/requirements.txt

# 验证版本
venv-quant/bin/python --version   # 应显示 3.10 或 3.11
```

| 场景 | 正确命令 |
|------|----------|
| 启动后端 | `./launch.sh start`（内部已用 `venv-quant/bin/python`） |
| 手动启后端 | `venv-quant/bin/python backend/app.py` |
| 运行 backend 脚本 | `bash backend/scripts/run_py.sh scripts/add_theme_watchlist.py` |
| ML 训练 worker | `venv-quant/bin/python backend/workers/qlib_train_worker.py '{}'` |

`launch.sh` 可通过环境变量覆盖解释器：`VENV_QUANT=/path/to/venv-quant/bin/python`。

更多说明见 [docs/VENV_QUANT.md](docs/VENV_QUANT.md)。

---

## 十一、启动方式

### 一键启动（推荐）
```bash
cd ai-fundamental-researcher
./launch.sh start    # 启动+30秒进程保活（后端自动用 venv-quant）
./launch.sh status   # 查看状态
./launch.sh stop     # 停止
```

### 手动启动
```bash
# 终端1: 后端
venv-quant/bin/python backend/app.py    # http://localhost:8800

# 终端2: 前端
cd frontend && npm run dev              # http://localhost:3000
```

### 首次安装
```bash
# 后端环境 + 依赖
bash scripts/setup_venv_quant.sh
venv-quant/bin/pip install -r backend/requirements.txt

# 前端依赖
cd frontend && npm install

# 种子数据（示例脚本，若存在）
venv-quant/bin/python scripts/seed_test_stocks.py
```

---

## 十二、已知限制

| 问题 | 影响 | 解决方案 |
|------|------|----------|
| 误用系统 Python 3.9 跑脚本 | `data_processor` 等 import 失败，onboard 全失败 | 统一用 `venv-quant` 或 `backend/scripts/run_py.sh` |
| Clash TUN模式拦截eastmoney部分API | 东财push2不可用 | 已切换腾讯财经+yfinance |
| 季度财报偶发失败 | 东财季报接口不稳定 | 已合并年度+季度抓取；数据页可看「季报」步骤状态 |
| PE/PB 与财报指标 | 已拆分为 `valuation_snapshots` 与 `financial_indicators` | 估值带 `as_of_date` |
| 行业名yfinance返回英文 | 部分股票显示英文行业 | 已有中文映射表 |
| 后端偶尔崩溃 | 服务中断 | launch.sh 30秒自动重启 |

---

## 十三、Roadmap

- [x] Phase 1: 基础数据+后台+前端Dashboard
- [x] Phase 1.5: PE/PB估值+AI分析+定时调度
- [x] Phase 2A: 行业对比+季度趋势分析
- [x] Phase 2B: 财报 PDF 上传 + RAG 问答（`/api/rag`）
- [x] Phase 3: 同行业深度对比（`/api/analysis/{id}/deep-peers`）
- [ ] Phase 4: 跨市场扩展（美股+港股）— **暂不实现**
