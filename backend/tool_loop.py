"""工具循环 Agent（阶段 1：从"写死管道"升级为"模型自主决策"）。

与 llm_backends.RealLLMBackend 的本质区别：
  - 旧版：代码写死"并发查 3 路 POI -> 拼上下文 -> tool_choice 强绑提交"，模型零决策权。
  - 新版：把 search_poi / geocode / get_weather / submit_trip_plan 暴露成工具，
    tool_choice=auto，由模型自己决定"查什么、查几次、何时收工"。

工程要素（简历可写）：
  1. 工具循环：LLM 决策 -> 执行工具 -> 结果回填 messages -> 再决策，直到 submit 或触顶。
  2. 终止条件：MAX_TURNS 上限 + 全局超时；触顶/超时降级为 mock 计划，保证永远有结果。
  3. 失败兜底：工具异常/坏 JSON/校验失败都转成 tool message 回填，让模型自行纠错重试。
  4. 可观测：每轮 trace 记录 turn / tool / args / degraded，直接喂给阶段 2 的评测集。
  5. 依赖注入：llm_client 与 amap_client 可注入，零 Key 即可离线测试整条循环。

复用：继承 RealLLMBackend，直接复用其 _find/_enrich/_build_weather/_plan_schema，
不重复造轮子，也不改动旧类（旧并发测试照常通过，legacy 模式仍可用旧版做对照）。
"""
from __future__ import annotations

import json
import os
import time
from dataclasses import dataclass, field
from typing import Any

from llm_backends import RealLLMBackend
from models import TripPlan
from observability import get_logger

log = get_logger("travel.agent")

SYSTEM_PROMPT = """你是专业旅行规划师。你只能通过调用工具来获取真实数据，禁止凭空编造景点/餐厅/酒店名称与坐标。

可用工具：
- search_poi(keywords, category, page_size)：搜索目的地真实候选。category 取值 attraction/meal/hotel。
  你可以多次调用、换不同关键词，以获取足够候选。
- geocode(address)：把地名解析成 adcode，用于查天气。
- get_weather(adcode)：查询多日天气。
- submit_trip_plan(...)：提交最终行程。只能引用 search_poi 返回过的候选名称。

工作方式：
1. 先根据用户需求，自行决定需要查哪些数据、查几轮（不必一次查全，可先查景点再补餐饮/酒店）。
2. 收集到足够信息后，调用 submit_trip_plan 提交完整 TripPlan（overview/attractions/meals/hotel/budget/days_plan）。
3. 若工具返回错误，请读懂错误并调整参数重试，不要放弃。
4. 不要只输出文字，务必以 submit_trip_plan 工具调用结束。"""


@dataclass
class AgentResult:
    """一次规划的完整可观测结果。trace 供阶段 2 评测集统计平均轮次/工具命中。"""

    plan: TripPlan
    turns: int
    trace: list[dict] = field(default_factory=list)
    degraded: bool = False
    reason: str = ""


