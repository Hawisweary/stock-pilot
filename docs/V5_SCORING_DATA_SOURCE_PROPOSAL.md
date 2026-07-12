# V5 打分系统 — 数据源接入方案书

> 版本：v1.0  
> 日期：2026-06-05  
> 状态：**Phase 1+2（~80%）已落地；Phase 3（~20%）待评审**  
> 范围：**不含 Tushare Pro、Wind/iFinD** 等商业终端（EPS 一致预期修正除外，见 §6）  
> 关联文档：  
> - [ONBOARDING_AND_DATA_SOURCE_PROPOSAL.md](./ONBOARDING_AND_DATA_SOURCE_PROPOSAL.md)（东财/腾讯主路径）  
> - [BATCH_DIMENSION_SCORE_PROPOSAL.md](./BATCH_DIMENSION_SCORE_PROPOSAL.md)（八维 batch-fill）  
> - V5 打分框架（机构级十维 + 短板惩罚 + IC 监控，见用户方案书 2026-06-05）

---

## 0. 执行摘要

| 阶段 | 覆盖 | 状态 | 说明 |
|------|------|------|------|
| **Phase 1+2** | ~80% | ✅ **已落地** | 东财/腾讯/巨潮/自算；`POST /api/v5/sync` 一键同步 |
| **Phase 3** | ~20% | ⏳ 待开工 | 盈利预测/EPS 修正、事件分类、政策超额、风险否决、情绪代理 |
| **算法层** | 打分闭环 | ❌ 未做 | 十维 -2~+2、短板惩罚、一票否决、IC 5/20/60 → `comprehensive_scores` |

**原则：** 与 Track B 一致——**东财 datacenter + 腾讯 + 巨潮 + 自算** 为主；akshare 仅 lazy fallback；商业源仅 EPS 一致预期一项可选采购。

**一句话：** 数据底座已够支撑 V5「骨架」；剩余 20% 以**免费代理 + 规则/LLM** 为主，仅行业 EPS 修正在 IC 不达标时考虑朝阳永续。

---

## 1. V5 十维与数据源映射

| V5 维度 | 权重 | 核心指标 | Phase 1+2 覆盖 | Phase 3 缺口 |
|---------|------|----------|----------------|--------------|
| 基本面 | 20% | 单季营收/利润同比、环比档位 | ✅ `stock_v5_metrics` 自算 | 档位映射进 scorer |
| 质量因子 | 15% | CFO/NP、应计、负债率 | ✅ `stock_v5_metrics` | AR 周转（需 `accounts_receivable` 补抓） |
| 行业景气 | 10% | EPS 修正、板块资金流、相对强度 | ⚠️ 2/3（资金流+RS） | **EPS 一致预期修正** |
| 资金面 | 15% | 主力/两融/龙虎榜 | ⚠️ 数据有、**未进 capital_scorer** | 北向降级为持股变化 |
| 估值 | 10% | PE 分位、PEG、宏观调节 | ✅ 估值快照 + 宏观 | 3 年 PE 历史序列 |
| 技术面 | 10% | 趋势/量价/位置 | ✅ `tech_analysis_cache` | 五档标准化 |
| 大盘环境 | 10% | 指数技术 60% + 宏观 40% | ✅ 宏观四件套 + 指数 K | 独立 `market_env_score` 维 |
| 政策面 | 5% | 等级 × 行业资金响应 | ⚠️ 关键词 only | **政策后 T+20 行业超额** |
| 新闻面 | 3% | 事件驱动分类 | ⚠️ 情感 only | **公告/新闻 event_type** |
| 情绪面 | 2% | 股吧逆向 | ❌ | **代理指标**（换手+涨跌停+新闻过热） |

---

## 2. Phase 1+2 — 已落地（~80%）

### 2.1 宏观四件套

| 指标 | 主源 | 说明 |
|------|------|------|
| PMI / CPI / M2 / LPR / Shibor | 东财 datacenter RPT | 原有 |
| 社融代理 | `RPT_ECONOMY_RMB_LOAN` | 新增人民币贷款作代理，非完整社融口径 |
| 10Y 国债收益率 | 中债信息网 HTML 解析 | `yield.chinabond.com.cn` |
| USD/CNY | 外汇交易中心 JSON | `chinamoney.com.cn`，东财离岸价备用 |

