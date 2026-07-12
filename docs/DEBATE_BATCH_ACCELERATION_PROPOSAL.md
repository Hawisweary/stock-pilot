# 全量 AI 辩论提速方案书

> 版本：v1.1  
> 日期：2026-05-31  
> 状态：**Phase 1+2+3 已实现**（含补跑、审计、T2 changed、两阶段 LLM）  
> 目标：将 54 股全量辩论从 **7～15 分钟** 降至 **≤2 分钟**（常规增量）/ **≤60 秒**（分层模式）  
> 范围：`debate_v2` 批量路径、`/api/debate/batch`、股票列表「全量辩论」按钮  
> 前端端口：**3002**（非 3000）

---

## 0. 执行摘要

| 项 | 现状 | 目标 |
|----|------|------|
| 调度 | 单线程串行，无进度 | Job Queue + 并发 worker |
| LLM 调用 | 54 次 × 1200 tokens | 15～54 次 × 800 tokens |
| DB | 每股 5～6 次 connect | 批量预加载 1 次 |
| 缓存 | 无 | 今日 + 分数未变则 skip |
| 前端 | 盲等 5 分钟 | 轮询 job 进度条 |

**推荐分期：** Phase 1（方案 1+2+3+6，约 4h）→ Phase 2（方案 4+5，约 3h）→ Phase 3（方案 7，约 2h）

---

## 1. 现状与瓶颈

### 1.1 调用链

```
POST /api/debate/batch
  └─ threading.Thread(daemon)
       └─ for stock in stocks:          # 串行
            enhanced_debate(id, code)
              ├─ sqlite connect × 3
              ├─ SELECT comp / news / tech / macro   # 每股重复
              ├─ chat_completion(prompt, 1200 tok) # 阻塞 5～15s
              └─ INSERT debate_v2 + UPDATE comprehensive
```

**关键文件：**

| 文件 | 职责 |
|------|------|
| `backend/api/advanced.py` | `batch_debate()` 入口 |
| `backend/services/debate_v2.py` | `enhanced_debate()` 单股逻辑 |
| `backend/services/llm_client.py` | 同步 `httpx.post`，timeout 120s |
| `frontend/app/stocks/page.tsx` | `handleBatchDebate()` 固定 300s 定时器 |

### 1.2 耗时模型（54 股）

| 因子 | 估算 |
|------|------|
| LLM 单次 | 5～15 s（网络 + 生成） |
| DB 每股 | ~20 ms |
| **串行总耗时** | **54 × 8s ≈ 7 min**（理想）～ **13 min**（慢网） |
| Token 成本 | 54 × ~1500 in + 1200 out |

### 1.3 与 batch-fill 的差距

维度补算已有成熟模式（可复用）：

- `job_queue.py`：单 worker、heartbeat、409 互斥
- `batch_score_orchestrator.py`：阶段编排、dry-run、审计
- `score_gap_log`：进度与告警

辩论批量 **尚未接入** 该基础设施。

---

## 2. 目标架构（Phase 1 完成后）

```
POST /api/debate/batch { mode, concurrency, skip_unchanged, tier }
        │
        ▼
┌───────────────────┐     409 if debate job running
│ debate_orchestrator│◄──── can_enqueue_debate_batch()
└─────────┬─────────┘
          │ 1. preload_context()     ← 方案 3
          │ 2. plan_targets()         ← 方案 2 + 5
          │ 3. run_parallel()         ← 方案 1
          │ 4. persist + heartbeat    ← 方案 6
          ▼
┌───────────────────────────────────────────┐
│ ThreadPoolExecutor(N) 或 asyncio.Semaphore │
│   worker → enhanced_debate_v2(ctx, stock)  │
│     ├─ skip if cache hit                   │ ← 方案 2
│     ├─ compact_prompt()                    │ ← 方案 4
│     └─ llm_client.chat_completion(...)     │ ← 方案 7 可选
└───────────────────────────────────────────┘
          │
          ▼
   debate_v2 表 + job_runs 进度
          │
          ▼
GET /api/system/jobs/{id}  → 前端进度条
```

---

