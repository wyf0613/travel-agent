"""阶段 2：离线评测框架（零 Key / 零网络）。

度量 Agent 编排层的四项质量指标：
  1. schema 通过率      —— 最终输出能被 TripPlan 校验的比例
  2. 幻觉拦截率          —— 模型引用未搜索 POI 时，被防幻觉逻辑挡下的比例
  3. 预算合规率          —— 有 budget 约束的 case 中，total 不超限的比例
  4. 平均轮次 / 降级率   —— 工具循环效率与触顶降级比例

诚实边界（写进报告）：本评测用 FakeLLM 按 case 脚本模拟模型行为，
测的是"编排逻辑在各类输入/故障下的正确性"，不是真实 LLM 的准确率。

用法（项目根目录）：
    python eval/run_eval.py
    python eval/run_eval.py --save-baseline   # 首次运行，保存基线
    python eval/run_eval.py --compare         # 与 baseline 对比（回归检测）
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime
from types import SimpleNamespace

_HERE = os.path.dirname(os.path.abspath(__file__))
_BASE = os.path.dirname(_HERE)
sys.path.insert(0, os.path.join(_BASE, "backend"))

from tool_loop import ToolLoopBackend

CASES_PATH = os.path.join(_HERE, "cases.json")
BASELINE_PATH = os.path.join(_HERE, "baseline.json")
REPORT_PATH = os.path.join(_HERE, "report.md")


# ---------------- 测试替身 ----------------
class FakeAmap:
    """稳定的高德替身：按关键词返回带坐标的候选。"""

    def search_poi(self, keywords, city=None, poi_type=None, page_size=15):
        return [{
            "name": f"{keywords}-候选", "address": "评测地址",
            "lng": 116.39, "lat": 39.91, "adcode": "110000",
            "cost": 80, "type": "test",
        }]

    def geocode(self, address, city=None):
        return {"lng": 116.39, "lat": 39.91, "adcode": "110000"}

    def weather(self, adcode):
        return {"casts": [{"dayweather": "晴", "daytemp": "25", "nighttemp": "15"}]}


class FakeCompletions:
    def __init__(self, script):
        self.script = script
        self.i = 0

    def create(self, **kwargs):
        resp = self.script[min(self.i, len(self.script) - 1)]
        self.i += 1
        return resp


class FakeLLM:
    def __init__(self, script):
        self.chat = SimpleNamespace(completions=FakeCompletions(script))


def _tc(cid, name, args):
    payload = args if isinstance(args, str) else json.dumps(args, ensure_ascii=False)
    return SimpleNamespace(id=cid, function=SimpleNamespace(name=name, arguments=payload))


def _resp(*tcs):
    return SimpleNamespace(choices=[SimpleNamespace(
        message=SimpleNamespace(content=None, tool_calls=list(tcs))
    )])


def _submit_args(case, attr_name, meal_name, hotel_name, *, omit_field=False, bad_type=False):
    """构造一个 submit 参数；可按 case 预算设定 total。"""
    total = case.get("budget") or 560
    data = {
        "overview": f"{case['city']}{case['days']}天行程",
        "attractions": [{"id": "a1", "name": attr_name, "ticket_price": 80,
                         "visit_duration_hours": 2, "category": "历史古迹"}],
        "meals": [{"id": "m1", "name": meal_name, "price_per_person": 80,
                   "cuisine": "本地菜", "meal_type": "lunch"}],
        "hotel": {"id": "h1", "name": hotel_name, "price_per_night": 300, "star": 4},
        "budget": {"attractions": 80, "hotel": 300, "meals": 80,
                   "transport": 100, "total": total},
        "days_plan": [{"day": 1, "attraction_ids": ["a1"], "meal_ids": ["m1"],
                       "summary": "第一天"}],
    }
    if omit_field:
        del data["budget"]  # 制造 schema 缺字段
    if bad_type:
        data["days"] = "不是数字"  # 制造类型错误（后端会覆盖，但先测其它字段）
        data["attractions"][0]["ticket_price"] = "NaN"
    return data


# ---------------- 按 behavior 生成模型脚本 ----------------
def build_script(case):
    """返回 (script, expect) —— expect 描述系统应有的正确行为。"""
    city, days = case["city"], case["days"]
    kw_attr, kw_meal, kw_hotel = f"{city}景点", f"{city}美食", f"{city}酒店"
    name_attr, name_meal, name_hotel = f"{kw_attr}-候选", f"{kw_meal}-候选", f"{kw_hotel}-候选"
    behavior = case["behavior"]

    search = lambda k, cat, cid: _tc(cid, "search_poi", {"keywords": k, "category": cat})

    if behavior == "submit_valid":
        script = [
            _resp(search(kw_attr, "attraction", "c1")),
            _resp(search(kw_meal, "meal", "c2")),
            _resp(search(kw_hotel, "hotel", "c3")),
            _resp(_tc("c4", "submit_trip_plan", _submit_args(case, name_attr, name_meal, name_hotel))),
        ]
        return script, {"want_degraded": False, "want_schema_ok": True}

    if behavior == "submit_hallucinated":
        # 先查全 餐饮/酒店（正常），景点却提交一个没搜过的幻觉 POI -> 期望被拦截；
        # 再补查该景点并正常提交 -> 期望自愈收敛。
        hallucination = "虚构景点-幻觉"
        script = [
            _resp(search(kw_meal, "meal", "c1")),
            _resp(search(kw_hotel, "hotel", "c2")),
            _resp(_tc("c3", "submit_trip_plan",
                      _submit_args(case, hallucination, name_meal, name_hotel))),
            _resp(search(hallucination, "attraction", "c4")),
            _resp(_tc("c5", "submit_trip_plan",
                      _submit_args(case, f"{hallucination}-候选", name_meal, name_hotel))),
        ]
        return script, {"want_degraded": False, "want_hallucination_blocked": True,
                        "want_schema_ok": True}

    if behavior == "submit_bad_schema":
        # 第一次提交缺 budget 字段 -> 校验失败；第二次补全 -> 自愈。
        script = [
            _resp(search(kw_attr, "attraction", "c1")),
            _resp(search(kw_meal, "meal", "c2")),
            _resp(search(kw_hotel, "hotel", "c3")),
            _resp(_tc("c4", "submit_trip_plan",
                      _submit_args(case, name_attr, name_meal, name_hotel, omit_field=True))),
            _resp(_tc("c5", "submit_trip_plan",
                      _submit_args(case, name_attr, name_meal, name_hotel))),
        ]
        return script, {"want_degraded": False, "want_schema_ok": True}

    if behavior == "no_submit":
        # 只查不提交，脚本最后一条会重复 -> 必然触顶降级。
        script = [_resp(search(kw_attr, "attraction", "c1"))]
        return script, {"want_degraded": True, "want_schema_ok": True}

    if behavior == "bad_json_then_ok":
        script = [
            _resp(_tc("c1", "search_poi", "{bad json")),
            _resp(search(kw_attr, "attraction", "c2")),
            _resp(search(kw_meal, "meal", "c3")),
            _resp(search(kw_hotel, "hotel", "c4")),
            _resp(_tc("c5", "submit_trip_plan",
                      _submit_args(case, name_attr, name_meal, name_hotel))),
        ]
        return script, {"want_degraded": False, "want_schema_ok": True}

    raise ValueError(f"未知 behavior: {behavior}")


# ---------------- 单条 case 评测 ----------------
def run_case(case):
    script, expect = build_script(case)
    backend = ToolLoopBackend(
        llm_client=FakeLLM(script), amap_client=FakeAmap(),
        max_turns=8, timeout=10,
    )
    plan = backend.plan(case["city"], case["days"], case["prefs"], case.get("budget"))
    r = backend.last_result

    schema_ok = plan is not None and bool(plan.attractions) and bool(plan.budget)
    hallucination_blocked = any(
        t.get("tool") == "submit_trip_plan"
        and not t.get("ok")
        and "不在搜索结果中" in str(t.get("error", ""))
        for t in r.trace
    )
    budget = case.get("budget")
    budget_ok = (budget is None) or (plan.budget.total <= budget)

    checks = {
        "schema_ok": schema_ok == expect["want_schema_ok"],
        "degraded_as_expected": r.degraded == expect.get("want_degraded", False),
        "budget_ok": budget_ok,
    }
    if "want_hallucination_blocked" in expect:
        checks["hallucination_blocked"] = hallucination_blocked == expect["want_hallucination_blocked"]

    return {
        "id": case["id"],
        "city": case["city"],
        "days": case["days"],
        "behavior": case["behavior"],
        "budget": budget,
        "schema_ok": schema_ok,
        "degraded": r.degraded,
        "reason": r.reason,
        "turns": r.turns,
        "hallucination_blocked": hallucination_blocked,
        "budget_ok": budget_ok,
        "budget_total": plan.budget.total,
        "checks": checks,
        "passed": all(checks.values()),
    }


# ---------------- 汇总指标 ----------------
def aggregate(results):
    n = len(results)
    schema_pass = sum(1 for r in results if r["schema_ok"])
    halluc_cases = [r for r in results if r["behavior"] == "submit_hallucinated"]
    halluc_blocked = sum(1 for r in halluc_cases if r["hallucination_blocked"])
    budget_cases = [r for r in results if r["budget"] is not None]
    budget_ok = sum(1 for r in budget_cases if r["budget_ok"])
    degraded = sum(1 for r in results if r["degraded"])
    total_turns = sum(r["turns"] for r in results)
    passed = sum(1 for r in results if r["passed"])

    return {
        "total_cases": n,
        "passed_cases": passed,
        "schema_pass_rate": round(schema_pass / n, 4) if n else 0,
        "hallucination_block_rate": round(halluc_blocked / len(halluc_cases), 4) if halluc_cases else None,
        "budget_compliance_rate": round(budget_ok / len(budget_cases), 4) if budget_cases else None,
        "degrade_rate": round(degraded / n, 4) if n else 0,
        "avg_turns": round(total_turns / n, 2) if n else 0,
        "all_assertions_passed": passed == n,
    }


def pct(x):
    return "N/A" if x is None else f"{x * 100:.1f}%"


def print_table(results, metrics):
    print("\n" + "=" * 72)
    print("阶段 2 离线评测结果")
    print("=" * 72)
    print(f"{'case':<6}{'城市':<8}{'行为':<22}{'轮次':<6}{'降级':<6}{'断言':<6}")
    print("-" * 72)
    for r in results:
        print(f"{r['id']:<6}{r['city']:<8}{r['behavior']:<22}{r['turns']:<6}"
              f"{'是' if r['degraded'] else '否':<6}{'PASS' if r['passed'] else 'FAIL':<6}")
    print("-" * 72)
    print(f"  schema 通过率      : {pct(metrics['schema_pass_rate'])}")
    print(f"  幻觉拦截率          : {pct(metrics['hallucination_block_rate'])}")
    print(f"  预算合规率          : {pct(metrics['budget_compliance_rate'])}")
    print(f"  降级率              : {pct(metrics['degrade_rate'])}")
    print(f"  平均轮次            : {metrics['avg_turns']}")
    print(f"  断言通过            : {metrics['passed_cases']}/{metrics['total_cases']}")
    print("=" * 72)


def write_report(results, metrics, baseline=None):
    lines = [
        "# 阶段 2 离线评测报告",
        "",
        f"> 生成时间：{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
        "",
        "## 评测边界（重要）",
        "",
        "本评测零 Key 运行，用 FakeLLM 按 case 脚本模拟模型输出。因此度量的是",
        "**Agent 编排层在各类输入与故障下的正确性**（schema 兜底 / 幻觉拦截 / 预算合规 / 轮次控制），",
        "**不是真实 LLM 的准确率**。真实模型质量需另接 online 评测（配 Key），二者互补。",
        "",
        "## 核心指标",
        "",
        "| 指标 | 数值 |",
        "|---|---|",
        f"| schema 通过率 | {pct(metrics['schema_pass_rate'])} |",
        f"| 幻觉拦截率 | {pct(metrics['hallucination_block_rate'])} |",
        f"| 预算合规率 | {pct(metrics['budget_compliance_rate'])} |",
        f"| 降级率 | {pct(metrics['degrade_rate'])} |",
        f"| 平均轮次 | {metrics['avg_turns']} |",
        f"| 断言通过 | {metrics['passed_cases']}/{metrics['total_cases']} |",
        "",
    ]
    if baseline:
        lines += [
            "## 与基线对比",
            "",
            "| 指标 | 基线 | 当前 | 变化 |",
            "|---|---|---|---|",
        ]
        for key, label in [
            ("schema_pass_rate", "schema 通过率"),
            ("hallucination_block_rate", "幻觉拦截率"),
            ("budget_compliance_rate", "预算合规率"),
            ("degrade_rate", "降级率"),
            ("avg_turns", "平均轮次"),
        ]:
            b, c = baseline.get(key), metrics.get(key)
            if b is None or c is None:
                delta = "-"
            else:
                d = c - b
                delta = f"{d:+.4f}" if key != "avg_turns" else f"{d:+.2f}"
            lines.append(f"| {label} | {pct(b) if key != 'avg_turns' else b} | "
                         f"{pct(c) if key != 'avg_turns' else c} | {delta} |")
        lines.append("")

    lines += [
        "## 逐 case 明细",
        "",
        "| case | 城市 | 行为 | 轮次 | 降级 | 断言 |",
        "|---|---|---|---|---|---|",
    ]
    for r in results:
        lines.append(f"| {r['id']} | {r['city']} | {r['behavior']} | {r['turns']} | "
                     f"{'是' if r['degraded'] else '否'} | {'PASS' if r['passed'] else 'FAIL'} |")
    lines.append("")

    with open(REPORT_PATH, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))
    print(f"\n报告已写入: {REPORT_PATH}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--save-baseline", action="store_true", help="把当前指标存为基线")
    ap.add_argument("--compare", action="store_true", help="与已有基线对比")
    args = ap.parse_args()

    with open(CASES_PATH, encoding="utf-8") as f:
        data = json.load(f)
    cases = data["cases"]

    results = [run_case(c) for c in cases]
    metrics = aggregate(results)
    print_table(results, metrics)

    baseline = None
    if os.path.exists(BASELINE_PATH):
        with open(BASELINE_PATH, encoding="utf-8") as f:
            baseline = json.load(f)

    if args.save_baseline:
        with open(BASELINE_PATH, "w", encoding="utf-8") as f:
            json.dump(metrics, f, ensure_ascii=False, indent=2)
        print(f"基线已保存: {BASELINE_PATH}")
        baseline = metrics  # 保存基线时也一并写入报告对比

    write_report(results, metrics, baseline if args.compare or args.save_baseline else None)

    if metrics["all_assertions_passed"]:
        print("\nALL_EVALS_PASSED")
    else:
        failed = [r["id"] for r in results if not r["passed"]]
        print(f"\nSOME_EVALS_FAILED: {failed}")
        sys.exit(1)


if __name__ == "__main__":
    main()
