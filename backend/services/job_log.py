"""调度作业分步日志(P14)。

写到 data/logs/<job>_YYYYMMDD.log,每行带时间戳并立即 flush。
调度器用子进程跑作业、stdout 被捕获,作业被看门狗强杀后只有这个文件能看出卡在哪一步。
"""
from __future__ import annotations

import os
import threading
from datetime import date, datetime

from config import DATA_DIR


class JobLog:
    """可调用的日志器:`log("文字")`。打不开文件时退化为什么都不做,绝不影响作业本身。"""

    def __init__(self, job: str):
        self.path = os.path.join(DATA_DIR, "logs", f"{job}_{date.today():%Y%m%d}.log")
        self._lock = threading.Lock()
        try:
            os.makedirs(os.path.dirname(self.path), exist_ok=True)
            self._fh = open(self.path, "a", encoding="utf-8")
        except OSError:
            self._fh = None

    def __call__(self, text: str) -> None:
        if self._fh is None:
            return
        with self._lock:
            try:
                self._fh.write(f"{datetime.now():%H:%M:%S}  {text}\n")
                self._fh.flush()
            except (OSError, ValueError):
                pass

    def close(self) -> None:
        if self._fh is not None:
            try:
                self._fh.close()
            except OSError:
                pass
            self._fh = None


def noop_log(_text: str) -> None:
    """没有传日志器时的默认值。"""
