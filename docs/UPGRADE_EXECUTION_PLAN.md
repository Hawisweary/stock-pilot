# 量化基础设施升级执行方案（已落地代码）

参见 Phase 0–Batch 4 实施。关键环境变量默认均为 **关闭** 新引擎：

```env
AFR_DATA_ENGINE=sqlite
AFR_BACKTEST_ENGINE=python
AFR_USE_POLARS=false
AFR_FACTOR_MERGE_ENABLED=false
AFR_QLIB_ENABLED=false
AFR_RUST_BACKTEST_APPROVED=false
AFR_QLIB_PREDICTIONS_APPROVED=false
```

## 脚本

| 脚本 | 用途 |
|------|------|
| `scripts/reconcile_data.py` | G1 数据对账 |
| `scripts/reconcile_indicators.py` | G3 Polars vs MyTT |
| `scripts/reconcile_backtest.py` | Golden 回测对账 |
| `scripts/db_snapshot.sh` | 只读 DB 副本 |

## 测试

```bash
pytest tests/test_beta_modules.py tests/test_golden_backtest.py -q
```
