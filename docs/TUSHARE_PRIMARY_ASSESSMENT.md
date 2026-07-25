# #60 Tushare 主数据源 — 现状盘点与方案(动手前)

**结论先行**:大部分数据类型 Tushare **已经是主源**。#60 真正剩下的只有**一件事**——把**日线 EOD OHLC 批量同步**从腾讯切到 Tushare。而**实时/现价**因 Tushare 是 EOD-only,**必须留在腾讯**,不在本任务范围。适配器 `fetch_daily_adjusted` 已写好且口径对齐,是「已造好但未接线」状态。

---

## 1. 现状:谁是主源(实测代码路径)

| 数据类型 | 当前主源 | 说明 |
|----------|---------|------|
| 行业分类 | **Tushare** ✅ | #58 已切 |
| 龙虎榜/指数/资金流/宏观/停牌 | **Tushare** ✅ | #59 已切(`lhb_fetch`/`market_index`/`fund_flow_sync`/`macro_sync`) |
| 财报三表/财务指标 | **Tushare** ✅ | `fetch_financial_reports`/`fetch_fina_indicator` |
| 预告/快报/披露日/概念板块 | **Tushare** ✅ | `fetch_forecast_vip` 等 |
| **日线 EOD OHLC(历史行情)** | **腾讯** ❌ | `data_fetcher._fetch_daily_quotes` → `tencent_adapter.fetch_daily_quotes`;`quote_sync` 夜间批同步也走这条 |
| 实时/现价(盘中、成交定价) | 腾讯(必须) | `tencent_quote`,10+ 消费方;Tushare 无实时,**不能切** |

**关键发现**:`fetch_daily_adjusted`(Tushare 日线+前复权)**已实现且口径对齐库表**(raw `close` + `adj_close` 分列、vol 手→股、amount 千元→元、日期归一),但目前**只在 `scripts/tushare_backfill_fundamentals.py`(一次性回填脚本)里用过,live 路径没接**。

**已有隐患**:历史行情是 Tushare 回填的(前复权),而**夜间增量是腾讯**的——库里 `stock_daily_quotes` 目前是**混源**。切成 Tushare 反而能统一复权口径。

**无源选择开关**:源硬编码在 `_fetch_daily_quotes`(腾讯主 / yfinance 禁用备)。没有 `AFR_QUOTE_SOURCE` 之类的 flag。

---

## 2. #60 真实范围(收窄后)

只做一件:**日线 EOD 批量同步主源 腾讯 → Tushare**,并**统一复权口径**。
- ✅ 在范围:`quote_sync.sync_active_stock_quotes`(夜间批)+ onboard 首拉的历史日线。
- ❌ 不在范围:实时现价(`tencent_quote` 消费方全保留)、盘中成交定价。
- ⚠️ 边界:onboard 首拉用 Tushare 单股 `fetch_daily_adjusted`(有限速);夜间全量批用 `fetch_market_daily(trade_date)`(按日 bulk,1 次/天,省 points)。

---

## 3. 风险点(按严重度)

1. **复权口径接缝(最高危)**:全站历史行情/K线/回测/ML 特征都依赖 `adj_close` 前复权。若 Tushare 的 `adj_factor` 与腾讯 qfq 口径微差,切换点会出现**净值跳变**,污染回测与因子。→ 必须 shadow 对比同一天两源的 adj_close,差异 >阈值即报警。
2. **覆盖率**:Tushare 是否覆盖全池(北交所 920、ST、次新)?`fetch_market_daily(trade_date)` 是否含北交所?→ 需按池子实测覆盖数。
3. **Points/限速**:主源切 Tushare 后,日线走 bulk(1 调/天)可控;但 onboard 单股 `fetch_daily_adjusted` 每股 2 调(daily+adj_factor),批量 onboard 会打限速。→ onboard 也应尽量走 bulk 或加节流。
4. **交易日历**:批同步要按 `fetch_trade_calendar` 遍历交易日,别用自然日。
5. **成交额/换手/融资**:`_fetch_daily_quotes` 现在腾讯 OHLCV 之外还挂了东财成交额换手 + 融资(`_apply_adj_after_quotes`/quote_extras)。切 Tushare 后这些副产品的补挂逻辑要保留(Tushare `daily_basic` 有换手/量比,可一并取)。
6. **回滚**:切换必须能一键退回腾讯(flag),且切换不删旧数据。

---

## 4. 建议方案(分阶段 + flag + shadow,可随时回滚)

**Phase 0 — 加开关(零行为变更)**
- `config`: `AFR_QUOTE_SOURCE = os.getenv("AFR_QUOTE_SOURCE", "tencent")`(值:`tencent`/`tushare`)。
- `_fetch_daily_quotes` 按 flag 分派到腾讯或新的 `_fetch_daily_quotes_tushare`。默认 `tencent` → **上线零变化**。

