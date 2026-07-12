# A股投研系统 v3.5 → v4.0 升级方案书

> 日期: 2026-05-24 | 版本: v1.0 | 状态: 待审批

---

## 总览

| 维度 | v3.5 现状 | v4.0 目标 |
|------|----------|----------|
| 回测性能 | Python 单线程，360天~15s | Rust 引擎，360天~3s |
| 因子体系 | 简单 IC 排名 | 分层回测 + IC 衰减 + 多因子合成 |
| 指标计算 | Pandas 逐列循环 | Polars 向量化 + 零拷贝 |
| AI 因子 | 仅 LLM 辩论 | Qlib 滚动训练 + Alpha158 因子库 |
| 数据存储 | SQLite 单文件 6MB | DolphinDB 时序引擎，毫秒级聚合 |
| 架构 | 单体 FastAPI | 微服务 + 消息队列 |

---

# 第一章：QARSBacktest — Rust 回测引擎

## 1.1 现状

```
当前回测 (price_backtest.py)
├── Python 逐日循环 → 360天/39股/矩阵计算 → ~15s
├── 每次请求重复解析 39×2000×5 字段 ≈ 39万次字典访问
├── 内存峰值 ~50MB（全量日线存 dict）
└── 无并行能力
```

## 1.2 目标架构

```
Future: FastAPI 回测微服务
├── POST /api/backtest/fast → Rust qars2 引擎
│   ├── 输入: JSON params {days, top_n, factors, constraints}
│   ├── 引擎: QARSBacktest.run(strategy, account, data)
│   └── 输出: JSON {returns, drawdowns, trades, metrics}
├── QARSAccount: 1,000 虚拟账户并行回测
├── QARSStrategy: 自定义因子策略模板
└── 性能: 10年日线 → ~3s | 内存 → ~5MB
```

## 1.3 实施计划

| 阶段 | 内容 | 工作量 | 风险 |
|------|------|--------|------|
| **Phase 1** (1h) | `pip install qars2` → FastAPI 适配层 | 低 | qars2 API 兼容性 |
| **Phase 2** (1h) | 编写 QARSStrategy 因子策略模板 | 中 | Rust 策略 DSL 学习 |
| **Phase 3** (0.5h) | 前端适配新 API 响应格式 | 低 | JSON 字段映射 |

## 1.4 验收标准

- [ ] 360天回测耗时 < 5s（当前 15s）
- [ ] 10年回测耗时 < 10s（当前不可行）
- [ ] 回测结果与 Python 版偏差 < 2%
- [ ] 支持 5/10/20 持仓组合一键对比
- [ ] 前端回测页面无感切换

## 1.5 预期收益

```
回测加速: 15s → 3s (5x)
内存占用: 50MB → 5MB (-90%)
并行能力: 1线程 → N线程（多参数网格搜索）
```

---

# 第二章：QADataBridge + Polars — 零拷贝加速

## 2.1 现状分析

```
当前指标计算瓶颈 (TechnicalView + CapitalScorer)
├── pandas.DataFrame.iterrows(): 39股 × 2000条 × 逐个字段
├── 每次 MACD/RSI/KDJ 计算: pandas apply 逐行
├── 数据格式转换: SQLite row → dict → DataFrame (3次拷贝)
└── 热点路径耗时: ~8s（全量指标刷新）
```

### 热点函数分析

| 函数 | 位置 | 耗时 | 可优化 |
|------|------|------|--------|
| MACD/RSI/KDJ 批量计算 | `api/stocks/technical` | ~4s | Polars ewm/rolling |
| 资金面评分 | `capital_scorer.py` | ~2s | 向量化 |
| 日线数据加载 | SQLite → dict → df | ~1s | 零拷贝 |
| 跨进程通信 | Python GIL | - | Arrow IPC |

## 2.2 目标架构

```
QADataBridge 零拷贝管线
┌──────────────┐
│ SQLite/Raw   │  --read_sql-->  Polars DataFrame
└──────────────┘                     │
                          ┌──────────▼──────────┐
                          │  QADataBridge.convert │
                          │  pandas ↔ polars      │
                          │  arrow ↔ numpy        │
                          └──────────┬──────────┘
                                     │
              ┌──────────────────────┼──────────────────────┐
              ▼                      ▼                      ▼
     MACD/RSI/KDJ             资金面评分              因子计算
     Polars rolling        Polars groupby            向 量化
     批量 0.3s              聚合 0.2s                ｜ 运算
```