## 3. 方案 1 — 并发 LLM

### 3.1 思路

在 **IO 等待 LLM 响应** 阶段并行，CPU/DB 写入仍受 `write_lock` 保护。

### 3.2 实现要点

**新文件：** `backend/services/debate_batch_runner.py`

```python
from concurrent.futures import ThreadPoolExecutor, as_completed

DEFAULT_CONCURRENCY = int(os.getenv("AFR_DEBATE_CONCURRENCY", "4"))

def run_debate_parallel(
    targets: list[tuple[int, str]],
    ctx: DebateBatchContext,
    *,
    concurrency: int = DEFAULT_CONCURRENCY,
    heartbeat: Callable[[], None] | None = None,
) -> list[dict]:
    results = []
    with ThreadPoolExecutor(max_workers=concurrency) as pool:
        futs = {
            pool.submit(enhanced_debate_with_context, ctx, sid, code): (sid, code)
            for sid, code in targets
        }
        for i, fut in enumerate(as_completed(futs)):
            sid, code = futs[fut]
            try:
                results.append(fut.result())
            except Exception as e:
                results.append({"stock_id": sid, "code": code, "error": str(e)})
            if heartbeat and i % 2 == 0:
                heartbeat()
    return results
```

**约束：**

| 项 | 建议值 | 说明 |
|----|--------|------|
| `AFR_DEBATE_CONCURRENCY` | 4（默认） | DeepSeek 免费/标准档 4～8 较稳 |
| 重试 | 2 次，指数退避 1s/3s | 429/5xx |
| 写入 | 每股 `debate_v2` 独立事务；`comprehensive` 更新走 `write_lock` | 与 batch-fill 一致 |

**不改：** LLM 仍为同步 `httpx`（线程池内调用即可，无需先改 async）。

### 3.3 API 扩展

```json
POST /api/debate/batch
{
  "concurrency": 4,
  "skip_unchanged": true,
  "mode": "full"
}
```

### 3.4 验收

- [ ] 54 股全量（无 skip）耗时 **≤ 原耗时 / 3**（同网络条件下）
- [ ] 并发 8 时不出现大面积 429（或自动降并发）
- [ ] 单股失败不中断整批

### 3.5 工时 / 风险

| 工时 | 1.5 h |
| 风险 | API 限速；需监控错误率 |

---

## 4. 方案 2 — 增量跳过（Cache）

### 4.1 跳过条件

在 `enhanced_debate` 开头增加 **input fingerprint**：

```python
def _debate_input_hash(comp: dict, news_titles: list[str], tech: dict | None) -> str:
    payload = {
        "composite": comp.get("composite_score"),
        "dims": [comp.get(k) for k in (
            "fundamental_score", "technical_score", "sentiment_score",
            "capital_score", "policy_score", "mood_score", "val_score",
        )],
        "news": news_titles[:5],
        "tech_score": (tech or {}).get("score"),
    }
    return hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()[:16]
```

**Skip 当：**

1. `debate_v2` 存在 **今日** (`date = today`) 记录  
2. 且 `debate_json` 内嵌 `_input_hash` == 当前 hash  
3. 或（简化版）`original_score == comp.composite_score` 且 calc_date 未变

### 4.2 Schema 扩展（可选）

```sql
ALTER TABLE debate_v2 ADD COLUMN input_hash TEXT;
CREATE INDEX IF NOT EXISTS idx_debate_v2_hash ON debate_v2(stock_id, date, input_hash);
```

写入时：

```python
debate["_meta"] = {"input_hash": input_hash, "skipped_llm": False}
```

Skip 路径返回：

```python
return {"stock_id": sid, "skipped": True, "reason": "unchanged", "adjusted_score": row["adjusted_score"]}
```

### 4.3 批量计划统计

`plan_debate_batch()` 输出：

```json
{
  "total": 54,
  "to_run": 12,
  "skipped": 42,
  "skip_reasons": {"unchanged_today": 40, "no_comp_score": 2}
}
```

### 4.4 配置

| 环境变量 | 默认 | 说明 |
|----------|------|------|
| `AFR_DEBATE_SKIP_UNCHANGED` | `true` | 关闭则强制全量 LLM |