**Phase 1 — 接 Tushare 日线路径(默认关)**
- 新增 `_fetch_daily_quotes_tushare`:onboard 用 `fetch_daily_adjusted`,夜间批优先 `fetch_market_daily(trade_date)` bulk。
- 写入同一张 `stock_daily_quotes`,close/adj_close 分列口径对齐。
- 保留成交额/换手/融资副产品补挂。

**Phase 2 — Shadow 对比(不切主源,只观测)**
- 一个脚本:同一交易日,腾讯 vs Tushare 的 close/adj_close/vol 逐股对比,输出差异分布。
- **门槛(kill 线)**:adj_close 中位相对差 < 0.1% 且 P95 < 0.5%、覆盖率 ≥ 腾讯的 99%,才允许切。达不到 → 停,留 flag 关着。

**Phase 3 — 灰度切换**
- Shadow 达标后,`AFR_QUOTE_SOURCE=tushare`。观察 1-2 个交易日净值/K线无跳变。
- 一键回滚:flag 改回 `tencent`。

**Phase 4 — 清理**
- 稳定后更新注释(`data_fetcher.py` 头部那句"主源已是Tushare"目前是**错的**),文档定稿。

---

## 5. 需要你拍板的开放问题

1. **范围确认**:#60 收窄为「只切日线 EOD 批量,实时留腾讯」——认可吗?(实时无法切,这是硬约束)
2. **Points 预算**:你的 Tushare 账号积分够 bulk `fetch_market_daily` 每日全市场吗?(单股 `fetch_daily_adjusted` 回填历史更耗分)
3. **复权接缝容忍度**:kill 线定 adj_close 中位差 <0.1% / P95 <0.5% —— 松紧合适吗?
4. **动手节奏**:先只做 Phase 0-2(加 flag + 接线 + shadow 观测,**不切主源**),把 shadow 差异报告给你看了再决定 Phase 3?我建议这样——先看数据,不盲切核心价格源。

---

## 6. Phase 2 执行结果(2026-07-25,shadow 已跑)

已加 `AFR_QUOTE_SOURCE` flag(默认 tencent,零行为变更)+ `scripts/tushare_quote_shadow.py`。
抽样 40 股 × 最近 60 交易日,腾讯 close(qfq) vs Tushare adj_close(qfq):

| 指标 | 值 | kill线 | 结果 |
|------|-----|--------|------|
| 复权价相对差 中位 | **0.0000%** | <0.1% | ✅ |
| P95 | **0.24%** | <0.5% | ✅ |
| P99 / 最大 | 0.89% / 1.34% | — | 少数股(复权事件附近)偏离,未超 P95 门槛 |
| 覆盖率 | **100%**(40/40) | ≥99% | ✅ |

**→ 复权口径 kill 线通过。** Tushare adj_close 与腾讯 qfq 在中位上完全吻合。

## 7. 切换前唯一剩下的 gating 任务:`close` 列语义

**发现**:`adj_close` 是全库一致的前复权锚点(`adjust_factor_sync` 保证),但 `close` 列**语义随源变**:
- 腾讯行:`close` = 前复权 qfq(`transform_to_db_rows` 里 adj_close=close)
- Tushare 行:`close` = **原始 raw**,`adj_close` = qfq

大多数下游读 `COALESCE(adj_close, close)`(ml_quotes / factor_expression / ic_engine / backtest)→ **切源后不受影响**。
需逐一审计的是**直接读裸 `close`** 的消费方(约 5-7 处):`risk_scorer`、`capital_scorer`、`sector_rotation`、`sentiment_scorer`、`policy_event_sync`、`score_history_expand`。切 Tushare 后它们拿到的是 raw 而非 qfq。

**两条收尾路线(需你选)**:
- **A(推荐,零下游风险)**:Tushare 写库时把 `close` 也存成 qfq(= adj_close,同腾讯口径),并把历史 Tushare 回填行的 close 也统一成 qfq。→ 全库 `close` 恒为 qfq,所有下游零改动;raw 若某处需要(如换手=raw价×量)另开 `raw_close` 列。
- **B**:保留 `close`=raw,逐一改上述 5-7 个裸 close 读取点为 `COALESCE(adj_close, close)`。→ 更"正确"但改动面大、易漏。

## 8. 下一步(待拍板)

1. shadow 已过 → 认可进入切换准备吗?
2. `close` 语义选 **A(统一 qfq,零下游风险)** 还是 **B(逐点改读 adj_close)**?我倾向 A。
3. 定了之后:Phase 1 接线(`_fetch_daily_quotes_tushare` + 按 flag 分派)→ 小样本灰度 → 全量切 `AFR_QUOTE_SOURCE=tushare`,一键可回滚。

---
*盘点基于只读代码走查 + shadow 实测(`data_fetcher`/`quote_sync`/`tushare_adapter`/`adjust_factor_sync`);已加 flag(默认关)与 shadow 脚本,未改动任何 live 取数行为。*
