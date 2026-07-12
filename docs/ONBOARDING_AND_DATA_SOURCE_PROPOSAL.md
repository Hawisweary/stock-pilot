# 新股票就绪 & 数据源去 akshare 依赖 — 综合方案书

> 版本：v1.2  
> 日期：2026-06-05  
> 状态：**§11 决策已锁定；Track C（V5 数据源）Phase 1+2 已落地**  
> 范围：**不含 Tushare Pro、iWind** 等商业终端；仅免费/开源直连源（V5 EPS 修正商业源见 Track C）  
> 关联文档：  
> - [BATCH_DIMENSION_SCORE_PROPOSAL.md](./BATCH_DIMENSION_SCORE_PROPOSAL.md)（八维 batch-fill）  
> - [V5_SCORING_DATA_SOURCE_PROPOSAL.md](./V5_SCORING_DATA_SOURCE_PROPOSAL.md)（**V5 十维数据源：80% 已接入 + 20% 路线图**）  
> - [UPGRADE_PROPOSAL.md](../UPGRADE_PROPOSAL.md)（v4 升级总览）  
> - [ARCHIVE_ANALYSIS.md](../ARCHIVE_ANALYSIS.md)（a-stock-data 多源架构参考）

---

## 0. 执行摘要

| 目标 | 现状 | 目标态 |
|------|------|--------|
| 加 1 只股票到「有综合分」 | 抓 ~2min + **手动** batch-fill；易因缺 akshare 失败 | **一键 onboard**，~3min 内八维分就绪 |
| 加 N 只股票 | batch-add 开 N 线程抢 akshare | **队列 + 限流并行**，线性可控 |
| 数据源稳定性 | 财报/指标强依赖 akshare 封装 | **东财 datacenter 直连 + 腾讯 + ADATA** 三源；akshare 降为 fallback |
| 运维步骤 | 3～5 个分散 API | **1 个 job + poll 进度** |

**推荐实施顺序：** Track A（加股流水线 P0）与 Track B（数据源 D0）**并行**，2 周内可交付 MVP。

---

## 1. 背景与问题

### 1.1 业务诉求

用户将新股票加入跟踪列表后，期望个股页尽快展示：

- 中文名称、行业、日 K、估值（PE/PB/市值）
- 财报与基本面指标
- **八维综合分**（fundamental / technical / sentiment / capital / policy / mood / val + composite）

### 1.2 现状诊断（2026-06-01）

#### 加股链路

```
POST /api/stocks
  └─ threading.Thread → DataFetcher.fetch_all_for_stock (单只)
       ├─ info / quotes / financials / indicators / valuation
       └─ FactorEngine.calculate_all([stock_id])   ← 仅五因子
  ✗ 不走 fetch_job（无 status）
  ✗ 不触发 batch-fill（八维分空白）
  ✗ batch-add 时对每只 code 各起一个线程
```

#### 数据抓取瓶颈

| 步骤 | 数据源 | 单股耗时占比 | 问题 |
|------|--------|-------------|------|
| 日 K | 腾讯 → yfinance | ~15% | 已较稳 |
| 估值 | 腾讯 | ~5% | 已较稳 |
| **财报三表** | akshare ×6 + sleep | **~55%** | 慢、缺包即挂、并发易限流 |
| **财务指标** | akshare | ~20% | 同上 |
| 基本信息 | 腾讯/东财/akshare | ~5% | 东财已更稳，akshare 仅备选 |

#### 分数链路

| 分数类型 | 表 | 加股后是否自动 |
|----------|-----|----------------|
| 五因子 | `factor_scores` | ✅ fetch 末尾计算 |
| 八维 | `comprehensive_scores` | ❌ 需 `POST /scores/batch-fill` |
| 辩论/ML | `debate_v2` 等 | ❌ 独立流程 |

#### 已发生故障

- `No module named 'akshare'` → 整股抓取失败（`data_fetcher` 顶层 import 或 venv 未装依赖）
- 腾讯 HTTP 302 → 分时/5 分 K 空白（已改 HTTPS）
- 八维分大量 `sync_only` 缺口（见 BATCH 方案书 §1.2）

---

## 2. 目标架构

### 2.1 新股票 Onboarding 流水线

