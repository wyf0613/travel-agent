"""高德地图 Web 服务 API 封装（restapi.amap.com）。

真实模式下负责获取真实数据：
- POI 文本搜索：景点 / 餐饮 / 酒店（含真实经纬度、地址、人均消费）
- 地理编码：地址 -> 经纬度
- 天气：城市 adcode -> 多日预报

所有方法在无网络 / 无 key 时抛出 AmapError，由上层 LLM 后端捕获并降级。
高德是具有测绘资质的正规地图服务商，合规可用。
"""
from __future__ import annotations

import os

from errors import ConfigError, UpstreamError


class AmapError(UpstreamError):
    """高德 API 调用失败时抛出（归类为上游错误 502）。

    继承 UpstreamError 使得：既保留旧代码 `except AmapError` 的兼容性，
    又能被 FastAPI 全局异常处理器按 502 统一处理。
    """


class AmapClient:
    BASE = "https://restapi.amap.com"

    def __init__(self, api_key: str | None = None):
        self.api_key = api_key or os.getenv("AMAP_API_KEY")
        if not self.api_key:
            # 缺 key 是配置问题（500），不是上游调用失败（502）
            raise ConfigError(
                "缺少 AMAP_API_KEY（高德服务端 key）。请在 .env 配置后使用真实模式，"
                "申请地址 https://lbs.amap.com/ （创建「Web 服务」类型 key）。"
            )

    # ---------- 内部请求 ----------
    def _get(self, path: str, params: dict) -> dict:
        try:
            import requests
        except ImportError:
            raise AmapError("真实模式需要 requests 库，请先 pip install requests")
        params = {k: v for k, v in params.items() if v is not None}
        params["key"] = self.api_key
        try:
            resp = requests.get(self.BASE + path, params=params, timeout=15)
            data = resp.json()
        except requests.Timeout as e:
            raise AmapError(f"高德请求超时：{e}", status_code=504, code="UPSTREAM_TIMEOUT") from e
        except Exception as e:  # 其他网络错误
            raise AmapError(f"高德请求失败：{e}") from e
        if data.get("status") != "1":
            raise AmapError(f"高德 API 错误：{data.get('info')} (code={data.get('infocode')})")
        return data

    # ---------- 对外能力 ----------
    def search_poi(
        self, keywords: str, city: str | None = None, poi_type: str | None = None, page_size: int = 15
    ) -> list[dict]:
        """文本搜索 POI，返回标准化列表。

        元素: {name, address, lng, lat, adcode, cost, type}
        cost 为人均消费（餐饮/酒店才有，景点常为 None）。
        """
        params = {
            "keywords": keywords,
            "city": city,
            "citylimit": "true" if city else "false",
            "offset": page_size,
            "extensions": "all",
        }
        if poi_type:
            params["types"] = poi_type
        data = self._get("/v3/place/text", params)
        out = []
        for p in data.get("pois", []):
            loc = p.get("location", "") or ""
            lng, lat = (loc.split(",") + ["", ""])[:2] if loc else ("", "")
            biz = p.get("biz_ext", {}) or {}
            cost = biz.get("cost") or ""
            out.append(
                {
                    "name": p.get("name", ""),
                    "address": p.get("address", ""),
                    "lng": float(lng) if lng else None,
                    "lat": float(lat) if lat else None,
                    "adcode": p.get("adcode", ""),
                    "cost": float(cost) if cost not in ("", "-", None) else None,
                    "type": p.get("type", ""),
                }
            )
        return out

    def geocode(self, address: str, city: str | None = None) -> dict | None:
        data = self._get("/v3/geocode/geo", {"address": address, "city": city})
        geos = data.get("geocodes", [])
        if not geos:
            return None
        loc = geos[0].get("location", "") or ""
        lng, lat = (loc.split(",") + [None, None])[:2] if loc else (None, None)
        return {
            "lng": float(lng) if lng else None,
            "lat": float(lat) if lat else None,
            "adcode": geos[0].get("adcode", ""),
        }

    def weather(self, adcode: str | None) -> dict:
        if not adcode:
            return {}
        try:
            return self._get(
                "/v3/weather/weatherInfo", {"city": adcode, "extensions": "all"}
            ).get("forecasts", [{}])[0]
        except AmapError:
            return {}
