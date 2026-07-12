# V5 迁移清单 — V5_MIGRATION_INVENTORY.md

> **生成日期**：2026-06-21  
> **用途**：v3.0 升级过程中逐 Phase 勾销；grep 三栏区分废弃/维度内部/权威字段

---

## 说明

| 栏 | 含义 |
|----|------|
| **DEPRECATED** | 属于八维聚合层或辩论层，v3.0 停止写入，2 周后 API 不再返回 |
| **DIMENSION_SCORE** | 各维度子表/scorer 内部分，**保留**，为 V5 提供输入 |
| **AUTHORITATIVE** | `composite_v5`，v3.0 唯一权威综合分 |

---

## DEPRECATED — 需删除/停止写入

### `composite_score`（八维加权综合分，`comprehensive_scores` 表）

| 文件 | 行 | Phase | 状态 |
|------|----|-------|------|
| `backend/database.py` | 167 | P3 | ☐ 停写（列保留，migration 注释） |
| `backend/migrations.py` | 104 | P3 | ☐ migration v34 标注 DEPRECATED |
| `backend/api_models.py` | 152, 161 | P3 | ☐ 改为 Optional + deprecated 注释 |
| `backend/api/stocks.py` | 30 | P3 | ☐ 去掉 SELECT composite_score |
| `backend/api/scores.py` | 43–44, 65, 69, 83–84, 95, 209–210, 234–235, 406, 411, 438, 443, 457, 533 | P3 | ☐ 全部改为 composite_v5 |
| `backend/api/analysis.py` | 131, 151, 174 | P3 | ☐ 改为 composite_v5 |
| `backend/api/dashboard.py` | 86, 242, 245–246, 292–293, 295 | P3 | ☐ 情绪/告警维持各维度分；overview 仅 V5 |
| `backend/services/comprehensive_store.py` | 95–102, 165, 178, 224, 281 | P1/P3 | ☐ 删 recompute_composite |
| `backend/services/comprehensive.py` | 9, 107, 196 | P1/P3 | ☐ 去掉 recompute_composite 调用 |
| `backend/services/freshness.py` | 46–47 | P3 | ☐ 删 SCORE_WEIGHTS 引用 |
| `backend/services/score_history_expand.py` | 8, 144 | P3 | ☐ 删 SCORE_WEIGHTS 引用 |
| `backend/config.py` | 35–43 | P3 | ☐ 删 SCORE_WEIGHTS / DEFAULT_SCORE |
| `frontend/lib/api.ts` | 201, 218, 236, 295, 540 | P5 | ☐ 删 composite_score 类型字段 |
| `frontend/app/stocks/[code]/page.tsx` | 402 | P5 | ☐ 删 final_score fallback |

### `debate_v2` / `adjusted_score` / `final_score`（辩论链路）

| 文件 | 行 | Phase | 状态 |
|------|----|-------|------|
| `backend/api/advanced.py` | 218, 248–259 | P2 | ☐ `/debate/*` → 410 |
| `backend/api/stocks.py` | 36 | P2/P3 | ☐ 去掉 dv.adjusted_score JOIN |
| `backend/api/scores.py` | 243–251, 554 | P2 | ☐ 删 debate 查询 |
| `backend/services/debate_v2.py` | 全文 | P2 | ☐ 归档 → `legacy/` |
| `backend/services/debate_orchestrator.py` | 全文 | P2 | ☐ 归档 |
| `backend/services/debate_context.py` | 全文 | P2 | ☐ 归档 |
| `backend/services/debate_prompt.py` | 全文 | P2 | ☐ 归档 |
| `backend/services/debate_align.py` | 全文 | P2 | ☐ 归档 |
| `backend/services/debate_tiered.py` | 全文 | P2 | ☐ 归档 |
| `backend/services/debate_batch_runner.py` | 全文 | P2 | ☐ 归档 |
| `backend/services/debate_llm_runner.py` | 全文 | P2 | ☐ 归档 |
| `backend/services/debate_retry.py` | 全文 | P2 | ☐ 归档 |
| `backend/services/debate_batch_log.py` | 全文 | P2 | ☐ 归档 |
| `backend/services/debate.py` | 全文 | P2 | ☐ 归档 |
| `backend/services/factor_incremental.py` | 197 | P2/P3 | ☐ 删 debate_v2 查询，改读 composite_v5 |
| `backend/services/factor_factory.py` | 232 | P2 | ☐ 删 debate_v2 查询 |
| `backend/services/job_queue.py` | debate 触发 | P2 | ☐ 删 debate batch 入队 |
| `backend/config.py` | 181–204 | P2 | ☐ 删所有 DEBATE_* 配置 |
| `frontend/lib/api.ts` | 32, 100, 628, 633–664, 926–927, 1399 | P5 | ☐ 删 debate* 方法 |

