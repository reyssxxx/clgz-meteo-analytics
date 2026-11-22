import numpy as np
import pandas as pd
import pytest

from meteo import insights as I
from tests.test_core import synthetic_daily


@pytest.mark.parametrize("n,expected", [(1, "1 день"), (2, "2 дня"), (5, "5 дней"), (11, "11 дней"),
                                        (21, "21 день"), (112, "112 дней"), (24, "24 дня")])
def test_plural(n, expected):
    assert I.plural(n, I.DAYS) == expected


@pytest.mark.parametrize("s,e,expected", [
    ("2024-01-01", "2024-01-31", "январь 2024"),
    ("2024-06-01", "2024-08-31", "лето 2024"),
    ("2019-12-01", "2020-02-29", "зиму 2019/20"),
    ("2023-01-01", "2023-12-31", "2023 год"),
    ("2010-07-01", "2010-08-31", "июль–август 2010"),
    ("2024-03-05", "2024-03-20", "период 05.03.2024–20.03.2024"),
])
def test_describe_period(s, e, expected):
    assert I.describe_period(pd.Timestamp(s), pd.Timestamp(e)) == expected


def test_same_window_and_rank():
    d = synthetic_daily(years=20)
    d.loc["2005-07-01":"2005-07-31", "t_air"] += 10
    v = I.same_window(d, "t_air", "2005-07-01", "2005-07-31")
    assert len(v) == 20 and v.idxmax() == 2005
    assert I.rank_phrase(v, 2005) == "это максимум за 20 лет наблюдений"


def test_window_crossing_new_year_labels_by_end_year():
    d = synthetic_daily(years=10)
    v = I.same_window(d, "t_air", "1995-12-01", "1996-02-29")
    assert 1996 in v.index and 1990 not in v.index   # зима 1989/90 не попадает — нет данных за декабрь 1989


def test_generate_mentions_heat_and_survives_short_data():
    d = synthetic_daily(years=20)
    d.loc["2005-07-01":"2005-07-31", ["t_air", "t_max", "t_min"]] += 10
    text = I.to_markdown(I.generate(d, "2005-07-01", "2005-07-31", ("1991", "2008")))
    assert "выше среднего за" in text and "максимум за" in text and "Суточные рекорды тепла" in text
    short = d.loc["2005-07-01":"2005-07-20"]
    assert I.generate(short, "2005-07-01", "2005-07-20")   # без нормы и трендов, но без падений
