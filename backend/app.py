"""FastAPI 入口（阶段 0 工程止血后）。

启动：在 backend/ 目录执行  .venv/Scripts/python -m uvicorn app:app --reload --port 8000
接口：POST /api/trip/plan  { destination, days, preferences[], budget_limit? } -> TripPlan

可观测性与错误处理：
  - 每个请求分配 request_id，贯穿 access log / 业务日志 / 错误响应
  - 类型化异常按 code 返回统一结构（见 errors.py），不再把所有错误拍成 400
"""
from __future__ import annotations

import os
import time

from dotenv import load_dotenv
from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from pydantic import BaseModel
from typing import List, Optional

from errors import AppError, ValidationError
from llm_backends import get_backend
from models import TripPlan
from observability import get_logger, get_request_id, new_request_id, setup_logging

load_dotenv()
setup_logging()
log = get_logger("travel.api")

app = FastAPI(title="Travel Agent API", version="0.2.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # 本地开发放开；上线按前端域名收口
    allow_methods=["*"],
    allow_headers=["*"],
)


# ---------- 请求上下文 + access log ----------
@app.middleware("http")
async def request_context(request: Request, call_next):
    rid = new_request_id()
    started = time.perf_counter()
    response = None
    try:
        response = await call_next(request)
        return response
    except Exception:
        # 兜底：未预期的异常也带 request_id 记录，再由异常处理器转成响应
        log.exception("unhandled_exception", extra={"path": request.url.path})
        raise
    finally:
        elapsed_ms = round((time.perf_counter() - started) * 1000, 1)
        status = response.status_code if response is not None else 500
        log.info(
            "http_request",
            extra={"method": request.method, "path": request.url.path,
                   "status": status, "elapsed_ms": elapsed_ms},
        )
        if response is not None:
            response.headers["X-Request-ID"] = rid


# ---------- 统一异常处理器 ----------
@app.exception_handler(AppError)
async def handle_app_error(request: Request, exc: AppError):
    # 注意：extra 里不能用 "message" 键——它是 LogRecord 保留字段，
    # 用了会在 makeRecord 时抛 KeyError（踩过坑）。改用 err_msg。
    log.warning("app_error", extra={"code": exc.code, "err_msg": exc.message,
                                    "path": request.url.path})
    return JSONResponse(status_code=exc.status_code, content=exc.to_dict(get_request_id()))


@app.exception_handler(RequestValidationError)
async def handle_validation_error(request: Request, exc: RequestValidationError):
    # FastAPI 原生 422 转成统一结构，前端只需认 error.code
    message = "; ".join(
        f"{'.'.join(str(x) for x in e['loc'])}: {e['msg']}" for e in exc.errors()
    )
    err = ValidationError(f"请求参数校验失败: {message}")
    log.warning("validation_error", extra={"path": request.url.path, "detail": message})
    return JSONResponse(status_code=err.status_code, content=err.to_dict(get_request_id()))


@app.exception_handler(Exception)
async def handle_unexpected_error(request: Request, exc: Exception):
    err = AppError(f"服务内部错误: {exc}")
    log.exception("unexpected_error", extra={"path": request.url.path})
    return JSONResponse(status_code=err.status_code, content=err.to_dict(get_request_id()))


# ---------- 路由 ----------
class PlanRequest(BaseModel):
    destination: str
    days: int = 3
    preferences: List[str] = []
    budget_limit: Optional[float] = None
    start_date: Optional[str] = None  # 出发日期 YYYY-MM-DD，用于查询天气


@app.get("/health")
def health():
    return {"status": "ok", "agent_mode": os.getenv("AGENT_MODE", "mock")}


@app.post("/api/trip/plan", response_model=TripPlan)
def plan_trip(req: PlanRequest) -> TripPlan:
    # 业务参数校验：显式检查，抛 422 而非让非法值流进后端
    if not req.destination or not req.destination.strip():
        raise ValidationError("destination 不能为空")
    if req.days < 1 or req.days > 30:
        raise ValidationError("days 必须在 1~30 之间")
    if req.budget_limit is not None and req.budget_limit <= 0:
        raise ValidationError("budget_limit 必须大于 0")

    log.info("plan_request", extra={
        "destination": req.destination, "days": req.days,
        "preferences": req.preferences, "budget_limit": req.budget_limit,
    })
    backend = get_backend()  # 配置错（缺 key）会以 ConfigError 冒泡 -> 500
    plan = backend.plan(
        req.destination, req.days, req.preferences, req.budget_limit, req.start_date
    )
    log.info("plan_response", extra={
        "destination": req.destination, "attractions": len(plan.attractions),
        "days_planned": len(plan.days_plan), "total": plan.budget.total,
    })
    return plan
