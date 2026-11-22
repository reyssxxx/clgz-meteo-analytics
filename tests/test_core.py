import numpy as np
import pandas as pd
import pytest

from meteo import analytics as A
from meteo.loader import guess_mapping, load


def synthetic_daily(years=30, trend_per_decade=0.5, seed=0):
    rng = np.random.default_rng(seed)
    idx = pd.date_range("1990-01-01", periods=int(365.25 * years), freq="D")
    doy = idx.day_of_year.to_numpy()
    t = (5 - 14 * np.cos(2 * np.pi * (doy - 15) / 365.25)
         + trend_per_decade * (np.arange(len(idx)) / 3652.5) + rng.normal(0, 3, len(idx)))
    df = pd.DataFrame({"t_air": t, "t_max": t + 4, "t_min": t - 4,
                       "precip": rng.gamma(0.5, 4, len(idx))}, index=idx)
    df.index.name = "time"
    return df


# ------------------------------------------------------------------ загрузчик

def test_loader_russian_csv(tmp_path):
    text = ("Метеостанция Фёдоровское\n\n"
            "Дата;Срок;Температура воздуха, °C;Осадки, мм;Давление, мм рт. ст.;Высота снега, м\n"
            "01.01.2024;0;-5,2;0,0;750,0;0,35\n"
            "01.01.2024;3;-6,1;-9999;751,0;0,35\n"
            "01.01.2024;3;-6,1;0;751,0;0,35\n"
            "02.01.2024;0;-7,0;1,5;752,0;0,40\n")
    p = tmp_path / "st.csv"
    p.write_bytes(text.encode("cp1251"))
    df, rep = load(p)
    assert rep.encoding == "cp1251" and rep.separator == ";" and rep.decimal == ","
    assert rep.time_columns == ["Дата", "Срок"]
    assert list(df.index) == list(pd.to_datetime(["2024-01-01 00:00", "2024-01-01 03:00", "2024-01-02 00:00"]))
    assert df["t_air"].iloc[0] == pytest.approx(-5.2)
    assert np.isnan(df["precip"].iloc[1])            # -9999 → NaN
    assert rep.duplicates_dropped == 1
    assert df["pressure"].iloc[0] == pytest.approx(999.9, abs=0.1)   # 750 мм рт. ст.
    assert df["snow_depth"].iloc[0] == pytest.approx(35)             # м → см


def test_mapping_keywords():
    m, t, st = guess_mapping(["date", "Station", "T max", "T min", "Temperature", "RH", "Wind speed",
                              "Wind gust max", "Precipitation"])
    assert t == ["date"] and st == "Station"
    assert m == {"T max": "t_max", "T min": "t_min", "Temperature": "t_air", "RH": "rh",
                 "Wind speed": "wind_speed", "Wind gust max": "wind_gust", "Precipitation": "precip"}


def test_excel_sheets_are_stations(tmp_path):
    p = tmp_path / "x.xlsx"
    with pd.ExcelWriter(p) as w:
        for name in ("Ст1", "Ст2"):
            pd.DataFrame({"Дата": pd.date_range("2024-01-01", periods=3), "Температура": [1, 2, 3]}) \
                .to_excel(w, sheet_name=name, index=False)
    df, rep = load(p)
    assert sorted(df["station"].unique()) == ["Ст1", "Ст2"] and len(df) == 6


# ------------------------------------------------------------------ агрегация

def test_to_daily_rules_and_coverage():
    idx = pd.date_range("2024-01-01", periods=48, freq="h")
    df = pd.DataFrame({"t_air": np.r_[np.arange(24.0), np.full(24, np.nan)], "precip": 1.0}, index=idx)
    df.loc[idx[24:], "precip"] = np.nan
    df.iloc[30, 1] = 2.0  # одно наблюдение во вторых сутках
    d = A.to_daily(df)
    assert d.loc["2024-01-01", "t_air"] == pytest.approx(11.5)
    assert d.loc["2024-01-01", "precip"] == 24
    assert d.loc["2024-01-01", "t_max"] == 23 and d.loc["2024-01-01", "t_min"] == 0
    assert np.isnan(d.loc["2024-01-02", "precip"])   # 1 из 24 часов — неполные сутки


def test_aggregate_monthly_sum_mean_and_partial_month():
    idx = pd.date_range("2024-01-01", "2024-03-10", freq="D")
    d = pd.DataFrame({"t_air": 1.0, "precip": 2.0}, index=idx)
    m = A.aggregate(d, "MS")
    assert m.loc["2024-01-01", "precip"] == 62 and m.loc["2024-01-01", "t_air"] == 1
    assert np.isnan(m.loc["2024-03-01", "precip"])   # март заполнен на 10/31


