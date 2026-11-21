"""全市场逐只抓取的通用执行器(P14):小并发 + 时间预算 + 游标续抓。

背景:夜间任务原来对 5000+ 只股票串行抓公告和新闻,合计约 3.6 小时,
每天都在 3 小时超时处被强杀,排在后面的股票几乎轮不到,后续步骤也从没跑到。

做法:
- 网络请求(fetch)放在线程池里并发,每个线程请求后自带间隔(限速);
- 写库(store)只在调用方线程里按顺序执行,避免多线程抢写锁;
- 到了时间预算就停止派发新任务,已派发的跑完后保存游标;
- 下次从游标之后继续(到末尾再绕回开头),保证每只股票都轮得到。
"""
from __future__ import annotations

import json
import os
import threading
import time
from collections import deque
from concurrent.futures import ThreadPoolExecutor
from typing import Any, Callable, Iterable, Optional, Sequence

from config import DATA_DIR

CURSOR_PATH = os.path.join(DATA_DIR, "fetch_cursors.json")
_cursor_lock = threading.Lock()


def load_cursor(key: str, path: str = CURSOR_PATH) -> Optional[int]:
    try:
        with open(path, encoding="utf-8") as f:
            v = json.load(f).get(key)
        return int(v) if v is not None else None
    except (OSError, ValueError, TypeError):
        return None


def save_cursor(key: str, stock_id: int, path: str = CURSOR_PATH) -> None:
    with _cursor_lock:
        data: dict = {}
        try:
            with open(path, encoding="utf-8") as f:
                data = json.load(f)
        except (OSError, ValueError):
            data = {}
        data[key] = int(stock_id)
        tmp = path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        os.replace(tmp, path)


def rotate_after(rows: Sequence[tuple[int, str]], last_id: Optional[int]) -> list[tuple[int, str]]:
    """rows 按 id 升序;返回从 last_id 之后开始、绕回开头的顺序。"""
    rows = list(rows)
    if last_id is None or not rows:
        return rows
    start = next((i for i, (sid, _) in enumerate(rows) if sid > last_id), 0)
    return rows[start:] + rows[:start]


def run_rotating_fetch(
    rows: Iterable[tuple[int, str]],
    fetch: Callable[[str], Any],
    store: Callable[[int, Any], int],
    *,
    workers: int = 1,
    sleep_s: float = 0.0,
    budget_s: Optional[float] = None,
    cursor_key: Optional[str] = None,
    cursor_path: str = CURSOR_PATH,
    on_batch: Optional[Callable[[], None]] = None,
    batch_size: int = 50,
    log: Optional[Callable[[str], None]] = None,
    label: str = "",
) -> dict:
    """对 rows 里的每只股票先 fetch(code)(线程池里跑)、再 store(stock_id, 结果)(本线程按顺序跑)。

    - store 返回新增条数;fetch 或 store 抛异常只记错误,不中断
    - on_batch:每处理 batch_size 只和结束时各调用一次(调用方用来 commit)
    - 返回 stocks / processed / added / errors / elapsed_s / complete_cycle / stopped_by_budget / cursor
    """
    ordered = list(rows)
    if cursor_key:
        ordered = rotate_after(ordered, load_cursor(cursor_key, cursor_path))
    total = len(ordered)
    t0 = time.time()
    workers = max(1, int(workers))

    def _fetch(code: str) -> Any:
        try:
            return fetch(code)
        finally:
            if sleep_s:
                time.sleep(sleep_s)

    processed = added = 0
    errors: list[str] = []
    last_done: Optional[int] = None
    stopped_by_budget = False
    progress_every = max(500, batch_size)

    def _over_budget() -> bool:
        return budget_s is not None and time.time() - t0 >= budget_s

    with ThreadPoolExecutor(max_workers=workers) as pool:
        pending: deque = deque()
        it = iter(ordered)

        def _submit_next() -> bool:
            nxt = next(it, None)
            if nxt is None:
                return False
            pending.append((nxt[0], nxt[1], pool.submit(_fetch, nxt[1])))
            return True

        for _ in range(workers * 2):
            if not _submit_next():
                break

        while pending:
            sid, code, fut = pending.popleft()
            try:
                added += int(store(sid, fut.result()) or 0)
            except Exception as e:  # noqa: BLE001 —— 单只失败不影响整体
                errors.append(f"{code}:{str(e)[:80]}")
            processed += 1
            last_done = sid

            if on_batch and processed % batch_size == 0:
                on_batch()
            if cursor_key and processed % batch_size == 0:
                save_cursor(cursor_key, sid, cursor_path)
            if log and processed % progress_every == 0:
                log(f"   {label} 进度 {processed}/{total},新增 {added},错误 {len(errors)},"
                    f"用时 {time.time() - t0:.0f}s")

            if not stopped_by_budget and _over_budget():
                stopped_by_budget = True
            if not stopped_by_budget:
                _submit_next()
            # 超预算后不再派发,只把已派发的处理完

    if on_batch:
        on_batch()
    if cursor_key and last_done is not None:
        save_cursor(cursor_key, last_done, cursor_path)

    return {
        "stocks": total,
        "processed": processed,
        "added": added,
        "errors": errors[:10],
        "error_count": len(errors),
        "elapsed_s": round(time.time() - t0, 1),
        "complete_cycle": processed >= total,
        "stopped_by_budget": stopped_by_budget,
        "cursor": last_done,
    }