**模块：** `eastmoney_macro.py` · `macro_sync.py`  
**表：** `macro_indicators` 扩展列（migration v23）  
**API：** `POST /api/macro/sync` · `GET /api/macro/indicators`

### 2.2 资金面数据

| 指标 | 主源 | 持久化 |
|------|------|--------|
| 个股主力 5/20 日 | 东财 push2his `fflow/daykline` | `stock_fund_flow_daily` |
| 板块净流入 + RS vs 沪深300 | 东财 push2 `m:90+t:2` | `sector_fund_flow_daily` |
| 两融 | 东财 datacenter（已有） | `eastmoney_margin` |
| 龙虎榜 | 东财 RPT（已有） | 实时 API，可选日表 |

**模块：** `fund_flow_sync.py` · `sector_fund_flow_sync.py`  
**API：** `GET /api/v5/fund-flow/{id}` · `GET /api/v5/sector-fund-flow`

### 2.3 基本面 / 质量 V2

| 指标 | 来源 | 表 |
|------|------|-----|
| 单季营收/利润 YoY、QoQ delta | `financial_reports` 季度自算 | `stock_v5_metrics` |
| CFO/NP、Accrual、负债 vs 行业 | 三表 + `financial_indicators` | `stock_v5_metrics` |
| 应收账款 | 资产负债表科目（待全量补抓） | `financial_reports.accounts_receivable` |

**模块：** `quality_metrics_calc.py`  
**API：** `GET /api/v5/metrics/{stock_id}`

### 2.4 行业归属

| 字段 | 主源 | 说明 |
|------|------|------|
| `industry_sw` | 申万一级（已有） | push2 / ADATA |
| `industry_sw2` | 申万二级或东财板块名 | `industry_l2_sync.py` |

### 2.5 一键同步与运维

```bash
# 全量 V5 数据源（宏观 + 板块流 + 个股主力 + L2 + Quality V2）
curl -X POST http://127.0.0.1:8800/api/v5/sync -H 'Content-Type: application/json' -d '{}'

# 分项跳过（调试）
curl -X POST http://127.0.0.1:8800/api/v5/sync -d '{"skip_fund_flow":true}'
```

**编排：** `v5_data_sync.py`  
**调度：** `scheduler.run_daily_tasks` 每日 15:30（宏观单独 sync + V5 其余步）  
**前端：** 数据管理 / 市场页 **「V5数据源」** 按钮；宏观面板展示社融/10Y/汇率

### 2.6 Schema（migration v23）

| 表 / 列 | 用途 |
|---------|------|
| `stock_fund_flow_daily` | 个股主力序列 |
| `sector_fund_flow_daily` | 板块资金流 + RS |
| `stock_v5_metrics` | Quality V2 + 单季增速 |
| `stocks.industry_sw2` | 行业二级归属 |
| `macro_indicators.*` | 社融代理、10Y、汇率 |
| `financial_reports.accounts_receivable` | AR 周转（字段已建，抓取待强化） |

### 2.7 已知限制（Phase 1+2）

1. **社融** 为新增信贷代理，否决条件「社融连降两月」需用 `social_financing_yoy` 序列判定，口径与央行社融有偏差。  
2. **东财 push2** 偶发断连 → 板块流 / 行业 L2 可能部分失败，需重试 `POST /api/v5/sync`。  
3. **资金面数据未写入 `capital_scorer`**，八维资金分仍用换手/量能代理。  
4. **十维档位、短板惩罚、一票否决** 尚未接入 `comprehensive_scores`（属算法层，见 §5）。

---

## 3. Phase 3 — 剩余 ~20% 接入方案

### 3.1 总策略：三层接入

```
┌─────────────────────────────────────────────────────────────┐
│ L1 免费代理（推荐先做）— 东财/巨潮/自算，精度略降可上线      │
├─────────────────────────────────────────────────────────────┤
│ L2 规则 + 轻量 LLM — 公告分类、政策事件、情绪翻转            │
├─────────────────────────────────────────────────────────────┤
│ L3 商业源（可选）— 仅行业 EPS 一致预期修正（朝阳永续）       │
└─────────────────────────────────────────────────────────────┘
```

---

### 3.2 行业景气 · EPS 一致预期修正（核心缺口）

**V5 要求：** 申万二级行业未来一年 EPS 3 个月变化率（上修/下修档位）。