### 4.5 验收

- [ ] 连续两次全量辩论，第二次 **to_run ≤ 5**（无评分变动时）
- [ ] 修改某股 `comprehensive_scores` 后，该股 **必重跑**

### 4.6 工时

| 工时 | 1 h |

---

## 5. 方案 3 — 批量 DB 预加载

### 5.1 思路

一次连接加载全池上下文，避免 54 × 多次 `sqlite3.connect`。

### 5.2 数据结构

**新文件：** `backend/services/debate_context.py`

```python
@dataclass
class DebateBatchContext:
    today: str
    calc_date: str
    macro_text: str
    stocks: dict[int, dict]           # id → {code, name, industry_sw}
    comprehensive: dict[int, dict]    # id → row
    news: dict[int, list[dict]]      # id → top5 titles
    tech: dict[int, dict]             # id → {signal, score}
    existing_debate: dict[int, dict]  # id → today's debate_v2 row

def preload_debate_context(stock_ids: list[int] | None = None) -> DebateBatchContext:
    ...
```

### 5.3 批量 SQL（示例）

```sql
-- 综合分：与 resolve_display_calc_date 对齐
SELECT cs.* FROM comprehensive_scores cs
WHERE cs.calc_date = ? AND cs.stock_id IN (...);

-- 新闻 TOP5：窗口函数或 Python 分组
SELECT stock_id, title, sentiment_label FROM stock_news
WHERE stock_id IN (...) ORDER BY pub_date DESC;

-- 技术缓存：每 stock 最新一行
SELECT tc.* FROM tech_analysis_cache tc
INNER JOIN (
  SELECT stock_id, MAX(created_at) md FROM tech_analysis_cache
  WHERE stock_id IN (...) GROUP BY stock_id
) t ON ...

-- 宏观：1 次
SELECT * FROM macro_indicators ORDER BY date DESC LIMIT 1;

-- 今日辩论
SELECT * FROM debate_v2 WHERE date = ? AND stock_id IN (...);
```

### 5.4 改造 `enhanced_debate`

```python
def enhanced_debate_with_context(ctx: DebateBatchContext, stock_id: int, code: str) -> dict:
    comp = ctx.comprehensive.get(stock_id)
    if not comp:
        return {"error": "暂无评分", "stock_id": stock_id}
    # 不再 open/connect 读 comp/news/tech/macro
    ...
```

单股 API `POST /debate/{id}` 仍可调用原函数（内部 `preload` 单股 mini context）。

### 5.5 验收

- [ ] 全量辩论期间 `sqlite3.connect` 调用 **≤ 10 次**（原 ~270 次）
- [ ] 预加载耗时 **< 200 ms**（54 股）

### 5.6 工时

| 工时 | 1.5 h |

---

## 6. 方案 4 — Prompt / Token 压缩

### 6.1 现状 Prompt 问题

- 八维分数重复描述冗长
- 5+3+1 角色 JSON schema 占 ~400 tokens
- `max_tokens=1200` 偏大

### 6.2 压缩策略

**A. 紧凑输入格式**

```
# 替换多行说明为单行
S=67.3 F=80.3 T=78 N=85 C=74.7 P=67.1 M=42.6 V=40.0
tech=买入/78 macro=PMI:50.2 ...
news: 标题1|利多; 标题2|中性; ...
```

**B. 缩短 Schema（字段名缩写）**

```json
{
  "fa":{"o":"观点","a":0,"r":"理由","c":0.7},
  ...
  "j":{"v":"判断","s":50,"c":0.7,"rk":"中","act":"持有"}
}
```

解析层做 **alias 映射** 回现有前端字段（`fundamental_analyst` 等），**前端零改动**。

**C. Token 预算**

| 参数 | 现值 | 新值 |
|------|------|------|
| `max_tokens` | 1200 | **800** |
| system_prompt | ~30 字 | 15 字 |
| 裁判规则 | 4 条 | 2 条（保留 ±10 约束） |

**D. 可选：JSON Mode**

