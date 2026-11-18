"""结构化日志与请求追踪（阶段 0：工程止血）。

提供：
  - JSON 行日志（可被 ELK / Loki 等直接采集），关键事件带结构化字段
  - request_id（ContextVar）—— 一次请求内的所有日志共享同一个 ID，便于串联
  - 幂等初始化，避免 uvicorn --reload 重复注册 handler 导致日志重复

不引入任何第三方依赖，纯标准库。
"""
from __future__ import annotations

import json
import logging
import sys
from contextvars import ContextVar
from datetime import datetime, timezone

# 一次请求一个 ID；默认 "-" 表示请求上下文之外（如启动脚本、离线测试）
_request_id: ContextVar[str] = ContextVar("request_id", default="-")


def new_request_id() -> str:
    """生成并绑定一个新的 request_id，返回它。在请求入口 middleware 调用。"""
    import uuid

    rid = uuid.uuid4().hex[:12]
    _request_id.set(rid)
    return rid


def get_request_id() -> str:
    return _request_id.get()


class JsonFormatter(logging.Formatter):
    """把日志记录序列化成单行 JSON。extra 里的自定义字段会平铺到顶层。"""

    _RESERVED = {
        "name", "msg", "args", "levelname", "levelno", "pathname", "filename",
        "module", "exc_info", "exc_text", "stack_info", "lineno", "funcName",
        "created", "msecs", "relativeCreated", "thread", "threadName",
        "processName", "process", "taskName",
    }

    def format(self, record: logging.LogRecord) -> str:
        payload = {
            "ts": datetime.fromtimestamp(record.created, tz=timezone.utc).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "request_id": get_request_id(),
            "event": record.getMessage(),
        }
        for key, value in record.__dict__.items():
            if key not in self._RESERVED and not key.startswith("_"):
                payload[key] = value
        if record.exc_info:
            payload["exc"] = self.formatException(record.exc_info)
        return json.dumps(payload, ensure_ascii=False, default=str)


def setup_logging(level: str | None = None) -> None:
    """幂等配置根 logger 输出 JSON 到 stdout。重复调用不会重复加 handler。"""
    root = logging.getLogger()
    if getattr(root, "_travel_agent_logging_ready", False):
        if level:
            root.setLevel(level.upper())
        return

    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(JsonFormatter())
    root.addHandler(handler)
    root.setLevel((level or "INFO").upper())
    root._travel_agent_logging_ready = True  # type: ignore[attr-defined]


def get_logger(name: str) -> logging.Logger:
    """取一个模块级 logger；调用方此前应先 setup_logging()。"""
    return logging.getLogger(name)
