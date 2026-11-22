"""Подготовка черновика для восстановления пропусков по суточным данным."""
from __future__ import annotations

import json
import sqlite3
from contextlib import closing
from hashlib import sha256
from pathlib import Path

import pandas as pd

from . import analytics as A
from . import quality as Q
from .schema import VARIABLES


def preview(raw: pd.DataFrame, dailies: dict[str, pd.DataFrame], station: str, var: str, start, end) -> dict:
    """Покрытие источников. Наличие наблюдений ещё не подтверждает пригодность станции."""
    dates = pd.date_range(start, end, freq="D")
    target = dailies[station][var].dropna().index
    missing = pd.Series(~dates.isin(target), index=dates)
    source_var = Q.observation_var(raw, var)
    observed = raw[source_var].dropna().index.normalize() if source_var in raw else pd.DatetimeIndex([])
    incomplete = missing & dates.isin(observed)
    gaps = []
    for first, last, count in A._runs(missing, 1):
        partial = int(incomplete.loc[first:last].sum())
        gaps.append({"start": first, "end": last, "days": count,
                     "incomplete_days": partial, "days_without_usable_observations": count - partial})

    gap_dates = dates[missing.to_numpy()]
    stations = []
    for name, daily in dailies.items():
        if name == station or var not in daily:
            continue
        available = daily[var].dropna().index
        if not len(available):
            continue
        common = target.intersection(available)
        stations.append({"station": name, "gap_days_covered": len(available.intersection(gap_dates)),
                         "overlap_days": len(common),
                         "overlap_years": int(common.year.nunique()) if len(common) else 0})
    stations.sort(key=lambda row: (-row["gap_days_covered"], -row["overlap_days"], row["station"]))
    return {"schema_version": 1, "station": station, "parameter": var, "unit": VARIABLES[var].unit,
            "resolution": "day", "start": dates[0], "end": dates[-1], "days": len(dates),
            "missing_days": int(missing.sum()), "incomplete_days": int(incomplete.sum()),
            "gaps": gaps, "station_candidates": stations}


MIN_PAIRED_DAYS = 30
MAX_MAE_C = 2.5


def observations(daily: pd.DataFrame, var: str, resolution: str) -> pd.Series:
    if resolution == "month":
        return A.aggregate(daily[[var]], "MS")[var].dropna()
    return daily[var].dropna()


def archive_proposal(daily, var, start, end, entries, reference, resolution):
    original = observations(daily, var, resolution)
    limits = VARIABLES[var].limits
    rows = []
    seen = set()
    first = pd.Timestamp(start)
    if resolution == "month":
        first = first.to_period("M").to_timestamp()
    for day, value in entries:
        at = pd.Timestamp(day)
        if resolution == "month" and at.day != 1:
            raise ValueError("Для месячного значения укажите месяц в формате ГГГГ-ММ.")
        if not first <= at <= pd.Timestamp(end):
            raise ValueError(f"{day}: дата вне выбранного периода.")
        if at in original.index:
            raise ValueError(f"{day}: в официальном ряду уже есть полное значение.")
        if not limits[0] <= value <= limits[1]:
            raise ValueError(f"{day}: значение вне диапазона для этого параметра.")
        if day in seen:
            raise ValueError(f"{day}: дата повторяется.")
        seen.add(day)
        rows.append({"date": day, "value": value, "resolution": resolution, "method": "archive",
                     "source": reference, "mae": None, "error90": None, "validation_days": None})
    return sorted(rows, key=lambda row: row["date"])


