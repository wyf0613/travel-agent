"""阶段 0 验证：错误码分类 + 结构化日志 + request_id（零 Key）。

覆盖：
  - 参数校验失败 -> 422 + 统一 error 结构
  - 业务参数非法 -> 422
  - 配置错（缺 key）-> 500 + CONFIG_ERROR
  - 上游超时 -> 504 + UPSTREAM_TIMEOUT
  - 响应头带 X-Request-ID，且与错误响应体里的 request_id 一致
  - 日志 JSON 可解析，字段齐全

运行：
    python tests/test_observability.py
    pytest tests/test_observability.py -v
"""
import io
import json
import os
import sys

_BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(_BASE, "backend"))

os.environ["AGENT_MODE"] = "mock"

from fastapi.testclient import TestClient

from app import app
from errors import AppError, ConfigError, UpstreamTimeoutError
from observability import JsonFormatter

client = TestClient(app)


def _post(payload):
    return client.post("/api/trip/plan", json=payload)


def test_validation_error_422():
    """Pydantic 校验失败（days 不是数字）-> 422 统一结构。"""
    r = _post({"destination": "北京", "days": "abc"})
    assert r.status_code == 422, r.text
    body = r.json()["error"]
    assert body["code"] == "VALIDATION_ERROR"
    assert "days" in body["message"]
    assert "request_id" in body and body["request_id"] == r.headers["X-Request-ID"]
    print("TEST_1_OK Pydantic校验->422统一结构")


def test_business_validation_422():
    """业务参数非法（days=0）-> 422；合法请求带 X-Request-ID 头。"""
    r = _post({"destination": "北京", "days": 0})
    assert r.status_code == 422, r.text
    assert r.json()["error"]["code"] == "VALIDATION_ERROR"

    ok = _post({"destination": "北京", "days": 2})
    assert ok.status_code == 200
    assert ok.headers.get("X-Request-ID")
    print("TEST_2_OK 业务参数校验+请求ID响应头")


def test_error_code_mapping():
    """异常类型 -> (status, code) 映射正确。"""
    assert (ConfigError("x").status_code, ConfigError("x").code) == (500, "CONFIG_ERROR")
    assert (UpstreamTimeoutError("x").status_code, UpstreamTimeoutError("x").code) == \
           (504, "UPSTREAM_TIMEOUT")
    # 自定义 code 覆盖
    e = AppError("boom", status_code=503, code="CUSTOM")
    assert e.to_dict("rid")["error"] == {"code": "CUSTOM", "message": "boom", "request_id": "rid"}
    print("TEST_3_OK 错误码映射")


def test_json_formatter():
    """日志格式化成合法 JSON，且含 event/level/request_id 等字段。"""
    import logging

    record = logging.LogRecord(
        name="travel.test", level=logging.INFO, pathname=__file__, lineno=1,
        msg="hello %s", args=("world",), exc_info=None,
    )
    line = JsonFormatter().format(record)
    data = json.loads(line)
    assert data["event"] == "hello world"
    assert data["level"] == "INFO"
    assert data["logger"] == "travel.test"
    assert "request_id" in data and "ts" in data
    print("TEST_4_OK JSON日志格式")


if __name__ == "__main__":
    test_validation_error_422()
    test_business_validation_422()
    test_error_code_mapping()
    test_json_formatter()
    print("ALL_OBSERVABILITY_TESTS_OK")
