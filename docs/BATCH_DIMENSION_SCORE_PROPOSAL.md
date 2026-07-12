# 批量补算维度分方案书

> 版本：v1.4  
> 日期：2026-05-31  
> 状态：**Phase 1 最小集已锁定，可开工**  
> 目标：一键识别「未更新 / 未计算 / 未同步」的八维评分，并按依赖顺序批量补算  
> 变更记录：  
> - v1.3：阻塞竞态、force_recompute、双 sync_rate、job heartbeat  
> - v1.4：write_lock 选型、sentiment 闭区间窗口、日志 scope；Phase 1 收窄为 sync_only 路径

### 评审结论

| 维度 | 评价 |
|------|------|
| 完整性 / 可行性 / 安全性 | v1.3 已闭合；v1.4 消除编码歧义 |
| Phase 1 范围 | **仅 sync_only**；force_recompute / 审计 / 告警 → Phase 2 |

**开发门禁：** §4.0 阻塞项 + §4.0.1 编码细节（非阻塞但已明确）。

---

## 1. 背景与问题

### 1.1 用户诉求

系统中每只股票有 **8 个维度分** + **1 个综合分**，写入 `comprehensive_scores` 表。日常使用中常见三类问题：

| 类型 | 表现 | 用户感知 |
|------|------|----------|
| **未计算** | 维度源表无记录（如 `capital_scores` 无当日行） | 个股页某维度显示 `--` |
| **已过期** | 源表有分，但日期早于最新交易日 | 分数陈旧，排名失真 |
| **未同步** | 源表有分，但 `comprehensive_scores` 对应列为 NULL | 综合分存在、维度分缺失（最隐蔽） |

当前 **没有统一的「缺口扫描 + 定向补算」入口**，运维需手动依次调用 6～8 个分散 API，且无法知道「到底缺什么」。

### 1.2 实测诊断（2026-05-31，54 只活跃股）

| 维度 | comprehensive 缺失 | 源表有分 |
|------|-------------------|----------|
| fundamental_score | **54/54** | factor_scores 61 条 |
| technical_score | 22/54 | tech_analysis_cache 55 条 |
| sentiment_score | **54/54** | stock_news 有 sentiment |
| capital_score | **54/54** | capital_scores 54 条 |
| policy_score | **54/54** | policy_scores 55 条 |
| mood_score | **54/54** | sentiment_scores 54 条 |
| val_score | **54/54** | valuation_scores 54 条 |
| composite_score | 0/54 | — |

对比 **2026-05-29** 批次：八维全部 54/54 完整。

**根因**：ML 混合写入（`sync_ml_to_comprehensive`）在 `2026-05-31` 创建了仅有 `technical_score + composite_score` 的行，**未触发其余维度同步**；而 `POST /scores/comprehensive/calculate` 仅同步 3 个维度（基本面/技术/新闻），不含资金/政策/情绪/估值。

---

## 2. 八维评分架构（现状）

```
┌─────────────────────────────────────────────────────────────────┐
│                    comprehensive_scores                          │
│  fundamental │ technical │ sentiment │ capital │ policy │ mood │
│  val_score   │ composite_score (加权汇总)                        │
└────────▲──────────▲───────────▲─────────▲─────────▲───────▲─────┘
         │          │           │         │         │       │
   factor_scores  tech_cache  stock_news  capital  policy  sentiment
                              (news面)   _scores  _scores  _scores
         │                                      │
   FactorEngine                          valuation_scores
   /scores/recalculate                    (val_score)
```

### 2.1 维度映射表

| 维度列 | 源表 | 计算函数 | 现有批量 API |
|--------|------|----------|--------------|
| `fundamental_score` | `factor_scores` | `FactorEngine.calculate_all` | `POST /api/scores/recalculate` |
| `technical_score` | `tech_analysis_cache` | `tech_ai` 规则/LLM | `POST /api/technical/analyze-all` |
| `sentiment_score` | `stock_news` | `get_stock_sentiment_score()` 见 §2.4 | `POST /api/news/analyze-all` |
| `capital_score` | `capital_scores` | `compute_all_capital` | `POST /api/stocks/capital/analyze-all` |
| `policy_score` | `policy_scores` | `compute_policy_score` | `POST /api/stocks/policy/analyze-all` |
| `mood_score` | `sentiment_scores` | `compute_all_sentiment` | `POST /api/sentiment/analyze-all` |
| `val_score` | `valuation_scores` | `compute_valuation_scores` | `POST /api/stocks/valuation/compute` |
| `composite_score` | — | `recompute_composite` | 各维度 upsert 后自动重算 |

### 2.2 已有能力 vs 缺口

**已有：**
- 各维度独立的 `analyze-all`（Phase II 已优化为批量 SQL，资金/情绪 ~140ms/54 股）
- `GET /api/dashboard/health`：按源表检查数据新鲜度（**不检查 comprehensive 同步状态**）
- `job_queue`：单线程异步任务框架（`job_runs` 表）
- `scheduler.run_daily_tasks`：15:30 定时跑估值/因子/资金/情绪/ML

**缺失：**
1. **缺口扫描**：`(stock_id, dimension) → status` 矩阵
2. **编排器**：按 DAG 依赖顺序、只算缺失项
3. **统一 API**：一个入口触发 + 进度查询
4. **comprehensive 全量同步**：7 维一次性从源表 upsert（现有 `calculate_all` 只覆盖 3 维）
5. **审计与运维能力**：缺口历史、dry-run 预览、并发限流、持续低同步率告警

### 2.3 鲁棒性增强（v1.1 新增）

| 能力 | 说明 | 阶段 |
|------|------|------|
| **缺口审计日志** | 每次扫描/补算写入 `score_gap_log`，便于复盘 | Phase 2 |
| **dry-run 模式** | 只返回计划操作，不写入 DB | Phase 2 |
| **优先级队列** | 先快后慢，用户尽早看到修复效果 | Phase 2 |
| **监控告警** | sync_rate < 100% 持续 30 分钟触发钉钉/邮件 | Phase 2 |
| **API 限流** | 同时最多 1 个补算 job，防过载 | Phase 2 |

### 2.4 sentiment_score 聚合规则（v1.2 明确）

`sentiment_score`（新闻面）与 `mood_score`（市场情绪）不同：前者来自 **个股新闻**，后者来自 **行情/量价情绪**。

**聚合函数** `get_stock_sentiment_score(stock_id, target_date)`：

```python
def get_stock_sentiment_score(stock_id: int, target_date: str, *, window_days: int = 7) -> float | None:
    """
    最近 window_days 天内已分析新闻的 sentiment_score 加权平均。
    权重：时间衰减 w = 0.85 ** days_ago（越新权重越高）。
    无新闻或全部 NULL → None（gap 状态 no_source）。
    """
```

