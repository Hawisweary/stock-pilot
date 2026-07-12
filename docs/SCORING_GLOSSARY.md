# 评分字段语义 Glossary

> **版本**：v3.0（2026-06-21）  
> **原则**：全项目只有一个权威综合分 `comprehensive_scores.composite_v5`。  
> 机器可读语义见 `backend/schema_glossary.py`。

---

## 一、已废弃（v3.0 停止写入）

这些字段在 v3.0 后不再更新，2 周兼容期结束后 API 不再返回。

| 字段 | 所在表 | 废弃原因 |
|------|--------|----------|
| `composite_score` | `comprehensive_scores` | 八维加权综合分，与 V5 算法不一致，调度写回互相覆盖（C1/H1）|
| `debate_locked` | `comprehensive_scores` | 辩论锁，随辩论链路整体下线 |
| `adjusted_score` | `debate_v2` | LLM 辩论微调分，与 composite_v5 脱节 |
| `final_score` | API 对外字段 | 辩论链路别名，v3.0 删除 |
| `SCORE_WEIGHTS` | `config.py` | 八维权重配置，随 recompute_composite 一并删除 |

> ⚠️ **grep 辨别**：`comprehensive_scores.composite_score` = 废弃；  
> `factor_scores.composite_score` 等子表同名字段 = 维度内部分（见下节），**不废弃**。

---

## 二、维度内部分（保留，非八维综合）

这些 `composite_score` 是**各维度 scorer 的内部输出**，作为 V5 的输入，不是综合分。

| 字段 | 所在表 | 含义 | V5 对应维 |
|------|--------|------|-----------|
| `composite_score` | `factor_scores` | 基本面维度分（ROE/毛利率/净利率/ROIC 加权） | fundamental |
| `composite_score` | `valuation_scores` | 估值维度分（PE/PB/股息率/PEG） | valuation |
| `composite_score` | `capital_scores` | 资金面维度分（主力净流、融资余额等） | capital |
| `composite_score` | `policy_scores` | 政策维度分（关键词/LLM/补贴） | policy |
| `composite_score` | `sentiment_scores` | 情绪维度分（换手/量比/乖离） | mood（回退） |
| `score` | `tech_analysis_cache` | 技术维度分（均线/KDJ/MACD 规则引擎） | technical |

**命名消歧规则**：引用维度分时总带表名前缀，如 `factor_scores.composite_score`，  
禁止裸写 `composite_score` 不带表名（grep 无法区分废弃/维度内部）。

---

## 三、权威综合分（v3.0 唯一）

```
score = comprehensive_scores.composite_v5
```

| 字段 | 所在表 | 含义 |
|------|--------|------|
| `composite_v5` | `comprehensive_scores` | **唯一权威综合分**，十维 tier + 短板惩罚 + veto |
| `v5_breakdown_json` | `comprehensive_scores` | 十维详情 JSON，每维含 `source`/`status`/`tier`/`score` |
| `veto_status` | `comprehensive_scores` | `ok` / `excluded`（基本面硬否决触发） |
| `score` | `v_stock_scores`（视图，migration v34） | `composite_v5` 的视图别名，API 统一查询入口 |
| `veto_status` | `v_stock_scores` | 视图透传，等同 `comprehensive_scores.veto_status` |
| `breakdown_json` | `v_stock_scores` | 视图透传，等同 `v5_breakdown_json` |

**写入路径**（唯一）：
```
v5_scorer.compute_stock_v5_tiers() → persist_v5_score()
    → comprehensive_scores.composite_v5 + v5_breakdown_json + veto_status
```

**视图 DDL**（migration v34）：
```sql
CREATE VIEW v_stock_scores AS
SELECT s.id AS stock_id, s.code, s.name, s.market, ...,
       cs.composite_v5 AS score, cs.veto_status,
       cs.v5_breakdown_json AS breakdown_json, <all dim scores>
FROM stocks s
LEFT JOIN (latest comprehensive_scores per stock) cs ON s.id = cs.stock_id
WHERE s.is_active = 1;
```

**消费方**：Dashboard / 排名 / strategy_selector / 组合 / 回测 — **全部只读 `v_stock_scores.score`（= composite_v5）**。

---

## 四、调度 DAG 缺数语义

| V5 维 | 缺数处理 | 禁止 |
|-------|----------|------|
| fundamental, quality | skip（tier=None） | 默认 50 |
| industry, news | skip，**不缓存** | 默认 50 |
| capital, valuation, policy, mood | 回退上次有效缓存；全无则 skip | 默认 50 |
| market_env | macro 用 last valid cache；beta 缺 skip | 默认 50 |
| technical | skip | 默认 50 |

**铁律**：scorer 源头返回 `None` → tier skip；`v5_breakdown_json` 必写每维 status。

---

## 五、API 对外字段（v3.0）

| 对外字段 | 值来源 | 说明 |
|----------|--------|------|
| `score` | `composite_v5` | 唯一综合分 |
| `composite_v5` | 同上 | 完整字段名（冗余返回，兼容期） |
| `veto_status` | `comprehensive_scores` | 保留 |
| `v5_breakdown` | `v5_breakdown_json` 解析 | 前端展示用 |
| ~~`composite_score`~~ | 废弃 | 兼容期 `_deprecated` 包装 |
| ~~`final_score`~~ | 废弃 | v3.0 删除 |
| ~~`adjusted_score`~~ | 废弃 | v3.0 删除 |
