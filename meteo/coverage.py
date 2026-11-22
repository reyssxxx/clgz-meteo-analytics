"""Правила полноты данных из задания (Центрально-Лесной заповедник).

Сутки:
  • частые сроки (шаг < 30 мин, АМЕ-60 — 15 мин): нужно ≥ 75 % сроков, для АМЕ-60 — 72 из 96;
    считаются уникальные записанные сроки;
  • шаг 30–60 мин (Сокол М1, смешанный шаг): нужны наблюдения ≥ 18 разных часов из 24 —
    несколько записей в одном часу считаются за один час;
  • редкие сроки (шаг > 60 мин): снова ≥ 75 % сроков.
  Полнота проверяется по каждому параметру отдельно: учитываются только его валидные значения
  (не отмеченные флагом). Так, давление Кр1 за 2024 год даёт 334 полных дня и 11 месяцев.
Месяц: учитывается, если неполных дней не больше 10 и нет 5 и более неполных дней подряд.
Суточное среднее — по всем валидным значениям за сутки (несколько записей в одном часу входят все).
Сезон — если валидны все его 3 месяца; год — если валидны все 12.
"""
from __future__ import annotations

import math
from dataclasses import dataclass

import numpy as np
import pandas as pd

from .schema import STATION_PREFIXES

MAX_INVALID_DAYS = 10
MAX_INVALID_RUN = 4          # 5 подряд — уже нельзя


@dataclass(frozen=True)
class DayRule:
    mode: str                # "slots" — уникальные сроки, "hours" — уникальные часы
    need: int                # сколько нужно за сутки
    of: int                  # из скольких возможных

    def describe(self) -> str:
        return (f"не менее {self.need} из {self.of} часов" if self.mode == "hours"
                else f"не менее {self.need} из {self.of} сроков")


def infer_step(index: pd.DatetimeIndex) -> pd.Timedelta:
    idx = index.unique().sort_values()
    if len(idx) < 2:
        return pd.Timedelta("1D")
    return pd.Series(idx).diff().median()


def day_rule(index: pd.DatetimeIndex, station: str | None = None) -> DayRule:
    if station == STATION_PREFIXES["ame60"]:
        return DayRule("slots", 72, 96)
    if station in (STATION_PREFIXES["kr1"], STATION_PREFIXES["kr2"]):
        return DayRule("hours", 18, 24)
    step = infer_step(index)
    if pd.Timedelta("30min") <= step <= pd.Timedelta("60min"):
        return DayRule("hours", 18, 24)
    n = max(1, round(pd.Timedelta("1D") / step))
    return DayRule("slots", math.ceil(0.75 * n), n)


def day_counts(index: pd.DatetimeIndex, rule: DayRule) -> pd.Series:
    """Сколько уникальных сроков (или часов) есть в каждых сутках."""
    if len(index) == 0:
        return pd.Series(dtype=int)
    keys = index.floor("h") if rule.mode == "hours" else index
    s = pd.Series(1, index=pd.DatetimeIndex(keys.unique()))
    out = s.groupby(s.index.normalize()).size()
    out.index.name = "time"
    return out


def valid_days(index: pd.DatetimeIndex, rule: DayRule | None = None) -> pd.Series:
    """Булев ряд по календарным дням: сутки полные или нет (дни без данных — неполные)."""
    rule = rule or day_rule(index)
    c = day_counts(index, rule)
    if c.empty:
        return pd.Series(dtype=bool)
    cal = pd.date_range(c.index.min(), c.index.max(), freq="D")
    return (c.reindex(cal, fill_value=0) >= rule.need)


def _max_run(mask: np.ndarray) -> int:
    best = cur = 0
    for v in mask:
        cur = cur + 1 if v else 0
        best = max(best, cur)
    return best


def month_valid(day_ok: pd.Series) -> pd.Series:
    """По булеву ряду полных суток → валиден ли каждый месяц. Дни вне ряда считаются неполными."""
    if day_ok.empty:
        return pd.Series(dtype=bool)
    start = day_ok.index.min().to_period("M").to_timestamp()
    end = day_ok.index.max().to_period("M").to_timestamp(how="end").normalize()
    full = day_ok.reindex(pd.date_range(start, end, freq="D"), fill_value=False)
    res = {}
    for m, g in full.groupby(full.index.to_period("M")):
        bad = ~g.to_numpy()
        res[m.to_timestamp()] = bool(bad.sum() <= MAX_INVALID_DAYS and _max_run(bad) <= MAX_INVALID_RUN)
    return pd.Series(res, dtype=bool)


def monthly_valid_from_daily(daily_values: pd.Series) -> pd.Series:
    """То же по суточному ряду значений: NaN = неполные сутки."""
    return month_valid(daily_values.notna())


def coverage_table(df: pd.DataFrame) -> pd.DataFrame:
    """Покрытие станции по месяцам, как в coverage_by_month.csv: число полных дней и валидность месяца.
    Считается по записанным срокам станции (без конфликтных строк), независимо от параметров."""
    idx = df.index
    names = df["station"].unique() if "station" in df else []
    rule = day_rule(idx, names[0] if len(names) == 1 else None)
    ok = valid_days(idx, rule)
    if ok.empty:
        return pd.DataFrame(columns=["year", "month", "days_in_month", "valid_days", "month_valid"])
    mv = month_valid(ok)
    start, end = ok.index.min(), ok.index.max()
    years = range(start.year, end.year + 1)
    full = ok.reindex(pd.date_range(f"{years[0]}-01-01", f"{years[-1]}-12-31", freq="D"), fill_value=False)
    rows = []
    for m, g in full.groupby(full.index.to_period("M")):
        ts = m.to_timestamp()
        rows.append({"year": ts.year, "month": ts.month, "days_in_month": len(g), "valid_days": int(g.sum()),
                     "month_valid": bool(mv.get(ts, False))})
    return pd.DataFrame(rows)