**SQL 逻辑（批量版，sync_all_dimensions 必须使用，避免 N+1）：**

```sql
-- 窗口：闭区间 [target_date - window_days, target_date]（含当日，覆盖收盘后发布的新闻）
-- target_date 必须为 'YYYY-MM-DD'
SELECT stock_id,
       SUM(sentiment_score * POWER(0.85, julianday(date(:target_date)) - julianday(date(pub_date))))
       / NULLIF(SUM(POWER(0.85, julianday(date(:target_date)) - julianday(date(pub_date)))), 0)
       AS sentiment_avg
FROM stock_news
WHERE stock_id IN (...)
  AND sentiment_score IS NOT NULL
  AND date(pub_date) >= date(:target_date, '-' || CAST(:window_days AS TEXT) || ' days')
  AND date(pub_date) <= date(:target_date)
GROUP BY stock_id;
```

**scan_gaps 中 sentiment 的 NO_SOURCE 判定：**

```python
def sentiment_gap_status(stock_id: int, target_date: str) -> GapStatus:
    score = get_stock_sentiment_score(stock_id, target_date)
    if score is not None:
        return OK if comprehensive_has_value else MISSING  # 可 sync
    # score is None：区分「无新闻」vs「有新闻但未分析」
    has_any_news = conn.execute(
        "SELECT 1 FROM stock_news WHERE stock_id=? LIMIT 1", (stock_id,)
    ).fetchone()
    if not has_any_news:
        return NO_SOURCE  # 确认无源
    return NO_SOURCE  # 有新闻但窗口内无有效 sentiment → 需 news/analyze-all
```

周末/节假日：`sentiment_score` 允许 stale，**不纳入必需维度告警**（见 §4.3 Monitor）。

### 2.5 遗留问题跟踪（v1.3）

| 问题 | 状态 | 方案 |
|------|------|------|
| sentiment 聚合规则不明确 | ✅ | §2.4 + 批量 SQL |
| sync 并发竞态 | ✅ Phase 1 | §4.0 阻塞 #1 |
| sentiment None 与 NO_SOURCE 脱节 | ✅ Phase 1 | §4.0 #2 + sync 伪代码 |
| force_recompute 仍跑 P0 | ✅ Phase 1 | §4.0 #3 |
| stale 阈值不可配置 | ⏳ Phase 3 | `AFR_GAP_STALE_DAYS` |
| no_source prefetch 不完整 | ⚠️ Phase 2 | §4.4 + STILL_NO_SOURCE |
| ML 覆盖冲突 | ✅ Phase 1 | overwrite=False + write_lock |
| 双 sync_rate 日志 | ✅ Phase 2 | score_gap_log 四字段 |
| 历史 calc_date 回溯 | ❌ 不支持 | §Phase 3 范围外 |
| score_gap_log 无限增长 | ⏳ Phase 3 | 90 天清理任务 |
| fundamental 5000 股 | ⏳ Phase 3 | 增量百分位 |

---

## 3. 缺口定义（Score Gap Model）

### 3.1 三态模型

对每只股票 `s`、每个维度 `d`、目标交易日 `T`（默认 `latest_trading_date()`）：

```python
class GapStatus(str, Enum):
    OK = "ok"              # comprehensive[T][d] 有值 且 源表日期 >= T-1
    STALE = "stale"        # 有值但源表日期 < T-N（默认 N=1 交易日）
    MISSING = "missing"    # comprehensive[T][d] IS NULL
    NO_SOURCE = "no_source"  # 源表也无数据（需先抓数据再算分）
```

### 3.2 扫描 SQL 示例（fundamental）

```sql
-- 找出 comprehensive 缺 fundamental 但 factor_scores 有分的股票
SELECT s.id, s.code, fs.composite_score, fs.calc_date
FROM stocks s
LEFT JOIN comprehensive_scores cs
  ON cs.stock_id = s.id AND cs.calc_date = :target_date
LEFT JOIN factor_scores fs
  ON fs.stock_id = s.id
  AND fs.calc_date = (SELECT MAX(calc_date) FROM factor_scores WHERE stock_id = s.id)
WHERE s.is_active = 1
  AND (cs.fundamental_score IS NULL OR cs.calc_date IS NULL)
  AND fs.composite_score IS NOT NULL;
```

### 3.3 扫描结果结构

```json
{
  "target_date": "2026-05-31",
  "active_stocks_count": 54,
  "sync_rate_all": 0.11,
  "sync_rate_required": 0.22,
  "summary": { "...": "各维度 ok/missing/stale/no_source 计数" },
  "gaps": [ "..." ],
  "recommended_actions": [ "..." ]
}
```

> **v1.3：** `GapReport` 同时携带 `sync_rate_all`（7 维全齐全）与 `sync_rate_required`（必需 5 维）。告警与趋势用后者；Dashboard 两者都展示。

---

## 4. 方案设计

### 4.0 Phase 1 阻塞项（开发前必须纳入）

#### 阻塞 #1：`overwrite=False` 与并发 sync 竞态

**问题：** T1 sync 读源表准备写 85 分 → T2 ML 写入 92 → T1 `UPDATE ... WHERE col IS NULL` 用 85 覆盖 92。

**Phase 1 采用方案（最小可行，不引入 version 字段）：**

1. **复用现有 `database.write_lock`**（`backend/database.py` 中的模块级 `threading.Lock`，与 `factor_engine`、`data_fetcher` 一致）。`sync_all_dimensions` 全程 `with write_lock:` + 单 SQLite 事务。
2. **batch-fill** 经 API 限流 + 单线程执行（Phase 1 可同步调用 sync，Phase 2 再入 `job_queue`）。
3. **scheduler** 执行前 `can_run_sync()`，若 batch-fill 进行中则跳过。

```python
def can_run_sync() -> tuple[bool, str]:
    """Phase 1：内存标志或简单文件锁；Phase 2：查 job_runs pending/running"""
    if _batch_fill_in_progress():  # POST /batch-fill 执行期间置 True
        return False, "skipped: batch-fill in progress"
    return True, "ok"
```

> **write_lock 选型（v1.4 明确，二选一已决策）：**
>
> | 方案 | Phase 1 | 说明 |
> |------|---------|------|
> | **A. `database.write_lock`** | ✅ **采用** | 与现有写路径一致；改动最小 |
> | B. `job_type="sync_dimensions"` 入队 | Phase 2 可选 | scheduler 须 `enqueue_job` 而非直接调函数，否则 bypass 队列 |
>
> Phase 1 **不**新增独立 `_sync_lock`；所有 comprehensive 写操作统一走 `database.write_lock`。

