# legacy/

v3.0 归档：辩论（debate）链路于 2026-06-21 从主链路移除。

## 归档原因
- `debate_v2.adjusted_score` 是第三个综合分，与 `composite_score` 和 `composite_v5` 形成三源冲突
- v3.0 Single Source of Truth：`composite_v5` 为唯一权威综合分

## 文件清单
- `debate*.py` — 辩论链路服务文件（12 个）
- `test_debate*.py` — 对应测试（3 个）

## API 变更
- `/api/debate/*` → 410 Gone
- `/api/scores/debate-scores` → 410 Gone

## 若需恢复
检出 commit `pre-v3-debate` tag 前的版本。
