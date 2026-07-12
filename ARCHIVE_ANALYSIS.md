# 📊 a-stock-data + TradingAgents 详细分析报告

> 评估日期: 2026-05-23 | 项目: A股投研系统 v3.0

---

## 一、a-stock-data V3.1

### 来源
GitHub `simonlin1212/a-stock-data`，V3.1（2026-05-19 验证），star 量级 1K+。

**核心特性**：零 akshare 依赖，全部直连原始 HTTP API（腾讯/东财/同花顺/百度/新浪/巨潮），28 个端点，不封 IP。

### 七层数据架构

```
行情层 (3端点)
├── mootdx    → TCP 7709 K线 + 五档盘口 + 逐笔成交
├── 腾讯财经   → PE/PB/市值/换手率/涨跌停价/指数/ETF
└── 百度K线    → 自带 MA5/10/20 均线

研报层 (3端点)
├── 东财 reportapi → 研报列表 + PDF下载 + 评级 + 三年EPS
├── 同花顺 THS    → 一致预期EPS
└── iwencai       → NL语义搜索 (需API Key)

信号层 (7端点)
├── 同花顺热点    → 强势股 + 题材归因 reason tags (73ms)
├── 同花顺北向    → 沪股通/深股通分钟流向 + 自缓存历史
├── 百度概念      → 行业/概念/地域三维分类
├── 东财资金流    → 个股主力散户分钟级
├── 龙虎榜席位    → 买卖TOP5 + 机构动向
├── 全市场龙虎榜  → 净买额排名
├── 限售解禁日历  → 历史+未来90天
└── 行业板块排名  → 东财行业涨跌排名

资金面/筹码层 (5端点)
├── 融资融券明细  → 融资余额/买入/偿还
├── 大宗交易      → 成交价/量 + 买卖营业部
├── 股东户数变化  → 筹码集中度
├── 分红送转历史  → 每股派息/送股/转增
└── 120日资金流   → 主力/散户日级净流入

新闻层 (3端点)
├── 东财个股新闻  → JSONP 接口
├── 财联社快讯    → cls.cn 电报
└── 东财全球资讯  → 7x24 财经

基础数据层 (4端点)
├── mootdx finance → 37字段季报快照
├── mootdx F10     → 9大类公司资料
├── 东财个股信息   → 行业/股本/市值/上市日期
└── 新浪财报三表   → 资产负债表/利润表/现金流

公告层 (2端点)
├── 巨潮 cninfo    → 全文检索+下载
└── mootdx F10     → 最新公告摘要
```

---

## 二、TradingAgents V0.2.5

### 来源
GitHub `TauricResearch/TradingAgents`，学术级多智能体框架（arXiv:2412.20138）。

### 核心架构

```
┌─────────────────────────────────────────────┐
│              TradingAgents 框架              │
├───────────┬─────────────┬───────────────────┤
│ 分析师层   │ 研究员层     │ 决策层             │
│           │             │                   │
│ 🔬 基本面  │ 🐂 多头研究  │ 📋 研究经理        │
│ 📊 技术面  │ 🐻 空头研究  │ 💼 组合经理        │
│ 📰 新闻    │             │ 📈 交易员          │
│ 💬 社媒    │ 辩论层       │                   │
│ 🎯 情绪    │ ⚔️ 激进辩论  │ 工具层             │
│           │ 🛡️ 保守辩论  │ 🔧 股票工具        │
│           │ ⚖️ 中立辩论  │ 📊 基本面数据      │
│           │             │ 🧠 记忆系统        │
└───────────┴─────────────┴───────────────────┘
```

### 关键组件

| 组件 | 文件 | 功能 |
|------|------|------|
| 基本面分析师 | `fundamentals_analyst.py` | 五大维度自动评分 (财务/估值/安全/成长/动量) |
| 情绪分析师 | `sentiment_analyst.py` | grounded 情绪分析 v0.2.5 新特性 |
| 新闻分析师 | `news_analyst.py` | 新闻聚合+影响评估 |
| 市场分析师 | `market_analyst.py` | 宏观+板块+技术综合 |
| 多空研究员 | `bull/bear_researcher.py` | 对抗性研究辩论 |
| 风险辩论 | `aggressive/conservative/neutral_debator.py` | 三视角风险评估 |
| 组合经理 | `portfolio_manager.py` | 结构化输出 (v0.2.4) |
| 交易员 | `trader.py` | 最终决策+仓位建议 |
| LangGraph | `trading_graph.py` | 工作流编排+checkpoint |

### 技术栈
- LangGraph 工作流引擎
- 多 LLM 支持 (GPT/Claude/Gemini/DeepSeek/Qwen/GLM)
- 结构化输出 (Pydantic schema)
- 记忆系统 + 持久化决策日志

---

## 三、匹配分析

### ✅ a-stock-data 直接可用的模块

