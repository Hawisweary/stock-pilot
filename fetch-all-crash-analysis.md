# fetch-all Daemon 线程崩溃根因分析报告

## 1. 问题概述

| 项目 | 描述 |
|------|------|
| 问题接口 | `POST /api/data/fetch-all` |
| 关键文件 | `backend/api/data.py`, `backend/services/fetch_job.py`, `backend/services/data_fetcher.py` |
| 数据库 | `data/afr.db` (SQLite, WAL 模式) |
| 症状 | daemon 线程只处理 4-13/54 只股票后停止，`fetch-status` 自动重置为 `finished=true, running=false` |

---

## 2. 数据证据

### 2.1 抓取日志时间线分析（2026-05-26）

通过分析 `data_fetch_log` 表，发现**同一时间段内存在 3 批并发抓取任务**：

```
批次1 (05:11 - 06:21): stock_id=23→42 (部分完成)
  23: 05:11:22 → 05:12:57  ✓ 完成
  24: 05:12:58 → 05:14:13  ✓ 完成
  ...
  32: 05:35:33 → 05:39:08  ✓ 完成 (5步)
  33: 05:39:09 → 05:43:21  ✓ 完成
  34: 05:43:22 → 05:55:36  ✓ 完成
  35-41: 持续到 06:21
  42: 06:20:56 → 06:20:58  ✗ 仅2步后中断

批次2 (06:04 - 06:05): stock_id=60,61,64,67 (并发运行)
  60: 06:04:08 → 06:05:05
  61: 06:04:09 → 06:05:06 (与批次1的stock 34-37 时间重叠)

批次3 (08:11 - 08:48): stock_id=23→32 (重复抓取)
  23: 08:11:22 → 08:13:06
  24: 08:13:07 → 08:20:32
  ...
  32: 08:47:55 → 08:48:19 (仅 info + quotes error)
```

### 2.2 单只股票耗时

每只股票平均抓取耗时约 **3.2 分钟**（含 akshare 财务报表重试），54 只股票总耗时约 **2.9 小时**。

---

## 3. 根因分析

### 主因 A：僵死超时机制的原子性缺陷 ⭐

**代码位置**: `backend/api/data.py:25` 和 `backend/api/data.py:36-44`

```python
_FETCH_STALE_SECONDS = 20 * 60  # 20分钟

def _maybe_reset_stale_fetch():
    if not _fetch_all_status.get("running"):
        return
    if _fetch_started_mono <= 0:
        return
    if time.monotonic() - _fetch_started_mono > _FETCH_STALE_SECONDS:
        _reset_fetch_all_status(...)  # 只重置状态标志，不终止线程！
```

**问题机制**:

```
时间轴:
T+0min   ─── daemon线程启动，开始抓取 stock #1
T+20min  ─── 前端轮询 fetch-status → _maybe_reset_stale_fetch() 触发
              running=False, finished=True  ← 状态被"虚假"重置
T+20min  ─── 前端看到 "completed"，发起新的 fetch-all 请求
              running=False，允许启动新线程 → 第二个 daemon 线程启动
T+20~60min ── 两个 daemon 线程并发写 SQLite → database locked → 崩溃
```

**核心问题**: `_maybe_reset_stale_fetch()` 只重置了内存状态标志，**没有终止正在运行的 daemon 线程**。这导致：

1. `_FETCH_STALE_SECONDS = 20min` 远小于 54 只股票的实际处理时间（~3h）
2. 前端/用户看到 `finished=true` 后会发起新一轮 `fetch-all`
3. 多个 daemon 线程并发写入 SQLite，触发 "database is locked" 异常
4. 线程崩溃后 `finally` 再次重置状态，`data_fetch_log` 无后续记录写入

### 主因 B：异常传播导致循环提前终止 ⭐

**代码位置**: `backend/api/data.py:99-132`

```python
try:                           # 外层 try
    for i, s in enumerate(stocks):
        try:                   # 内层 try
            fetch_job.start_job(s["id"])
            result = fetch_job.sync_fetch_one(s["id"], s["code"], s["market"])
            fetch_job.complete_job(s["id"], result)
            ...
        except Exception as e: # 内层 except
            fetch_job.fail_job(s["id"], str(e))  # ← 可能再次抛异常！
            print(f"[FetchAll] {s['code']} 失败: {e}")
    ...
except Exception as e:         # 外层 except
    print(f"[FetchAll] 批量抓取异常: {e}")
finally:
    _reset_fetch_all_status(...)
```

