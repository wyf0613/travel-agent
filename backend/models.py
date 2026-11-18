"""行程数据模型（Pydantic）。

对应教程 13.2 的「自底向上建模」：Location -> Attraction/Meal/Hotel/Budget
-> DayPlan -> TripPlan。前端 TS interface 与这里的结构保持一致。
"""
from __future__ import annotations

from typing import List, Optional

from pydantic import BaseModel, Field


class Location(BaseModel):
    name: str
    address: str = ""
    lat: float
    lng: float


class Weather(BaseModel):
    """单日天气（mock 与高德真实数据统一走这一结构）。

    source 用于标注数据来源：mock=内置示意 / amap=高德实时 / estimated=超出预报范围降级。
    """

    date: str = ""          # 2026-09-20
    condition: str = ""     # 晴 / 多云 / 小雨 ...
    temp_min: float = 0.0
    temp_max: float = 0.0
    desc: str = ""          # 晴 18~25℃（前端直接展示）
    source: str = "mock"


class Attraction(BaseModel):
    id: str
    name: str
    location: Location
    description: str = ""
    ticket_price: float = 0.0
    visit_duration_hours: float = 2.0
    category: str = "sightseeing"  # 历史古迹/园林/博物馆/艺术/地标/胡同
    image_url: Optional[str] = None


class Meal(BaseModel):
    id: str
    name: str
    location: Location
    cuisine: str = ""
    price_per_person: float = 0.0
    meal_type: str = "lunch"  # breakfast/lunch/dinner


class Hotel(BaseModel):
    id: str
    name: str
    location: Location
    price_per_night: float = 0.0
    star: int = 3


class Budget(BaseModel):
    total: float = 0.0
    attractions: float = 0.0
    hotel: float = 0.0
    meals: float = 0.0
    transport: float = 0.0
    currency: str = "CNY"


class DayPlan(BaseModel):
    day: int
    date: str = ""
    weather: Optional[Weather] = None
    attraction_ids: List[str] = Field(default_factory=list)
    meal_ids: List[str] = Field(default_factory=list)
    summary: str = ""


class TripPlan(BaseModel):
    destination: str
    days: int
    preferences: List[str] = Field(default_factory=list)
    attractions: List[Attraction] = Field(default_factory=list)
    meals: List[Meal] = Field(default_factory=list)
    hotel: Optional[Hotel] = None
    budget: Budget
    days_plan: List[DayPlan] = Field(default_factory=list)
    overview: str = ""
