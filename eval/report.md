# 阶段 2 离线评测报告

> 生成时间：2026-11-17 18:14:35

## 评测边界（重要）

本评测零 Key 运行，用 FakeLLM 按 case 脚本模拟模型输出。因此度量的是
**Agent 编排层在各类输入与故障下的正确性**（schema 兜底 / 幻觉拦截 / 预算合规 / 轮次控制），
**不是真实 LLM 的准确率**。真实模型质量需另接 online 评测（配 Key），二者互补。

## 核心指标

| 指标 | 数值 |
|---|---|
| schema 通过率 | 100.0% |
| 幻觉拦截率 | 100.0% |
| 预算合规率 | 100.0% |
| 降级率 | 6.7% |
| 平均轮次 | 4.5 |
| 断言通过 | 30/30 |

## 与基线对比

| 指标 | 基线 | 当前 | 变化 |
|---|---|---|---|
| schema 通过率 | 100.0% | 100.0% | +0.0000 |
| 幻觉拦截率 | 100.0% | 100.0% | +0.0000 |
| 预算合规率 | 100.0% | 100.0% | +0.0000 |
| 降级率 | 6.7% | 6.7% | +0.0000 |
| 平均轮次 | 4.5 | 4.5 | +0.00 |

## 逐 case 明细

| case | 城市 | 行为 | 轮次 | 降级 | 断言 |
|---|---|---|---|---|---|
| C01 | 北京 | submit_valid | 4 | 否 | PASS |
| C02 | 上海 | submit_valid | 4 | 否 | PASS |
| C03 | 成都 | submit_valid | 4 | 否 | PASS |
| C04 | 杭州 | submit_valid | 4 | 否 | PASS |
| C05 | 西安 | submit_valid | 4 | 否 | PASS |
| C06 | 广州 | submit_valid | 4 | 否 | PASS |
| C07 | 重庆 | submit_valid | 4 | 否 | PASS |
| C08 | 三亚 | submit_valid | 4 | 否 | PASS |
| C09 | 南京 | submit_valid | 4 | 否 | PASS |
| C10 | 厦门 | submit_valid | 4 | 否 | PASS |
| C11 | 大连 | submit_valid | 4 | 否 | PASS |
| C12 | 北京 | submit_valid | 4 | 否 | PASS |
| C13 | 上海 | submit_valid | 4 | 否 | PASS |
| C14 | 成都 | submit_valid | 4 | 否 | PASS |
| C15 | 杭州 | submit_valid | 4 | 否 | PASS |
| C16 | 拉萨 | submit_valid | 4 | 否 | PASS |
| C17 | 大理 | submit_valid | 4 | 否 | PASS |
| C18 | 哈尔滨 | submit_valid | 4 | 否 | PASS |
| C19 | 青岛 | submit_valid | 4 | 否 | PASS |
| C20 | 苏州 | submit_valid | 4 | 否 | PASS |
| C21 | 北京 | submit_hallucinated | 5 | 否 | PASS |
| C22 | 上海 | submit_hallucinated | 5 | 否 | PASS |
| C23 | 成都 | submit_hallucinated | 5 | 否 | PASS |
| C24 | 杭州 | submit_bad_schema | 5 | 否 | PASS |
| C25 | 西安 | submit_bad_schema | 5 | 否 | PASS |
| C26 | 南京 | no_submit | 8 | 是 | PASS |
| C27 | 厦门 | no_submit | 8 | 是 | PASS |
| C28 | 广州 | bad_json_then_ok | 5 | 否 | PASS |
| C29 | 重庆 | bad_json_then_ok | 5 | 否 | PASS |
| C30 | 大理 | submit_valid | 4 | 否 | PASS |