| 方案 | 数据源 | 实现要点 | 优先级 |
|------|--------|----------|--------|
| **A 粗糙版（免费）** | 东财盈利预测 RPT / F10 | 个股 EPS 预测入库 → 按 `industry_sw2` 聚合 3 个月 delta | **P0** |
| **B 复用已有** | `institution_scorer` 分析师排名逻辑 | 改东财直连，弃 akshare 硬依赖；输出 `forecast_growth_pct` | P0 |
| **C 完整版** | 朝阳永续 / Wind | 仅当 A/B 行业 IC &lt; 0.02 持续 20 日再采购 | P2 |

**拟新增：**

```python
# services/eastmoney_forecast_sync.py
def fetch_stock_eps_forecast(code: str) -> list[dict]: ...
def sync_industry_eps_revision() -> dict: ...
```

**拟新增表：**

```sql
CREATE TABLE stock_eps_forecast (
    stock_id INTEGER, as_of_date TEXT, eps_fy1 REAL, eps_fy2 REAL,
    analyst_count INTEGER, source TEXT, UNIQUE(stock_id, as_of_date)
);
CREATE TABLE industry_eps_revision_daily (
    industry_sw2 TEXT, trade_date TEXT,
    revision_3m_pct REAL, tier INTEGER, source TEXT,
    UNIQUE(industry_sw2, trade_date)
);
```

**API 草案：** `GET /api/v5/industry-eps-revision` · 纳入 `POST /api/v5/sync` 新步骤 `eps_revision`

**akshare 对照（实现时直连东财，不硬依赖包）：** `stock_profit_forecast_em` reportName 抓包一次写入常量。

---

### 3.3 情绪面 · 股吧逆向（建议不真爬股吧）

**V5 要求：** 股吧热度、极端多空；**狂热 + 资金面≤0 → 翻转至 -1**。

| 方案 | 说明 | 推荐 |
|------|------|------|
| **代理指标** | 换手极端分位 + 涨跌停家数 + 新闻情感过热 + 主力 5 日净流出 | ✅ **默认** |
| 东财股吧 | 阅读/发帖数，反爬重、维护成本高 | 低频备选 |
| LLM 抽样 | 热门股股吧标题情感 | Top N 可选 |

**拟改造：** `mood_scorer.py`

```python
# 伪代码
mood_raw = pct_rank(turnover_extreme) + news_sentiment_heat
if mood_raw >= tier(+2) and capital_tier <= 0:
    mood_tier = -1  # V5 强制翻转
```

**不需新外部源**；依赖 `stock_fund_flow_daily`、`limit-stats`、`sentiment_aggregate`。

---

### 3.4 北向资金（建议降级，不硬接日频净买）

**背景：** 交易所自 2024-08-19 起不再披露北向日频净买额（`northbound_fetch.py` 已有说明）。

| 替代 | 数据源 | 用途 |
|------|--------|------|
| **持股变化** | 东财 `RPT_MUTUAL_HOLD_DET` 等沪深港通持股 RPT | 5/20 日持股变动替代净流入 |
| **资金面重组** | 主力 5 日 + 两融 + 龙虎榜机构席 | V5 资金子分主路径 |
| **仅展示** | 现有 `GET /api/market/northbound` | 不进硬打分 |

**结论：** 北向从 V5 资金面硬指标中**剔除或降权**，避免长期 0 分失真。

---

### 3.5 政策面 · 行业资金响应（V5 乘法模型）

**V5 公式：** `政策影响分 = 政策等级(±2) × 行业资金响应系数(0.5~1.5)`

| 步骤 | 已有 | 待建 |
|------|------|------|
| 政策等级 | `policy_scorer` 关键词 | 国务院/证监会 RSS + 公告标题规则 → `policy_events` |
| 行业超额 | 板块行情、全 A 指数 | 政策发布日 **T+20** 行业 vs 全 A 超额 → `policy_industry_response` |
| 乘数入库 | — | `policy_score_v5 = level × coef` |

**拟新增表：**

```sql
CREATE TABLE policy_events (
    id INTEGER PRIMARY KEY, pub_date TEXT, title TEXT,
    level INTEGER, industries_json TEXT, source TEXT
);
CREATE TABLE policy_industry_response (
    event_id INTEGER, industry_sw2 TEXT,
    excess_return_20d REAL, coef REAL
);
```