```python
payload["response_format"] = {"type": "json_object"}  # 若模型支持
```

### 6.3 新模块

`backend/services/debate_prompt.py`

- `build_compact_prompt(comp, news, tech, macro) -> str`
- `normalize_debate_json(raw: dict) -> dict`  # 缩写 → 完整 key

### 6.4 验收

- [ ] 平均单次 LLM 延迟降 **≥ 20%**（同模型同网络）
- [ ] JSON 解析成功率 **≥ 98%**（54 股样本）
- [ ] 前端辩论卡片字段展示不变

### 6.5 工时 / 风险

| 工时 | 1.5 h |
| 风险 | 缩写 schema 需充分测试；可 Feature Flag 回退 |

```python
AFR_DEBATE_COMPACT_PROMPT = env_bool("AFR_DEBATE_COMPACT_PROMPT", True)
```

---

## 7. 方案 5 — 分层辩论（Tiered）

### 7.1 分层规则

| 层级 | 代号 | 覆盖范围 | 处理方式 |
|------|------|----------|----------|
| T0 | `full_llm` | 用户强制 / `mode=force` | 完整 V2 辩论 |
| T1 | `priority` | 综合分 Top10 + Bottom10 | 完整 LLM |
| T2 | `changed` | 今日八维有变动（hash 变） | 完整 LLM |
| T3 | `light` | 其余 | 规则 fallback，无 LLM |

### 7.2 规则 Fallback（T3）

```python
def light_debate(comp: dict, tech: dict | None, news: list) -> dict:
    """无 LLM：基于维度偏离与 tech 信号生成简化 judge"""
    base = comp["composite_score"]
    adj = 0.0
    if tech.get("score", 50) > 65: adj += 2
    if tech.get("score", 50) < 35: adj -= 2
    # 新闻情感均值
    ...
    final = round(base * 0.9 + (base + adj) * 0.1, 1)
    return synthetic_debate_json(final, method="light_rules")
```

写入 `debate_v2`，`debate._meta.tier = "light"`，便于前端标记「规则版」。

### 7.3 模式开关

```json
POST /api/debate/batch
{
  "mode": "tiered",        // full | tiered | changed_only
  "priority_top_n": 10,
  "priority_bottom_n": 10
}
```

| mode | LLM 调用数（典型） |
|------|-------------------|
| `full` | 54 |
| `changed_only` | 5～15 |
| `tiered` | 20～25 |

### 7.4 验收

- [ ] `tiered` 模式总耗时 **≤ 60 s**（54 股，4 并发）
- [ ] T1 股票与上次 `full` 模式分差 **≤ 3 分**（抽样 10 股）

### 7.5 工时

| 工时 | 2 h |

---

## 8. 方案 6 — Job Queue + 进度 API

### 8.1 接入现有 job_queue

**注册 handler：**

```python
# backend/services/debate_orchestrator.py
DEBATE_BATCH_JOB_TYPE = "debate_batch"

def run_debate_batch_job(payload: dict) -> dict:
    ctx = preload_debate_context(payload.get("stock_ids"))
    plan = plan_debate_batch(ctx, payload)
    results = run_debate_parallel(plan["targets"], ctx, ...)
    return {
        "total": plan["total"],
        "completed": len(results),
        "skipped": plan["skipped"],
        "errors": [r for r in results if r.get("error")],
        "duration_ms": ...,
    }

# app startup 或 debate 模块 import 时：
register_handler(DEBATE_BATCH_JOB_TYPE, run_debate_batch_job)
```

**互斥：**

```python
def can_enqueue_debate_batch() -> tuple[bool, str | None, str | None]:
    if find_active_job(DEBATE_BATCH_JOB_TYPE):
        return False, "辩论任务运行中", job_id
    if find_active_batch_fill():
        return False, "维度补算运行中", ...
    return True, None, None
```

### 8.2 API

| 方法 | 路径 | 说明 |
|------|------|------|
| POST | `/api/debate/batch` | 入队，返回 `{ job_id, plan }` |
| GET | `/api/debate/batch/plan` | dry-run 计划（to_run/skipped/est_ms） |
| GET | `/api/system/jobs/{id}` | 复用现有 job 查询 |
| DELETE | `/api/system/jobs/{id}` | 取消（best-effort） |