**问题**: 内层 `except` 中调用的 `fail_job()` → `_persist()` 会创建独立的数据库连接并写入。当存在两个 daemon 线程并发写 SQLite 时，`_persist()` 可能因 `database is locked` 而失败，异常**逃逸出内层 except 块**，传播到外层 except，**提前终止整个 for 循环**。

`_persist()` 代码（`backend/services/fetch_job.py:38-71`）：

```python
def _persist(stock_id, job):
    db = _connect()              # 创建新 SQLite 连接
    try:
        db.execute("INSERT INTO fetch_jobs ...")  # ← 此处可能 database locked
        db.commit()
    finally:
        db.close()               # 异常向上传播，没有 except 捕获
```

### 次要因素 C：`_persist()` 无写锁保护

`DataFetcher._log()` 和 `DataFetcher._upsert_batch()` 使用 `write_lock` 保护写操作，但 `fetch_job._persist()` 没有。在并发环境下，多个连接竞争 SQLite WAL writer lock，增加了 "database is locked" 概率。

### 次要因素 D：`pd.concat([None, df])` 安全隐患

**代码位置**: `backend/services/data_fetcher.py:342-350`

```python
all_income = df_income                     # 可能为 None（全部重试失败）
if df_income_q is not None and not df_income_q.empty:
    all_income = pd.concat([df_income, df_income_q])  # ← df_income 为 None 时抛出 TypeError
```

当年度财报全部抓取失败但季度报表成功时，`pd.concat([None, DataFrame])` 会抛出 `TypeError`。虽然被外层 try/except 捕获，但会导致该步骤的数据丢失。

### 次要因素 E：daemon 线程错误不可见

daemon 线程异常仅 `print()` 到 stdout，未调用 `system.track_error()` 写入 `error_logs` 表，导致排查困难。

---

## 4. 修复方案

### 修复 1：提高僵死超时并添加守护令牌 🔴 关键

**文件**: `backend/api/data.py`

```python
# 将 20 分钟超时提高到 4 小时
_FETCH_STALE_SECONDS = 4 * 60 * 60  # 4h，覆盖 54只×3.2min≈3h

# 添加守护令牌（generation counter），防止并发 fetch-all
_fetch_generation = 0

def _maybe_reset_stale_fetch():
    global _fetch_all_status, _fetch_started_mono
    if not _fetch_all_status.get("running"):
        return
    if _fetch_started_mono <= 0:
        return
    # 不再自动重置！只记录告警日志
    if time.monotonic() - _fetch_started_mono > _FETCH_STALE_SECONDS:
        print(f"[FetchAll] 警告: 抓取任务运行超过 {_FETCH_STALE_SECONDS//60} 分钟"
              f"，当前进度 {_fetch_all_status.get('progress')}")
        # 不重置状态，让线程自然完成
```

同时在 `fetch_all_stocks` 和 `do_fetch` 中使用 generation 防止并发：

```python
@router.post("/fetch-all")
async def fetch_all_stocks():
    global _fetch_all_status, _fetch_started_mono, _fetch_generation

    _maybe_reset_stale_fetch()

    if _fetch_all_status["running"]:
        return {"status": "already_running", "progress": _fetch_all_status["progress"]}

    # 递增世代计数器
    _fetch_generation += 1
    my_gen = _fetch_generation

    def do_fetch():
        ...
        for i, s in enumerate(stocks):
            # 检查是否被新请求取代
            if _fetch_generation != my_gen:
                print(f"[FetchAll] 任务已被新请求取代，世代 {my_gen} 退出")
                return  # 优雅退出
            ...
```

### 修复 2：加固内层 except 块 🔴 关键

**文件**: `backend/api/data.py`

```python
# do_fetch() 中的内层循环
for i, s in enumerate(stocks):
    try:
        fetch_job.start_job(s["id"])
        result = fetch_job.sync_fetch_one(s["id"], s["code"], s["market"])
        fetch_job.complete_job(s["id"], result)
        if result.get("status") != "error":
            completed += 1
    except Exception as e:
        # 嵌套保护：fail_job 失败不传播到外层
        try:
            fetch_job.fail_job(s["id"], str(e))
        except Exception as fe:
            print(f"[FetchAll] fail_job 也失败: {fe}")
        print(f"[FetchAll] {s['code']} 失败: {e}")
        # 记录到 error_logs
        try:
            from api.system import track_error
            track_error("fetch-all", str(e), {"stock": s["code"], "id": s["id"]})
        except Exception:
            pass
```

### 修复 3：`_persist()` 添加异常保护 🟡 重要

