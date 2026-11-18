"""Agent 后端抽象层（策略模式）。

- MockLLMBackend：零 Key，用 mock_data 确定性生成（默认）。
- RealLLMBackend：OpenAI 兼容 SDK + function calling，真实景点/餐饮/酒店来自高德 Web 服务，
  LLM 仅负责「从真实候选里规划 + 写文案」，并用 schema 约束输出——解决教程「JSON 解析脆弱」的痛点。

简历亮点：后端可插拔（mock / 真实 LLM），开发用 mock，上线换真实 Provider。
"""
from __future__ import annotations

import concurrent.futures
import json
import os
from datetime import date, datetime, timedelta

from errors import ConfigError
from models import TripPlan, Weather
from mock_data import generate_trip


class LLMBackend:
    def plan(
        self,
        destination: str,
        days: int,
        preferences: list[str],
        budget_limit=None,
        start_date: str | None = None,
    ) -> TripPlan:
        raise NotImplementedError


class MockLLMBackend(LLMBackend):
    def plan(self, destination, days, preferences, budget_limit=None, start_date=None):
        return generate_trip(destination, days, preferences, budget_limit, start_date)


class RealLLMBackend(LLMBackend):
    """真实路径：高德取真实数据 + LLM function calling 约束输出。

    需 pip install openai requests，并在 .env 配置 LLM_* 与 AMAP_API_KEY。
    相比教程（小众 hello_agents 库 + 弱 JSON 解析），本实现用通用 OpenAI 兼容接口 +
    function calling，更稳、更通用，也更易写进简历。
    """

    def __init__(self):
        from amap import AmapClient, AmapError

        try:
            from openai import OpenAI
        except ImportError:
            raise ConfigError("真实模式需要 openai 库，请先 pip install openai")

        api_key = os.getenv("LLM_API_KEY")
        if not api_key:
            raise ConfigError("缺少 LLM_API_KEY，请在 .env 配置后使用真实模式。")

        self.client = OpenAI(
            api_key=api_key,
            base_url=os.getenv("LLM_BASE_URL", "https://api.openai.com/v1"),
        )
        self.model = os.getenv("LLM_MODEL_ID", "gpt-4o-mini")

        try:
            self.amap = AmapClient()
        except AmapError as e:
            # 高德 key 缺失也属于配置错；其他高德初始化异常同样归配置类
            raise ConfigError(str(e))

    def _fetch_candidates(self, destination: str, pref: str):
        """并发拉取高德真实候选：3 路 POI 搜索 + 地理编码，线程池并发避免串行阻塞 I/O。

        教程原版把 3 次搜索顺序执行（10~30s），改为线程池后整体耗时≈单次请求，
        且各请求独立 result，单路失败不影响其余。
        """
        def _geo():
            g = self.amap.geocode(destination)
            return g["adcode"] if g else None

        with concurrent.futures.ThreadPoolExecutor(max_workers=4) as ex:
            f_attr = ex.submit(self.amap.search_poi, pref, destination, None, 15)
            f_meal = ex.submit(self.amap.search_poi, "美食", destination, None, 12)
            f_hotel = ex.submit(self.amap.search_poi, "酒店", destination, None, 8)
            f_geo = ex.submit(_geo)
            attr_pool = f_attr.result()
            meal_pool = f_meal.result()
            hotel_pool = f_hotel.result()
            adcode = f_geo.result()
        return attr_pool, meal_pool, hotel_pool, adcode

    def plan(self, destination, days, preferences, budget_limit=None, start_date=None):
        pref = "、".join(preferences) if preferences else "热门景点"

        # 1) 并发取真实候选（高德 Web 服务）：3 路 POI + 地理编码并行
        attr_pool, meal_pool, hotel_pool, adcode = self._fetch_candidates(destination, pref)
        forecast = self.amap.weather(adcode)
        casts = forecast.get("casts", []) if forecast else []
        weather_list = self._build_weather(destination, days, start_date, casts)

        # 1.5) 高德未返回任何景点候选（城市名过偏/Key 配额等），降级为 mock 保证可用
        if not attr_pool:
            plan = generate_trip(destination, days, preferences, budget_limit, start_date)
            for i, dp in enumerate(plan.days_plan):
                if i < len(weather_list):
                    dp.weather = weather_list[i]
            return plan

        # 2) 候选上下文明文，喂给 LLM
        def fmt(pool):
            return (
                "\n".join(
                    f"- {p['name']} | 地址:{p['address']} | 人均:{p['cost']} | 坐标:{p['lng']},{p['lat']}"
                    for p in pool
                    if p["lng"]
                )
                or "（无候选）"
            )

        attr_ctx, meal_ctx, hotel_ctx = fmt(attr_pool), fmt(meal_pool), fmt(hotel_pool)
        weather_ctx = json.dumps(
            casts[0] if casts else {}, ensure_ascii=False
        )

        # 3) function calling 约束输出（LLM 被迫按 schema 填空，杜绝自由文本 JSON）
        tools = [
            {
                "type": "function",
                "function": {
                    "name": "submit_trip_plan",
                    "description": "提交最终行程计划，必须从候选景点/餐饮/酒店中挑选",
                    "parameters": self._plan_schema(),
                },
            }
        ]
        system = (
            "你是专业旅行规划师。必须严格从【真实候选景点/餐饮/酒店】中挑选，"
            "不要把名称改成候选列表里没有的名字。按天数合理分配到每天，生成结构化行程。"
            f"目的地={destination}，天数={days}，偏好={preferences}，预算上限={budget_limit or '不限'}。"
        )
        user = (
            f"【真实候选景点】\n{attr_ctx}\n\n"
            f"【真实候选餐饮】\n{meal_ctx}\n\n"
            f"【真实候选酒店】\n{hotel_ctx}\n\n"
            f"【天气(首日)】{weather_ctx}\n\n"
            "请输出完整 TripPlan：含 overview、attractions、meals、hotel、budget、days_plan。"
            "attraction_ids/meal_ids 需引用上面列出的 id。"
        )
        resp = self.client.chat.completions.create(
            model=self.model,
            messages=[
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            tools=tools,
            tool_choice={"type": "function", "function": {"name": "submit_trip_plan"}},
            temperature=0.3,
        )
        msg = resp.choices[0].message
        if msg.tool_calls:
            args = json.loads(msg.tool_calls[0].function.arguments)
        else:  # 个别模型忽略 tool_choice，退而从 content 解析
            args = json.loads(msg.content)

        # 4) 用真实候选回填经纬度/地址/价格（防止 LLM 改写坐标导致地图错位）
        args = self._enrich(args, attr_pool, meal_pool, hotel_pool)
        # 补上模型必填字段：由请求决定，不让 LLM 猜测（schema 未含这两个字段）
        args["destination"] = destination
        args["days"] = days
        args["preferences"] = preferences
        plan = TripPlan.model_validate(args)
        # 回填天气（高德真实预报；超范围自动降级 mock）
        for i, dp in enumerate(plan.days_plan):
            if i < len(weather_list):
                dp.weather = weather_list[i]
        return plan

    @staticmethod
    def _build_weather(destination, days, start_date, casts):
        """依据出发日期，把高德 4 天预报对齐到每天；超出免费预报范围则降级为示意天气。"""
        today = date.today()
        try:
            start = datetime.strptime(start_date, "%Y-%m-%d").date() if start_date else today
        except ValueError:
            start = today
        offset = (start - today).days
        from mock_data import _mock_weather

        out = []
        for d in range(days):
            day_date = (start + timedelta(days=d)).strftime("%Y-%m-%d")
            idx = offset + d
            if 0 <= idx < len(casts):
                c = casts[idx]
                cond = c.get("dayweather", "")
                try:
                    tmax = float(c.get("daytemp", 0) or 0)
                    tmin = float(c.get("nighttemp", 0) or 0)
                except (ValueError, TypeError):
                    tmax = tmin = 0
                out.append(
                    Weather(
                        date=day_date,
                        condition=cond,
                        temp_min=tmin,
                        temp_max=tmax,
                        desc=f"{cond} {int(tmin)}~{int(tmax)}℃",
                        source="amap",
                    )
                )
            else:
                # 高德免费版仅支持未来 ~4 天预报，超出范围降级示意
                out.append(_mock_weather(destination, day_date, source="estimated"))
        return out

    @staticmethod
    def _find(pool, name):
        """按名称取候选：精确 -> 包含匹配 -> 兜底第一个，保证 location 永远有值。"""
        if not pool:
            return None
        name = (name or "").strip()
        for p in pool:
            if p["name"] == name:
                return p
        for p in pool:
            pn = p["name"]
            if pn and (pn in name or name in pn):
                return p
        return pool[0]

    def _enrich(self, args, attr_pool, meal_pool, hotel_pool):
        for a in args.get("attractions", []):
            c = self._find(attr_pool, a.get("name"))
            if c and c.get("lng"):
                a["location"] = {
                    "name": c["name"],
                    "address": c["address"],
                    "lat": c["lat"],
                    "lng": c["lng"],
                }
                if c.get("cost") is not None and not a.get("ticket_price"):
                    a["ticket_price"] = c["cost"]

        for m in args.get("meals", []):
            c = self._find(meal_pool, m.get("name"))
            if c and c.get("lng"):
                m["location"] = {
                    "name": m.get("name"),
                    "address": c.get("address", ""),
                    "lat": c["lat"],
                    "lng": c["lng"],
                }
                if c.get("cost") is not None and not m.get("price_per_person"):
                    m["price_per_person"] = c["cost"]

        h = args.get("hotel")
        if h:
            c = self._find(hotel_pool, h.get("name"))
            if c and c.get("lng"):
                h["location"] = {
                    "name": c["name"],
                    "address": c.get("address", ""),
                    "lat": c["lat"],
                    "lng": c["lng"],
                }
        return args

    @staticmethod
    def _plan_schema():
        return {
            "type": "object",
            "properties": {
                "overview": {"type": "string"},
                "attractions": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "id": {"type": "string"},
                            "name": {"type": "string"},
                            "description": {"type": "string"},
                            "ticket_price": {"type": "number"},
                            "visit_duration_hours": {"type": "number"},
                            "category": {"type": "string"},
                        },
                        "required": ["id", "name"],
                    },
                },
                "meals": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "id": {"type": "string"},
                            "name": {"type": "string"},
                            "cuisine": {"type": "string"},
                            "price_per_person": {"type": "number"},
                            "meal_type": {"type": "string"},
                        },
                        "required": ["id", "name"],
                    },
                },
                "hotel": {
                    "type": "object",
                    "properties": {
                        "id": {"type": "string"},
                        "name": {"type": "string"},
                        "price_per_night": {"type": "number"},
                        "star": {"type": "integer"},
                    },
                    "required": ["id", "name"],
                },
                "budget": {
                    "type": "object",
                    "properties": {
                        "attractions": {"type": "number"},
                        "hotel": {"type": "number"},
                        "meals": {"type": "number"},
                        "transport": {"type": "number"},
                        "total": {"type": "number"},
                    },
                    "required": ["total"],
                },
                "days_plan": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "day": {"type": "integer"},
                            "date": {"type": "string"},
                            "attraction_ids": {
                                "type": "array",
                                "items": {"type": "string"},
                            },
                            "meal_ids": {
                                "type": "array",
                                "items": {"type": "string"},
                            },
                            "summary": {"type": "string"},
                        },
                        "required": ["day"],
                    },
                },
            },
            "required": [
                "overview",
                "attractions",
                "meals",
                "hotel",
                "budget",
                "days_plan",
            ],
        }


def get_backend() -> LLMBackend:
    mode = os.getenv("AGENT_MODE", "mock").lower()
    if mode == "real":
        from tool_loop import ToolLoopBackend

        return ToolLoopBackend()
    if mode == "legacy":
        # 保留旧版写死管道，便于与新工具循环做 A/B 对照
        return RealLLMBackend()
    return MockLLMBackend()