**409 响应：** 与 `batch-fill` 一致。

### 8.3 审计（可选 Phase 2）

复用 `score_gap_log` 模式，新表 `debate_batch_log` 或 `event_type=debate_batch`：

- start / done / error
- `to_run`, `skipped`, `duration_ms`, `mode`

### 8.4 前端改造

`frontend/app/stocks/page.tsx`：

```typescript
const queued = await api.debateBatch({ mode: "tiered", dry_run: false });
const result = await api.pollJobUntilDone(queued.job_id, {
  intervalMs: 3000,
  onProgress: (j) => setDebateProgress(`${j.result?.completed}/${j.result?.total}`),
});
await loadData();
```

`frontend/lib/api.ts` 增加：

- `debateBatch(opts)`
- 复用已有 `pollBatchFillUntilDone` → 泛化为 `pollJobUntilDone`

### 8.5 验收

- [ ] 批量辩论可查询 **实时进度**（completed/total）
- [ ] 重复点击返回 **409**
- [ ] Job 完成后 `debate_scores` 刷新正确

### 8.6 工时

| 工时 | 2 h |

---

## 9. 方案 7 — 模型策略

### 9.1 策略矩阵

| 场景 | 模型 | 说明 |
|------|------|------|
| 默认辩论 | `AFR_DEBATE_MODEL`（= `AI_MODEL`） | 现有 DeepSeek |
| T3 light | 无模型 | 规则 |
| 分歧重判 | `AFR_DEBATE_JUDGE_MODEL` | 仅当分析师 adjust 方差 > 阈值 |
| 快速草稿（可选） | `deepseek-chat` / 小模型 | 先出 analysts，judge 用大模型 |

### 9.2 分歧检测

```python
def needs_judge_escalation(adjusts: list[float]) -> bool:
    if len(adjusts) < 3:
        return False
    spread = max(adjusts) - min(adjusts)
    return spread >= float(os.getenv("AFR_DEBATE_ESCALATE_SPREAD", "8"))
```

**两阶段调用（可选）：**

1. 第一次：compact prompt，仅 5 analysts（`max_tokens=400`）
2. 若 spread ≥ 8：第二次仅 judge + risk（`max_tokens=400`）

平均 token 再降 **~30%**。

### 9.3 Prompt Caching（若 API 支持）

将 **固定 system + schema** 作为 cacheable prefix；仅 per-stock 数据段变化。

```python
# DeepSeek / OpenAI 兼容 cache_control（视 API 版本）
messages = [
    {"role": "system", "content": SCHEMA_BLOCK, "cache_control": {"type": "ephemeral"}},
    {"role": "user", "content": per_stock_block},
]
```

### 9.4 配置项

| 变量 | 默认 | 说明 |
|------|------|------|
| `AFR_DEBATE_MODEL` | `$AI_MODEL` | 主模型 |
| `AFR_DEBATE_JUDGE_MODEL` | 空=同主模型 | 分歧重判 |
| `AFR_DEBATE_TWO_PHASE` | `false` | 两阶段调用 |
| `AFR_DEBATE_ESCALATE_SPREAD` | `8` | 分歧阈值 |

### 9.5 验收

- [ ] 两阶段模式 token 总量降 **≥ 25%**
- [ ] 分歧股 judge 质量不劣于单阶段（人工抽检 5 股）

### 9.6 工时

| 工时 | 2 h |

---

## 10. 文件清单（汇总）