```python
# comprehensive.py — Phase 1
from database import write_lock

def sync_all_dimensions(...):
    with write_lock:
        conn.execute("BEGIN")
        try:
            ...
            conn.commit()
        except Exception:
            conn.rollback()
            raise
```

4. scheduler 跳过时写应用日志（Phase 2 入库 `event_type=sync_skipped`）。

#### 阻塞 #2：sentiment 返回 None 时的 sync / scan 一致性

见 §2.4 与 §4.3 `sync_all_dimensions` 伪代码：`score is None` → **跳过该股票该维度**，保持 NULL，计入 `skipped`。

#### 阻塞 #3：`force_recompute` 跳过 P0

```python
def build_phase_plan(mode: str) -> list[str]:
    if mode == "sync_only":
        return ["P0"]
    if mode == "force_recompute":
        return ["P1", "P2", "P3", "P4", "P4_end_sync"]  # 无 P0
    return ["P0", "P1", "P2", "P3", "P4", "P4_end_sync"]  # compute_and_sync
```

- `force_recompute`：**不**在开头 sync 旧源表分，直接 P1～P4 全量重算；**P4 结束后**执行一次 `sync_all_dimensions` 兜底（此时源表已是新结果，sync 为一致性刷新）。
- `compute_and_sync`：保留 P0 秒级修复路径。

### 4.0.1 编码前可确认细节（非阻塞，v1.4）

#### 细节 1：write_lock 范围

见阻塞 #1：`database.write_lock` 覆盖 `sync_all_dimensions` 整段读写。任何其他写 `comprehensive_scores` 的路径（如 `upsert_dimension_score`）已在 `comprehensive_store` 内独立连接提交；Phase 1 sync 与 ML upsert 的互斥靠 **can_run_sync + write_lock 顺序获取**，避免 scheduler 与用户 sync 重叠。

#### 细节 2：`batch_get_sentiment_scores` 窗口

采用 **闭区间** `[target_date - window_days, target_date]`（见 §2.4 SQL）。`window_days` 默认 7，可 env `AFR_SENTIMENT_WINDOW_DAYS` 配置。含 `target_date` 当日，以覆盖收盘后发布的新闻。

#### 细节 3：`score_gap_log` 计数与 scope（Phase 2 字段，Phase 1 scan 响应应对齐）

```python
def build_log_context(report: GapReport, stock_ids: list[int] | None) -> dict:
    scope_ids = stock_ids or report.scanned_stock_ids
    return {
        "active_stocks_count": len(scope_ids),  # 实际扫描数量，非全库固定值
        "stock_scope_json": json.dumps(stock_ids if stock_ids else "all_active"),
        "sync_rate_all_before": report.sync_rate_all,
        "sync_rate_required_before": report.sync_rate_required,
    }
```

- `scan` 事件：写入上述 before 字段
- `fill_start`：再次 `scan_gaps` 取最新 before（或复用请求开始时快照）
- `fill_done`：写入 `*_after` 字段

Phase 1 的 `GET /scores/gaps` 响应须含 `active_stocks_count` 与双 sync_rate，便于验收，**不必**落库 audit 表。

### 4.1 总体架构

```
                    ┌──────────────────────┐
  GET /scores/gaps  │  ScoreGapScanner     │  扫描 comprehensive + 源表
                    └──────────┬───────────┘
                               │ 写入 score_gap_log (scan)
                               │
                    ┌──────────▼───────────┐
  POST /scores/     │  BatchScoreOrchestrator │
  batch-fill        │  ─────────────────────  │
  ?dry_run=true     │  0. 限流检查（≤1 并发）  │
                    │  1. 按优先级队列排序     │
                    │  2. 只算 gap != OK      │
                    │  3. 批量 compute_all    │
                    │  4. sync → comprehensive│
                    │  5. recompute_composite │
                    └──────────┬───────────┘
                               │ 写入 score_gap_log (fill)
                               │
                    ┌──────────▼───────────┐
  GET /jobs/{id}    │  job_queue (async)    │  长任务不阻塞 API
                    └──────────┬───────────┘
                               │
                    ┌──────────▼───────────┐
  Dashboard 告警    │  ScoreHealthMonitor   │  sync_rate 持续偏低 → 钉钉/邮件
                    └──────────────────────┘
```

### 4.2 执行 DAG 与优先级队列

**维度优先级（数值越小越先执行）：**

| 优先级 | 阶段 | 维度 / 操作 | 典型耗时（54 股） | 用户可见效果 |
|--------|------|-------------|-------------------|--------------|
| P0 | Phase B | `sync_all_dimensions` | <1s | 七维从源表立刻补齐 |
| P1 | Phase A | `fundamental_score` | ~3s | 基本面排名恢复 |
| P2 | Phase A | `capital_score`, `mood_score`, `policy_score`, `val_score` | ~3s | 资金/情绪/政策/估值恢复 |
| P3 | Phase A | `sentiment_score` | ~5s | 新闻面恢复 |
| P4 | Phase A | `technical_score` | ~60s | 技术面恢复（最慢，放最后） |
| P5 | Phase C | ML 混合（可选） | 视配置 | 综合分微调 |

**编排规则（v1.3）：**
- `mode=sync_only`：仅 P0
- `mode=compute_and_sync`：P0 → P1 → P2 → P3 → P4；intermediate sync **仅 P0 结束与 P4 结束**
- `mode=force_recompute`：**跳过 P0** → P1～P4 全量重算 → P4 结束 sync 兜底

**同档并行策略（v1.2 明确）：**

| 档位 | 维度 | 并行？ | 实现 |
|------|------|--------|------|
| P0 | sync_all_dimensions | 否 | 单次批量 SQL |
| P1 | fundamental | 否 | 必须全池串行（百分位依赖） |
| P2 | capital / mood / policy / val | **可选并行** | Phase 2 默认 **串行**（复用 job_queue，实现简单）；Phase 3 可改 `asyncio.gather` 缩至 ~3s |
| P3 | sentiment | 否 | 批量聚合 SQL |
| P4 | technical | 否 | 逐股 Ashare，瓶颈 |

> **决策：Phase 2 接受 P2 串行**，总耗时 +~6s 但无需改造 job_queue；文档与代码保持一致。

**Intermediate sync 策略（v1.2 明确）：**

- **P0 结束后**：必须 `sync_all_dimensions` + `recompute_composite`（秒级恢复大部分维度）
- **P1～P3 结束后**：**不**全量 sync——各维度 compute 已通过 `upsert_dimension_score` 写入 comprehensive
- **P4 结束后**：再次 `sync_all_dimensions` + `recompute_composite`（兜底 technical / sentiment 漏网）

```
compute_and_sync:
  P0  sync → recompute_composite
  P1～P4  compute（各 upsert_dimension_score）
  P4_end  sync → recompute_composite

force_recompute:          ← 无 P0
  P1～P4  compute
  P4_end  sync → recompute_composite
```

