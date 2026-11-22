"""Проверка правил задания на реальных данных заповедника.

Главный тест — полное совпадение с эталоном coverage_by_month.csv от организаторов
(число полных дней и валидность каждого месяца по всем станциям и годам).
"""
from io import BytesIO
from pathlib import Path

import numpy as np
import pandas as pd
import pytest
from fastapi import HTTPException, UploadFile
from openpyxl import load_workbook

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


def test_gust_uses_every_unflagged_observation_and_exports_mean(tmp_path):
    from meteo import tables as T

    f = tmp_path / "kr1_2026.csv"
    rows = ["datetime,wind_gust,qc_flag"]
    rows += [f"2026-01-01 {h:02d}:07,2," for h in range(18)]
    rows += ["2026-01-01 00:37,21,", "2026-01-01 00:53,100,wind_gust:range"]
    # 20 сроков, но только 17 разных часов.
    rows += [f"2026-01-02 {h:02d}:07,2," for h in range(17)]
    rows += [f"2026-01-02 00:{m},30," for m in (17, 27, 37)]
    f.write_text("\n".join(rows))
    raw, _ = load(f)
    station = "Кр1 (поляна)"
    daily = A.to_daily(raw, station)
    assert pd.Timestamp("2026-01-01 00:37") in raw.index
    assert daily.loc["2026-01-01", "wind_gust"] == 3.0
    assert daily.loc["2026-01-01", "wind_gust_max"] == 21.0
    assert pd.isna(daily.loc["2026-01-02", "wind_gust"])
    tab = T.daily_table(raw, daily, "2026-01-01", station)
    assert tab["summary"][0]["values"]["wind_gust"] == 3.0
    assert tab["extremes"]["wind_gust"]["value"] == 21.0
    assert load_workbook(BytesIO(T.to_excel(tab, station))).active["B6"].value == 3.0


def test_partial_decades_do_not_include_days_outside_selection():
    from meteo import tables as T

    idx = pd.date_range("2025-01-01", "2025-01-31")
    daily = pd.DataFrame({"wind_gust": idx.day}, index=idx)
    tab = T.decade_table(daily, "2025-01-19", "2025-01-22")
    second, third, month = tab["blocks"][0]["rows"]
    assert (second["decade"], third["decade"]) == (2, 3)
    assert second["values"]["wind_gust"] == 19.5
    assert third["values"]["wind_gust"] == 21.5
    assert month["partial"] and month["values"]["wind_gust"] == 20.5


def test_reference_counts_calendar_dates_and_valid_months():
    from meteo import views as V

    idx = pd.date_range("2023-01-01", "2025-12-31")
    daily = pd.DataFrame({"t_air": idx.year - 2020.0}, index=idx)
    daily.loc["2024-03-01":"2024-03-05", "t_air"] = np.nan
    ref = A.daily_anomalies(daily, "t_air", ("2023", "2024"))
    assert ref.loc["2024-02-29", "years"] == 1
    assert ref.loc["2025-03-01", "norm"] == 3.0
    assert ref.loc["2025-03-01", "years"] == 1
    norm, years = A.monthly_reference(daily, ("2023", "2024"))
    assert years.loc[2, "t_air"] == 2 and years.loc[3, "t_air"] == 1
    assert norm.loc[3, "t_air"] == 3.0
    chart = V.years_chart(daily, "t_air", "2024-02-28", "2024-03-01", ("2023", "2024"))
    assert chart["base_years"] == [2, 1, 1]
    assert next(line for line in chart["lines"] if line["year"] == 2023)["y"] == [3.0, None, 3.0]


def test_diurnal_balances_days_and_wind_rose_excludes_incomplete_days():
    from meteo import views as V

    idx = pd.to_datetime(["2025-01-01 08:00", "2025-01-01 08:30", "2025-01-02 08:00", "2025-01-03 00:00"])
    raw = pd.DataFrame({"t_air": [0, 0, 12, 99], "wind_speed": [2, 2, 2, 9], "wind_dir": [0, 0, 0, 180]}, index=idx)
    days = idx.normalize().unique()
    daily = pd.DataFrame({"t_air": [0, 12, np.nan], "wind_speed": [2, 2, np.nan], "wind_dir": [0, 0, np.nan]}, index=days)
    assert A.diurnal(raw, daily, "t_air", days[0], days[1]).loc[8, "value"] == 6
    rose = V.wind(raw, daily, True, days[0], days[1])
    assert sum(values[8] for values in rose["values"].values()) == 0


def test_case_upload_is_validated_as_batch_and_reloaded(tmp_path, monkeypatch):
    from meteo import api

    source = tmp_path / "sokol_m1"
    source.mkdir()
    sample = ("datetime,t_air\n" + "".join(f"2025-01-01 {h:02d}:00,2\n" for h in range(18))).encode()
    (source / "kr1_2025.csv").write_bytes(sample)
    monkeypatch.setattr(api, "CASE_DIR", tmp_path)
    monkeypatch.setattr(api, "UPLOADS", tmp_path / "uploads")
    monkeypatch.setattr(api, "STORE", {})
    api._load_demos()

    def file(name, data=sample):
        return UploadFile(BytesIO(data), filename=name)

    incoming = sample.replace(b"2025", b"2026")
    with pytest.raises(HTTPException) as exc:
        api.add_files(api.CASE_ID, [file("kr1_2026.CSV", incoming), file("broken.zip")])
    assert exc.value.status_code == 400
    assert not api.UPLOADS.exists()
    assert len(api.STORE[api.CASE_ID].files) == 1
    added = api.add_files(api.CASE_ID, [file("kr1_2026.CSV", incoming)])
    assert added["stations"] == ["Кр1 (поляна)"]
    api.STORE.clear()
    api._load_demos()
    _, daily, _, _ = api.STORE[api.CASE_ID].station("Кр1 (поляна)")
    assert daily.loc["2026-01-01", "t_air"] == 2.0


def test_public_station_joins_case_and_is_available_after_reload(tmp_path, monkeypatch):
    from meteo import api

    case_dir = tmp_path / "clgz"
    original = case_dir / "ame60"
    original.mkdir(parents=True)
    csv = b"datetime,t_air\n2024-01-01,2\n2024-01-02,3\n"
    (original / "ame60_2024.csv").write_bytes(csv)
    public = tmp_path / "public_stations"
    public.mkdir()
    (public / "USW00094728.csv").write_bytes(csv)
    monkeypatch.setattr(api, "DATA", tmp_path)
    monkeypatch.setattr(api, "CASE_DIR", case_dir)
    monkeypatch.setattr(api, "UPLOADS", case_dir / "uploads")
    monkeypatch.setattr(api, "STORE", {})
    result = api.add_public_station("USW00094728")
    assert result["dataset"] == api.CASE_ID
    assert api.add_public_station("USW00094728") == result
    api.STORE.clear()
    case = api.get(api.CASE_ID)
    assert set(case.raw.station) == {"АМЕ-60", result["station"]}
    assert list(case.reports[f"uploads/{result['station']}.csv"].stations) == [result["station"]]
