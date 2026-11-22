from io import BytesIO

import numpy as np
import pandas as pd
import pytest
from openpyxl import load_workbook

from meteo import analytics as A
from meteo import quality as Q
from meteo import tables as T
from meteo.loader import load
from meteo.report import build_sheets, to_excel


def test_repeating_measurements_use_real_time_and_other_sensors():
    idx = pd.date_range("2025-04-01", periods=22, freq="h")
    raw = pd.DataFrame({"t_air": [2, 3, *([9] * 16), 2, 3, 4, 5], "rh": np.linspace(40, 72, len(idx))}, index=idx)
    issues = A.quality_issues(raw)
    plateaus = issues[issues["Проблема"].str.startswith("Одинаковое")]
    assert len(plateaus) == 1
    event = plateaus.iloc[0]
    assert (event["Время"], event["Конец"], event["Наблюдений"]) == (idx[2], idx[17], 16)
    assert event["Приоритет"] == Q.IMPORTANT
    assert "Межсезонье" in event["Контекст"] and "влажность" in event["Контекст"]
    separated = raw.drop(idx[7:14])
    assert not A.quality_issues(separated)["Проблема"].str.startswith("Одинаковое").any()


@pytest.mark.parametrize(("month", "with_context", "expected"), [(1, False, Q.IMPORTANT), (4, True, Q.WEATHER)])
def test_step_assessed_in_context_without_deleting_weather(month, with_context, expected):
    idx = pd.date_range(pd.Timestamp(2025, month, 1), periods=25, freq="h")
    low = [1, 1.2, 1.1, 1.2, 1, 1.2, 1.1, 1, 1.1, 1, 1, 1]
    high = [12, 12.1, 12.2, 12.3, 12.1, 12, 12.1, 12.2, 12.3, 12.2, 12, 12.2, 12.1]
    raw = pd.DataFrame({"t_air": low + high, "rh": [80] * 12 + [60 if with_context else 80] * 13}, index=idx)
    issues = A.quality_issues(raw)
    shift = issues[(issues["Параметр"] == "t_air") & issues["Проблема"].str.startswith("Скачок")].iloc[0]
    assert shift["Приоритет"] == expected
    assert "Медианы до и после" in shift["Контекст"]
    assert A.to_daily(raw, "Кр1 (поляна)").iloc[0]["t_air"] == pytest.approx(raw["t_air"].iloc[:24].mean())


def test_source_flags_are_auditable_per_parameter_in_both_exports():
    idx = pd.date_range("2008-05-01", periods=31 * 24, freq="h")
    rows = ["datetime,t_air,rh,qc_flag"]
    rows += [f'{t:%Y-%m-%d %H:%M},6,79,' + ("t_air:fault" if i == 0 else "") for i, t in enumerate(idx)]
    raw, report = load("\n".join(rows).encode(), "kr1_2008.csv")
    station = "Кр1 (поляна)"
    source = Q.exclusions_for([report], station)
    daily = A.to_daily(raw, station)
    issues = A.quality_issues(raw)
    start, end = idx[0], idx[-1].normalize()
    monthly = A.aggregate(daily, "MS")
    review = Q.export_sheets(raw, daily, monthly, issues, source, station, "t_air", start, end)
    audit = review[Q.AUDIT_SHEET].iloc[0]
    assert (audit["Использовано"], audit["Исключено"], audit["Причины при импорте"]) == (743, 1, "t_air:fault × 1")
    assert Q.audit_months(raw, daily, monthly, source, "rh", start, end).iloc[0]["Использовано"] == 744
    assert len(source) == 1 and source.iloc[0]["Значение"] == 6
    confirmed = review[Q.IMPORTANT_SHEET].query('Статус == "Исключено при импорте"')
    assert confirmed["Проблема"].tolist() == ["t_air:fault"]
    tab = T.daily_table(raw, daily, start, station)
    for output in (T.to_excel(tab, station, review),
                   to_excel(build_sheets(daily, start, end, None, issues), review)):
        book = load_workbook(BytesIO(output))
        assert Q.IMPORTANT_SHEET in book.sheetnames
        assert book[Q.IMPORTANT_SHEET].auto_filter.ref
        assert book[Q.EXCLUSIONS_SHEET]["E2"].value == "t_air:fault"
        assert book[Q.AUDIT_SHEET]["E2"].value == 743
