"""统一日志配置 — JSON 结构化输出 + 文件轮转"""
from __future__ import annotations

import json
import logging
import os
import time
import traceback
from logging.handlers import RotatingFileHandler
from pathlib import Path

LOG_DIR = Path(__file__).parent.parent / "logs"
LOG_DIR.mkdir(exist_ok=True)

LOG_FORMAT = os.getenv("LOG_FORMAT", "json")  # "json" | "text"


class JsonFormatter(logging.Formatter):
    """每行一个 JSON 对象，机器可读，Datadog/Loki 兼容。"""

    def format(self, record: logging.LogRecord) -> str:
        payload: dict = {
            "ts": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(record.created)),
            "level": record.levelname,
            "logger": record.name,
            "msg": record.getMessage(),
        }
        # extra fields from logger.info("x", extra={...})
        for key, val in record.__dict__.items():
            if key not in logging.LogRecord.__dict__ and not key.startswith("_"):
                payload[key] = val
        if record.exc_info:
            payload["exc"] = traceback.format_exception(*record.exc_info)[-1].strip()
        return json.dumps(payload, ensure_ascii=False)


def _make_handler(path: Path, level: int) -> RotatingFileHandler:
    h = RotatingFileHandler(path, maxBytes=10 * 1024 * 1024, backupCount=3, encoding="utf-8")
    h.setLevel(level)
    h.setFormatter(JsonFormatter() if LOG_FORMAT == "json" else logging.Formatter(
        "%(asctime)s [%(levelname)s] %(name)s: %(message)s", datefmt="%Y-%m-%d %H:%M:%S"
    ))
    return h


def get_logger(name: str, level: str | None = None) -> logging.Logger:
    """获取预配置的日志器（幂等）。"""
    if not level:
        level = os.getenv("LOG_LEVEL", "INFO")
    logger = logging.getLogger(name)
    if logger.handlers:
        return logger
    logger.setLevel(getattr(logging, level.upper(), logging.INFO))
    logger.addHandler(_make_handler(LOG_DIR / "afr.log", logging.DEBUG))
    # 控制台仅 WARNING+（开发模式可覆盖）
    ch = logging.StreamHandler()
    ch.setLevel(logging.WARNING)
    ch.setFormatter(logging.Formatter("[%(levelname)s] %(name)s: %(message)s"))
    logger.addHandler(ch)
    return logger


def configure_root_logging(level: str = "INFO") -> None:
    """在 app.py 启动时调用一次，配置根 logger 为 JSON 输出。"""
    root = logging.getLogger()
    if any(isinstance(h, RotatingFileHandler) for h in root.handlers):
        return  # 已配置，幂等
    root.setLevel(getattr(logging, level.upper(), logging.INFO))
    root.addHandler(_make_handler(LOG_DIR / "afr.log", logging.DEBUG))
    # 控制台保持简洁
    ch = logging.StreamHandler()
    ch.setLevel(logging.WARNING)
    ch.setFormatter(logging.Formatter("[%(levelname)s] %(name)s: %(message)s"))
    root.addHandler(ch)


# 预创建常用 logger
db_log = get_logger("afr.database")
api_log = get_logger("afr.api")
score_log = get_logger("afr.scorer")
