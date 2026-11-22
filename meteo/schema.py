"""Единая внутренняя схема метеоданных.

Всё, что ниже загрузчика, работает только с этими именами. Данные кейса приводятся
к ним через сопоставление колонок в loader.py; когда придут реальные данные,
меняется только сопоставление (и, при необходимости, этот список).

Канонический DataFrame: DatetimeIndex `time` (наивное местное время), колонки — имена
переменных из VARIABLES, плюс необязательная колонка `station`.
"""
from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class Variable:
    name: str
    label: str
    unit: str
    # как сворачивать срочные (часовые) значения в сутки
    daily_agg: str
    # как сворачивать суточные значения в месяц/год
    period_agg: str
    # физически допустимый диапазон (для контроля качества)
    limits: tuple[float, float]
    # максимальный правдоподобный скачок между соседними сроками (за 1 час)
    max_step_per_hour: float | None = None
    # ключевые слова для автосопоставления колонок (регулярные выражения, нижний регистр)
    patterns: tuple[str, ...] = field(default_factory=tuple)


VARIABLES: dict[str, Variable] = {v.name: v for v in [
    Variable("t_air", "Температура воздуха", "°C", "mean", "mean", (-60, 50), 8,
             (r"^t$", r"^temp", r"темп.*возд", r"^температура(?!.*(макс|мин|max|min|почв))",
              r"temperature(?!.*(max|min))", r"t_?air", r"t2m")),
    Variable("t_max", "Максимальная температура", "°C", "max", "mean", (-60, 50), None,
             (r"(темп|temp|t_?).*(макс|max)", r"^tmax", r"(макс|max).*(темп|temp)")),
    Variable("t_min", "Минимальная температура", "°C", "min", "mean", (-60, 50), None,
             (r"(темп|temp|t_?).*(мин|min)", r"^tmin", r"(мин|min).*(темп|temp)")),
    Variable("precip", "Осадки", "мм", "sum", "sum", (0, 300), None,
             (r"осад", r"precip", r"^rr", r"rain", r"дожд")),
    Variable("rh", "Относительная влажность", "%", "mean", "mean", (0, 100), 40,
             (r"влажн", r"humid", r"^rh", r"^u$")),
    Variable("pressure", "Атмосферное давление", "гПа", "mean", "mean", (870, 1085), 6,
             (r"давл", r"press", r"^p$", r"^slp", r"msl")),
    Variable("wind_speed", "Скорость ветра", "м/с", "mean", "mean", (0, 75), None,
             (r"(скор|speed).*(ветр|wind)(?!.*(max|макс|порыв|gust))",
              r"(ветр|wind).*(скор|speed)(?!.*(max|макс|порыв|gust))", r"^ff$", r"^ws")),
    Variable("wind_gust", "Порывы ветра", "м/с", "mean", "mean", (0, 100), None,
             (r"порыв", r"gust", r"(ветр|wind).*(макс|max)", r"^fx")),
    Variable("wind_dir", "Направление ветра", "°", "circmean", "circmean", (0, 360), None,
             (r"направ", r"direction", r"^dd$", r"^wd", r"wind_?dir")),
    Variable("snow_depth", "Высота снежного покрова", "см", "mean", "mean", (0, 500), None,
             (r"(высот|depth).*(снег|snow)", r"(снег|snow).*(высот|depth)", r"^sd$")),
    Variable("snowfall", "Снегопад", "см", "sum", "sum", (0, 200), None,
             (r"снегопад", r"snowfall")),
    # почва на глубинах (АМЕ-60) — раньше общей «температуры почвы», чтобы глубина распознавалась первой
    Variable("soil_05", "Температура почвы, 5 см", "°C", "mean", "mean", (-40, 45), None,
             (r"soil_?0?5\b", r"почв.*\b5\b", r"soil.*\b5\s*cm")),
    Variable("soil_10", "Температура почвы, 10 см", "°C", "mean", "mean", (-40, 45), None,
             (r"soil_?10\b", r"почв.*\b10\b", r"soil.*\b10\s*cm")),
    Variable("soil_20", "Температура почвы, 20 см", "°C", "mean", "mean", (-40, 45), None,
             (r"soil_?20\b", r"почв.*\b20\b", r"soil.*\b20\s*cm")),
    Variable("t_soil", "Температура почвы", "°C", "mean", "mean", (-60, 70), None,
             (r"почв", r"soil")),
    Variable("radiation", "Солнечная радиация", "Вт/м²", "mean", "mean", (0, 1500), None,
             (r"радиац", r"radiat", r"solar")),
]}

TIME_PATTERNS = (r"дата", r"врем", r"date", r"time", r"срок", r"timestamp", r"^dt$")
# колонка флагов качества: «параметр:причина» через «;», dup_conflict — строка исключается целиком
QC_PATTERNS = (r"^qc", r"qc_?flag", r"флаг", r"^flags?$", r"quality")

# Станции заповедника: префикс файла → название. Файлы вида ame60_1993.csv, kr1_2024.csv
STATION_PREFIXES = {
    "ame60": "АМЕ-60",
    "kr1": "Кр1 (поляна)",
    "kr2": "Кр2 (поле)",
}


def label(name: str) -> str:
    v = VARIABLES.get(name)
    return f"{v.label}, {v.unit}" if v else name