## 2.3 实施计划

| 阶段 | 内容 | 工时 |
|------|------|------|
| Phase 1 | 安装 Polars，替换 pandas 热点路径 | 1h |
| Phase 2 | QADataBridge 零拷贝转换层 | 0.5h |
| Phase 3 | Arrow IPC 跨进程共享内存 | 0.5h |

## 2.4 改造清单

```python
# 改造前 (pandas, ~8s)
df = pd.DataFrame(rows)                    # 拷贝1
macd = df.groupby('code').apply(macd_fn)  # 拷贝2 GIL锁

# 改造后 (polars, ~3s)
pl_df = pl.from_arrow(arrow_table)         # 零拷贝
macd = pl_df.group_by("code").map_groups(  # 向量化
    lambda g: g.with_columns(
        pl.col("close").ewm_mean(span=12) - 
        pl.col("close").ewm_mean(span=26)
    )
)
```

## 2.5 验收标准

- [ ] 全量指标计算 < 4s（当前 ~8s）
- [ ] 内存峰值降低 40%+
- [ ] 前端无感知
- [ ] 不破坏现有 pandas 接口

---

# 第三章：QAFactor — 因子全生命周期

## 3.1 现状

```
当前因子体系 (scores.py)
├── 8维综合评分: 加权平均（无IC/IR验证）
├── 因子IC分析: 仅页面展示，无语义
├── 无分层回测（Top/Bottom 组合对比）
├── 无因子衰减分析
└── 无多因子合并（仅线性加权）
```

## 3.2 目标架构

```
QAFactor 因子工厂
├── 因子入库
│   ├── 单因子注册: factor_id, name, category, formula
│   ├── 批量计算: 39股 × 2000天 × N因子
│   └── 存储: factor_values 表 (stock_id, date, factor_id, value)
│
├── 因子分析
│   ├── IC序列: 每日 Rank IC + 累计IC曲线
│   ├── IC衰减: lag(1,5,10,20) 衰减图
│   ├── 分层回测: Top/Bottom 20% 组合收益差
│   ├── 因子相关性: 热力图矩阵
│   └── 因子收益: 多空组合累计收益
│
├── 因子合成
│   ├── 等权合并
│   ├── IC_IR加权
│   └── 滚动窗口最优权重
│
└── API 暴露
    ├── GET  /api/factors/list          → 因子库
    ├── GET  /api/factors/{id}/analysis → IC/分层/衰减
    ├── POST /api/factors/merge         → 合成因子
    └── GET  /api/factors/compare       → 多因子对比
```

## 3.3 因子库设计（首批 15 因子）

### 已有因子（8个 → 规范化）

| factor_id | 名称 | 类别 | 来源 |
|-----------|------|------|------|
| F001 | composite_score | 综合 | comprehensive_scores |
| F002 | fundamental_score | 基本面 | 同上 |
| F003 | technical_score | 技术面 | 同上 |
| F004 | sentiment_score | 情绪面 | 同上 |
| F005 | capital_score | 资金面 | 同上 |
| F006 | policy_score | 政策面 | 同上 |
| F007 | mood_score | 情绪 | 同上 |
| F008 | val_score | 估值 | 同上 |

### 新增因子（7个）

| factor_id | 名称 | 类别 | 公式 |
|-----------|------|------|------|
| F009 | momentum_20d | 动量 | close / close.shift(20) - 1 |
| F010 | volatility_20d | 低波 | -std(returns, 20) |
| F011 | volume_ratio | 量价 | vol_5d / vol_20d |
| F012 | rsi_divergence | 反转 | RSI(14) |
| F013 | ma_crossover | 趋势 | MA5 > MA20 ? 1 : -1 |
| F014 | turnover_adj | 流动性 | turnover / turnover.mean(20) |
| F015 | debate_final | AI | debate_v2.adjusted_score |

## 3.4 前端新增页面

```
因子分析页面 (新增路由 /factors)
├── 因子列表卡片（IC/IR/胜率一览）
├── 因子详情
│   ├── IC序列折线图 + 累计IC
│   ├── IC衰减柱状图
│   ├── 分层回测收益曲线（5层）
│   └── 因子相关性热力图
└── 多因子合成面板
    ├── 等权合成
    ├── IC_IR加权合成
    └── 滚动最优权重合成
```

## 3.5 实施计划