def station_proposal(target, donor, start, end, donor_name):
    """Помесячная поправка к донору, проверенная с исключением целых лет."""
    paired = pd.concat({"target": target, "donor": donor}, axis=1, sort=True).dropna()
    selected = donor.loc[pd.Timestamp(start):pd.Timestamp(end)].dropna()
    missing = selected.index.difference(target.dropna().index)
    if not len(missing):
        raise ValueError("У выбранной станции нет полных значений на даты пропусков.")
    result, skipped = [], []
    for month in missing.month.unique():
        sample = paired[paired.index.month == month]
        years = sample.index.year.unique()
        dates = missing[missing.month == month]
        if len(sample) < MIN_PAIRED_DAYS or len(years) < 2:
            skipped.append({"month": int(month), "reason": "Мало общих дней в разных годах."})
            continue
        offsets = sample["target"] - sample["donor"]
        errors = []
        for year in years:
            holdout = sample.index.year == year
            learn = offsets[~holdout]
            if len(learn) >= 10:
                errors.extend((offsets[holdout] - learn.median()).abs().tolist())
        if len(errors) < MIN_PAIRED_DAYS:
            skipped.append({"month": int(month), "reason": "Мало дат для проверки по исключённым годам."})
            continue
        mae = sum(errors) / len(errors)
        if mae > MAX_MAE_C:
            skipped.append({"month": int(month), "reason": f"Ошибка проверки {mae:.2f} °C выше {MAX_MAE_C:g} °C."})
            continue
        error90 = pd.Series(errors).quantile(.9)
        offset = offsets.median()
        lo, hi = VARIABLES["t_air"].limits
        for day, value in (selected.reindex(dates) + offset).items():
            if lo <= value <= hi:
                result.append({"date": day.strftime("%Y-%m-%d"), "value": round(float(value), 2),
                               "resolution": "day", "method": "station_offset", "source": donor_name,
                               "mae": round(mae, 3), "error90": round(float(error90), 3), "validation_days": len(errors)})
    return sorted(result, key=lambda row: row["date"]), skipped


_SCHEMA = """CREATE TABLE IF NOT EXISTS recovery (
    dataset TEXT NOT NULL, station TEXT NOT NULL, parameter TEXT NOT NULL,
    date TEXT NOT NULL, resolution TEXT NOT NULL, method TEXT NOT NULL,
    source TEXT NOT NULL, value REAL NOT NULL, payload TEXT NOT NULL,
    PRIMARY KEY (dataset, station, parameter, date, resolution, method, source, value)
)"""


def save(path: Path, dataset: str, station: str, var: str, rows: list[dict]) -> int:
    path.parent.mkdir(parents=True, exist_ok=True)
    with closing(sqlite3.connect(path)) as db, db:
        db.execute(_SCHEMA)
        cursor = db.executemany("INSERT OR IGNORE INTO recovery VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)", [
            (dataset, station, var, row["date"], row["resolution"], row["method"], row["source"], row["value"],
             json.dumps(row, ensure_ascii=False))
            for row in rows
        ])
        return cursor.rowcount


def saved(path: Path, dataset: str, station: str, var: str, start, end) -> list[dict]:
    if not path.is_file():
        return []
    first = pd.Timestamp(start)
    month = first.to_period("M").start_time
    with closing(sqlite3.connect(path)) as db, db:
        db.execute(_SCHEMA)
        rows = db.execute("""SELECT payload FROM recovery
            WHERE dataset = ? AND station = ? AND parameter = ? AND date >= ? AND date <= ?
            AND (resolution = 'month' OR date >= ?)
            ORDER BY date, method, source, value""",
            (dataset, station, var, str(month.date()), str(end), str(first.date())))
        return [json.loads(row[0]) for row in rows]


def with_observation_status(rows: list[dict], daily: pd.DataFrame, var: str) -> list[dict]:
    available = {res: observations(daily, var, res).index for res in {row["resolution"] for row in rows}}
    return [row | {"has_observation": pd.Timestamp(row["date"]) in available[row["resolution"]]} for row in rows]


def proposal_token(dataset: str, station: str, var: str, rows: list[dict]) -> str:
    payload = json.dumps([dataset, station, var, rows], sort_keys=True, ensure_ascii=False)
    return sha256(payload.encode("utf-8")).hexdigest()