```
┌──────────────┐     ┌─────────────────────────────────────────────────┐
│ POST         │     │            onboard_job (job_queue)               │
│ /stocks/     │────▶│ ① register  入库 / 恢复 is_active              │
│ onboard      │     │ ② prefetch  腾讯批量行情 + push2 校验 (可选)    │
└──────────────┘     │ ③ fetch     线程池 N=4~6 逐只 DataFetcher       │
                     │ ④ factor    FactorEngine.calculate_all(new_ids) │
                     │ ⑤ score     batch-fill compute_and_sync(new_ids)│
                     │ ⑥ optional  新闻 prefetch（仅 new_ids）          │
                     └─────────────────────────────────────────────────┘
                                          │
                                          ▼
                              GET /api/system/jobs/{job_id}
                              个股页八维分就绪
```

**原则：**

1. **加股只入库 + 入队**，不在 API handler 内直接 `threading.Thread` 抓 akshare  
2. **抓取与评分解耦**，job 链自动串联：fetch 完成 → enqueue batch-fill  
3. **能批则批**：行情预取、因子分、P2 四维度并行（`AFR_P2_PARALLEL`）  
4. **单股可重入**：失败步骤可单独 retry，不重头抓全量历史  

### 2.2 数据源分层（去 akshare 硬依赖）

```
┌─────────────────────────────────────────────────────────────────┐
│ L1 行情 / 估值（生产主路径，已就绪）                              │
│   腾讯 qt.gtimg.cn  │  Ashare  │  push2 批量  │  yfinance 兜底 │
├─────────────────────────────────────────────────────────────────┤
│ L2 基本面 / 财报（本方案核心改造）                                │
│   东财 datacenter 直连  →  ADATA Core  →  mootdx  →  akshare 备  │
├─────────────────────────────────────────────────────────────────┤
│ L3 新闻 / 资金 / 筹码                                             │
│   东财 JSONP/datacenter  │  ADATA 北向  │  akshare 备             │
├─────────────────────────────────────────────────────────────────┤
│ L4 宏观（低优先级，可暂留 akshare）                               │
│   东财 macro RPT  →  akshare macro_*                             │
└─────────────────────────────────────────────────────────────────┘
```

**不采用：** Tushare Pro、Wind/iFinD 及一切需商业 token 的源。

---

## 3. akshare 替代映射表

### 3.1 按数据类型

| 数据类型 | 现 akshare 接口 | 替代主源 | 项目内已有 | 备用 |
|----------|----------------|----------|-----------|------|
| 日 K | — | 腾讯 `tencent_adapter` | ✅ | yfinance |
| 实时/估值 | — | 腾讯 `tencent_quote` | ✅ | push2 |
| 分时/5分K | — | 腾讯 ifzq HTTPS | ✅ | — |
| 股票信息 | `stock_individual_info_em` | **东财 push2** `eastmoney_stock_info` | ✅ | ADATA 行业 |
| 申万行业 | — | **ADATA** `get_industry_sw` | ✅ | 东财 f127 |
| **利润表** | `stock_profit_sheet_*_em` | **东财 datacenter** RPT | 待建 | mootdx / akshare |
| **资产负债表** | `stock_balance_sheet_*_em` | **东财 datacenter** RPT | 待建 | 新浪 HTML / akshare |
| **现金流** | `stock_cash_flow_*_em` | **东财 datacenter** RPT | 待建 | akshare |
| 财务指标 | `stock_financial_analysis_indicator` | **ADATA** `get_core_finance` | 部分 | 东财摘要 RPT |
| 财务宽表 | `stock_financial_abstract` | 东财 datacenter / ADATA | 待建 | akshare |
| 个股新闻 | `stock_news_em` | **东财** `eastmoney_stock_news` | ✅ fallback | akshare |
| 分红 | `stock_history_dividend_detail` | **东财** `dividend_history` | ✅ | akshare |
| 两融 | `stock_margin_detail_*` | **东财** `margin_trading` | ✅ | akshare |
| 北向 | `stock_hsgt_*` | **ADATA** `get_north_flow` | ✅ adapter | 东财 RPT |
| 龙虎榜/解禁/大宗 | 多个 `*_em` | 东财 datacenter 同名 RPT | astock_data 包 ak | 直连 RPT |
| 宏观 | `macro_china_*` | 东财 macro RPT（Phase 3） | — | akshare |
| 全 A 列表 | `stock_info_a_code_name` | 本地 cache + 东财 clist | 部分 | akshare |

