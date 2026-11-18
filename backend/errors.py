"""类型化异常与统一错误码（阶段 0：工程止血）。

改造前：app.py 用一个 `except Exception -> 400` 把所有错误拍平，
配置缺失、参数错误、上游超时全都返回 400，调用方无法区分，也不利于监控。

改造后：每类异常携带 (http_status, code)，全局异常处理器统一转成
  {"error": {"code": "...", "message": "...", "request_id": "..."}}
前端可按 code 决定提示，运维可按 code 聚合告警。
"""
from __future__ import annotations


class AppError(Exception):
    """所有业务异常基类。code 用于程序判断，message 用于人读。"""

    status_code = 500
    code = "INTERNAL_ERROR"

    def __init__(self, message: str, *, status_code: int | None = None, code: str | None = None):
        super().__init__(message)
        self.message = message
        if status_code is not None:
            self.status_code = status_code
        if code is not None:
            self.code = code

    def to_dict(self, request_id: str = "-") -> dict:
        return {
            "error": {
                "code": self.code,
                "message": self.message,
                "request_id": request_id,
            }
        }


class ConfigError(AppError):
    """配置缺失/非法（缺 Key 等）——服务端问题，500。"""

    status_code = 500
    code = "CONFIG_ERROR"


class ValidationError(AppError):
    """业务参数校验失败——客户端问题，422。"""

    status_code = 422
    code = "VALIDATION_ERROR"


class UpstreamTimeoutError(AppError):
    """上游 LLM / 高德超时——504。"""

    status_code = 504
    code = "UPSTREAM_TIMEOUT"


class UpstreamError(AppError):
    """上游 LLM / 高德返回错误——502。"""

    status_code = 502
    code = "UPSTREAM_ERROR"


class AgentError(AppError):
    """Agent 编排内部错误——500。"""

    status_code = 500
    code = "AGENT_ERROR"