**模块：** `policy_event_sync.py`（巨潮 + 东财公告管道复用）· 扩展 `policy_scorer.py`

---

### 3.6 新闻面 · 事件分类

**V5 要求：** 仅合同/获批/减持/诉讼等；业绩预告归基本面，不重复计分。

| 层级 | 做法 |
|------|------|
| **规则（P0）** | `stock_announcements` + `stock_news` 标题 → `event_type` 枚举 |
| **LLM（P1）** | 每日新公告批量轻量分类（复用 debate LLM 基础设施） |
| **去重** | 含「业绩预告」「季报」「年报」→ `fundamental`，跳过 `news_event` |

**拟新增列：** `stock_announcements.event_type` · `stock_news.event_type`

**关键词示例：**

| event_type | 关键词 |
|------------|--------|
| `contract` | 重大合同、中标、框架协议 |
| `approval` | 获批、核准、注册 |
| `sell_down` | 减持、清仓式减持 |
| `litigation` | 诉讼、仲裁、立案 |
| `asset_sale` | 出售资产、转让子公司 |

---

### 3.7 一票否决 · 结构化风险

| V5 条件 | 接入方式 | 模块 |
|---------|----------|------|
| 大盘环境 = -2 | 指数趋势 + 宏观合成 `market_env_score` | 算法层 §5 |
| PMI&lt;48 且社融连降两月 | `macro_indicators` 序列判定 | `macro_sync.py` 扩展 |
| 质量 = -2 | `stock_v5_metrics.quality_tier` | 已有 |
| 立案 / 年报非标 | 公告 `event_type` + ST 标记 | `risk_flags` 表 |
| 连续跌停且无量 | `limit-stats` + 日 K 连板规则 | `risk_engine.py` |

**拟新增表：**

```sql
CREATE TABLE risk_flags (
    stock_id INTEGER, flag_date TEXT, flag_type TEXT,
    severity TEXT, detail TEXT, source TEXT,
    UNIQUE(stock_id, flag_date, flag_type)
);
-- flag_type: investigation | non_standard_audit | limit_down_streak | st
```

**API 草案：** `GET /api/v5/risk-flags/{stock_id}` · 综合分计算前强制检查

---

## 4. 商业源决策矩阵

| 缺口 | 免费能否凑合 | 建议采购 | 说明 |
|------|--------------|----------|------|
| 行业 EPS 一致预期修正 | 东财盈利预测可凑合 | **唯一值得买** | IC 回测后决定 |
| 股吧情绪 | 代理指标够用 | 否 | 维护成本 &gt; 收益 |
| 北向日频净买 | 已停更 | 否 | 改持股变化 |
| 政策 NLP | 规则 + LLM 够用 | 可选 | 非必须 |
| Wind 行业 ETF 资金流 | 东财板块流已有 | 否 | — |

---

## 5. 算法层（非数据源，但是 V5 闭环必需）

数据源齐后，需将 **0–100 八维** 迁移为 **V5 十维 -2~+2**：

```
各维原始指标 → 档位(-2..+2) → 百分制(档位+2)×25
    → 加权求和 → 短板惩罚 → 一票否决 → composite_v5
```

| 步骤 | 输入表 | 输出 |
|------|--------|------|
| 基本面/质量 | `stock_v5_metrics` | `growth_tier` / `quality_tier` |
| 行业景气 | `sector_fund_flow_daily` + `industry_eps_revision_daily` | `industry_tier` |
| 资金面 | `stock_fund_flow_daily` + `eastmoney_margin` + LHB | `capital_tier` |
| 大盘环境 | 指数 K + `macro_indicators` | `market_env_tier` |
| IC 监控 | `ic_engine` 扩展 5/20/60 合成 | 季度调权建议 |

**拟扩展 `comprehensive_scores`：** `quality_score` · `industry_score` · `market_env_score` · `composite_v5` · `veto_status`

---

## 6. 实施计划

### 6.1 阶段划分