### 3.2 为何「东财直连」优于「换另一个 Python 包」

akshare 的东财接口本质是 **HTTP 封装**；直连 `datacenter-web.eastmoney.com`：

- 少一层包版本/字段变更风险  
- 自控重试、并发、超时（`http_client.py` 已有域名白名单）  
- **不依赖** `pip install akshare` 才能启动抓取模块  

akshare **保留为 fallback**：东财/ADATA 均失败时再 lazy 调用 `_ak()`。

### 3.3 已有可复用模块

| 文件 | 能力 |
|------|------|
| `services/data_sources.py` | push2、datacenter 框架、分红、两融、东财新闻 |
| `services/adata_adapter.py` | 申万行业、核心财务、北向 |
| `services/tencent_adapter.py` | 日 K |
| `services/push2_adapter.py` | 东财批量行情 |
| `services/astock_data.py` | mootdx 财务、sina 财报 HTML |
| `services/fetch_job.py` | 单股 job + complete 后 gap sync |
| `services/batch_score_orchestrator.py` | batch-fill 编排 |
| `services/job_queue.py` | 异步 job |

---

## 4. Track A — 新股票效率提升

### 4.1 API 设计

#### 新增：`POST /api/stocks/onboard`

```json
{
  "codes": ["300450", "600519"],
  "market": "A",
  "auto_score": true,
  "score_mode": "compute_and_sync",
  "skip_existing": true,
  "fetch_parallel": 4
}
```

`score_mode` 省略时读 `AFR_ONBOARD_SCORE_MODE`（默认 `compute_and_sync`）。

**响应：**

```json
{
  "ok": true,
  "job_id": "uuid",
  "poll_url": "/api/system/jobs/{job_id}",
  "registered": [{"code": "300450", "stock_id": 70, "status": "added"}]
}
```

#### 改造：`POST /api/stocks` / `POST /api/stocks/batch-add`

| 行为 | 现在 | 改后 |
|------|------|------|
| 单加 | 线程内 `DataFetcher` | 入库 → `fetch_job` → 可选自动 batch-fill |
| 批量加 | 每 code 一线程 | **仅入库**；返回 `stock_ids`；文档引导 onboard / fetch-batch |

#### 新增（可选）：`POST /api/data/fetch-batch`

```json
{ "stock_ids": [70, 71], "prefetch_quotes": true }
```

等价于 fetch-all 的子集，避免全库重抓。

### 4.2 Job 阶段定义

| Phase | 名称 | 动作 | 批量 |
|-------|------|------|------|
| P1 | register | INSERT/UPDATE stocks | SQL 批量 |
| P2 | prefetch | `tencent_quote(codes)` + valuation_snapshots | ✅ 1 次 API |
| P3 | fetch | `DataFetcher.fetch_all_for_stock` × N | 线程池 4~6 |
| P4 | factor | `FactorEngine.calculate_all(new_ids)` | ✅ 1 次 |
| P5 | score | `fill_gaps(mode=compute_and_sync, stock_ids=new_ids)` | ✅ 定向 |
| P6 | news | `fetch_sentiment_for_gaps`（可选） | 定向 |

**进度字段：** `{phase, done, total, message, errors[]}`

### 4.3 与现有能力对齐

| 需求 | 复用 |
|------|------|
| 单股 status | `fetch_job.status_payload` |
| 八维补算 | `job_queue.enqueue_batch_fill` |
| P2 并行四维度 | `batch_score_maintenance.run_p2_phases_parallel` |
| fetch 后 gap sync | `sync_gaps_after_fetch`（扩展为仅 new_ids） |
| 前端进度 | 数据页 job poll（与 batch-fill 相同组件） |

### 4.4 配置项（与 §11 锁定默认值一致）

实现时读取 `backend/config.py` 中 **Onboarding** 段；`.env` 示例：

