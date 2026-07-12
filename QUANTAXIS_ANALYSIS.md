# QUANTAXIS 架构分析与可复用模块

> 分析日期: 2026-05-24 | 项目: QUANTAXIS v2.1.0-alpha2 (10.6k Stars)

---

## 一、核心模块全景（13 个模块 → 当前项目可用度）

| QUANTAXIS 模块 | 功能 | 当前项目状态 | 复用度 |
|:---|:---|:---|:---:|
| **QAFetch** | 多源数据获取（tushare/pytdx/CTP） | ✅ 已有 akshare+腾讯 | ⭐⭐ |
| **QAData** | 内存数据库，L2/Tick/Transaction | ❌ 无 L2 数据 | ⭐⭐⭐ |
| **QADataBridge** | Pandas↔Polars↔Arrow 零拷贝 | ❌ 无 | ⭐⭐⭐⭐⭐ |
| **QAFactor** | 因子全生命周期：入库/测试/合并 | ⚠️ 简单因子 IC | ⭐⭐⭐⭐ |
| **QAIndicator** | 自定义指标 + 全市场批量计算 | ✅ 已有 RSI/MACD/KDJ | ⭐⭐ |
| **QAStrategy** | CTA/套利策略模板 | ⚠️ 仅价格因子回测 | ⭐⭐⭐ |
| **QARSBacktest** | Rust 回测引擎（**10x 加速**） | ❌ 纯 Python 回测 | ⭐⭐⭐⭐⭐ |
| **QARSAccount** | Rust 账户 + 持仓管理（**100x**） | ❌ 简单持仓 | ⭐⭐⭐⭐ |
| **QIFI/qifiaccount** | 统一账户协议，Python/Rust/C++ 一致 | ❌ 无 | ⭐⭐⭐⭐ |
| **QAPubSub** | RabbitMQ 消息队列 | ❌ 无 | ⭐⭐⭐ |
| **QASchedule** | 定时任务调度 | ⚠️ 无自动化 | ⭐⭐⭐ |
| **QAWebServer** | Tornado Web | ✅ FastAPI 更现代 | ⭐ |
| **marketpreset** | 手续费/保证金/最小变动 | ❌ 无 | ⭐⭐ |

---

## 二、高价值可复用项（按优先级）

### P0 — 直接提升现有系统

| 模块 | 适用场景 | 集成方式 |
|------|---------|---------|
| **QARSBacktest** (Rust) | 替换 Python 回测引擎 → 10年日线 3 秒 | `pip install qars2`，FastAPI 封装 REST |
| **QADataBridge** (零拷贝) | 技术指标批量计算（39股×2000条）5x 提速 | `QADataBridge.convert(df, to="polars")` |
| **QAFactor** (因子工厂) | 替换简单 IC → 分层回测/因子衰减/多因子合并 | 独立微服务，FastAPI 调用 |

### P1 — 增强功能深度

| 模块 | 适用场景 | 集成方式 |
|------|---------|---------|
| **QIFI 账户协议** | 模拟交易持仓管理、保证金计算 | FastAPI 风险服务 |
| **QAStrategy** | CTA/突破/套利策略模板参考 | 参考设计，自研适配 |
| **QASchedule** | 每日收盘后自动跑：数据刷新→评分→因子→回测 | APScheduler 替代 |

### P2 — 架构升级

| 模块 | 适用场景 | 集成方式 |
|------|---------|---------|
| QAPubSub + RabbitMQ | 实时行情推送、订单流 WebSocket | FastAPI SSE → Next.js |
| QAData + MongoDB | 替换 SQLite，支持 Tick/L2 | 仅当需要高频数据时 |

---

## 三、关键性能指标

| 操作 | 当前 Python | QARS2 Rust | 提升 |
|------|-----------|------------|------|
| 10年日线回测 | ~15s | ~3s | **5x** |
| 39股全量指标计算 | ~8s | ~3s | **2.5x** |
| 创建1000虚拟账户 | - | ~0.5s | - |
| 内存占用（回测） | ~50MB | ~5MB | **-90%** |

---

## 四、当前项目最大缺失 → QUANTAXIS 补齐

| 当前缺口 | QUANTAXIS 对应模块 |
|----------|-------------------|
| 回测性能差 | **QARSBacktest** (Rust 引擎) |
| 因子体系单薄 | **QAFactor** (分层回测/衰减/合并) |
| 无模拟交易账户 | **QIFI** (统一账户协议) |
| 数据层级浅 | **QAData** (L2/Tick/逐笔) |
| 无自动化调度 | **QASchedule** (定时任务) |
| 计算慢（Python 循环） | **QADataBridge** (Polars 加速) |

---

## 五、其他可参考的开源项目

| 项目 | 核心价值 | 适用维度 |
|------|---------|---------|
| **vnpy** (22k⭐) | CTP/XTP 实盘交易接口，事件驱动引擎 | 模拟交易→实盘 |
| **zipline** (17k⭐) | Pipeline API 因子研究，Bcolz 数据存储 | 因子+回测 |
| **backtrader** (14k⭐) | 事件驱动回测，Cerebro 引擎 | 回测框架 |
| **Qlib** (15k⭐) | 微软出品，AI 因子挖掘，滚动训练 | AI+因子 |
| **ricequant/rqalpha** (5k⭐) | 事件驱动回测，支持股票/期货 | 回测 |
| **tqsdk** | 天勤量化，Python 实时行情 | 行情数据 |
| **DolphinDB** | 高性能时序数据库，C++ 内核 | 存储性能 |

---

## 六、推荐执行路径（4h）

| 步骤 | 动作 | 耗时 |
|------|------|------|
| 1 | 安装 qars2，替换回测引擎 | 1h |
| 2 | QAFactor 因子分层回测接入因子看板 | 1.5h |
| 3 | QADataBridge → Polars 加速指标计算 | 0.5h |
| 4 | QASchedule 每日自动跑全流程 | 1h |
