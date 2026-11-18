"""验证真实模式候选抓取的并发正确性 + 提速效果（无需真实 Key / 网络）。

用 FakeAmap 替代真实高德客户端：每个搜索 sleep 模拟网络延迟，
断言 3 路并发后总耗时显著低于串行，且结果正确返回。

运行：
    python tests/test_concurrency.py        # 不依赖 pytest
    pytest tests/test_concurrency.py -v     # pytest 风格
"""
import os
import sys
import time

_BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(_BASE, "backend"))

from llm_backends import RealLLMBackend


class FakeAmap:
    """替代真实高德客户端：每个调用 sleep 模拟网络延迟。"""

    def __init__(self, latency: float = 0.3):
        self._latency = latency
        self.calls: list[str] = []

    def _wait(self, kind: str):
        self.calls.append(kind)
        time.sleep(self._latency)

    def search_poi(self, keywords, city=None, poi_type=None, page_size=15):
        self._wait(f"poi:{keywords}")
        return [
            {
                "name": f"{keywords}-A",
                "address": "x",
                "lng": 1.0,
                "lat": 1.0,
                "adcode": "110000",
                "cost": 50,
                "type": "x",
            }
        ]

    def geocode(self, address, city=None):
        self._wait(f"geo:{address}")
        return {"lng": 1.0, "lat": 1.0, "adcode": "110000"}

    def weather(self, adcode):
        return {"casts": [{"dayweather": "晴", "daytemp": "25", "nighttemp": "15"}]}


def test_fetch_candidates_concurrent():
    # 绕过 __init__ 的 Key 检查，直接注入 FakeAmap
    backend = RealLLMBackend.__new__(RealLLMBackend)
    fake = FakeAmap(latency=0.3)
    backend.amap = fake

    t0 = time.perf_counter()
    attr, meal, hotel, adcode = backend._fetch_candidates("北京", "热门景点")
    elapsed = time.perf_counter() - t0

    # 正确性
    assert attr and meal and hotel, "三路候选必须都返回"
    assert adcode == "110000", "adcode 必须由 geocode 正确返回"

    # 并发：3 路 POI(0.3s) + 1 geo(0.3s) 串行需 ~1.2s，并发应明显更短
    assert elapsed < 0.9, f"并发未生效，耗时 {elapsed:.2f}s"

    # 四类调用都执行过
    assert any(c.startswith("poi:") for c in fake.calls)
    assert any(c.startswith("geo:") for c in fake.calls)


if __name__ == "__main__":
    test_fetch_candidates_concurrent()
    print("CONCURRENCY_TEST_OK")