**文件**: `backend/services/fetch_job.py`

```python
def _persist(stock_id: int, job: dict[str, Any]) -> None:
    _memory[stock_id] = job
    try:
        db = _connect()
        try:
            db.execute("""
                INSERT INTO fetch_jobs (stock_id, status, running, quotes,
                    financials, indicators, errors_json, error, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, datetime('now'))
                ON CONFLICT(stock_id) DO UPDATE SET
                    status=excluded.status, running=excluded.running,
                    quotes=excluded.quotes, financials=excluded.financials,
                    indicators=excluded.indicators, errors_json=excluded.errors_json,
                    error=excluded.error, updated_at=datetime('now')
            """, (...))
            db.commit()
        finally:
            db.close()
    except Exception as e:
        print(f"[FetchJob] _persist 失败 stock_id={stock_id}: {e}")
        # 不向上传播异常，避免中断主循环
```

### 修复 4：`pd.concat` None 安全检查 🟡 重要

**文件**: `backend/services/data_fetcher.py`

```python
# 合并年度+季度数据时的 None 安全处理
all_income = df_income if df_income is not None else pd.DataFrame()
all_balance = df_balance if df_balance is not None else pd.DataFrame()
all_cf = df_cf if df_cf is not None else pd.DataFrame()

if df_income_q is not None and not df_income_q.empty:
    frames = [df_income, df_income_q]
    frames = [f for f in frames if f is not None and not f.empty]
    if frames:
        all_income = pd.concat(frames).drop_duplicates(
            subset=['REPORT_DATE', 'REPORT_TYPE'])
# 对 balance 和 cf 做同样处理
```

### 修复 5：daemon 异常写入 error_logs 🟢 改善

**文件**: `backend/api/data.py`

在外层 `except` 中增加错误持久化：

```python
except Exception as e:
    import traceback
    tb = traceback.format_exc()
    print(f"[FetchAll] 批量抓取异常: {e}\n{tb}")
    try:
        from api.system import track_error
        track_error("fetch-all-daemon", str(e),
                    {"traceback": tb, "completed": completed, "total": total})
    except Exception:
        pass
```

### 修复 6：添加手动停止机制 🟢 改善

**文件**: `backend/api/data.py`

```python
@router.post("/fetch-stop")
async def fetch_stop():
    """手动停止正在运行的抓取任务"""
    global _fetch_all_status, _fetch_generation
    if not _fetch_all_status.get("running"):
        return {"status": "not_running"}
    # 递增世代使 daemon 线程退出
    _fetch_generation += 1
    _reset_fetch_all_status(_fetch_all_status.get("progress", "0/0"))
    return {"status": "stopped", "progress": _fetch_all_status["progress"]}
```

---

## 5. 修复优先级

| 优先级 | 修复编号 | 影响范围 | 修复工作量 |
|--------|----------|----------|------------|
| 🔴 P0 | 修复 1 (超时+守护令牌) | 核心根因，阻止并发 fetch-all | 中 |
| 🔴 P0 | 修复 2 (内层异常加固) | 防止单点失败导致整批终止 | 小 |
| 🟡 P1 | 修复 3 (_persist 安全) | 防止 DB 锁异常传播 | 小 |
| 🟡 P1 | 修复 4 (concat None) | 防止财报合并失败 | 小 |
| 🟢 P2 | 修复 5 (错误日志) | 提升可观测性 | 小 |
| 🟢 P2 | 修复 6 (手动停止) | 提升运维可控性 | 小 |

---

## 6. 关键代码位置汇总

| 文件 | 行号 | 问题 |
|------|------|------|
| `backend/api/data.py` | 25 | `_FETCH_STALE_SECONDS = 20*60` 太短 |
| `backend/api/data.py` | 36-44 | `_maybe_reset_stale_fetch` 只重置标志不杀线程 |
| `backend/api/data.py` | 100-108 | 内层 except 中 `fail_job` 可能二次抛异常 |
| `backend/api/data.py` | 129-132 | 外层 except 仅 print，`finally` 无条件重置状态 |
| `backend/services/fetch_job.py` | 38-71 | `_persist` 无异常捕获，DB 锁会传播 |
| `backend/services/fetch_job.py` | 18-23 | `_connect` 每次创建新连接，无连接池 |
| `backend/services/data_fetcher.py` | 342-350 | `pd.concat([None, df])` 安全隐患 |
| `backend/services/data_fetcher.py` | 299-338 | 财报抓取总耗时过长（6次API调用+重试） |
