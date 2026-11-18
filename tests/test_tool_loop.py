"""工具循环 Agent 的离线测试（零 Key / 零网络）。

用 FakeLLM 按脚本逐轮返回 tool_calls，模拟"模型自主决策"：
  - 模型可以决定查几轮、查什么（这是与旧版写死管道的本质区别）
  - 用 FakeAmap 提供候选，验证 enrich 回填真实坐标
  - 覆盖：自主多轮收敛 / MAX_TURNS 触顶降级 / 坏 JSON + 工具异常兜底

运行：
    python tests/test_tool_loop.py
    pytest tests/test_tool_loop.py -v
"""
import json
import os
import sys
from types import SimpleNamespace

_BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(_BASE, "backend"))

from tool_loop import ToolLoopBackend


# ---------- 测试替身 ----------
class FakeAmap:
    """稳定的高德替身：按 category 返回带坐标的候选。"""

    def __init__(self, fail_search_times=0):
        self.fail_search_times = fail_search_times
        self.search_count = 0

    def search_poi(self, keywords, city=None, poi_type=None, page_size=15):
        self.search_count += 1
        if self.search_count <= self.fail_search_times:
            raise RuntimeError("模拟高德超时")
        return [{
            "name": f"{keywords}-候选", "address": "测试地址",
            "lng": 116.39, "lat": 39.91, "adcode": "110000",
            "cost": 80, "type": "test",
        }]

    def geocode(self, address, city=None):
        return {"lng": 116.39, "lat": 39.91, "adcode": "110000"}

    def weather(self, adcode):
        return {"casts": [{"dayweather": "晴", "daytemp": "25", "nighttemp": "15"}]}


class FakeCompletions:
    """按脚本逐次返回响应；每次调用记下 messages 供断言。"""

    def __init__(self, script):
        self.script = list(script)
        self.i = 0
        self.calls = []

    def create(self, **kwargs):
        self.calls.append(kwargs)
        resp = self.script[min(self.i, len(self.script) - 1)]
        self.i += 1
        return resp


class FakeLLM:
    def __init__(self, script):
        self.chat = SimpleNamespace(completions=FakeCompletions(script))


def _tc(call_id, name, args):
    """构造一个 tool_call 对象（兼容 codebuddy 的 OpenAI SDK 形态）。"""
    return SimpleNamespace(
        id=call_id,
        function=SimpleNamespace(
            name=name,
            arguments=args if isinstance(args, str) else json.dumps(args, ensure_ascii=False),
        ),
    )


def _resp(*tool_calls, content=None):
    msg = SimpleNamespace(content=content, tool_calls=list(tool_calls))
    return SimpleNamespace(choices=[SimpleNamespace(message=msg)])


def _submit_args(attr_name, meal_name, hotel_name):
    """一个能通过 TripPlan 校验的最小 submit 参数。"""
    return {
        "overview": "测试行程",
        "attractions": [{"id": "a1", "name": attr_name, "ticket_price": 80,
                         "visit_duration_hours": 2, "category": "历史古迹"}],
        "meals": [{"id": "m1", "name": meal_name, "price_per_person": 80,
                   "cuisine": "测试菜", "meal_type": "lunch"}],
        "hotel": {"id": "h1", "name": hotel_name, "price_per_night": 300, "star": 4},
        "budget": {"attractions": 80, "hotel": 300, "meals": 80,
                   "transport": 100, "total": 560},
        "days_plan": [{"day": 1, "attraction_ids": ["a1"], "meal_ids": ["m1"],
                       "summary": "第一天"}],
    }