| 阶段 | 内容 | 工时 |
|------|------|------|
| Phase 1 | factor_values 表 + 15因子计算入库 | 1.5h |
| Phase 2 | IC/分层回测/衰减分析引擎 | 1.5h |
| Phase 3 | 因子合成 + 前端因子分析页 | 1h |

---

# 第四章：Qlib — AI 因子挖掘 + 滚动训练

## 4.1 现状

```
当前 AI 能力
├── LLM 辩论: 5分析师 + 裁判 → 文本决策
├── 无 ML 模型预测
├── 无滚动窗口训练
├── 无特征工程管线
└── 无模型回测验证
```

## 4.2 Qlib 能带来的能力

```
Microsoft Qlib (15k⭐)
├── Alpha158 因子库 → 158个标准化因子，开箱即用
├── 20+ ML 模型 → LightGBM/XGBoost/LSTM/Transformer
├── 滚动训练 → 每季度重新训练，预测下期收益
├── 回测框架 → 策略回测 + 模型性能评估
├── 组合优化 → 均值方差/风险平价/Black-Litterman
└── 数据格式 → 统一 bin 格式，高性能 IO
```

## 4.3 目标集成

```
Qlib 预测管线
┌──────────────┐     ┌─────────────┐     ┌──────────────┐
│ stock_daily   │────▶│ Alpha158     │────▶│ LightGBM      │
│ quotes        │     │ 因子计算     │     │ 滚动训练      │
└──────────────┘     └─────────────┘     └──────┬───────┘
                                                 │
                    ┌────────────────────────────▼──┐
                    │ 预测输出                       │
                    │ ├── 每日预测收益 (pred_label)   │
                    │ ├── 涨跌概率 (pred_score)      │
                    │ └── Top K 推荐                 │
                    └────────────┬───────────────────┘
                                 │
              ┌──────────────────▼──────────────────┐
              │ 前端集成                              │
              │ ├── AI 预测分 → comprehensive_scores  │
              │ ├── Dashboard: ML 信号卡片           │
              │ └── 回测: Qlib 回测 vs 价格因子回测  │
              └─────────────────────────────────────┘
```

## 4.4 Alpha158 因子子集（选 30 个高 IC 因子）

| 类别 | 因子数 | 示例 |
|------|--------|------|
| 动量类 | 8 | KMID, KLEN, KMID 归一化 |
| 反转类 | 6 | 短期反转、月度反转 |
| 波动类 | 6 | STD20, STD60, 振幅 |
| 量价类 | 5 | VEMA5, VEMA10, 换手率 |
| 情绪类 | 5 | 异常成交量、价格冲击 |

## 4.5 API 设计

```python
# 新增端点
POST /api/qlib/train          # 触发滚动训练
GET  /api/qlib/predictions     # 获取最新预测
GET  /api/qlib/factors         # Alpha158 因子值
GET  /api/qlib/backtest        # Qlib 模型回测
```

## 4.6 实施计划

| 阶段 | 内容 | 工时 | 风险 |
|------|------|------|------|
| Phase 1 | `pip install qlib` + 数据格式转换(A股 → bin) | 1h | A股适配 |
| Phase 2 | Alpha158 + LightGBM 滚动训练管线 | 2h | 训练时间 |
| Phase 3 | 预测结果接入 Dashboard + 回测 | 1h | 前后端对接 |

---

# 第五章：DolphinDB — 高性能时序引擎

## 5.1 当前存储瓶颈

```
SQLite (6.3MB, 25K rows)
├── stock_daily_quotes: 24,960 行 × 8 字段
├── comprehensive_scores: ~200 行
├── 查询: 单表全扫描 OK
├── 聚合: 无原生时序函数
├── 并发: 单写多读（FastAPI async 可应对）
└── 瓶颈: 暂无，但增长到 1000 股 × 10年 时不可行
```

### 预测增长

| 时间 | 股票数 | 日线行数 | SQLite 大小 | 是否可用 |
|------|--------|---------|------------|---------|
| 当前 | 39 | 25K | 6MB | ✅ |
| 半年后 | 200 | 128K | 30MB | ✅ |
| 1年后 | 500 | 320K | 75MB | ⚠️ 慢查询 |
| 全A股 | 5000+ | 3.2M | 750MB | ❌ SQLite 上限 |

## 5.2 DolphinDB 方案

```
DolphinDB Community Edition (免费)
├── 时序数据库: 列式存储，原生 OHLC 支持
├── 查询性能: 5000股 × 10年 聚合 → 毫秒级
├── 内置函数: msum/mavg/mstd/mbeta/mcorr 等 1000+ 时序函数
├── 流计算: 实时行情 → 实时因子 → 实时信号
├── Python API: `import dolphindb as ddb` → 无缝集成
└── 部署: 单节点 Docker，资源占用 ~1GB
```

