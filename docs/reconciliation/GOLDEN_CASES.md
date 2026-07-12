# Golden 回测用例（Phase 0 baseline）

固定参数与期望指标区间，用于 Python 回测回归与 Rust 对账。

## G1 — 综合分周调仓

| 参数 | 值 |
|------|-----|
| days | 90 |
| top_n | 5 |
| strategy | composite |
| rebalance | weekly |
| min_score | 50 |

记录：`total_return_pct`, `max_drawdown_pct`, `trade_count`, `sharpe`

## G2 — 基本面月调仓

| 参数 | 值 |
|------|-----|
| days | 180 |
| top_n | 3 |
| strategy | val |
| rebalance | monthly |
| min_score | 50 |

## G3 — 动量周调仓

| 参数 | 值 |
|------|-----|
| days | 60 |
| top_n | 5 |
| strategy | momentum |
| rebalance | weekly |
| lookback | 20 |

## 对账阈值（Python vs Rust）

- 累计收益偏差 < 2%
- 最大回撤偏差 < 3%
- 交易笔数偏差 < 5%

Baseline 文件：`baseline_python.json`（由 `tests/test_golden_backtest.py` 在 temp DB 上生成逻辑校验）