| 阶段 | 内容 | 工作量 | 依赖 | 状态 |
|------|------|--------|------|------|
| **P1+2** | 宏观扩展、主力/板块流、Quality V2、L2、v5 sync API | 3d | migration v23 | ✅ 完成 |
| **P3a** | 东财盈利预测 + `industry_eps_revision` | 2d | P1+2 | ⏳ |
| **P3b** | 公告/新闻 `event_type` + `risk_flags` | 1.5d | 公告管道 | ⏳ |
| **P3c** | 政策事件 + T+20 行业超额 | 2d | 板块行情 | ⏳ |
| **P3d** | 情绪代理 + 翻转规则；北向降级文档化 | 1d | 主力/情绪 | ⏳ |
| **P4** | V5 十维档位 + 短板/否决 + IC 多周期 | 5d | P3a–d | ❌ |
| **P5（可选）** | 朝阳永续 EPS 修正 | 商务 | P4 IC 不达标 | ❌ |

### 6.2 三周节奏（Phase 3 + 算法起步）

| 周 | 任务 | 覆盖 |
|----|------|------|
| **W1** | P3a 盈利预测入库 + P3b 事件分类规则 | ~10% |
| **W2** | P3c 政策超额 + P3d 情绪代理 + `risk_flags` | ~7% |
| **W3** | P4 十维档位接入 `comprehensive_scores`；IC 5/20/60 | 算法闭环 |

### 6.3 验收标准

#### M1 — Phase 3 数据（P3a–d）

- [ ] `industry_eps_revision_daily` 覆盖 ≥90% `industry_sw2` 非空股票所在行业  
- [ ] 公告 `event_type` 覆盖率 ≥80%（规则版）  
- [ ] `risk_flags` 可识别立案/非标/连跌停样本股  
- [ ] `POST /api/v5/sync` 返回 `eps_revision` / `policy` / `risk` 步骤状态  

#### M2 — V5 算法（P4）

- [ ] 个股页展示十维档位（-2~+2）及 `composite_v5`  
- [ ] 触发否决时 `veto_status != ok`，综合分受限或标记「回避」  
- [ ] IC 报告含 5/20/60 合成与季度建议  

#### M3 — 商业源（P5，可选）

- [ ] 行业景气 IC 使用粗糙版 vs 朝阳永续 A/B 报告  

---

## 7. 风险与对策

| 风险 | 影响 | 对策 |
|------|------|------|
| 东财盈利预测 RPT 变更 | EPS 修正失败 | reportName 常量化 + 单测 + akshare fallback |
| 社融代理口径偏差 | 宏观否决误报 | 文档标明代理；可选接入 mofcom 社融（SSL 不稳） |
| 股吧反爬 | 情绪直连失败 | **默认代理指标**，不阻塞上线 |
| 政策事件漏检 | 政策分偏低 | RSS + 公告双通道；LLM 补漏 |
| Phase 3 与八维并存 | 用户困惑 | 过渡期双显：`composite_score` + `composite_v5` |

---

## 8. 运维手册

### 8.1 日常同步

```text
市场页 / 数据管理 → 点击「V5数据源」
或 POST /api/v5/sync
```

### 8.2 Phase 3 上线后

```text
POST /api/v5/sync  # 含 eps_revision、event_classify、policy_response、risk_scan
GET  /api/v5/industry-eps-revision
GET  /api/v5/risk-flags/{stock_id}
```

### 8.3 与 batch-fill 关系

- V5 指标表更新 **不替代** `POST /api/scores/batch-fill`  
- 新股 onboard 后：`fetch` → `v5/sync` → `batch-fill compute_and_sync`  

---

## 9. 代码锚点（已实现）

| 模块 | 路径 |
|------|------|
| 宏观扩展 | `backend/services/eastmoney_macro.py` |
| 主力/板块流 | `backend/services/fund_flow_sync.py` · `sector_fund_flow_sync.py` |
| Quality V2 | `backend/services/quality_metrics_calc.py` |
| 行业 L2 | `backend/services/industry_l2_sync.py` |
| 编排 | `backend/services/v5_data_sync.py` |
| API | `backend/api/v5_data.py` |
| 测试 | `backend/tests/test_v5_data_sources.py` |
| 迁移 | `backend/migrations.py` v23 |

---

## 10. 变更记录

| 版本 | 日期 | 说明 |
|------|------|------|
| v1.0 | 2026-06-05 | 初版：Phase 1+2 落地清单 + Phase 3（20%）接入方案 + 算法层路线图 |

---

**下一步：** 评审 Phase 3a（东财盈利预测 + `industry_eps_revision`）→ 与 Phase 3b 事件分类并行开工。