**原则：**
- Phase B（P0/P4 sync）修复「源表有分、comprehensive 无分」
- Phase A 仅对 `status in (missing, stale, no_source)` 的维度触发
- `no_source` 按 §4.4 prefetch 策略尝试补数据

### 4.3 核心模块

#### `services/score_gap_scanner.py`

```python
DIMENSION_SPEC = {
    "fundamental_score": ("factor_scores", "composite_score", "calc_date"),
    "technical_score": ("tech_analysis_cache", "score", "created_at"),
    "sentiment_score": ("stock_news", "_aggregated", "pub_date"),  # 见 get_stock_sentiment_score
    "capital_score": ("capital_scores", "composite_score", "date"),
    "policy_score": ("policy_scores", "composite_score", "date"),
    "mood_score": ("sentiment_scores", "composite_score", "date"),
    "val_score": ("valuation_scores", "composite_score", "date"),
}

def scan_gaps(target_date: str | None = None, stock_ids: list[int] | None = None) -> GapReport: ...
```

#### `services/batch_score_orchestrator.py`

```python
def fill_gaps(
    *,
    dimensions: list[str] | None = None,  # None = 全部
    stock_ids: list[int] | None = None,   # None = 全部活跃股
    mode: Literal["sync_only", "compute_and_sync", "force_recompute"] = "compute_and_sync",
    target_date: str | None = None,
) -> FillReport: ...
```

#### 扩展 `services/comprehensive.py`

```python
# 现有 calculate_all 仅 3 维 → 扩展为 7 维全量同步
DIMENSION_SYNC_MAP = {
    "fundamental_score": {"table": "factor_scores", "score_col": "composite_score", "date_col": "calc_date", "overwrite": False},
    "technical_score": {"table": "tech_analysis_cache", "score_col": "score", "date_col": "created_at", "overwrite": False},
    "sentiment_score": {"fn": "get_stock_sentiment_score", "overwrite": False},  # 非直表
    "capital_score": {"table": "capital_scores", "score_col": "composite_score", "date_col": "date", "overwrite": False},
    "policy_score": {"table": "policy_scores", "score_col": "composite_score", "date_col": "date", "overwrite": False},
    "mood_score": {"table": "sentiment_scores", "score_col": "composite_score", "date_col": "date", "overwrite": False},
    "val_score": {"table": "valuation_scores", "score_col": "composite_score", "date_col": "date", "overwrite": False},
}

def sync_all_dimensions(
    stock_ids: list[int],
    calc_date: str,
    *,
    overwrite: bool = False,
) -> dict:
    """
    在 write_lock + 单事务内执行。
    返回见下方结构。
    """
    ...
```

**overwrite 与行级语义（v1.3 明确）：**

| 场景 | 行为 |
|------|------|
| comprehensive **无** `(stock_id, calc_date)` 行 | `INSERT` 一行，仅 SET 源表有值的维度列 |
| 行已存在、部分维度 NULL | `UPDATE` **只 SET 那些当前仍为 NULL 的列**（逐列判断，非 blind UPDATE） |
| 行已存在、列已有值 | `overwrite=False` 时**跳过该列**；`overwrite=True` 强制覆盖 |
| 全部 7 维 upsert 完成后 | **统一调用一次** `recompute_composite`（不在每列 upsert 时重复计算） |

**sentiment 同步伪代码（None 则跳过）：**

```python
sentiment_map = batch_get_sentiment_scores(stock_ids, calc_date)  # §2.4 批量 SQL
for sid in stock_ids:
    score = sentiment_map.get(sid)
    if score is None:
        skipped["sentiment_score"] += 1
        continue
    _upsert_column_if_null(sid, calc_date, "sentiment_score", score, overwrite=overwrite)
```

**technical 同步：** 使用 `tech_analysis_cache` 批量 `IN (stock_ids)` 查询，取每 stock 最新 `score`（与 sentiment 同为批量，禁止 N+1）。

**返回值结构：**

```python
{
    "calc_date": "2026-05-31",
    "synced": {"fundamental_score": 54, "capital_score": 54, "sentiment_score": 41, ...},
    "skipped": {"sentiment_score": 13, "technical_score": 22},  # None / 无源
    "unchanged": {"technical_score": 10},  # 列已有值且 overwrite=False
}
```

#### `services/score_gap_log.py`（Phase 2）

```sql
CREATE TABLE IF NOT EXISTS score_gap_log (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    event_type          TEXT NOT NULL,   -- scan | fill_start | fill_done | fill_error | alert | alert_resolved
    target_date         TEXT NOT NULL,
    alert_key           TEXT,            -- 告警去重键，如 '2026-05-31:required_dims'
    mode                TEXT,
    job_id              TEXT,
    active_stocks_count INTEGER,
    stock_scope_json    TEXT,
    sync_rate_all_before     REAL,       -- 7 维全齐全率（Dashboard）
    sync_rate_required_before REAL,      -- 必需 5 维齐全率（告警/趋势）
    sync_rate_all_after      REAL,
    sync_rate_required_after REAL,
    gap_summary_json    TEXT,
    actions_json        TEXT,            -- 含 per-dimension 失败明细，见下
    alert_detail_json   TEXT,            -- event_type=alert 时：持续分钟数、渠道、sync_rate
    filled_count        INTEGER DEFAULT 0,
    skipped_count       INTEGER DEFAULT 0,
    error_count         INTEGER DEFAULT 0,
    duration_ms         INTEGER,
    triggered_by        TEXT DEFAULT 'api',
    created_at          TEXT DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS idx_gap_log_date ON score_gap_log(target_date, created_at);
CREATE INDEX IF NOT EXISTS idx_gap_log_alert ON score_gap_log(alert_key, event_type, created_at);
```

```python
def log_scan(report: GapReport) -> int: ...
def log_fill_start(job_id: str, plan: FillPlan, rates_before: dict) -> int: ...
def log_fill_done(job_id: str, result: FillReport, rates_after: dict) -> int: ...
```

**`actions_json` 部分成功明细（Phase 2）：**

```json
{
  "technical_score": {
    "filled": 20,
    "failed_stocks": [{"stock_id": 12, "code": "600703", "reason": "ashare_timeout"}],
    "still_no_source": [{"stock_id": 34, "reason": "quotes<20_after_fetch"}]
  },
  "sentiment_score": {"filled": 41, "skipped_null": 13}
}
```

`GET /jobs/{id}` 的 `errors` 列表与 `actions_json` 保持一致，供 API 轮询。

```python
def log_alert(alert_key: str, detail: dict, channels: list[str]) -> int: ...
def log_alert_resolved(alert_key: str, sync_rate_required_after: float) -> int: ...
def alert_cooled_down(alert_key: str, cooldown_min: int = 60) -> bool: ...
def query_gap_history(limit: int = 50, target_date: str | None = None) -> list[dict]: ...
```