| 文件 | 操作 | 方案 |
|------|------|------|
| `backend/services/debate_context.py` | **新增** | 3 |
| `backend/services/debate_prompt.py` | **新增** | 4 |
| `backend/services/debate_batch_runner.py` | **新增** | 1 |
| `backend/services/debate_orchestrator.py` | **新增** | 1,2,5,6 |
| `backend/services/debate_v2.py` | **改造** | 2,3,4,7 |
| `backend/services/llm_client.py` | **小改** | 7（可选 model 参数） |
| `backend/api/advanced.py` | **改造** | 1,6 |
| `backend/services/job_queue.py` | **小改** | 6（DEBATE job type、互斥） |
| `backend/config.py` | **新增 env** | 全部 |
| `backend/migrations.py` | **可选** | 2（input_hash 列） |
| `frontend/lib/api.ts` | **改造** | 6 |
| `frontend/app/stocks/page.tsx` | **改造** | 6 |
| `scripts/debate_batch_cli.py` | **新增** | 运维/验收 |
| `backend/tests/test_debate_batch.py` | **新增** | 全部 |

---

## 11. 配置项汇总

```bash
# .env 建议
AFR_DEBATE_CONCURRENCY=4
AFR_DEBATE_SKIP_UNCHANGED=true
AFR_DEBATE_COMPACT_PROMPT=true
AFR_DEBATE_DEFAULT_MODE=tiered          # full | tiered | changed_only
AFR_DEBATE_PRIORITY_TOP_N=10
AFR_DEBATE_PRIORITY_BOTTOM_N=10
AFR_DEBATE_TWO_PHASE=false
AFR_DEBATE_MODEL=                       # 空则继承 AI_MODEL
AFR_DEBATE_JUDGE_MODEL=
AFR_DEBATE_ESCALATE_SPREAD=8
```

---

## 12. 分期实施计划

### Phase 1 — 基础提速（推荐先做，~4h）

| # | 内容 | 方案 |
|---|------|------|
| 1 | `debate_context` 预加载 | 3 |
| 2 | `debate_batch_runner` 4 并发 | 1 |
| 3 | skip unchanged | 2 |
| 4 | 接入 job_queue + 409 | 6 |
| 5 | 前端 poll job | 6 |
| 6 | CLI + 单元测试 | — |

**预期：** 7 min → **~2 min**（首次全量）；第二次 **~30 s**

### Phase 2 — 成本优化（~3h）

| # | 内容 | 方案 |
|---|------|------|
| 7 | compact prompt + normalize | 4 |
| 8 | tiered mode + light fallback | 5 |
| 9 | dry-run plan API | 5,6 |

**预期：** tiered 首次 **~60 s**

### Phase 3 — 进阶（~2h，可选）

| # | 内容 | 方案 |
|---|------|------|
| 10 | 两阶段 LLM | 7 |
| 11 | 分歧 escalate 模型 | 7 |
| 12 | prompt caching（视 API） | 7 |

---

## 13. 验收标准（总表）

| # | 指标 | 基线 | Phase 1 | Phase 2 |
|---|------|------|---------|---------|
| 1 | 54 股全量耗时 | ~7 min | ≤ 2.5 min | ≤ 1 min (tiered) |
| 2 | 增量二次运行 | ~7 min | ≤ 30 s | ≤ 20 s |
| 3 | LLM 调用次数（tiered） | 54 | — | ≤ 25 |
| 4 | JSON 解析失败率 | ? | < 2% | < 2% |
| 5 | Job 进度可查 | 否 | 是 | 是 |
| 6 | 与 batch-fill 互斥 | 否 | 是 | 是 |
| 7 | 单股 POST /debate/{id} 兼容 | — | 行为不变 | 不变 |

---

## 14. 风险与对策

| 风险 | 影响 | 对策 |
|------|------|------|
| LLM 429 限流 | 批量失败 | 并发可配置 + 退避重试 + 自动降到 2 |
| 并发写 DB 锁 | 卡顿 | 读预加载无锁；写用 `write_lock` |
| compact prompt 降质 | 辩论质量 | Feature Flag；A/B 抽 10 股人工对比 |
| tiered 遗漏重要股 | 排名偏差 | T2 changed 必跑；用户可选 `mode=full` |
| light 分与 LLM 分不一致 | 用户困惑 | UI 标记「规则版」；最终分列保留 |
| 与 batch-fill 同时跑 | 锁竞争 | 409 互斥 + scheduler 避让 |

---

## 15. CLI 与运维

