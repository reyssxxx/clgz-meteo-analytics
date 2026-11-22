"""Скачивает открытый архив Open-Meteo по координатам Центрально-Лесного заповедника.

Только для разработки, пока нет настоящих данных кейса. Файлы намеренно сохраняются
в «неудобных» форматах, похожих на российские выгрузки, чтобы проверить загрузчик:
- data/sample_daily.csv   — суточные, ',' и точка, UTF-8
- data/sample_hourly.csv  — срочные, ';' и десятичная запятая, cp1251, русские заголовки
"""
from pathlib import Path

import time

import pandas as pd
import requests

LAT, LON = 56.48, 32.93  # пос. Фёдоровское, Тверская обл.
URL = "https://archive-api.open-meteo.com/v1/archive"
OUT = Path(__file__).resolve().parent.parent / "tests" / "data"


def fetch(kind: str, variables: list[str], start: str, end: str) -> pd.DataFrame:
    for attempt in range(6):
        r = requests.get(
            URL,
            params={
                "latitude": LAT,
                "longitude": LON,
                "start_date": start,
                "end_date": end,
                kind: ",".join(variables),
                "timezone": "Europe/Moscow",
                "wind_speed_unit": "ms",
            },
            timeout=120,
        )
        if r.status_code != 429:
            break
        print(f"429, жду 65 с (попытка {attempt + 1})")
        time.sleep(65)
    r.raise_for_status()
    return pd.DataFrame(r.json()[kind])


NEIGHBOURS = {"Нелидово": (56.22, 32.78), "Андреаполь": (56.65, 32.27)}


def _save_stations() -> None:
    """Многостанционный демо-набор: заповедник + две соседние точки, одна таблица с колонкой станции."""
    vars_ = ["temperature_2m_mean", "temperature_2m_max", "temperature_2m_min", "precipitation_sum",
             "wind_speed_10m_max", "wind_direction_10m_dominant", "relative_humidity_2m_mean"]
    base = pd.read_csv(OUT / "sample_daily.csv")
    parts = [base[["time"] + [v for v in vars_ if v in base]].assign(station="Фёдоровское")]
    global LAT, LON
    lat0, lon0 = LAT, LON
    for name, (lat, lon) in NEIGHBOURS.items():
        LAT, LON = lat, lon
        parts.append(fetch("daily", vars_, "1990-01-01", "2025-12-31").assign(station=name))
        print(name, "ok")
        time.sleep(5)
    LAT, LON = lat0, lon0
    df = pd.concat(parts)[["station", "time"] + vars_]
    df.to_csv(OUT / "sample_stations.csv", index=False)
    print("stations", df.shape)


def main() -> None:
    OUT.mkdir(exist_ok=True)
    if not (OUT / "sample_stations.csv").exists():
        _save_stations()

    if (OUT / "sample_daily.csv").exists():
        print("daily: уже есть")
    else:
        _save_daily()
    if not (OUT / "sample_hourly.csv").exists():
        _save_hourly()


def _save_daily() -> None:
    daily = fetch(
        "daily",
        [
            "temperature_2m_mean", "temperature_2m_max", "temperature_2m_min",
            "precipitation_sum", "snowfall_sum", "wind_speed_10m_max",
            "relative_humidity_2m_mean", "pressure_msl_mean",
        ],
        "1990-01-01", "2025-12-31",
    )
    daily.to_csv(OUT / "sample_daily.csv", index=False)
    print("daily", daily.shape)


def _save_hourly() -> None:
    hourly = fetch(
        "hourly",
        [
            "temperature_2m", "relative_humidity_2m", "precipitation",
            "pressure_msl", "wind_speed_10m", "wind_direction_10m", "snow_depth",
        ],
        "2023-01-01", "2025-12-31",
    )
    hourly["time"] = pd.to_datetime(hourly["time"]).dt.strftime("%d.%m.%Y %H:%M")
    hourly = hourly.rename(columns={
        "time": "Дата и время",
        "temperature_2m": "Температура воздуха, °C",
        "relative_humidity_2m": "Отн. влажность, %",
        "precipitation": "Осадки, мм",
        "pressure_msl": "Давление, гПа",
        "wind_speed_10m": "Скорость ветра, м/с",
        "wind_direction_10m": "Направление ветра, °",
        "snow_depth": "Высота снега, м",
    })
    hourly.to_csv(OUT / "sample_hourly.csv", index=False, sep=";", decimal=",", encoding="cp1251")
    print("hourly", hourly.shape)


if __name__ == "__main__":
    main()