**用途：**
- 复盘：`sync_rate_all_*` + `sync_rate_required_*` + `active_stocks_count`
- 告警审计：`event_type=alert` 记录渠道与持续时长；`alert_resolved` 记录恢复
- **冷却去重**：`alert_cooled_down(alert_key)` 查最近 60min 是否已有同 key 的 `alert` 记录

#### `services/score_health_monitor.py`（Phase 2）

```python
# 必需维度：每日收盘后理应齐全；technical/sentiment 允许偶发缺失
REQUIRED_DIMENSIONS = [
    "fundamental_score", "capital_score", "policy_score", "mood_score", "val_score"
]  # 可 env: AFR_SYNC_REQUIRED_DIMENSIONS

SYNC_RATE_ALERT_THRESHOLD = 1.0       # 必需维度齐全率阈值
SYNC_RATE_ALERT_DURATION_MIN = 30
SYNC_RATE_ALERT_COOLDOWN_MIN = 60

def check_sync_rate(target_date: str | None = None) -> dict:
    """
    返回:
      sync_rate_all      — 7 维全齐全比例（Dashboard 展示）
      sync_rate_required — 必需维度齐全比例（告警判定）
      missing_by_dimension, alert_eligible, alert_key
    """

def maybe_send_alert(report: dict) -> None:
    """sync_rate_required < threshold 且持续 > 30min → 钉钉/邮件；写 score_gap_log(alert)"""

def maybe_send_recovery(report: dict) -> None:
    """fill_done 且 sync_rate_required >= threshold → 恢复通知；写 score_gap_log(alert_resolved)"""
```

告警渠道（`.env`）：`AFR_ALERT_DINGTALK_WEBHOOK`、`AFR_ALERT_EMAIL_TO`

**告警示例（基于必需维度）：**
> 【AFR 评分告警】2026-05-31 必需维度同步率 22%（12/54），已持续 32 分钟。缺失：fundamental 54、capital 54…

**恢复通知示例：**
> 【AFR 评分恢复】2026-05-31 必需维度同步率已恢复至 100%（54/54），补算 job bf-20260531-a1b2 已完成。

#### API 限流与 Job 生命周期（Phase 2）

```python
MAX_CONCURRENT_BATCH_FILL = 1
JOB_HEARTBEAT_INTERVAL_SEC = 30
JOB_STALE_TIMEOUT_MIN = 10          # 10 分钟无 heartbeat → 自动 failed，释放锁

def can_enqueue_batch_fill() -> tuple[bool, str | None]:
    """
    1. 检查 job_runs 中 batch_score_fill status in (pending, running)
    2. 对 running job：若 now - heartbeat_at > JOB_STALE_TIMEOUT_MIN → 标记 failed，允许新 job
    """

def touch_job_heartbeat(job_id: str) -> None: ...
```

**job_queue 改造点（v1.3，Phase 2 必做）：**

现有 `job_queue` 无 heartbeat。改造方案：

```python
# 方案 A（推荐）：job_worker 内嵌心跳线程
def _run_with_heartbeat(job_id: str, fn: Callable):
    stop = threading.Event()
    def _beat():
        while not stop.wait(JOB_HEARTBEAT_INTERVAL_SEC):
            touch_job_heartbeat(job_id)
    t = threading.Thread(target=_beat, daemon=True)
    t.start()
    try:
        return fn()
    finally:
        stop.set()

# 方案 B：长任务内显式调用（technical 循环每 N 股 touch 一次）
```

`run_job` / batch-fill handler 统一经 `_run_with_heartbeat` 包装；进程崩溃依赖 **10min stale 检测** 释锁。

**job_runs 表扩展：**
```sql
ALTER TABLE job_runs ADD COLUMN heartbeat_at TEXT;
ALTER TABLE job_runs ADD COLUMN stale_timeout_min INTEGER DEFAULT 10;
```

**API：**
- `POST /scores/batch-fill` 入队前检查；冲突 **409** + 现有 `job_id`
- `DELETE /api/jobs/{job_id}` — 管理员强制终止（标记 `failed`，释放限流锁）
- `dry_run=true` **不受限流约束**

### 4.4 no_source 分维度 Prefetch 策略（Phase 2）

| 维度 | no_source 时动作 | 失败处理 |
|------|------------------|----------|
| `fundamental_score` | 检查 `financial_reports` 是否有 ≥2 期；无则 `POST /data/fetch?types=financials` | 超时 60s 后 skip |
| `technical_score` | 检查 `stock_daily_quotes` 近 60 日 ≥20 条；不足则 Ashare `get_price` | 仍 <20 条 → **STILL_NO_SOURCE**，保持 NULL，写入 `errors` / `actions_json` |
| | | Phase 3：低优先级次日重试任务（停牌复牌场景） |
| `sentiment_score` | 检查 `stock_news` 近 7 日；无则 `refresh_weighted_news` | 无 LLM 时用规则 fallback |
| `capital_score` / `mood_score` | 检查 `stock_daily_quotes` 最新日 | 无行情 → skip |
| `policy_score` | 无 prefetch，直接 compute（规则引擎不依赖实时 API） | skip |
| `val_score` | 检查 `valuation_snapshots` | 无则 trigger valuation compute |

```python
def prefetch_if_needed(dimension: str, stock_ids: list[int]) -> PrefetchReport:
    """返回 attempted / succeeded / still_no_source"""
```

dry-run 时调用 **轻量探测**（只查 DB + 可选 HEAD 请求 Ashare），不实际写入：

```json
{"stock_id": 12, "dimension": "technical_score", "would_fetch": true, "reason": "quotes<20", "estimated_extra_ms": [8000, 25000]}
```

### 4.5 dry-run 动态估时（Phase 2）

**基准耗时（54 股标定，来自 `benchmark_score_batch.py`）：**

```python
DIMENSION_BASE_MS = {
    "sync_all_dimensions": 300,
    "fundamental_score": 3000,
    "capital_score": 140,
    "mood_score": 120,
    "policy_score": 2000,
    "val_score": 800,
    "sentiment_score": 5000,
    "technical_score": 1100,  # 每股约 20ms（有缓存）～ 2500ms（需 Ashare）
}

def estimate_action_ms(action: str, affected_stocks: int, *, would_fetch_count: int = 0) -> dict:
    base = DIMENSION_BASE_MS[action]
    pool = max(active_stocks, 1)
    linear = int(base * affected_stocks / 54)
    if action == "technical_score":
        fetch_penalty = would_fetch_count * 2500
        return {
            "estimated_ms": linear + fetch_penalty,
            "estimated_ms_range": [linear, linear + fetch_penalty + 30000],
        }
    return {"estimated_ms": linear, "estimated_ms_range": [int(linear * 0.8), int(linear * 1.3)]}
```