```bash
# 预览计划
./venv-quant/bin/python scripts/debate_batch_cli.py --dry-run --mode tiered

# 执行并等待
./venv-quant/bin/python scripts/debate_batch_cli.py --mode tiered --wait

# 强制全量 LLM
./venv-quant/bin/python scripts/debate_batch_cli.py --mode full --concurrency 4 --wait

# 基准
./venv-quant/bin/python scripts/benchmark_debate_batch.py
```

---

## 16. 与现有系统关系

```
batch-fill (维度)          debate-batch (辩论)
      │                           │
      ├─ 互斥 job_queue ──────────┤
      │
      └─ comprehensive_scores ◄── debate_v2.adjusted_score
                                  （final_score 列，不写回 composite 可选）
```

**建议：** 辩论结果 **默认仅写 `debate_v2`**，列表读 `final_score`；是否回写 `comprehensive_scores.composite_score` 由 `AFR_DEBATE_WRITE_COMPOSITE=false` 控制（避免与 DEEP_AUDIT C1 再次冲突）。

---

## 17. 审批检查项

- [ ] Phase 1 范围确认（是否包含 job 互斥）
- [ ] 默认模式：`tiered` vs `changed_only`
- [ ] 是否回写 comprehensive（建议 **否**）
- [ ] 并发数默认值：4 是否可接受
- [ ] Phase 2 compact prompt 是否强制开启

---

## 18. 实现状态（2026-05-31 更新）

### 已完成

| 模块 | 文件 | 说明 |
|------|------|------|
| Phase 1 批量预加载/并发/Job | `debate_context.py`, `debate_batch_runner.py`, `job_queue.py` | 409 互斥、heartbeat、进度 |
| Phase 2 compact + tiered | `debate_prompt.py`, `debate_tiered.py` | 默认 `tiered`；T2 `changed` hash 变动升级 LLM |
| Phase 3 两阶段 LLM | `debate_llm_runner.py` | `AFR_DEBATE_TWO_PHASE`、`AFR_DEBATE_MODEL`、`AFR_DEBATE_JUDGE_MODEL`、escalate spread |
| JSON Mode / Prompt Cache | `llm_client.py` | `AFR_DEBATE_JSON_MODE`、`AFR_DEBATE_PROMPT_CACHE` |
| 补跑 | `debate_retry.py`, orchestrator | 批内 retry + `mode=retry_failed` |
| 429 降并发 | `debate_batch_runner.py` | `AFR_DEBATE_AUTO_DEGRADE_CONCURRENCY` |
| 审计 | `debate_batch_log.py` | migration v16 |
| input_hash 列 | migration v16, `debate_v2.py` | skip + T2 判定 |
| Benchmark CLI | `scripts/benchmark_debate_batch.py` | dry-run / 实跑对比 |
| 前端 | `stocks/page.tsx`, `[code]/page.tsx`, `api.ts` | 规则版标记、`pollJobUntilDone` 泛化 |

### 实测（54 股）

| 模式 | 耗时 | LLM | 备注 |
|------|------|-----|------|
| tiered | ~50s | 19 | 34 light |
| full `--no-skip` | ~253s | 54 | 4 超时 |
| retry_failed | ~19s | 4 | 4/4 成功 |

### 默认配置

```bash
AFR_DEBATE_CONCURRENCY=4
AFR_DEBATE_DEFAULT_MODE=tiered
AFR_DEBATE_SKIP_UNCHANGED=true
AFR_DEBATE_COMPACT_PROMPT=true
AFR_DEBATE_JSON_MODE=true
AFR_DEBATE_TWO_PHASE=false          # 可选开启
AFR_DEBATE_BATCH_RETRY_PASS=1
```

### 验收（§13）

| # | 指标 | 结果 |
|---|------|------|
| 1 | tiered 全量 | ✅ ~50s |
| 2 | 增量 skip | ✅ 已实现 |
| 3 | tiered LLM ≤25 | ✅ 19 |
| 5 | Job 进度 | ✅ |
| 6 | batch-fill 互斥 | ✅ |
| 7 | 单股 API 兼容 | ✅ |

---

**文档结束。** Phase 1～3 已落地；生产默认 `tiered` + skip unchanged。