```bash
# --- 新股票 onboard（§11 已锁定默认）---
AFR_ONBOARD_AUTO=true                      # POST /stocks 完成后走 onboard 流水线
AFR_AUTO_SCORE_ON_FETCH=true               # 抓取成功后自动 enqueue batch-fill
AFR_ONBOARD_SCORE_MODE=compute_and_sync    # 新股票默认：缺维度就算，再 sync
AFR_BATCH_ADD_AUTO_ONBOARD=true            # batch-add 是否自动 onboard
AFR_BATCH_ADD_AUTO_ONBOARD_MAX=5           # ≤5 只自动；>5 只仅入库+返回 stock_ids
AFR_FETCH_PARALLEL=4                       # onboard / fetch-all 抓取并发
AFR_FINANCE_FAST_PATH=true                 # 财报快路径：ADATA/mootdx；夜间 full 补全量
AFR_AKSHARE_SLEEP_MS=500                   # akshare fallback 请求间隔

# --- 已有 ---
AFR_P2_PARALLEL=true                       # batch-fill 四维度并行
```

**运维 override（非默认）：**

```bash
# 仅修复 comprehensive 同步、不重算维度（老股运维用，不用于新股票 onboard）
AFR_ONBOARD_SCORE_MODE=sync_only
```

### 4.5 预期效果

| 场景 | 现状 | Track A 完成后 |
|------|------|----------------|
| 加 1 只 → 有综合分 | 手动 2 步，~3~5min | 自动 1 步 poll，~3min |
| 加 10 只 | N 线程混乱 ~15min+ | onboard ~6~8min |
| 缺 akshare 包 | 整股失败 | 东财主路径仍可抓（Track B） |
| 运维 API 数 | 3~5 | 1 |

---

## 5. Track B — 数据源去 akshare 硬依赖

### 5.1 新建：`services/eastmoney_finance.py`

**职责：** 东财 datacenter 直连，输出与 akshare DataFrame **同构** 的列名，复用 `data_processor.transform_financial_reports`。

**接口草案：**

```python
def fetch_profit_sheet(code: str, period: Literal["yearly", "quarterly"]) -> pd.DataFrame: ...
def fetch_balance_sheet(code: str, period: Literal["yearly", "quarterly"]) -> pd.DataFrame: ...
def fetch_cashflow_sheet(code: str, period: Literal["yearly", "quarterly"]) -> pd.DataFrame: ...
def fetch_financial_abstract(code: str) -> pd.DataFrame: ...
```

**实现要点：**

- 复用 `data_sources._eastmoney_datacenter(report_name, filter_str, ...)`  
- `filter`: `(SECURITY_CODE="300450")` 等  
- reportName 与 akshare 同源（抓包或对照 akshare 源码一次，写入常量表）  
- 分页：年报通常 <50 条，`pageSize=500`  
- 统一 `User-Agent` + 无代理（与 `launch.sh` 一致）  

### 5.2 改造：`DataFetcher._fetch_financial_reports`

```python
# 伪代码 — 多源 fallback 链
for source in (eastmoney_finance, mootdx_snapshot, _ak):
    df_income, df_balance, df_cf = source.fetch_all(code)
    if has_valid_income(df_income):
        break
# 合并年度+季度 → transform_financial_reports → upsert
```

**sleep 策略：**

- 东财直连：**请求间 200~500ms**（可配置），6 次串行 ≈ 1.5~3s（现 6~12s）  
- akshare fallback 路径保留原间隔  

### 5.3 改造：`_fetch_financial_indicators`

```python
# 1. ADATA get_core_finance → transform
# 2. eastmoney_finance.fetch_financial_abstract
# 3. _ak().stock_financial_analysis_indicator
```

### 5.4 改造：`news_fetcher`

```python
# 主: eastmoney_stock_news
# 备: _ak().stock_news_em
```

### 5.5 改造：`eastmoney_sync` / `astock_data`

- 两融、分红、龙虎榜等：**优先 `data_sources` 已有函数**  
- `astock_data.py` 内 akshare 改为调用 `eastmoney_finance` 或 datacenter  
- mootdx：保留 `/api/signals/financials/mootdx/{code}`，纳入 onboard 快路径  

### 5.6 akshare 降级策略