## 5.3 迁移架构

```
Phase 1 (混合模式，当前阶段无需改动)
┌──────────────┐
│ FastAPI      │
│ SQLite R/W   │ ← 评分/元数据/用户配置
└──────────────┘

Phase 2 (选装，当数据 > 100MB 时)
┌──────────────┐     ┌─────────────────┐
│ FastAPI      │────▶│ DolphinDB       │
│ SQLite (元)  │     │ ├ stock_daily    │
│              │     │ ├ factor_values  │
└──────────────┘     │ ├ minute_quotes  │
                     │ └ stream_engine  │
                     └─────────────────┘

Phase 3 (全量，当接入实时行情)
┌──────────────┐     ┌─────────────────┐
│ Next.js SSE  │◀────│ DolphinDB       │
│ WebSocket    │     │ stream_engine   │
└──────────────┘     │ → 实时推送      │
                     └─────────────────┘
```

## 5.4 性能对比（预估）

| 操作 | SQLite | DolphinDB | 提升 |
|------|--------|-----------|------|
| 单股 2000 天查询 | 2ms | 0.5ms | 4x |
| 全股 MACD 批量计算 | 8s | 0.3s | 25x |
| 因子 IC 计算 (5000股) | ❌ 不可行 | 0.5s | ∞ |
| 分钟线聚合为日线 | N/A | 0.2s | - |

## 5.5 实施条件

```
Phase 2 触发条件（满足任一条即启动）:
├── stock_daily_quotes > 100MB 或 > 500K 行
├── 接入分钟线数据
├── 因子超过 50 个需要批量计算
└── 用户反馈查询响应 > 2s

当前状态: 25K行 / 6MB → 暂不触发，SQLite 完全够用
```

---

# 第六章：总体实施方案

## 6.1 优先级矩阵

```
                    高收益
                      │
        QARSBacktest │ QAFactor
          (5x加速)    │ (因子体系)
                      │
    ──────────────────┼──────────────────
                      │
    QADataBridge      │       Qlib
      (2.5x加速)      │   (AI因子)
                      │
        低影响        │   DolphinDB
                      │   (暂无需求)
                      │
                    低收益
```

## 6.2 推荐执行顺序

| 序号 | 模块 | 工时 | 理由 |
|------|------|------|------|
| **1** | QARSBacktest | 2.5h | 立竿见影，回测提速5x |
| **2** | QAFactor | 4h | 核心能力升级，前端新页面 |
| **3** | QADataBridge | 2h | 顺手优化，不破坏现有代码 |
| **4** | Qlib | 4h | AI 因子 + 滚动训练 |
| **5** | DolphinDB | 搁置 | 当前 SQLite 无瓶颈，数据量触发后再执行 |

## 6.3 总工时

```
Phase 1 (P0, 本次执行):    6.5h
Phase 2 (P1, 下次执行):    4h   (Qlib)
Phase 3 (P2, 条件触发):    搁置 (DolphinDB)
─────────────────────────────────
总计:                      10.5h
```

## 6.4 风险管控

| 风险 | 等级 | 缓解措施 |
|------|------|---------|
| qars2 API 不兼容 | 中 | Phase 1 先跑 Python↔Rust 对比验证 |
| Qlib A股数据格式 | 中 | 参考天池/量化社区 A股适配方案 |
| Polars 破坏 pandas | 低 | 仅替换性能热点，其余保持 pandas |
| DolphinDB 学习曲线 | 低 | Phase 2 条件触发，非必须 |

---

# 附录 A: v3.5 → v4.0 架构变化

```
v3.5 (当前)
FastAPI (单体) + SQLite + Next.js
    ├── api/backtest       (Python 回测)
    ├── api/scores         (简单 IC)
    ├── api/stocks         (pandas 指标)
    └── 无 AI 预测/定时调度

v4.0 (目标)
FastAPI (微服务) + SQLite + Docker + Next.js
    ├── api/backtest/fast  (QARSBacktest Rust)
    ├── api/factors/*      (QAFactor 因子工厂)
    ├── api/qlib/*         (Qlib ML 预测)
    ├── QADataBridge       (Polars 加速层)
    ├── APScheduler        (定时任务)
    └── (可选) DolphinDB   (时序引擎)
```