def test_circular_mean_wind_direction():
    idx = pd.date_range("2024-01-01", periods=24, freq="h")
    df = pd.DataFrame({"wind_dir": [350.0, 10.0] * 12}, index=idx)
    assert A.to_daily(df)["wind_dir"].iloc[0] == pytest.approx(0, abs=1e-6) or \
        A.to_daily(df)["wind_dir"].iloc[0] == pytest.approx(360, abs=1e-6)


# ------------------------------------------------------------------ тренды

def test_mann_kendall_and_sen():
    x = np.arange(20, dtype=float) * 0.3
    mk = A.mann_kendall(x)
    assert mk["p"] < 0.001 and mk["sen"] == pytest.approx(0.3) and mk["tau"] == pytest.approx(1)
    rng = np.random.default_rng(1)
    assert A.mann_kendall(rng.normal(size=40))["p"] > 0.05


def test_trend_table_detects_warming():
    d = synthetic_daily(trend_per_decade=0.8)
    tt = A.trend_table(d).set_index("var")
    assert tt.loc["t_air", "Вывод"] == "рост"
    assert tt.loc["t_air", "Наклон Сена / 10 лет"] == pytest.approx(0.8, abs=0.3)


# ------------------------------------------------------------------ аномалии и качество

def test_anomalies_find_injected_heat():
    d = synthetic_daily(trend_per_decade=0)
    d.loc["2015-01-10", "t_air"] += 20
    a = A.anomalous_days(d, "t_air", z=3)
    assert pd.Timestamp("2015-01-10") in a.index
    m = A.monthly_anomalies(d, "precip")
    assert "pct_of_norm" in m


def test_quality_issues():
    idx = pd.date_range("2024-01-01", periods=24, freq="h")
    df = pd.DataFrame({"t_air": np.linspace(0, 2, 24), "rh": 80.0 + np.arange(24) % 3}, index=idx)
    df.iloc[5, 0] = 80     # вне диапазона и скачок
    df.iloc[10:20, 1] = 55  # залипание влажности
    q = A.quality_issues(df)
    kinds = set(q["Проблема"].str.split().str[0])
    assert {"Вне", "Выброс", "Одинаковое"} <= kinds
    gaps = A.gaps(df.drop(df.index[12:15]))
    assert len(gaps) == 1 and gaps.iloc[0]["Длительность"] == pd.Timedelta("3h")


# ------------------------------------------------------------------ агро и экстремумы

def test_agro_indices_constant_climate():
    idx = pd.date_range("2020-01-01", "2020-12-31", freq="D")
    t = pd.Series(-5.0, index=idx)
    t["2020-05-01":"2020-09-30"] = 15.0
    d = pd.DataFrame({"t_air": t, "t_min": t - 3, "precip": 2.0})
    a = A.agro_indices(d).loc[2020]
    assert a["Σ активных T > 10 °C"] == 15 * 153
    assert str(a["Переход через 10 °C весной"]) == "2020-05-01"
    assert str(a["Переход через 10 °C осенью"]) == "2020-09-30"
    assert a["ГТК Селянинова"] == pytest.approx(2 * 153 / (0.1 * 15 * 153))
    assert str(a["Последний весенний заморозок"]) == "2020-04-30"
    assert str(a["Первый осенний заморозок"]) == "2020-10-01"


def test_extreme_events():
    idx = pd.date_range("2020-06-01", periods=40, freq="D")
    d = pd.DataFrame({"t_max": 20.0, "precip": 0.0}, index=idx)
    d.iloc[3:9, 0] = 31
    d.iloc[30, 1] = 35
    e = A.extreme_events(d)
    assert (e["Явление"] == "Волна жары").sum() == 1
    assert (e["Явление"] == "Сильные осадки").sum() == 1
    assert (e["Явление"] == "Засушливый период").sum() >= 1