| 层级 | 要求 |
|------|------|
| import | 全项目 lazy import（`_ak()` 模式），`requirements.txt` 仍保留 akshare |
| 运行 | venv-quant 安装；`launch.sh` 用 venv python 装依赖 |
| 监控 | `data_fetch_log` 增加 `source` 列：tencent / eastmoney / adata / akshare |

### 5.7 快路径 vs 全量路径

| 模式 | 适用 | 内容 |
|------|------|------|
| **fast** | onboard 首日 | ADATA 核心指标 + mootdx 近 4 季 + 腾讯日 K 120 日 |
| **full** | 夜间 fetch-all | 东财三表全历史 + 季度合并 |

配置：`AFR_FINANCE_FAST_PATH=true` 时 onboard 走 fast，scheduler 03:00 补 full。

---

## 6. 综合实施计划

### 6.1 阶段划分

| 阶段 | 轨道 | 内容 | 工作量 | 依赖 |
|------|------|------|--------|------|
| **S0** | B | `_ak()` 全项目 lazy；launch.sh venv 装依赖 | 0.5d | — |
| **S1** | B | `eastmoney_finance.py` + 财报/指标主路径 | 2d | — |
| **S2** | B | news / eastmoney_sync / astock_data 切主源 | 1d | S1 |
| **S3** | A | add_stock → fetch_job；抓完 auto batch-fill | 1d | — |
| **S4** | A | batch-add 去 N 线程；`AFR_FETCH_PARALLEL` | 0.5d | S3 |
| **S5** | A | `POST /stocks/onboard` + job 编排 | 2d | S3,S4 |
| **S6** | A+B | fast/full 财报路径 + mootdx/ADATA 接入 | 1.5d | S1,S5 |
| **S7** | A | 前端：加股/onboard 进度 UI | 1d | S5 |
| **S8** | B | 宏观改东财或保留 akshare；data_fetch_log.source | 1d | S2 |

**MVP（可对外）：S0 + S1 + S3 + S4 ≈ 4d**  
**完整版：S0～S8 ≈ 10d**

### 6.2 里程碑验收

#### M1 — 数据源 MVP（S0+S1）

- [ ] 无 akshare 环境下，300450 财报 `financials_count > 0`  
- [ ] `data_fetch_log` 主源标记为 `eastmoney`  
- [ ] akshare 可用时 fallback 仍成功  

#### M2 — 加股自动化（S3+S4）

- [ ] `POST /stocks` 后 `GET /data/fetch/{id}/status` 可 poll  
- [ ] success 后自动 enqueue batch-fill，`comprehensive_scores` 八维非空（允许 sentiment 延迟）  
- [ ] batch-add 10 只不会启动 10 个 unbounded 线程  

#### M3 — Onboard 一键（S5+S7）

- [ ] `POST /stocks/onboard` 返回 job_id，全流程 progress 可见  
- [ ] 加 1 只：从请求到 composite_score 有值 ≤ 5min（交易时段，网络正常）  
- [ ] 加 10 只：≤ 10min（fast 路径）  

#### M4 — 全量（S6+S8）

- [ ] fast 路径 ≤ 90s/股；夜间 full 补历史  
- [ ] 腾讯 vs push2 价差 >5% 写入 fusion 告警表  

---

## 7. 测试计划

### 7.1 单股回归用例

| code | 板块 | 验证点 |
|------|------|--------|
| 600519 | 沪主板 | 财报三表、指标、八维 |
| 300450 | 创业板 | 东财/filter 兼容性 |
| 688xxx | 科创板 | secid 前缀 |
| 8xxxxx | 北交所 | bj 前缀（若支持） |

### 7.2 脚本

```bash
# 端到端 onboard
curl -X POST localhost:8800/api/stocks/onboard \
  -H 'Content-Type: application/json' \
  -d '{"codes":["300450"],"auto_score":true}'

# 轮询 job / fetch status
curl localhost:8800/api/data/fetch/{id}/status

# 缺口扫描
curl 'localhost:8800/api/scores/gaps?stock_ids=70'
```

### 7.3 非功能