**dry_run 响应示例（v1.2）：**

```json
{
  "dry_run": true,
  "active_stocks_count": 54,
  "sync_rate_required_before": 0.22,
  "planned_actions": [
    {
      "priority": 0,
      "action": "sync_all_dimensions",
      "affected_stocks": 54,
      "estimated_ms": 300,
      "estimated_ms_range": [240, 390]
    },
    {
      "priority": 4,
      "action": "analyze_technical",
      "affected_stocks": 22,
      "would_fetch": 8,
      "estimated_ms": 24200,
      "estimated_ms_range": [4400, 62000]
    }
  ],
  "total_estimated_ms_range": [5000, 75000],
  "would_skip": [{"stock_id": 12, "dimension": "technical_score", "reason": "quotes<20", "would_fetch": true}],
  "prefetch_probe": {"attempted": 8, "would_succeed": 6, "would_fail": 2}
}
```

---

## 5. API 设计

### 5.1 缺口扫描（只读）

```
GET /api/scores/gaps
  ?target_date=2026-05-31      # 可选，默认 latest_trading_date
  ?stock_ids=1,2,3             # 可选，默认全部活跃股
  ?dimensions=fundamental,capital  # 可选
```

**响应：** 见 §3.3

### 5.2 批量补算（写操作，异步）

```
POST /api/scores/batch-fill
{
  "mode": "compute_and_sync",     // sync_only | compute_and_sync | force_recompute
  "dimensions": ["fundamental_score", "capital_score"],  // 省略=全部
  "stock_ids": null,              // 省略=仅补 gap 股票
  "target_date": "2026-05-31",
  "skip_no_source": true,         // 源数据缺失时跳过而非报错
  "dry_run": false                // true = 只返回计划，不写入、不入队
}
```

**正常响应（dry_run=false）：**
```json
{
  "job_id": "bf-20260531-a1b2",
  "status": "queued",
  "estimated_gaps": 378,
  "priority_plan": ["sync_all_dimensions", "fundamental", "capital", "mood", "policy", "val", "sentiment", "technical"],
  "poll_url": "/api/jobs/bf-20260531-a1b2"
}
```

**dry_run 响应：** 见 §4.5（动态 `estimated_ms_range`、`would_fetch`、`prefetch_probe`）

**限流冲突（409）：**
```json
{
  "detail": "已有补算任务运行中",
  "running_job_id": "bf-20260531-x9y8",
  "poll_url": "/api/jobs/bf-20260531-x9y8"
}
```

### 5.3 任务进度与 Job 管理

```
GET /api/jobs/{job_id}
DELETE /api/jobs/{job_id}    # 管理员：强制 failed，释放限流锁
```

```json
{
  "status": "running",
  "phase": "capital",
  "priority_tier": 2,
  "progress": {"done": 3, "total": 7, "current": "capital_score", "tiers_done": ["P0", "P1"]},
  "sync_rate_current": 0.85,
  "filled": 162,
  "skipped": 12,
  "errors": [{"stock_id": 12, "dimension": "technical", "reason": "行情不足"}]
}
```

### 5.4 缺口审计历史

```
GET /api/scores/gap-log
  ?limit=50
  ?target_date=2026-05-31
  ?event_type=fill_done
```

### 5.5 快捷模式（运维常用）

| 场景 | 调用 |
|------|------|
| **评审计划（不写库）** | `POST /scores/batch-fill {"dry_run":true,"mode":"compute_and_sync"}` |
| 仅修复同步（最快，~1s） | `POST /scores/batch-fill {"mode":"sync_only"}` |
| 日常收盘补算 | `POST /scores/batch-fill {"mode":"compute_and_sync"}` |
| 全量重算（慢，~2min） | `POST /scores/batch-fill {"mode":"force_recompute"}` |

---

## 6. 前端 / 运维入口（Phase 2）

### 6.1 Dashboard「评分健康」卡片

在现有 `GET /dashboard/health` 基础上增加：

- **Comprehensive 同步率**：7 维全齐全率 + **必需维度齐全率**（告警用）
- **同步率趋势图**：最近 **7 天** 来自 `score_gap_log`（`event_type=scan`）的 `sync_rate_required` 折线
- **告警状态**：必需维度 sync_rate 低于阈值且持续 >30min → 红色横幅
- **一键补算**：先 `dry_run` 预览（含 `estimated_ms_range`）→ 确认后 `batch-fill`
- **补算进度条**：轮询 job，`priority_tier` + `tiers_done`
- 红/黄/绿列表：按缺口维度分组

新增 API：

```
GET /api/dashboard/score-sync-health
GET /api/dashboard/score-sync-trend?days=7
```

```json
{
  "target_date": "2026-05-31",
  "active_stocks_count": 54,
  "sync_rate_all": 0.12,
  "sync_rate_required": 0.22,
  "stocks_full_required": 12,
  "missing_by_dimension": {"fundamental_score": 54, "capital_score": 54},
  "alert": {
    "active": true,
    "alert_key": "2026-05-31:required_dims",
    "since": "2026-05-31T10:00:00",
    "duration_minutes": 32,
    "last_notified_at": "2026-05-31T10:30:00",
    "channels_sent": ["dingtalk"]
  },
  "last_fill_job": {"job_id": "bf-...", "status": "done", "sync_rate_required_after": 1.0},
  "trend_7d": [
    {"date": "2026-05-25", "sync_rate_required": 1.0},
    {"date": "2026-05-31", "sync_rate_required": 0.22}
  ]
}
```

### 6.2 CLI 脚本

```bash
# 扫描
python scripts/score_gap_scan.py --json

# 预览计划（dry-run）
python scripts/score_batch_fill.py --mode compute_and_sync --dry-run

# 补算（同步模式，最快修复当前 2026-05-31 问题）
python scripts/score_batch_fill.py --mode sync_only

# 完整补算
python scripts/score_batch_fill.py --mode compute_and_sync

# 查看审计日志
python scripts/score_gap_log.py --last 20
```

---

## 7. 性能预估（54 股）

| 步骤 | 现有耗时 | 批量优化后 |
|------|----------|------------|
| gap 扫描 | — | ~50ms（7 条 SQL） |
| sync_only（7 维 upsert，**含 sentiment 批量 SQL**） | — | ~300ms |
| fundamental recalc | ~3s | ~3s（全池百分位需全量） |
| capital + mood | ~16s（旧，含 sleep） | **~280ms**（已优化） |
| policy analyze-all | ~27s（旧，含 sleep） | **~2s**（逐股但无 sleep） |
| technical analyze-all | ~60s（含 Ashare） | ~60s（瓶颈，可限流） |
| val + news | ~5s | ~5s |
| **sync_only 总计** | — | **<1s** |
| **compute_and_sync 总计** | — | **~70s**（technical 主导） |

