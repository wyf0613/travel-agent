"""Key 自检脚本：配完 .env 后跑一次，逐项确认 LLM 与高德 Key 是否可用。

用法（必须在 backend/ 目录下运行，否则读不到 .env）：
    .venv/Scripts/python.exe check_keys.py
"""
from __future__ import annotations

import os
import sys

from dotenv import load_dotenv

# 明确指定 .env 路径，避免"必须在某个目录下运行"的隐性依赖
HERE = os.path.dirname(os.path.abspath(__file__))
load_dotenv(os.path.join(HERE, ".env"))

PLACEHOLDER_HINTS = ("your_", "xxx", "替换", "sk-xxx")


def looks_placeholder(v: str | None) -> bool:
    if not v:
        return False
    low = v.strip().lower()
    return any(h in low for h in PLACEHOLDER_HINTS)


def ok(msg: str) -> None:
    print(f"  [OK]   {msg}")


def bad(msg: str) -> None:
    print(f"  [FAIL] {msg}")


def info(msg: str) -> None:
    print(f"  [--]   {msg}")


def main() -> int:
    failed = False

    print("=" * 60)
    print("1. 读取 .env")
    print("=" * 60)
    env_path = os.path.join(HERE, ".env")
    if not os.path.exists(env_path):
        bad(f"找不到 {env_path}")
        return 1
    info(f"已加载 {env_path}")

    mode = os.getenv("AGENT_MODE", "mock").lower()
    info(f"AGENT_MODE = {mode}")

    # ---------- LLM ----------
    print()
    print("=" * 60)
    print("2. LLM Key（OpenAI 兼容接口）")
    print("=" * 60)
    llm_key = os.getenv("LLM_API_KEY", "")
    base_url = os.getenv("LLM_BASE_URL", "https://api.openai.com/v1")
    model = os.getenv("LLM_MODEL_ID", "gpt-4o-mini")
    info(f"LLM_BASE_URL = {base_url}")
    info(f"LLM_MODEL_ID = {model}")

    if not llm_key:
        bad("LLM_API_KEY 为空")
        failed = True
    elif looks_placeholder(llm_key):
        bad(f"LLM_API_KEY 还是占位符（{llm_key}），请换成真实 Key")
        failed = True
    else:
        ok(f"LLM_API_KEY 已填写（{llm_key[:6]}...{llm_key[-4:]}，长度 {len(llm_key)}）")
        try:
            from openai import OpenAI

            client = OpenAI(api_key=llm_key, base_url=base_url, timeout=30)
            resp = client.chat.completions.create(
                model=model,
                messages=[{"role": "user", "content": "回复两个字：正常"}],
                max_tokens=8,
            )
            reply = (resp.choices[0].message.content or "").strip()
            ok(f"LLM 调用成功，模型返回：{reply!r}")
        except Exception as e:
            bad(f"LLM 调用失败：{type(e).__name__}: {e}")
            failed = True

    # ---------- 高德 ----------
    print()
    print("=" * 60)
    print("3. 高德 Web 服务 Key（真实模式下同样必需）")
    print("=" * 60)
    amap_key = os.getenv("AMAP_API_KEY", "")
    if not amap_key:
        bad("AMAP_API_KEY 为空")
        failed = True
    elif looks_placeholder(amap_key):
        bad("AMAP_API_KEY 还是占位符，请换成「Web 服务」类型的 Key")
        failed = True
    else:
        try:
            sys.path.insert(0, HERE)
            from amap import AmapClient, AmapError

            client = AmapClient(amap_key)
            geo = client.geocode("杭州")
            if not geo:
                bad("地理编码返回空，Key 可能无效或配额用尽")
                failed = True
            else:
                ok(f"高德连通，杭州坐标 = {geo['lng']}, {geo['lat']} (adcode={geo['adcode']})")
                pois = client.search_poi("西湖", "杭州", None, 5)
                if pois:
                    ok(f"POI 搜索成功，共 {len(pois)} 条，首条：{pois[0]['name']}")
                else:
                    bad("POI 搜索返回 0 条（Key 类型可能不是「Web 服务」）")
                    failed = True
        except Exception as e:
            bad(f"高德调用失败：{type(e).__name__}: {e}")
            failed = True

    # ---------- 汇总 ----------
    print()
    print("=" * 60)
    if failed:
        print("结论：还有问题，按上面 [FAIL] 逐条修。")
    elif mode != "real":
        print("结论：Key 全部可用，但 AGENT_MODE 还是 mock。")
        print("     把 .env 里 AGENT_MODE 改成 real 才会走真实链路。")
    else:
        print("结论：全部就绪，可以直接启动后端了。")
    print("=" * 60)
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
