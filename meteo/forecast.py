"""Краткосрочный прогноз из внешнего сервиса (Open-Meteo, без ключа) для района заповедника.

Собственной модели прогноза нет — по заданию она не нужна. Ответ кэшируется на час.
"""
from __future__ import annotations

import time

import requests

# район урочища «Красное», Центрально-Лесной заповедник (Тверская обл.)
LAT, LON = 56.48, 32.93
URL = "https://api.open-meteo.com/v1/forecast"
TTL = 3600
_cache: dict = {}


def get_forecast(days: int = 7) -> dict:
    now = time.time()
    if _cache.get("t", 0) + TTL > now and _cache.get("days") == days:
        return _cache["data"]
    r = requests.get(URL, params={
        "latitude": LAT, "longitude": LON, "timezone": "Europe/Moscow", "forecast_days": days,
        "wind_speed_unit": "ms",
        "hourly": "temperature_2m,relative_humidity_2m,wind_speed_10m,wind_gusts_10m,wind_direction_10m,surface_pressure",
        "daily": "temperature_2m_max,temperature_2m_min,wind_speed_10m_max,wind_gusts_10m_max,precipitation_sum",
    }, timeout=15)
    r.raise_for_status()
    j = r.json()
    data = {"lat": LAT, "lon": LON, "source": "Open-Meteo (open-meteo.com)", "fetched": int(now),
            "hourly": j["hourly"], "daily": j["daily"]}
    _cache.update(t=now, days=days, data=data)
    return data
