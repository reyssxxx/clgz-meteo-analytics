"""Ответы для веб-интерфейса: все экраны строятся на демо-данных и сериализуются в строгий JSON."""
import json
from pathlib import Path

import pandas as pd
import pytest

from meteo import analytics as A
from meteo import quality as Q
from meteo import views as V
from meteo.loader import load

DATA = Path(__file__).parent / "data"
BASE = ("1991", "2020")


@pytest.fixture(scope="module")
def demo():
    raw, rep = load(DATA / "sample_stations.csv")
    dailies = {s: A.to_daily(A.for_station(raw, s)) for s in raw["station"].unique()}
    return raw, rep, dailies


def strict(obj):
    """json.dumps с allow_nan=False падает на NaN/Infinity — браузерный JSON.parse их не принимает."""
    return json.loads(json.dumps(obj, allow_nan=False, ensure_ascii=False))


@pytest.mark.parametrize("var", ["t_air", "precip"])
@pytest.mark.parametrize("period", [("2024-06-01", "2024-08-31"), ("2024-01-01", "2024-01-31"), ("1990-01-01", "2025-12-31")])
def test_overview_parts(demo, var, period):
    _, _, dd = demo
    d = dd["Фёдоровское"]
    s, e = map(pd.Timestamp, period)
    y = strict(V.years_chart(d, var, s, e, BASE))
    assert y["mode"] == ("series" if (e - s).days > 366 else "years")
    if y["mode"] == "years":
        assert len(y["lines"]) >= 30 and len(y["x"]) == len(y["norm"]) == len(y["current"]["y"])
        assert y["cumulative"] == (var == "precip")
    h = strict(V.anomaly_heatmap(d, var, s, e, BASE))
    assert len(h["z"]) == len(h["years"]) and all(len(r) == 12 for r in h["z"])
    strict(V.ranking(d, var, s, e, BASE))
    f = strict(V.facts(d, var, s, e, BASE))
    assert f["facts"] and f["insights"]


def test_anomaly_explanations_use_complete_calendar_months_and_local_quality_notes():
    dates = pd.date_range("2019-01-01", "2022-04-30")
    daily = pd.DataFrame({"t_air": 5.0}, index=dates)
    daily.loc["2022-01", "t_air"] = 99
    daily.loc["2022-02", "t_air"] = 8
    daily.loc["2022-03", "t_air"] = -2
    daily.loc["2022-04-01":"2022-04-05", "t_air"] = float("nan")
    issues = pd.DataFrame([
        (pd.Timestamp("2022-02-28 22:00"), pd.Timestamp("2022-03-01 09:00"), "t_air", -2,
         "Повторяющиеся значения", Q.IMPORTANT, "Одинаковые показания несколько часов.", 12),
        (pd.Timestamp("2022-02-10"), pd.Timestamp("2022-02-10"), "t_air", 16,
         "Потепление", Q.WEATHER, "Изменилась погода.", 1),
    ], columns=Q.ISSUE_COLUMNS)

    result = strict(V.anomaly_explanations(daily, "t_air", "2022-01-02", "2022-04-30", ("2019", "2021"), issues))

    assert result["scale"] == "month"
    march, february = result["items"]
    assert march == {"date": "2022-03-01", "value": -2, "mean": 5, "years": 3, "difference": -7,
                     "quality_count": 1, "quality_note": "Повторяющиеся значения",
                     "quality_context": "Одинаковые показания несколько часов."}
    assert february["date"] == "2022-02-01" and february["difference"] == 3
    assert february["quality_count"] == 1


def test_anomaly_explanations_use_daily_comparisons_for_short_periods():
    dates = pd.date_range("2019-02-01", "2022-03-02")
    daily = pd.DataFrame({"wind_dir": 350.0}, index=dates)
    daily.loc["2022-03-01", "wind_dir"] = 10
    issues = pd.DataFrame(columns=Q.ISSUE_COLUMNS)

    result = strict(V.anomaly_explanations(daily, "wind_dir", "2022-02-28", "2022-03-02", ("2019", "2021"), issues))
    assert result["scale"] == "day" and result["has_comparison"]
    assert [(x["date"], x["difference"], x["years"]) for x in result["items"]] == [("2022-03-01", 20, 3)]

    result = strict(V.anomaly_explanations(daily, "wind_dir", "2022-02-28", "2022-03-02", ("2020", "2021"), issues))
    assert not result["has_comparison"] and not result["items"]


def test_category_scale():
    vals = pd.Series(range(1, 31), index=range(1991, 2021), dtype=float)
    assert V.category(vals, 2020)["label"] == "Рекордно высоко"
    assert V.category(vals, 1991)["label"] == "Рекордно низко"
    assert V.category(vals, 2005)["label"] == "Около среднего"
    assert V.category(vals, 2018)["index"] == 5      # верхние 10 %, но не рекорд
    assert V.category(vals.iloc[:5], 1995) is None   # слишком короткий ряд


def test_other_screens(demo):
    raw, rep, dd = demo
    d = dd["Фёдоровское"]
    s, e = pd.Timestamp("2010-01-01"), pd.Timestamp("2025-12-31")
    t = strict(V.trends(d, "t_air", d.index.min(), d.index.max(), BASE))
    assert t["table"] and all(r["var"] != "wind_dir" for r in t["table"]) and t["stripes"]
    assert strict(V.trends(d, "precip", d.index.min(), d.index.max(), BASE, season=6))["series"]["values"]
    c = strict(V.compare(d, "t_air", ("1991-01-01", "2005-12-31"), ("2011-01-01", "2025-12-31")))
    assert c["rows"][0]["verdict"] and len(c["cycle"]["a"]) == 12
    st = strict(V.stations_view(dd, "Фёдоровское", "t_air", "MS", s, e, None))
    assert len(st["series"]) == 3 and st["homogeneity"]["verdict"] in ("ok", "small", "shift")
    ag = strict(V.agro(d, pd.Timestamp("2024-06-01"), pd.Timestamp("2024-08-31")))
    assert ag["rows"] and ag["rows"][0]["year"] == 2024      # для сезона берётся его год
    data = A.for_station(raw, "Андреаполь")
    w = strict(V.wind(data, dd["Андреаполь"], False, s, e))
    assert w["available"] and len(w["sectors"]) == 16
    q = strict(V.quality(data, dd["Андреаполь"], A.quality_issues(data), {"f.csv": rep}))
    assert q["files"][0]["mapping"] and q["completeness"]["z"]
