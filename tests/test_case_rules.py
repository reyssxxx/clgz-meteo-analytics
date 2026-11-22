"""Проверка правил задания на реальных данных заповедника.

Главный тест — полное совпадение с эталоном coverage_by_month.csv от организаторов
(число полных дней и валидность каждого месяца по всем станциям и годам).
"""
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from meteo import analytics as A
from meteo import coverage as CV
from meteo.loader import combine, load, station_from_filename

DATA = Path(__file__).parent.parent / "data" / "clgz"
CODES = {"АМЕ-60": "AME60", "Кр1 (поляна)": "KR1", "Кр2 (поле)": "KR2"}


@pytest.fixture(scope="module")
def raw():
    return combine([load(f)[0] for f in sorted(DATA.glob("*/*.csv"))])


def test_station_names_from_files():
    assert station_from_filename("ame60_1993.csv") == "АМЕ-60"
    assert station_from_filename("kr1_2024.csv") == "Кр1 (поляна)"
    assert station_from_filename("KR2_2025.csv") == "Кр2 (поле)"
    assert station_from_filename("new_station_2026.csv") == "new_station"


def test_coverage_matches_reference_exactly(raw):
    ref = pd.read_csv(DATA / "coverage_by_month.csv")
    for st, code in CODES.items():
        tab = CV.coverage_table(raw[raw["station"] == st])
        r = ref[ref.station == code].merge(tab, on=["year", "month"], how="left", suffixes=("_ref", ""))
        assert len(r) == (ref.station == code).sum()
        diff = r[(r.valid_days_ref != r.valid_days) | (r.month_valid_ref != r.month_valid)]
        assert diff.empty, diff.head().to_string()


def test_day_rules_per_station(raw):
    assert CV.day_rule(raw[raw.station == "АМЕ-60"].index) == CV.DayRule("slots", 72, 96)
    assert CV.day_rule(raw[raw.station == "Кр1 (поляна)"].index) == CV.DayRule("hours", 18, 24)


def test_kr1_2024_pressure_per_parameter_coverage():
    df, rep = load(DATA / "sokol_m1" / "kr1_2024.csv")
    assert rep.qc_flagged["pressure"] == 1362
    d = A.to_daily(A.for_station(df, None))
    m = A.aggregate(d, "MS")
    assert d["pressure"].notna().sum() == 334 and m["pressure"].notna().sum() == 11
    assert np.isnan(m.loc["2024-04-01", "pressure"])
    assert d["t_air"].notna().sum() == 363 and m["t_air"].notna().sum() == 12


def test_flags_exclude_only_their_parameter_and_dup_rows(tmp_path):
    f = tmp_path / "ame60_2000.csv"
    rows = ["datetime,t_air,rh,qc_flag"]
    rows += [f"2000-01-01 {h:02d}:{m:02d},1.0,50," for h in range(24) for m in (0, 15, 30, 45)]
    rows[5] = rows[5][:-1] + "rh:range"
    rows[6] = rows[6].replace(",50,", ",999,") + "dup_conflict"
    f.write_text("\n".join(rows) + "\n")
    df, rep = load(f)
    assert rep.dup_conflict_rows == 1 and len(df) == 95
    assert df["rh"].isna().sum() == 1 and df["t_air"].notna().all()
    assert df["station"].iloc[0] == "АМЕ-60"


def test_month_rule_ten_days_and_five_in_a_row():
    days = pd.date_range("2020-01-01", "2020-01-31")
    ok = pd.Series(True, index=days)
    ok.iloc[[1, 3, 5, 7, 9, 11, 13, 15, 17, 19]] = False          # 10 неполных врозь — месяц валиден
    assert CV.month_valid(ok).iloc[0]
    ok.iloc[21] = False                                           # 11 неполных — нет
    assert not CV.month_valid(ok).iloc[0]
    ok2 = pd.Series(True, index=days)
    ok2.iloc[10:14] = False                                       # 4 подряд — ещё можно
    assert CV.month_valid(ok2).iloc[0]
    ok2.iloc[14] = False                                          # 5 подряд — нельзя
    assert not CV.month_valid(ok2).iloc[0]


def test_annual_mean_only_for_full_years(raw):
    d = A.to_daily(A.for_station(raw, "АМЕ-60"))
    y = A.aggregate(d, "YS")["t_air"].dropna()
    full = {1993, 2004, 2005, 2009, 2010, 2014, 2015, 2016}   # из описания кейса
    assert set(y.index.year) == full


def test_tables_daily_and_decades(raw):
    from meteo import tables as T
    st = A.for_station(raw, "АМЕ-60")
    d = A.to_daily(st)
    t = T.daily_table(st, d, "2018-01-01")          # январь 2018 у АМЕ-60 невалиден (README)
    assert t["days"] == 31 and t["complete_days"] == 10
    month = next(s for s in t["summary"] if s["kind"] == "month")
    assert not month["valid"]["t_air"] and np.isnan(month["values"]["t_air"])
    t2 = T.daily_table(st, d, "2016-07-01")
    m2 = next(s for s in t2["summary"] if s["kind"] == "month")
    assert m2["valid"]["t_air"] and t2["complete_days"] == 31
    dt = T.decade_table(d, "2016-01-01", "2016-12-31")
    assert len(dt["blocks"]) == 12 and dt["total"]["values"]["t_air"] == pytest.approx(A.aggregate(d, "YS").loc["2016-01-01", "t_air"])
    assert np.isnan(dt["total"]["values"]["soil_05"])   # сбой датчика 5 см в 2016: валидны не все 12 месяцев
    assert T.to_excel(t2, "АМЕ-60")[:2] == b"PK"


def test_polyana_vs_pole_2025_and_pressure_alert(raw):
    from meteo import views as V
    ra, rb = A.for_station(raw, "Кр1 (поляна)"), A.for_station(raw, "Кр2 (поле)")
    da, db = A.to_daily(ra), A.to_daily(rb)
    r = A.pair_compare(ra, da, rb, db, "t_air", "2025-01-01", "2025-12-31")
    assert r["common_days"] > 350 and len(r["diurnal_a"]) == 24
    day = r["diurnal_a"]["value"] - r["diurnal_b"]["value"]
    assert day.loc[12:15].mean() > 0 > day.loc[0:5].mean()      # поляна днём теплее, ночью холоднее
    p = V.pair(ra, da, rb, db, "Кр1", "Кр2", "pressure", "2024-01-01", "2024-12-31")
    assert {x["month"][:7] for x in p["suspicious"]} == {"2024-02", "2024-03"}
    assert p["months"]["a"][3] is None                            # апрель 2024 у давления Кр1 не учитывается
    t = V.pair(ra, da, rb, db, "Кр1", "Кр2", "t_air", "2025-01-01", "2025-12-31")
    assert t["suspicious"] == []