class ToolLoopBackend(RealLLMBackend):
    """模型自主决策的工具循环后端。

    真实运行：不传 client，走 RealLLMBackend.__init__ 初始化 OpenAI + 高德。
    离线测试：注入 llm_client / amap_client，跳过 Key 检查。
    """

    def __init__(
        self,
        llm_client: Any = None,
        amap_client: Any = None,
        max_turns: int | None = None,
        timeout: float | None = None,
    ):
        if llm_client is not None:
            # 注入模式（测试 / 离线）：完全跳过真实 SDK 与 Key 检查
            self.client = llm_client
            self.model = "fake-model"
            self.amap = amap_client
        else:
            super().__init__()  # 真实初始化：OpenAI client + 高德 + Key 校验
        self.max_turns = max_turns if max_turns is not None else int(os.getenv("AGENT_MAX_TURNS", "6"))
        self.timeout = timeout if timeout is not None else float(os.getenv("AGENT_TIMEOUT", "40"))
        self.last_result: AgentResult | None = None

    # ---------- 工具定义 ----------
    def _tools(self) -> list[dict]:
        return [
            {
                "type": "function",
                "function": {
                    "name": "search_poi",
                    "description": "搜索目的地真实候选 POI。可多次调用不同关键词。",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "keywords": {"type": "string", "description": "搜索关键词，如'古迹''博物馆''火锅'"},
                            "category": {
                                "type": "string",
                                "enum": ["attraction", "meal", "hotel"],
                                "description": "候选类别，用于分流到景点/餐饮/酒店",
                            },
                            "page_size": {"type": "integer", "description": "返回条数，默认15"},
                        },
                        "required": ["keywords", "category"],
                    },
                },
            },
            {
                "type": "function",
                "function": {
                    "name": "geocode",
                    "description": "把地名解析为 adcode（城市编码），用于查天气。",
                    "parameters": {
                        "type": "object",
                        "properties": {"address": {"type": "string"}},
                        "required": ["address"],
                    },
                },
            },
            {
                "type": "function",
                "function": {
                    "name": "get_weather",
                    "description": "查询目的地多日天气预报。",
                    "parameters": {
                        "type": "object",
                        "properties": {"adcode": {"type": "string"}},
                        "required": ["adcode"],
                    },
                },
            },
            {
                "type": "function",
                "function": {
                    "name": "submit_trip_plan",
                    "description": "提交最终行程计划，必须只引用 search_poi 返回过的候选名称。",
                    "parameters": self._plan_schema(),
                },
            },
        ]

    # ---------- 执行上下文：累积模型查到的真实数据 ----------
    @dataclass
    class _Ctx:
        candidates: dict = field(
            default_factory=lambda: {"attraction": [], "meal": [], "hotel": []}
        )
        adcode: str | None = None
        weather: dict = field(default_factory=dict)

    # ---------- 主入口 ----------
    def plan(self, destination, days, preferences, budget_limit=None, start_date=None) -> TripPlan:
        result = self._run(destination, days, preferences, budget_limit, start_date)
        self.last_result = result
        return result.plan

    def _run(self, destination, days, preferences, budget_limit, start_date) -> AgentResult:
        ctx = self._Ctx()
        trace: list[dict] = []
        messages: list[dict] = [
            {"role": "system", "content": SYSTEM_PROMPT},
            {
                "role": "user",
                "content": (
                    f"目的地={destination}，天数={days}，偏好={preferences}，"
                    f"预算上限={budget_limit or '不限'}，出发日期={start_date or '未指定'}。"
                    "请用工具获取真实数据并规划行程，最后调用 submit_trip_plan。"
                ),
            },
        ]
        started = time.monotonic()
        log.info("agent_loop_start", extra={
            "destination": destination, "days": days, "max_turns": self.max_turns,
        })

        for turn in range(1, self.max_turns + 1):
            # 终止条件 1：全局超时
            if time.monotonic() - started > self.timeout:
                return self._degrade(destination, days, preferences, budget_limit,
                                     start_date, ctx, trace, turn, "timeout")

            try:
                resp = self.client.chat.completions.create(
                    model=self.model,
                    messages=messages,
                    tools=self._tools(),
                    tool_choice="auto",  # 关键：把决策权交给模型
                    temperature=0.3,
                )
            except Exception as e:  # LLM 调用本身失败 -> 降级
                return self._degrade(destination, days, preferences, budget_limit,
                                     start_date, ctx, trace, turn, f"llm_error: {e}")

            msg = resp.choices[0].message
            tool_calls = getattr(msg, "tool_calls", None) or []

            # 兜底：模型没调工具只回文本 -> 推它一把，继续下一轮
            if not tool_calls:
                messages.append({"role": "assistant", "content": msg.content or ""})
                messages.append({
                    "role": "user",
                    "content": "请调用工具获取数据，并以 submit_trip_plan 结束，不要只输出文字。",
                })
                trace.append({"turn": turn, "tool": "<no-tool>", "args": {}})
                continue

            # 先把模型的 tool_calls 原样回填（OpenAI 要求 assistant message 在前）
            messages.append(self._assistant_msg(msg))

            submitted: dict | None = None
            for tc in tool_calls:
                name = tc.function.name
                # 兜底：坏 JSON 不致命，转成空参数，让工具/模型给出可读错误
                try:
                    args = json.loads(tc.function.arguments or "{}")
                except (json.JSONDecodeError, TypeError):
                    args = {}
                    trace.append({"turn": turn, "tool": name, "args": {}, "bad_json": True})
                else:
                    trace.append({"turn": turn, "tool": name, "args": args})

                try:
                    result = self._dispatch(name, args, ctx, destination, days,
                                            preferences, budget_limit, start_date)
                except Exception as e:  # 工具异常 -> 回填给模型，让它纠错重试
                    result = {"ok": False, "error": f"{type(e).__name__}: {e}"}

                # 把执行结果落进 trace，供阶段 2 评测精确统计
                # （幻觉拦截 / 预算超限 / 工具失败都在这里留下 ok=False 标记）
                if trace and trace[-1].get("tool") == name:
                    trace[-1]["ok"] = bool(result.get("ok"))
                    if not result.get("ok"):
                        trace[-1]["error"] = str(result.get("error", ""))[:200]
                log.info("tool_call", extra={
                    "turn": turn, "tool": name, "ok": bool(result.get("ok")),
                    "error": str(result.get("error", ""))[:120] or None,
                })

                if name == "submit_trip_plan":
                    submitted = result

                # 回填 tool message；plan 对象不序列化进上下文，只回摘要
                public = {k: v for k, v in result.items() if k != "plan"}
                messages.append({
                    "role": "tool",
                    "tool_call_id": tc.id,
                    "content": json.dumps(public, ensure_ascii=False, default=str),
                })

            # 终止条件 2：模型提交了 且 校验通过
            if submitted and submitted.get("ok"):
                log.info("plan_submitted", extra={"turns": turn, "degraded": False})
                return AgentResult(
                    plan=submitted["plan"], turns=turn, trace=trace,
                    degraded=False, reason="submitted",
                )
            # submit 校验失败 -> result 里带 error 已回填，继续下一轮让模型重试

        # 终止条件 3：触顶仍未成功提交 -> 降级，保证永远有结果
        return self._degrade(destination, days, preferences, budget_limit,
                             start_date, ctx, trace, self.max_turns, "max_turns")

    # ---------- 工具分发 ----------
    def _dispatch(self, name, args, ctx, destination, days, preferences, budget_limit, start_date):
        if name == "search_poi":
            keywords = args.get("keywords") or destination
            category = args.get("category", "attraction")
            if category not in ctx.candidates:
                return {"ok": False, "error": f"未知 category: {category}"}
            pool = self.amap.search_poi(
                keywords, destination, None,
                int(args.get("page_size", 15)),
            )
            ctx.candidates[category].extend(pool)
            # 只把精简摘要回给模型，避免坐标等噪声撑大上下文
            items = [{"name": p.get("name"), "cost": p.get("cost")} for p in pool]
            return {"ok": True, "category": category, "count": len(pool), "items": items}

        if name == "geocode":
            address = args.get("address") or destination
            g = self.amap.geocode(address)
            if not g or not g.get("adcode"):
                return {"ok": False, "error": f"geocode 未返回 adcode: {address}"}
            ctx.adcode = g["adcode"]
            return {"ok": True, "adcode": ctx.adcode}

        if name == "get_weather":
            adcode = args.get("adcode") or ctx.adcode
            if not adcode:
                return {"ok": False, "error": "缺少 adcode，请先调用 geocode"}
            w = self.amap.weather(adcode) or {}
            ctx.weather = w
            casts = w.get("casts", [])[:4]
            return {"ok": True, "casts": casts}

        if name == "submit_trip_plan":
            try:
                # 防幻觉：模型只能引用搜索过的候选。未命中则明确报错回填，
                # 让模型先 search_poi 再提交（也是阶段 2「候选命中率」的拦截点）。
                bad = self._unresolved_refs(args, ctx)
                if bad:
                    return {
                        "ok": False,
                        "error": "以下引用不在搜索结果中，请先调用 search_poi 再提交: "
                        + "; ".join(bad),
                    }
                a = dict(args)
                a = self._enrich(
                    a, ctx.candidates["attraction"],
                    ctx.candidates["meal"], ctx.candidates["hotel"],
                )
                a["destination"] = destination
                a["days"] = days
                a["preferences"] = preferences
                plan = TripPlan.model_validate(a)
                casts = ctx.weather.get("casts", []) if ctx.weather else []
                weather_list = self._build_weather(destination, days, start_date, casts)
                for i, dp in enumerate(plan.days_plan):
                    if i < len(weather_list):
                        dp.weather = weather_list[i]
                return {"ok": True, "plan": plan, "summary": "已通过校验并提交"}
            except Exception as e:
                return {"ok": False, "error": f"提交校验失败，请修正后重试: {e}"}

        return {"ok": False, "error": f"未知工具: {name}"}

    @staticmethod
    def _unresolved_refs(args: dict, ctx) -> list[str]:
        """返回在候选池中找不到匹配的引用（防幻觉）。

        逻辑与 _find 对齐：精确 -> 包含匹配。
        注意：某类池为空但 submit 里出现了该类引用 = 模型压根没搜就编造，必须拦截。
        （阶段 2 评测 C21-C23 曾暴露此漏洞：旧实现跳过空池，导致幻觉绕过拦截。）
        """
        bad: list[str] = []

        def matched(name, pool) -> bool:
            name = (name or "").strip()
            return any(
                p.get("name") and (p["name"] == name or name in p["name"] or p["name"] in name)
                for p in pool
            )

        checks = [
            ("景点", args.get("attractions", []), ctx.candidates["attraction"]),
            ("餐饮", args.get("meals", []), ctx.candidates["meal"]),
        ]
        hotel = args.get("hotel")
        if hotel:
            checks.append(("酒店", [hotel], ctx.candidates["hotel"]))

        for label, items, pool in checks:
            if not items:  # submit 里没有该类引用，无需检查
                continue
            for it in items:
                name = it.get("name") if isinstance(it, dict) else None
                # 空池 = 没搜过 = 一定幻觉；非空池则做包含匹配
                if not pool or not matched(name, pool):
                    bad.append(f"{label}:{name}")
        return bad

    # ---------- 降级：触顶 / 超时 / LLM 崩溃 ----------
    def _degrade(self, destination, days, preferences, budget_limit, start_date,
                 ctx, trace, turn, reason) -> AgentResult:
        from mock_data import generate_trip

        log.warning("agent_degraded", extra={
            "reason": reason, "turns": turn, "destination": destination,
        })
        plan = generate_trip(destination, days, preferences, budget_limit, start_date)
        # 若模型已查到真实天气，尽量保留真实数据
        casts = ctx.weather.get("casts", []) if ctx.weather else []
        if casts:
            weather_list = self._build_weather(destination, days, start_date, casts)
            for i, dp in enumerate(plan.days_plan):
                if i < len(weather_list):
                    dp.weather = weather_list[i]
        return AgentResult(plan=plan, turns=turn, trace=trace, degraded=True, reason=reason)

    # ---------- 工具方法 ----------
    @staticmethod
    def _assistant_msg(msg) -> dict:
        """把 SDK 返回的 message（对象或 dict）统一成可回填的 assistant dict。"""
        tool_calls = []
        for tc in (getattr(msg, "tool_calls", None) or []):
            fn = tc.function
            tool_calls.append({
                "id": tc.id,
                "type": "function",
                "function": {"name": fn.name, "arguments": fn.arguments},
            })
        return {"role": "assistant", "content": getattr(msg, "content", None) or "",
                "tool_calls": tool_calls}
