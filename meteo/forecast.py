"""Краткосрочный прогноз из внешнего сервиса (Open-Meteo, без ключа) для района заповедника.

Собственной модели прогноза нет — по заданию она не нужна. Ответ кэшируется на час.
"""
from __future__ import annotations

import time
from bisect import bisect_left
from datetime import datetime
from zoneinfo import ZoneInfo

import pandas as pd
import requests

# район урочища «Красное», Центрально-Лесной заповедник (Тверская обл.)
LAT, LON = 56.48, 32.93
URL = "https://api.open-meteo.com/v1/forecast"
TTL = 3600
TIMEZONE = ZoneInfo("Europe/Moscow")
HISTORY_DAYS = 14
_cache: dict = {}


def recommendations(history: dict, future: dict, snow_depth_m: float | None) -> list[dict]:
    """Оперативные подсказки на основе полей погоды, пороги служат для отбора событий."""
    dates = future["time"]
    rain = [a + b if a is not None and b is not None else None
            for a, b in zip(future["rain_sum"], future["showers_sum"])]
    signals = []

    def add(title, first, last, evidence, action):
        signals.append({"title": title, "start": dates[first], "end": dates[last],
                        "evidence": evidence, "action": action})

    # Снегопад сам по себе не характеризует запасы снега к началу оттепели.
    if snow_depth_m is not None and snow_depth_m >= .05:
        snowfall = [v for v in history["snowfall_sum"] if v is not None]
        maxima = future["temperature_2m_max"]
        means = future["temperature_mean"]
        for i in range(min(3, len(dates))):
            warm_run = i + 1 < len(dates) and all(v is not None and v >= 2 for v in means[i:i+2])
            rain_on_snow = rain[i] is not None and rain[i] >= 10 and maxima[i] is not None and maxima[i] > 0
            if warm_run or rain_on_snow:
                last = i + 1 if warm_run else i
                evidence = f"Накануне модель оценила снежный покров в {snow_depth_m * 100:.0f} см."
                if len(snowfall) == HISTORY_DAYS and any(v >= 1 for v in snowfall):
                    snowy_days = sum(v >= 1 for v in snowfall)
                    evidence += f" За 14 дней снегопад от 1 см отмечен в {snowy_days} из 14 дней."
                if warm_run:
                    evidence += " В прогнозе два дня со среднесуточной температурой от +2 °C."
                if rain_on_snow:
                    evidence += f" В день оттепели ожидается {rain[i]:.0f} мм дождя."
                add("Возможен усиленный сток при таянии снега", i, last, evidence,
                    "Проследите за уровнем воды у ручьёв и низких переправ. Для оценки паводка сверяйтесь с гидропостами и официальными предупреждениями.")
                break

    for values, threshold, above, title, unit, action in (
        (rain, 25, True, "Обильный дождь", "мм за сутки",
         "Проверьте состояние грунтовых дорог и низких участков перед выездом."),
        (future["wind_gusts_10m_max"], 15, True, "Сильные порывы ветра", "м/с",
         "Учтите ветер при планировании полевых работ на открытых участках и в лесу."),
        (future["temperature_2m_min"], 0, False, "Температура опустится ниже нуля", "°C",
         "Проверьте чувствительное к замерзанию оборудование и ёмкости с водой."),
    ):
        hits = [(i, v) for i, v in enumerate(values) if v is not None and (v >= threshold if above else v < threshold)]
        if hits:
            i, value = (max if above else min)(hits, key=lambda row: row[1])
            add(title, i, i, f"По прогнозу {value:.1f} {unit}.", action)
    return signals


def get_forecast(days: int = 7) -> dict:
    now = time.time()
    today = datetime.fromtimestamp(now, TIMEZONE).date().isoformat()
    if _cache.get("t", 0) + TTL > now and _cache.get("days") == days and _cache.get("date") == today:
        return _cache["data"]
    r = requests.get(URL, params={
        "latitude": LAT, "longitude": LON, "timezone": TIMEZONE.key, "forecast_days": days, "past_days": HISTORY_DAYS,
        "wind_speed_unit": "ms",
        "hourly": ("temperature_2m,relative_humidity_2m,wind_speed_10m,wind_gusts_10m,wind_direction_10m,"
                   "surface_pressure,snow_depth"),
        "daily": ("temperature_2m_max,temperature_2m_min,wind_speed_10m_max,wind_gusts_10m_max,"
                  "precipitation_sum,rain_sum,showers_sum,snowfall_sum"),
    }, timeout=15)
    r.raise_for_status()
    j = r.json()
    hourly = j["hourly"]
    predicted = pd.Series(hourly["temperature_2m"], index=pd.DatetimeIndex(hourly["time"]), dtype=float)
    daily_means = predicted.resample("D").mean().reindex(pd.DatetimeIndex(j["daily"]["time"]))
    j["daily"]["temperature_mean"] = daily_means.tolist()
    day_cut = bisect_left(j["daily"]["time"], today)
    hour_cut = bisect_left(hourly["time"], today)
    past = {key: values[max(0, day_cut - HISTORY_DAYS):day_cut] for key, values in j["daily"].items()}
    future = {key: values[day_cut:] for key, values in j["daily"].items()}
    yesterday = hourly["snow_depth"][max(0, hour_cut - 24):hour_cut]
    depth = next((value for value in reversed(yesterday) if value is not None), None)
    data = {"lat": LAT, "lon": LON, "source": "Open-Meteo (open-meteo.com)", "fetched": int(now),
            "hourly": {key: values[hour_cut:] for key, values in hourly.items()},
            "daily": future, "recommendations": recommendations(past, future, depth)}
    _cache.update(t=now, days=days, date=today, data=data)
    return data