**优化建议：**
- `sync_only` 作为每日 ML 写入后的 **必跑步骤**（scheduler 末尾加一行）
- technical 缺口多时使用 **缓存优先**：源表有近期分则跳过 Ashare 拉取
- 大于 200 股时 fundamental 保持全池一次 `_get_all_metrics`（已实现）
- **优先级队列**：首次全量补算时，P0 完成后 sync_rate 通常可从 ~12% 升至 ~85%（源表有分的前提下），用户无需等待 technical 完成即可看到主要维度恢复

---

## 8. 与现有 scheduler 集成

在 `scheduler.run_daily_tasks()` 末尾追加：

```python
from services.batch_score_guard import can_run_sync
from services.comprehensive import sync_all_dimensions
from services.score_gap_scanner import scan_gaps

if not can_run_sync()[0]:
    logger.info("[Scheduler] skip sync_all_dimensions: batch-fill active")
else:
    gaps = scan_gaps(target_date=today)
    if gaps.summary.missing_total > 0:
        sync_all_dimensions(stock_ids=None, calc_date=today)
```

避免 ML 写入后再次出现「有 composite、无维度」的半成品行。

**Monitor 集成（Phase 2）：** `app.py` startup daemon + `fill_done` 恢复通知。

---

## 9. 实施计划

### Phase 1 — 最小可行集（1～2 天，**仅 sync_only 修复路径**）

**目标：** 执行 `sync_only` 后，2026-05-31 批次中源表有分但 comprehensive 为 NULL 的维度全部补齐，**不覆盖**任何已有值。

**Phase 1 做：**

| 优先级 | 任务 | 文件 |
|--------|------|------|
| P0 | `can_run_sync()` + scheduler 互斥 | `batch_score_guard.py`, `scheduler.py` |
| P0 | `sync_all_dimensions(overwrite=False)`：7 维、sentiment/technical **批量 SQL**、None 跳过、`database.write_lock` + 单事务、末尾统一 `recompute_composite` | `comprehensive.py`, `sentiment_aggregate.py` |
| P0 | `scan_gaps`：missing / NO_SOURCE / `sync_rate_all` + `sync_rate_required` + `active_stocks_count` | `score_gap_scanner.py` |
| P0 | `POST /scores/batch-fill` **`mode=sync_only` only**（即执行 P0，等价于 `sync_all_dimensions`） | `api/scores.py`, `batch_score_orchestrator.py` |
| P1 | `GET /scores/gaps` | `api/scores.py` |
| P1 | CLI `scripts/score_batch_fill.py --mode sync_only` | scripts |

**Phase 1 不做（挪 Phase 2）：**

- `force_recompute` / `compute_and_sync` 全链路
- job 心跳、409 限流、dry-run
- `score_gap_log` 落库、Monitor 告警、Dashboard 卡片

**Phase 1 验收标准（54 股，2026-05-31）：**

1. `POST /scores/batch-fill {"mode":"sync_only"}` 后，comprehensive 中源表有分的缺失列全部非 NULL
2. 已有维度值（如 ML 写入的 `technical_score=65`）**数值不变**
3. `GET /scores/gaps` → `sync_rate_required` 从 **0.22 → 1.0**（必需 5 维齐全）
4. sentiment 无新闻股票：`sentiment_score` 仍为 NULL，`skipped` 计数正确
5. scheduler 与 batch-fill 同时触发时，一方 skip，无 double-write

**Phase 1 不做项验收：** force_recompute、审计日志、告警 — 留 Phase 2。

---

### Phase 1 原任务对照（归档）

| # | 任务 | Phase |
|---|------|-------|
| 0 | can_run_sync + write_lock | **1** |
| 1 | sync_all_dimensions 7 维 | **1** |
| 1b | batch_get_sentiment_scores | **1** |
| 2 | scan_gaps | **1** |
| 3 | GET /scores/gaps | **1** |
| 4 | POST batch-fill sync_only | **1** |
| 4b | force_recompute 跳过 P0 | **2** |
| 5 | CLI | **1** |
| 6 | scheduler can_run_sync | **1** |

### Phase 2 — 完整补算 + 运维鲁棒性（3～4 天）

| # | 任务 |
|---|------|
| 7 | orchestrator P0～P4 + **P2 串行** + intermediate sync 仅 P0/P4 |
| 8 | job_queue + heartbeat（**§4.3 `_run_with_heartbeat`**）+ 10min stale |
| 9 | `GET/DELETE /jobs/{id}` |
| 10 | **dry_run**：动态估时 + prefetch 探测 + `would_fetch` |
| 11 | **score_gap_log**（双 sync_rate 字段 + actions_json 失败明细） |
| 12 | API 限流 409 + stale job 自动释放 |
| 13 | **ScoreHealthMonitor**：必需维度告警 + **恢复通知** + 60min 冷却 |
| 14 | Dashboard 健康卡片 + **7 天趋势图** + dry-run 预览 |
| 15 | **no_source prefetch** + technical **STILL_NO_SOURCE** |
| 16 | 单元测试 + 集成测试 |

**验收：**
- 故意删除某股 capital_score → batch-fill 自动补回
- `dry_run=true` 返回 `estimated_ms_range` 且 DB 无变化
- 连续提交 2 个 batch-fill，第二个 409；模拟 job 卡死 10min 后 third 请求成功
- `DELETE /jobs/{id}` 释放锁
- 必需维度 sync_rate 低 30min → 告警；补算成功 → 恢复通知
- `score_gap_log` 可查 alert / alert_resolved 及 `active_stocks_count`
- ML 写入 technical 后 sync_only **不覆盖** 已有值（overwrite=False）

### Phase 3 — 增强（可选）

| # | 任务 |
|---|------|
| 17 | `AFR_GAP_STALE_DAYS` stale 阈值 |
| 18 | P2 四维度 `asyncio.gather` 并行 |
| 19 | fundamental 增量百分位（5000 股） |
| 20 | 与 `fetch_job` 联动 |
| 21 | 多实例分布式 job 锁 |
| 22 | **technical 次日低优先级重试**（STILL_NO_SOURCE 股票） |
| 23 | **`score_gap_log` 清理**：保留 90 天，或删除 `alert` 且 `created_at < now-30d` |

### 明确不支持（v1.3 范围外）

**历史 calc_date 回溯补算：** 默认仅针对 `latest_trading_date()`（或 API 指定的**当前最新交易日**）。历史批次缺口（如 2026-05-28）不自动补算——源表多为「最新快照」，回溯需手动逐维度调用。**Phase 3 也不优先支持**，除非有明确业务需求。

