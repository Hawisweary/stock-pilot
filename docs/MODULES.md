# 模块说明

## 核心（生产可用）

| 模块 | 路径 | 说明 |
|------|------|------|
| 股票与数据 | `/stocks`, `/data` | 自选股、统一抓取任务、新鲜度 |
| 五因子评分 | `/scores` | 行业百分位基准、`recalculate` |
| 财务与估值 | `/financials`, `valuation_snapshots` | 财报指标与 PE/PB 拆分 |
| Dashboard | `/` | 概览、排名、一键刷新 |
| AI 分析 | `/ai` | 基本面解读、趋势告警缓存 |
| 投研分析 | `/analysis` | 季度异动、PE 分位、**深度同业** |

## 实验功能（Beta）

可通过环境变量关闭对应路由：

| 变量 | 默认 | 模块 |
|------|------|------|
| `AFR_ENABLE_BACKTEST` | true | 回测 `/backtest` |
| `AFR_ENABLE_PORTFOLIO` | true | 模拟交易 `/portfolio` |
| `AFR_ENABLE_FACTOR_LAB` | true | 因子工厂、因子分析 |
| `AFR_ENABLE_ADVANCED` | true | 高级分析 API |
| `AFR_ENABLE_PREMIUM` | true | Premium 扩展 |
| `AFR_ENABLE_RAG` | true | 财报 PDF RAG `/api/rag` |

前端导航带 **Beta** 角标的功能属于实验模块，数据完整性不如核心管道。

## 数据抓取

- **唯一任务系统**：`POST /api/data/fetch/{id}` 与 `POST /api/stocks/{id}/fetch` 共用 `fetch_jobs` 表
- **状态**：`success` | `partial` | `error` | `pending`
- **partial**：行情或财报部分成功，见 `errors[]` 明细

## 财报 RAG（Phase 2B）

1. `POST /api/rag/stocks/{id}/upload` — 上传 PDF（≤25MB）
2. `GET /api/rag/stocks/{id}/documents` — 已入库文档
3. `POST /api/rag/stocks/{id}/ask` — 问答（有关键词检索 + 可选 LLM）

文件存储：`data/reports/{stock_id}/`

## 深度同业（Phase 3）

`GET /api/analysis/{stock_id}/deep-peers?market_cap_band=0.5`

- 按 `industry_sw` 选同行
- 默认保留市值 ±50% 范围内的公司
- 返回 PE/ROE/综合评分等在行业内的**分位**与优劣势摘要

## 未包含

- **港美股**：当前仅 A 股主流程；架构预留 `market` 字段，未实现多市场抓取。
