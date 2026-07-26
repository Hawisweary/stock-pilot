# ML v4  redesign：资金流 + LambdaRank + H5

**状态**：设计文档，等待 `moneyflow` 全历史回填完成。

**目标**：用 Tushare L2 个股资金流（2010 起）这一**新信息源**，重新验证 LightGBM 在 A 股是否有可持续 alpha。

**核心假设**：v1/v2/v3 失败不是因为模型不够复杂，而是因为信息源（日 K + 静态基本面）里没有剩余 alpha。资金流是**独立信息源**，且与日频价量/基本面相关性低，是最后一轮值得试的方向。

**Kill 线（开跑前定死）**：24 折 walk-forward mean RankIC < 0.025 → 关闭 ML，不再迭代。

---

## 1. 为什么 v1/v2/v3 救不回，v4 还有机会

| 版本 | 信息源 | 主要改进 | 24 折 H20 RankIC | 诊断 |
|------|--------|----------|------------------|------|
| v1 | 日 K + 基本面 raw | 无 | 0.0324 | 短窗像运气，全历史 ≈ 0 |
| v2 | 同上 | 缺数填充 + miss flag | 0.0164 | 缺数不是病灶 |
| v3 | 同上 | 行业中性分位 | 0.0071 | 行业混淆不是病灶 |
| **v4** | **L2 资金流** + 日 K | **LambdaRank + H5 + 短窗** | 待验证 | 唯一新信息源 |

v1/v2/v3 的失败说明：**在同一批日频特征里换模型形式没用**。v4 的变量是**数据**，不是模型结构。

---

## 2. 数据源：Tushare `moneyflow`

- **接口**：`pro.moneyflow(trade_date=...)`
- **起始**：2010 年
- **粒度**：个股日级别
- **字段**：小单/中单/大单/特大单 买入/卖出金额（元）、净流入额
- **口径**：基于交易所 L2 主动买卖单统计
- **积分要求**：≥ 2000；当前账户 5000+
- **调用方式**：按交易日全市场拉取，每天 1 次调用（约 5000 行），全历史约 4000 交易日
- **预计耗时**：~30 分钟（含 0.35s 频控）

已接入的同步脚本：`backend/scripts/tushare_sync_moneyflow_detail.py`（本次增加 `--full-backfill` 模式）。

---

## 3. 预测目标：H5（未来 5 日收益）

v1/v2/v3 用 H20，窗口太长，资金流信号衰减快。v4 改为 **H5**：

- 资金流是短期信号，5 日符合半衰期
- 训练窗口可缩短到 60 天，更快适应 regime 切换
- 与现有 H5 规则/特征口径对齐，方便对比

标签：
```python
y = (close[t+5] / close[t] - 1) * 100  # 未扣费，仅用于排序学习
```

---

## 4. 模型：LambdaRank 排序学习

v1/v2/v3 用回归（`objective: regression`），但评估用 RankIC。目标函数与评估指标不一致。v4 改为直接学习排序：

```python
from lightgbm import LGBMRanker

model = LGBMRanker(
    objective="lambdarank",
    metric="ndcg",
    lambdarank_truncation_level=20,
    lambdarank_norm=True,
    num_leaves=15,
    max_depth=5,
    learning_rate=0.02,
    lambda_l1=1.0,
    lambda_l2=1.0,
    feature_fraction=0.6,
    bagging_fraction=0.7,
    bagging_freq=1,
    min_data_in_leaf=50,
)
```

每组训练样本按交易日分组（`group=[每天股票数]`），模型学习当日股票相对排序。

---

## 5. 特征设计

### 5.1 资金流特征（核心增量）

```
# 净流入强度
f01: net_mf_amount / avg_amount_20d
f02: net_mf_amount_5d / avg_amount_20d

# 特大单主导
f03: (buy_elg - sell_elg) / turnover_amount
f04: (buy_elg + buy_lg) / total_buy_amount

# 散户反向指标
f05: (buy_sm - sell_sm) / turnover_amount

# 资金流入持续性
f06: net_mf_amount_5d / net_mf_amount_20d
f07: 连续净流入天数（近 5 日）

# 大单/小单背离
f08: (buy_lg + buy_elg - sell_lg - sell_elg) / (buy_sm - sell_sm)

# 行业中性资金流
f09: net_mf_amount - 行业当日平均 net_mf_amount
f10: net_mf_amount_5d - 行业 5 日平均 net_mf_amount
```

所有资金流特征**按交易日做截面 rank/z-score**，避免金额量纲差异。

### 5.2 保留的现有特征（精简版）

v4 不照搬 v3 的 18 维，只保留经 IC 筛选后的低冗余特征：

- 动量/反转：mom_5, reversal_5
- 波动：vol_5
- 换手：turnover_mean_5, turnover_std_5
- 技术：rsi_14, amihud_5
- 估值：pe_ttm_rank（截面分位）
- 基本面：revenue_yoy_q_rank（截面分位）

总计约 **12–14 维**，避免噪声堆积。

---

## 6. 数据清洗与样本构建

- 剔除 ST、退市、停牌、上市不足 60 日的新股
- 剔除当日成交额 < 500 万的小票（资金流噪音大）
- 剔除涨跌停不可交易样本
- 对标签做 1%/99% 截尾（winsorize）
- 按交易日分组，组内做截面 rank

---

## 7. Walk-forward 验证参数

```python
train_window_days = 60      # 短窗口，适应 regime 切换
step_days = 5               # 每 5 日滚动一折
embargo = 2                 # 短 embargo
forward_days = 5            # H5
max_folds = 50              # 或跑满历史
```

评估指标：
- OOS RankIC（主指标）
- OOS IC
- Long-Short 5 日收益（top 20% vs bottom 20%）
- Feature importance 稳定性（跨折 top 5 特征重叠度）

---

## 8. Kill 线与判定口径

**一票否决**：
1. 24 折 mean RankIC < 0.025
2. 全历史 mean RankIC < 0.015
3. 前/后 5 折漂移 > 0.05
4. 任一折 RankIC std > 0.12

若任一条件触发，v4 失败，ML 关闭，写进 `ML_STATUS.md` 定案。

**若通过**：
- 提交 v4 代码
- 将 `lightgbm_h5_wf_v4` 设为 live 模型
- 更新前端门控展示
- 仍需观察实盘 3 个月后再考虑扩大仓位

---

## 9. 风险与诚实预期

### 悲观但可能的情况（概率高）
- 即使资金流是独立信息源，A 股日频层面的 alpha 已被大量量化私募收割
- 公开 L2 资金流在散户/小资金容量下可能已无剩余 alpha
- 结果仍可能落在 0.00–0.02 RankIC，触发 kill 线

### 乐观情况（概率低）
- 资金流的截面 rank + LambdaRank 能捕捉到 0.03+ 的稳定 RankIC
- 特征与现有因子低相关，可作为独立信号源

**预期**：无论结果如何，v4 是 ML 方向的最后一轮。数据驱动，kill 线定生死。

---

## 10. 执行计划

1. **数据回填**：`tushare_sync_moneyflow_detail.py --full-backfill --skip-dc`（约 30 分钟）
2. **特征集成**：`ml_feature_sets.py` 新增 `H5_FEATURES_V4` + 资金流特征
3. **模型切换**：`ml_walkforward.py` 增加 LambdaRank 分支，H5 短窗参数
4. **验证**：跑 24 折 walk-forward
5. **判定**：过 kill 线则推广，否则关闭 ML

---

*相关文档：*`docs/ML_STATUS.md`（v1/v2/v3 定案）