---

## 10. 风险与对策

| 风险 | 对策 |
|------|------|
| ML 写入覆盖已有维度 | `overwrite=False` + **write_lock 单事务逐列判 NULL** + scheduler/batch-fill 互斥（§4.0） |
| SQLite 写锁 | job_queue 单线程 + `write_lock` |
| Ashare 限流 / technical 失败 | 重试 3 次；**STILL_NO_SOURCE** 入 actions_json；P4 最后 |
| fundamental 全池计算 | Phase 3 增量优化；当前全池串行 |
| 综合分与维度不一致 | sync 内**全部维度 upsert 后统一一次** `recompute_composite` |
| sync 与 ML 竞态（T1/T2） | §4.0：`can_run_sync` + 单写者 |
| 频繁 batch-fill 过载 | MAX_CONCURRENT=1；409 返回现有 job_id |
| 首次补算耗时长 | P0 秒级；P2 串行可接受 |
| 问题难复盘 | score_gap_log + active_stocks_count |
| 低同步率未发现 | 必需维度 Monitor + 30min 告警 |
| 告警风暴 | alert_key + 60min 冷却（查 score_gap_log） |
| **告警过严（7 维 100%）** | **仅监控必需 5 维**；technical/sentiment 展示但不告警 |
| **补算成功无反馈** | **alert_resolved 恢复通知** |
| **job 卡死永久占锁** | **heartbeat + 10min 超时 failed** + `DELETE /jobs/{id}` |
| dry-run 估时不准 | 动态公式 + `estimated_ms_range` + prefetch 探测 |

---

## 11. 验收标准

1. **扫描准确**：人工 spot-check 5 只股票，gap 状态与 DB 一致
2. **sync_only 修复**：当前 2026-05-31 批次 7 维 NULL → 54/54 补齐（源表有分的前提下）
3. **compute_and_sync**：删除测试股全部维度分 → batch-fill 后恢复
4. **性能**：sync_only P99 < 2s（54 股）；compute_and_sync P99 < 120s
5. **幂等**：连续调用 2 次 batch-fill，结果一致、无重复写入
6. **前端**：Dashboard 显示同步率 ≥ 95%（正常运营日）
7. **dry-run**：`dry_run=true` 返回 planned_actions，DB 零写入
8. **限流**：第二个 batch-fill 请求返回 409，不创建重复 job
9. **审计**：score_gap_log 含 sync_rate、active_stocks_count、alert 渠道
10. **告警**：必需维度 sync_rate 低 30min → 通知；补算成功 → 恢复通知
11. **优先级**：P0 后 sync_rate_required 秒级提升
12. **overwrite**：sync_only 不覆盖 ML 已写 technical
13. **job 超时**：10min 无 heartbeat 自动 failed，限流锁释放
14. **dry-run**：含 `estimated_ms_range` 与 `would_fetch`
15. **趋势**：Dashboard 7 天 sync_rate_required 折线

---

## 12. 立即可执行的临时修复

在 Phase 1 开发完成前，可手动执行：

```bash
# 1. 扫描现状
curl http://localhost:8800/api/dashboard/health | jq '.summary'

# 2. 触发已有分散 API（顺序执行）
curl -X POST http://localhost:8800/api/scores/recalculate
curl -X POST http://localhost:8800/api/scores/comprehensive/calculate
curl -X POST http://localhost:8800/api/stocks/capital/analyze-all
curl -X POST http://localhost:8800/api/sentiment/analyze-all
curl -X POST http://localhost:8800/api/stocks/policy/analyze-all
curl -X POST http://localhost:8800/api/technical/analyze-all
curl -X POST http://localhost:8800/api/stocks/valuation/compute

# 3. 再次 comprehensive/calculate（仍只同步 3 维，需 Phase 1 扩展）
```

> **注意**：步骤 2 无法修复 capital/policy/mood/val 的 comprehensive 同步，这正是本方案 Phase 1 要解决的 gap。

---

## 13. 结论

| 项目 | 建议 |
|------|------|
| 核心思路 | **先扫描、再定向补算、最后全量 sync** |
| 最快见效 | Phase 1 的 `sync_all_dimensions` + `sync_only`（<1 秒修复当前 54 股缺口） |
| 运维安全 | Phase 2 的 **dry-run + 限流 + 审计日志**，先预览再执行、避免重复跑 |
| 用户体验 | **优先级队列** P0 秒级恢复大部分维度，technical 最后异步 |
| 可观测性 | 审计 + **必需维度告警/恢复** + **7 天趋势** |
| 安全性 | 限流 + dry-run + **job 心跳/超时/强制终止** |
| 长期 | scheduler + Dashboard；ML 后必跑 P0 sync |
| 开发状态 | **v1.4 — Phase 1 最小集已锁定，可开工** |

---

*附录：相关现有文件*

- `backend/services/comprehensive.py` — 当前仅 3 维同步
- `backend/services/comprehensive_store.py` — upsert + recompute_composite
- `backend/services/scheduler.py` — 日终任务（缺 comprehensive 全量 sync）
- `backend/services/batch_score_guard.py` — `can_run_sync`（Phase 1 新增）
- `backend/services/job_queue.py` — 异步任务 + heartbeat 改造
- `backend/api/scores.py` — recalculate / comprehensive/calculate
- `scripts/benchmark_score_batch.py` — 批量性能基准

*附录：新增 env 配置*

| 变量 | 默认 | 说明 |
|------|------|------|
| `AFR_BATCH_FILL_MAX_CONCURRENT` | `1` | 同时运行的补算 job 上限 |
| `AFR_JOB_STALE_TIMEOUT_MIN` | `10` | job 无 heartbeat 超时（分钟） |
| `AFR_SYNC_REQUIRED_DIMENSIONS` | `fundamental,capital,policy,mood,val` | 告警监控的必需维度 |
| `AFR_SYNC_RATE_ALERT_THRESHOLD` | `1.0` | 必需维度齐全率阈值 |
| `AFR_SYNC_RATE_ALERT_MINUTES` | `30` | 低于阈值持续分钟数 |
| `AFR_SYNC_RATE_ALERT_COOLDOWN_MIN` | `60` | 同 alert_key 告警冷却 |
| `AFR_SENTIMENT_WINDOW_DAYS` | `7` | 新闻情感聚合窗口 |
| `AFR_ALERT_DINGTALK_WEBHOOK` | — | 钉钉 Webhook |
| `AFR_ALERT_EMAIL_TO` | — | 邮件收件人 |
| `AFR_GAP_LOG_RETENTION_DAYS` | `90` | 审计日志保留天数（Phase 3 清理任务） |
| `AFR_GAP_STALE_DAYS` | `1` | stale 判定（Phase 3） |