- 并发 onboard 10 只：akshare 调用次数 = 0（东财主路径）或仅 fallback 失败项  
- batch-fill 运行中 onboard 不 deadlock（`batch_score_guard`）  
- 代理环境：抓取任务 unset proxy  

---

## 8. 风险与对策

| 风险 | 影响 | 对策 |
|------|------|------|
| 东财 datacenter 字段变更 | 财报解析失败 | reportName 常量化 + 单测 + akshare fallback |
| 东财限流 | 批量 onboard 失败 | 并发 4~6 + 指数退避 + prefetch 合并 |
| mootdx 服务器不可用 | fast 路径缺数据 | 跳过 mootdx，ADATA+东财仍可用 |
| batch-fill 与 scheduler 竞态 | 重复计算 | 已有 `can_run_sync` / `batch_fill_session` |
| 新浪 HTML 结构变化 | 末位 fallback 失效 | 仅作末位，监控失败率 |
| LLM 维度慢 | onboard 总时长拉长 | 默认仍用 compute_and_sync；运维单次传 score_mode=sync_only |

---

## 9. 运维手册（目标态）

### 9.1 加 1 只股票

```text
POST /api/stocks/onboard {"codes":["300450"]}
→ poll job 至 done
→ 打开 /stocks/300450
```

### 9.2 加多只

```text
POST /api/stocks/onboard {"codes":[...], "fetch_parallel":4}
```

### 9.3 仅补分（数据已有）

```text
POST /api/scores/batch-fill
{"mode":"compute_and_sync","stock_ids":[70]}
```

### 9.4 环境

```bash
./launch.sh start          # 使用 venv-quant
venv-quant/bin/pip install -r backend/requirements.txt
# 确保无 HTTP_PROXY
```

---

## 10. 不在本方案范围内

- Tushare Pro / Wind / iFinD 接入（**例外：** V5 行业 EPS 一致预期修正，见 [V5_SCORING_DATA_SOURCE_PROPOSAL.md](./V5_SCORING_DATA_SOURCE_PROPOSAL.md) §6）  
- DolphinDB / 微服务拆分（见 UPGRADE_PROPOSAL）  
- 辩论 batch 加速（见 DEBATE_BATCH_ACCELERATION_PROPOSAL）  
- 因子实验室 S0~S4（已独立交付）  
- V5 十维档位算法、短板惩罚、一票否决（见 V5 方案书 §5 算法层）

---

## 10.1 Track C — V5 打分数据源（2026-06-05 增补）

与 Track B（去 akshare 硬依赖）正交，面向 **机构级 V5 十维打分** 的数据底座。详见独立方案书 [V5_SCORING_DATA_SOURCE_PROPOSAL.md](./V5_SCORING_DATA_SOURCE_PROPOSAL.md)。

| 子阶段 | 内容 | 状态 |
|--------|------|------|
| **C1（P1+2）** | 宏观四件套、板块/个股主力、Quality V2、申万 L2、`POST /api/v5/sync` | ✅ 已落地（schema v23） |
| **C2（P3）** | EPS 行业修正、公告事件分类、政策 T+20 超额、`risk_flags`、情绪代理 | ⏳ 待评审 |
| **C3（P4）** | 十维 -2~+2、短板惩罚、否决、IC 5/20/60 → `composite_v5` | ❌ 未开工 |
| **C4（P5，可选）** | 朝阳永续 EPS 修正（仅 IC 不达标时） | ❌ |

**运维：** 市场页 / 数据管理 **「V5数据源」** 按钮，等价于 `POST /api/v5/sync`。

---

## 11. 已锁定决策（2026-06-01）

### 11.1 决策 1：新股票默认评分模式 → **`compute_and_sync`**

| 模式 | 行为 | 新股票效果 | 默认 |
|------|------|-----------|------|
| **`compute_and_sync`** | 扫描缺口 → 对缺失维度 **compute**（fundamental/capital/mood/policy/val/sentiment/technical）→ `sync_all_dimensions` → 重算 composite | 一次到位，个股页八维尽量齐 | **✅ 是** |
| `sync_only` | 仅 `sync_all_dimensions`：把 **子表已有** 的分抄进 `comprehensive_scores` | 新股票子表多为空 → 大量 `--` | 否（仅运维/夜间） |