def test_excel_serial_dates_and_combine(tmp_path):
    from meteo.loader import combine
    p1 = tmp_path / "t.csv"
    p1.write_text("Дата,Температура\n45292,1.5\n45292.5,2.5\n45293,3\n", encoding="utf-8")
    p2 = tmp_path / "p.csv"
    p2.write_text("Дата,Осадки\n2024-01-01 00:00,0.4\n2024-01-02,1.0\n", encoding="utf-8")
    a, _ = load(p1, station="X")
    b, _ = load(p2, station="X")
    assert a.index[1] == pd.Timestamp("2024-01-01 12:00")
    c = combine([a, b])
    assert len(c) == 3 and c.loc["2024-01-01 00:00", "precip"] == 0.4 and c.loc["2024-01-01 00:00", "t_air"] == 1.5
    assert c["station"].unique().tolist() == ["X"]


def test_mask_issues_vectorized():
    idx = pd.date_range("2024-01-01", periods=5, freq="h")
    df = pd.DataFrame({"t_air": [1, 2, 99, 3, 4.0], "rh": [50.0] * 5}, index=idx)
    q = A.quality_issues(df)
    assert not q["Проблема"].str.startswith("Скачок").any()   # возврат после выброса — не скачок
    out = A.mask_issues(df, q)
    assert np.isnan(out["t_air"].iloc[2]) and out["t_air"].notna().sum() == 4 and out["rh"].notna().all()


def test_level_shift_is_warning_not_masked():
    idx = pd.date_range("2024-01-01", periods=8, freq="h")
    df = pd.DataFrame({"t_air": [1, 1.2, 1.1, 12, 12.1, 12.3, 12.2, 12.0]}, index=idx)
    q = A.quality_issues(df)
    assert q["Проблема"].str.startswith("Скачок").sum() == 1
    assert A.mask_issues(df, q)["t_air"].notna().all()


def test_compare_periods_uses_annual_values_and_detects_shift():
    d = synthetic_daily(years=30, trend_per_decade=0)
    d.loc["2010":, "t_air"] += 1.5
    t, unit = A.compare_periods(d, ("1990", "2004"), ("2005", "2019"))
    r = t.set_index("var")
    assert unit == "годовые значения" and r.loc["t_air", "n A"] == 15
    assert r.loc["t_air", "Вывод"] == "выше в B"
    assert r.loc["precip", "Вывод"] == "различие незначимо"
    _, unit2 = A.compare_periods(d, ("2001-06-01", "2001-08-31"), ("2002-06-01", "2002-08-31"))
    assert unit2.startswith("средние")


def test_pettitt_finds_step():
    rng = np.random.default_rng(3)
    x = pd.Series(np.r_[rng.normal(0, 0.3, 20), rng.normal(1.5, 0.3, 15)], index=range(1990, 2025))
    r = A.pettitt(x)
    assert r["p"] < 0.01 and r["change"] == 2009
    assert A.pettitt(pd.Series(rng.normal(size=30)))["p"] > 0.05


def test_fill_gaps_from_neighbour():
    a = synthetic_daily(years=5, seed=1)
    b = a.copy()
    b["t_air"] = a["t_air"] * 0.95 - 0.5 + np.random.default_rng(2).normal(0, 0.3, len(a))
    truth = a["t_air"].copy()
    a.loc["1992-03-01":"1992-03-20", "t_air"] = np.nan
    filled, rep = A.fill_gaps({"A": a, "B": b}, "A")
    r = rep.set_index("Параметр").loc["t_air"]
    assert r["Донор"] == "B" and r["Заполнено дней"] == 20 and r["Осталось пропусков"] == 0
    err = (filled.loc["1992-03-01":"1992-03-20", "t_air"] - truth.loc["1992-03-01":"1992-03-20"]).abs().mean()
    assert err < 0.6


def test_homogeneity_detects_sensor_change():
    a = synthetic_daily(years=30, seed=4)
    b = a.copy()
    b.loc["2008":, "t_air"] += 0.8      # «сменили датчик» на станции B
    diff, r = A.homogeneity({"A": a, "B": b}, "B", "A", "t_air")
    assert r["p"] < 0.01 and r["change"] == 2007


def test_wind_rose():
    idx = pd.date_range("2024-01-01", periods=100, freq="h")
    df = pd.DataFrame({"wind_dir": [0.0] * 50 + [90.0] * 30 + [355.0] * 10 + [180.0] * 10,
                       "wind_speed": [3.0] * 90 + [0.0] * 10}, index=idx)
    rose, calm = A.wind_rose(df)
    assert calm == pytest.approx(10)
    assert rose.loc["С"].sum() == pytest.approx(60)     # 0° и 355° — оба север
    assert rose.loc["В"].sum() == pytest.approx(30)
    assert rose.to_numpy().sum() + calm == pytest.approx(100)