| 端点 | 当前项目状态 | 建议动作 |
|------|-------------|---------|
| **mootdx 财务快照** | ❌ 财务标签页仅展示空表 | 📥 直接接入 `mootdx_client.finance()` → 填充 `financial_statements` 表 |
| **新浪财报三表** | ❌ 无资产负债表/现金流 | 📥 直接接入 `sina_financial_report()` |
| **同花顺热点 + 题材归因** | ❌ 无 | 📥 新 API: `GET /api/signals/hotspots` → Dashboard 卡片 |
| **龙虎榜席位** | ❌ 无 | 📥 新 API: `GET /api/signals/dragon-tiger/{code}` |
| **限售解禁日历** | ❌ 无 | 📥 自动化预警: 未来30天有解禁 → alerts 推送 |
| **融资融券明细** | ⚠️ 东财 sync 有但未填充 | 🔧 替换为 `eastmoney_datacenter("RPTA_WEB_RZRQ_GGMX")` |
| **股东户数变化** | ❌ 无 | 📥 筹码集中度分析 |
| **分红送转历史** | ❌ 无 | 📥 估值面增加股息率维度 |
| **大宗交易** | ❌ 无 | 📥 资金面补充 |
| **120日资金流** | ⚠️ 资金面仅简单统计 | 🔧 替换为 `stock_fund_flow_120d()` 获取完整历史 |
| **行业板块排名** | ⚠️ 有 but 数据浅 | 🔧 替换为东财 push2 `m:90+t:2` |
| **北向资金** | ⚠️ 东财 try 但可能 NaN | 🔧 改用同花顺 hsgtApi + 本地缓存 |
| **估值公式** | ❌ 无 PEG/PE消化 | 📥 `forward_pe()` `calc_peg()` `pe_digestion()` 纳入评分 |
| **巨潮公告** | ❌ 无 | 📥 API: `GET /api/announcements/{code}` |

### ✅ TradingAgents 直接可用的模块

| 组件 | 当前项目状态 | 建议动作 |
|------|-------------|---------|
| **多角色辩论框架** | ⚠️ 有简单版（bull/bear/judge） | 🔧 升级为 5 视角：基本面/技术/新闻/情绪/市场分析师 + 风险辩论 |
| **LangGraph 工作流** | ❌ 无 | 📥 用 LangGraph 编排自动化流水线 |
| **结构化输出** | ⚠️ LLM 返回不稳定 | 🔧 用 Pydantic schema 约束 LLM 输出 |
| **记忆系统** | ❌ 无 | 📥 交易决策日志+复盘 |
| **组合管理决策** | ❌ 无 | 📥 portfolio_manager.py 给模拟组合加 AI 建议 |
| **评分框架** | ✅ 已有 8 维度 | 🤝 对齐——TA 的 5 维度可映射到我们的 8 维度 |

---

## 四、优先级实施建议

### P0 — 本周末可完成 (4h)

| 序号 | 动作 | 来源 | 工时 |
|------|------|------|------|
| 1 | 接入 mootdx 财务快照 → 填充财务报表 | a-stock-data | 1h |
| 2 | 同花顺热点题材归因 → Dashboard 信号卡片 | a-stock-data | 1h |
| 3 | 龙虎榜 + 解禁 + 融资融券 → 股票详情新标签 | a-stock-data | 1h |
| 4 | 用 Pydantic schema 约束 LLM 输出 | TradingAgents | 1h |

### P1 — 下周可完成 (6h)

| 序号 | 动作 | 来源 | 工时 |
|------|------|------|------|
| 5 | 新浪财报三表 → 利润表/负债表/现金流表 | a-stock-data | 1h |
| 6 | 股东户数 + 分红 + 大宗交易 → 筹码层 API | a-stock-data | 1h |
| 7 | PEG/PE消化 估值公式 → 基本面评分增强 | a-stock-data | 1h |
| 8 | 5 分析师辩论 → 替换当前 3 角色 | TradingAgents | 2h |
| 9 | 北向资金同花顺替代 → 东财数据盲区修复 | a-stock-data | 1h |

### P2 — 后续 (4h)

| 序号 | 动作 | 来源 | 工时 |
|------|------|------|------|
| 10 | LangGraph 编排自动化流水线 | TradingAgents | 2h |
| 11 | 交易记忆系统 + 决策日志 | TradingAgents | 1h |
| 12 | iwencai NL 研报搜索集成 | a-stock-data | 1h |

---

## 五、量化收益预估

| 维度 | 当前 | 升级后 |
|------|------|--------|
| 数据端点 | ~12 | **28+** |
| 评分因子 | 8 | 8 + PEG/消化/筹码/股息 |
| AI 分析角色 | 3 (bull/bear/judge) | **5 分析师 + 3 辩论** |
| 信号覆盖 | 情绪/资金/政策 | + 热点归因/龙虎榜/解禁/融资融券/北向 |
| 数据刷新率 | 日级 | 日级 + 分钟级资金流(盘中) |
| LLM 输出稳定性 | 自由格式 | Pydantic 结构化约束 |

---

## 六、技术依赖

```bash
# a-stock-data 新依赖
pip install mootdx stockstats

# TradingAgents (按需)
pip install langgraph pydantic
```

两个项目均零额外 API Key 需求（除 iwencai 可选），全部走免费公开接口。