# ---------- 测试用例 ----------
def test_model_decides_multiple_rounds_then_submits():
    """模型自主决定：先查景点 -> 再补餐饮 -> 再提交（轮次由模型脚本决定，非写死）。"""
    script = [
        _resp(_tc("c1", "search_poi", {"keywords": "故宫", "category": "attraction"})),
        # 模型自己决定先 geocode 再 get_weather
        _resp(_tc("c2", "geocode", {"address": "北京"})),
        _resp(_tc("c3", "get_weather", {"adcode": "110000"})),
        _resp(_tc("c4", "search_poi", {"keywords": "烤鸭", "category": "meal"})),
        _resp(_tc("c5", "search_poi", {"keywords": "希尔顿", "category": "hotel"})),
        _resp(_tc("c6", "submit_trip_plan",
                  _submit_args("故宫-候选", "烤鸭-候选", "希尔顿-候选"))),
    ]
    llm = FakeLLM(script)
    amap = FakeAmap()
    backend = ToolLoopBackend(llm_client=llm, amap_client=amap, max_turns=6, timeout=10)

    plan = backend.plan("北京", 1, ["历史"], budget_limit=1000)
    r = backend.last_result

    # 模型决定查 5 轮再提交 -> 恰好 6 轮收敛
    assert r.turns == 6, f"应为 6 轮，实际 {r.turns}"
    assert not r.degraded and r.reason == "submitted"
    # trace 证明工具调用顺序由模型脚本决定（含 geocode -> get_weather）
    tools = [t["tool"] for t in r.trace]
    assert tools == [
        "search_poi", "geocode", "get_weather", "search_poi", "search_poi", "submit_trip_plan",
    ], tools
    # 每次请求都用 auto，未强绑工具
    for call in llm.chat.completions.calls:
        assert call["tool_choice"] == "auto"
    # enrich：景点/餐饮回填了真实坐标，酒店无候选则保留模型字段
    assert plan.attractions[0].location.lat == 39.91
    assert plan.meals[0].location.lng == 116.39
    # 天气回填
    assert plan.days_plan[0].weather is not None
    assert plan.days_plan[0].weather.source == "amap"
    print("TEST_1_OK 自主多轮收敛")


def test_max_turns_degrades_to_mock():
    """模型一直查不提交 -> 触顶降级，仍返回合法计划。"""
    script = [
        _resp(_tc("c1", "search_poi", {"keywords": "故宫", "category": "attraction"})),
    ]
    llm = FakeLLM(script)
    backend = ToolLoopBackend(llm_client=llm, amap_client=FakeAmap(),
                              max_turns=3, timeout=10)

    plan = backend.plan("北京", 2, [])
    r = backend.last_result

    assert r.turns == 3, r.turns
    assert r.degraded is True and r.reason == "max_turns"
    assert plan.destination == "北京" and len(plan.days_plan) == 2
    print("TEST_2_OK 触顶降级")


def test_bad_json_and_tool_error_recover():
    """坏 JSON + 工具异常都回填给模型，模型下一轮纠错后成功提交。"""
    script = [
        # 第 1 轮：坏 JSON，且高德前 1 次调用抛异常
        _resp(_tc("c1", "search_poi", "{bad json")),
        # 第 2 轮：模型收到错误后，正常查询（第 2 次调用不再失败）
        _resp(_tc("c2", "search_poi", {"keywords": "颐和园", "category": "attraction"})),
        _resp(_tc("c3", "search_poi", {"keywords": "烤鸭", "category": "meal"})),
        _resp(_tc("c4", "search_poi", {"keywords": "希尔顿", "category": "hotel"})),
        # 第 5 轮：全部命中后提交
        _resp(_tc("c5", "submit_trip_plan",
                  _submit_args("颐和园-候选", "烤鸭-候选", "希尔顿-候选"))),
    ]
    llm = FakeLLM(script)
    amap = FakeAmap(fail_search_times=1)
    backend = ToolLoopBackend(llm_client=llm, amap_client=amap, max_turns=6, timeout=10)

    plan = backend.plan("北京", 1, [])
    r = backend.last_result

    assert not r.degraded, r.reason
    assert r.trace[0].get("bad_json") is True
    # 第 2 轮工具抛异常被回填为可读错误（不影响最终收敛）
    assert any(t["tool"] == "submit_trip_plan" for t in r.trace)
    assert plan.attractions[0].name == "颐和园-候选"
    # 确认错误确实回填进了 messages
    assert any("模拟高德超时" in json.dumps(c["messages"], ensure_ascii=False, default=str)
               for c in llm.chat.completions.calls)
    print("TEST_3_OK 坏JSON与工具异常兜底")


if __name__ == "__main__":
    test_model_decides_multiple_rounds_then_submits()
    test_max_turns_degrades_to_mock()
    test_bad_json_and_tool_error_recover()
    print("ALL_TOOL_LOOP_TESTS_OK")
