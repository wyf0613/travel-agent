"""阶段 1 一键验证脚本：不靠口头说明，用可复现输出自证。

用法（项目根目录执行）：
    python tests/verify.py

特点：
  - 只读运行，不改任何业务代码
  - [1] 跑全部单元测试（含旧测试回归）
  - [2] 打印工具循环的真实 trace —— 每轮模型选了哪个工具、传了什么参数
  - [3] 证伪：MAX_TURNS=1 且模型死不提交 -> 必须降级（证明终止条件不是摆设）
  - [4] 证伪：submit 引用没搜过的 POI -> 必须被拦截（证明防幻觉生效）
  - [5] 三种模式切换正确（real 真的接到了工具循环）

只要有一条 FAIL，最后一行就不会是 ALL_VERIFICATIONS_PASSED。
"""
import json
import os
import subprocess
import sys

_BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(_BASE, "backend"))
sys.path.insert(0, os.path.join(_BASE, "tests"))

from llm_backends import get_backend
from test_tool_loop import FakeAmap, FakeLLM, _resp, _submit_args, _tc
from tool_loop import ToolLoopBackend


def section(n, title):
    print(f"\n[{n}] {title}")
    print("-" * 64)


def check(cond, label):
    print(f"  {'PASS' if cond else 'FAIL'}  {label}")
    return bool(cond)


def main():
    results = []

    # ---------- [1] 单元测试 ----------
    section(1, "单元测试（含旧测试回归）")
    proc = subprocess.run(
        [sys.executable, "-m", "pytest", os.path.join(_BASE, "tests"), "-q"],
        cwd=_BASE, capture_output=True, text=True,
    )
    out = (proc.stdout or proc.stderr).strip()
    print("\n".join("  " + line for line in out.splitlines()[-6:]))
    results.append(check(proc.returncode == 0, "全部测试通过（pytest 退出码 0）"))

    # ---------- [2] 真实 trace ----------
    section(2, "工具循环真实 trace：模型自主决策的证据")
    script = [
        _resp(_tc("c1", "search_poi", {"keywords": "故宫", "category": "attraction"})),
        _resp(_tc("c2", "geocode", {"address": "北京"})),
        _resp(_tc("c3", "get_weather", {"adcode": "110000"})),
        _resp(_tc("c4", "search_poi", {"keywords": "烤鸭", "category": "meal"})),
        _resp(_tc("c5", "search_poi", {"keywords": "希尔顿", "category": "hotel"})),
        _resp(_tc("c6", "submit_trip_plan",
                  _submit_args("故宫-候选", "烤鸭-候选", "希尔顿-候选"))),
    ]
    llm = FakeLLM(script)
    backend = ToolLoopBackend(llm_client=llm, amap_client=FakeAmap(), max_turns=6, timeout=10)
    plan = backend.plan("北京", 1, ["历史"])
    r = backend.last_result
    for i, t in enumerate(r.trace, 1):
        print(f"  turn {i}: {t['tool']:<18} args={json.dumps(t['args'], ensure_ascii=False)[:70]}")
    print(f"  => {r.turns} 轮收敛, degraded={r.degraded}, reason={r.reason}")
    print(f"  => 生成 {len(plan.attractions)} 景点 / {len(plan.meals)} 餐饮 / "
          f"天气来源={plan.days_plan[0].weather.source if plan.days_plan[0].weather else 'None'}")
    results.append(check(
        [t["tool"] for t in r.trace] ==
        ["search_poi", "geocode", "get_weather", "search_poi", "search_poi", "submit_trip_plan"]
        and not r.degraded,
        "工具顺序完全由脚本(即模型决策)决定，且正常提交",
    ))
    results.append(check(all(c["tool_choice"] == "auto" for c in llm.chat.completions.calls),
                         "每次请求 tool_choice=auto（决策权交给模型，非强绑）"))

    # ---------- [3] 证伪：终止条件 ----------
    section(3, "证伪测试 A：终止条件是否真的生效")
    never_submit = FakeLLM([_resp(_tc("c1", "search_poi",
                                      {"keywords": "故宫", "category": "attraction"}))])
    b2 = ToolLoopBackend(llm_client=never_submit, amap_client=FakeAmap(),
                         max_turns=1, timeout=10)
    p2 = b2.plan("北京", 1, [])
    r2 = b2.last_result
    print(f"  模型从不提交 + 限制 1 轮 => turns={r2.turns}, degraded={r2.degraded}, "
          f"reason={r2.reason}")
    results.append(check(r2.degraded and r2.reason == "max_turns" and p2.destination == "北京",
                         "触顶后降级，且仍返回合法计划（不会卡死/空返回）"))

    # ---------- [4] 证伪：防幻觉 ----------
    section(4, "证伪测试 B：防幻觉候选校验是否真的生效")
    ctx = ToolLoopBackend._Ctx()
    ctx.candidates["attraction"] = [{"name": "故宫博物院"}]
    bad = ToolLoopBackend._unresolved_refs(
        {"attractions": [{"name": "虚构景点999"}]}, ctx,
    )
    print(f"  候选池=['故宫博物院']，submit 引用='虚构景点999' => 拦截结果: {bad}")
    results.append(check(bool(bad) and "虚构景点999" in bad[0],
                         "没搜过的 POI 被识别为未命中（防幻觉拦截生效）"))

    # ---------- [5] 模式切换 ----------
    section(5, "模式切换：real 是否真接到工具循环")
    # 先备好假 key，否则 legacy/real 的 __init__ 会拒绝构造
    os.environ["LLM_API_KEY"] = "fake-key-for-verify"
    os.environ["AMAP_API_KEY"] = "fake-key-for-verify"
    os.environ["AGENT_MODE"] = "mock"
    m1 = type(get_backend()).__name__
    os.environ["AGENT_MODE"] = "legacy"
    m2 = type(get_backend()).__name__
    os.environ["AGENT_MODE"] = "real"
    m3 = type(get_backend()).__name__
    print(f"  mock -> {m1} | legacy -> {m2} | real -> {m3}")
    results.append(check(
        (m1, m2, m3) == ("MockLLMBackend", "RealLLMBackend", "ToolLoopBackend"),
        "三种模式路由正确，real 走的是新工具循环",
    ))

    # ---------- 结论 ----------
    print("\n" + "=" * 64)
    if all(results):
        print("ALL_VERIFICATIONS_PASSED")
    else:
        print("SOME_VERIFICATIONS_FAILED —— 检查上面的 FAIL 项")
        sys.exit(1)


if __name__ == "__main__":
    main()