### `SCORE_WEIGHTS` / `recompute_composite`

| 文件 | 行 | Phase | 状态 |
|------|----|-------|------|
| `backend/config.py` | 35–43 | P3 | ☐ 删除 |
| `backend/services/comprehensive_store.py` | 95–102 | P3 | ☐ 删除函数 |
| `backend/services/comprehensive.py` | 9, 196 | P3 | ☐ 删除调用 |
| `backend/services/freshness.py` | 46–47 | P3 | ☐ 删除引用 |
| `backend/services/score_history_expand.py` | 8, 144 | P3 | ☐ 删除引用 |

### `debate_locked`（`comprehensive_scores` 表）

| 对象 | Phase | 状态 |
|------|-------|------|
| `comprehensive_scores.debate_locked` | P3 停写；P4 migration v34 删列 | ☐ |

---

## DIMENSION_SCORE — 保留（V5 输入层）

> 这些 `composite_score` 是**维度自身的分**，非八维聚合综合分，**不删除**。

| 维度 | 子表 | 字段 | 写入模块 |
|------|------|------|----------|
| 基本面 | `factor_scores` | `composite_score` | `factor_engine.py` |
| 估值 | `valuation_scores` | `composite_score` | `valuation_scorer.py` |
| 资金 | `capital_scores` | `composite_score` | `capital_scorer.py` |
| 政策 | `policy_scores` | `composite_score` | `policy_scorer.py` |
| 情绪 | `sentiment_scores` | `composite_score` | `sentiment_scorer.py` |
| 技术 | `tech_analysis_cache` | `score` | `kline_technical.py` |

相关代码（**不需要修改**）：

- `backend/services/capital_scorer.py:173,197–202`
- `backend/services/valuation_scorer.py:109,113,124`
- `backend/services/sentiment_scorer.py:87,98–108`
- `backend/services/policy_scorer.py:354,361,389`
- `backend/api/capital.py:42`（写入 comprehensive_scores.capital_score，保留）
- `backend/api/system.py:68`（写入 comprehensive_scores.policy_score，保留）

---

## AUTHORITATIVE — composite_v5（唯一权威综合分）

> 所有消费方最终目标态：只读此字段。

| 文件 | 行 | 当前状态 | Phase |
|------|----|----------|-------|
| `backend/api/dashboard.py` | 49–93, 128–135, 232–237 | ✅ 已读 composite_v5 | 保持 |
| `backend/api/v5_data.py` | 210–269 | ✅ | 保持 |
| `backend/api/groups.py` | 29 | ✅ | 保持 |
| `backend/api/stocks.py` | 35 | ✅ cv5.composite_v5 | P3 确认唯一 |
| `backend/services/v5_score_query.py` | 全文 | ✅ | 保持 |
| `backend/services/score_sql.py` | 全文 | P4 改查 v_stock_scores 视图 | P4 |
| `frontend/lib/api.ts` | 172, 199, 227 | ✅ composite_v5 | P5 清理 legacy |
| `frontend/app/stocks/page.tsx` | 352–354 | ✅ | 保持 |
| `frontend/app/page.tsx` | 37, 50, 462–476 | ✅ | 保持 |

---

## 勾销进度

| Phase | 内容 | 完成 |
|-------|------|------|
| P0 | 本清单生成 | ✅ 2026-06-21 |
| P1 | 停写 composite_score；双路径 V5 | ☐ |
| P2 | 辩论链路 DEPRECATED 行全部清除 | ☐ |
| P3 | SCORE_WEIGHTS / recompute_composite 行全部清除 | ☐ |
| P3.5 | H4/H5 stocks.py | ☐ |
| P4 | migration v34；v_stock_scores 视图 | ☐ |
| P5 | 前端 DEPRECATED 行全部清除 | ☐ |
| P6 | 安全/可观测 | ☐ |