**与「fast」的区别（勿混淆）：**

| 概念 | 管什么 | 新股票默认 |
|------|--------|-----------|
| `AFR_ONBOARD_SCORE_MODE` | **八维分**：算不算缺失维度 | `compute_and_sync` |
| `AFR_FINANCE_FAST_PATH` | **财报数据**：全量三表 vs ADATA/mootdx 快路径 | `true`（与上项独立，可组合） |

**推荐组合（日常加自选）：** `FINANCE_FAST_PATH=true` + `ONBOARD_SCORE_MODE=compute_and_sync`。

**API 覆盖：** `POST /stocks/onboard` 请求体可传 `"score_mode":"sync_only"` 单次 override；不传则用 config 默认。

**实现锚点：** `services/batch_score_orchestrator.fill_gaps` · `MODE_PHASES["compute_and_sync"]`（见 BATCH 方案书）。

---

### 11.2 决策 2：batch-add 自动 onboard → **≤5 只自动，>5 只需确认**

| 行为 | batch-add 之后 | 适用 |
|------|----------------|------|
| **不自动 onboard** | 仅入库；需手动 `onboard` / `fetch-all` | 大批量导代码、稍后再抓 |
| **自动 onboard** | 入库 → prefetch → 池化 fetch → factor → batch-fill | 日常「加自选就要能用」 |

**锁定规则：**

```text
len(codes) ≤ AFR_BATCH_ADD_AUTO_ONBOARD_MAX (默认 5)
  → 自动 enqueue onboard_job（与 POST /stocks 单加行为一致）

len(codes) > 5
  → 仅入库，响应含 stock_ids + onboard_hint: "请调用 POST /stocks/onboard"
  → 前端弹确认后再调 onboard（防东财限流 / 长时间 job）
```

| | 不自动 | 自动（≤5） |
|---|--------|-----------|
| 用户步骤 | 加股 + 再点抓取/补分 | 加股 + poll job |
| 新股票分是否齐 | 易遗漏 | 与单加一致 |
| 大批量风险 | 低 | 需阈值保护 |

**实现锚点：** `api/stocks.py` · `batch_add_stocks`；阈值 `config.BATCH_ADD_AUTO_ONBOARD_MAX`。

---

### 11.3 决策 3：akshare → **保留 requirements，降为 optional fallback**

- `requirements.txt` **保留** `akshare>=1.18.0`（兼容旧路径）。  
- 代码 **全项目 lazy import**（`_ak()`）；主路径东财/ADATA 无 akshare 仍可抓财报。  
- CI 增加一条：**无 akshare 环境**下单股 fetch 回归（Track B · S0）。

---

### 11.4 决策矩阵（实现对照）

```text
                    batch-add 自动 onboard
                    ┌──────────────┬──────────────────────┐
                    │  否（>5只）  │  是（≤5只，默认）    │
        ┌───────────┼──────────────┼──────────────────────┤
score   │ sync_only │ 仅入库，    │ 不推荐：分易不齐     │
默认    │           │ 手动补分    │                      │
        ├───────────┼──────────────┼──────────────────────┤
        │ compute_  │ 手动两次    │ ✅ 锁定默认组合      │
        │ and_sync  │ API         │ 加完 poll 即可       │
        └───────────┴──────────────┴──────────────────────┘
```

---

## 12. 变更记录

| 版本 | 日期 | 说明 |
|------|------|------|
| v1.2 | 2026-06-05 | 增补 Track C；关联 [V5_SCORING_DATA_SOURCE_PROPOSAL.md](./V5_SCORING_DATA_SOURCE_PROPOSAL.md)（80% 落地 + 20% 路线图） |
| v1.1 | 2026-06-01 | §11 待决项锁定：compute_and_sync 默认、batch-add≤5 自动 onboard、akshare optional |
| v1.0 | 2026-06-01 | 初版：Track A onboard + Track B 东财/ADATA 替代 akshare |

---

**下一步：**

- Track A/B：按 **S0 → S1 → S3 → S4** 继续（加股自动化）  
- Track C：评审 [V5 方案书](./V5_SCORING_DATA_SOURCE_PROPOSAL.md) Phase 3a（盈利预测 + 行业 EPS 修正）
