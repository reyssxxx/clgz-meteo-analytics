from io import BytesIO

import numpy as np
import pandas as pd
import pytest
from fastapi import HTTPException
from openpyxl import load_workbook

from meteo import recovery as R
from meteo import api


def sample():
    dates = pd.date_range("2023-01-01", "2025-01-31", freq="D")
    donor = pd.Series(8 + np.sin(dates.day_of_year / 10), index=dates)
    target = donor + 1.5
    target.loc["2025-01-10":"2025-01-16"] = np.nan
    return target, donor


def test_gap_preview_and_disjoint_archive():
    target, donor = sample()
    raw = target.to_frame("t_air")
    raw.loc["2025-01-10", "t_air"] = 6.2
    historic = donor.loc["2023"].rename(index=lambda d: d.replace(year=1998))
    dailies = {"Кр1": target.to_frame("t_air"), "Кр2": donor.to_frame("t_air"), "Архив": historic.to_frame("t_air")}
    preview = R.preview(raw, dailies, "Кр1", "t_air", "2025-01-01", "2025-01-31")
    assert preview["missing_days"] == 7
    assert preview["incomplete_days"] == 1
    assert preview["gaps"][0]["days_without_usable_observations"] == 6
    kr2, archive = preview["station_candidates"]
    assert kr2["gap_days_covered"] == 7 and kr2["overlap_years"] == 3
    assert archive["gap_days_covered"] == archive["overlap_days"] == 0


def test_offset_reconstructs_held_out_week_without_modifying_values():
    target, donor = sample()
    original = target.copy()
    rows, skipped = R.station_proposal(target, donor, "2025-01-01", "2025-01-31", "Кр2")
    assert not skipped and len(rows) == 7
    for row in rows:
        assert row["value"] == pytest.approx(donor.loc[row["date"]] + 1.5, abs=.005)
        assert row["mae"] == row["error90"] == 0
    pd.testing.assert_series_equal(target, original)


def test_station_needs_validated_overlap():
    target, donor = sample()
    rows, skipped = R.station_proposal(target.loc["2025"], donor, "2025-01-01", "2025-01-31", "Кр2")
    assert not rows and skipped[0]["month"] == 1
    near_only = donor.loc["2023"]
    with pytest.raises(ValueError, match="нет полных значений"):
        R.station_proposal(target, near_only, "2025-01-01", "2025-01-31", "Архив")


def test_archive_storage_monthly_resolution_idempotency_and_excel(tmp_path, monkeypatch):
    target, donor = sample()
    raw = pd.concat([target.to_frame("t_air").assign(station="Кр1"), donor.to_frame("t_air").assign(station="Кр2")]).sort_index()
    ds = api.Dataset(api.CASE_ID, "Кейс", {}, raw=raw)
    ds.cache[("st", "Кр1", True)] = (raw.loc[raw.station == "Кр1"], target.to_frame("t_air"), pd.DataFrame(), False)
    ds.cache[("st", "Кр2", True)] = (raw.loc[raw.station == "Кр2"], donor.to_frame("t_air"), pd.DataFrame(), False)
    monkeypatch.setattr(api, "STORE", {api.CASE_ID: ds})
    monkeypatch.setattr(api, "RECOVERY_STORE", tmp_path / "actual.sqlite3")
    common = dict(station="Кр1", parameter="t_air", start="2025-01-01", end="2025-01-31", reference="Том 1, стр. 12")
    request = api.RecoveryRequest(**common, source="archive", entries=[{"date": "2025-01-10", "value": 5.5}])
    def save(proposal):
        token = api.recovery_proposal(proposal)["preview_token"]
        return api.recovery_proposal(proposal.model_copy(update={"preview_token": token}), save=True)

    assert api.recovery_proposal(request)["saved"] == 0
    with pytest.raises(HTTPException, match="Предпросмотр устарел"):
        api.recovery_proposal(request, save=True)
    assert not api.RECOVERY_STORE.exists()
    assert save(request)["saved"] == 1
    assert save(request)["saved"] == 0
    second_source = request.model_copy(update={"reference": "Том 2, стр. 15"})
    assert save(second_source)["saved"] == 1
    model = api.RecoveryRequest(**common, source="station", donor="Кр2")
    assert save(model)["saved"] == 7
    assert pd.isna(target.loc["2025-01-10"])
    request_month = api.RecoveryRequest(**(common | {"start": "2025-01-14"}), source="archive", resolution="month",
                                        entries=[{"date": "2025-01-01", "value": 2.3}])
    assert save(request_month)["saved"] == 1
    invalid = api.RecoveryRequest(**common, source="archive", entries=[{"date": "2025-01-02", "value": 6.0}])
    with pytest.raises(HTTPException, match="официальном ряду"):
        api.recovery_proposal(invalid, save=True)
    result = api.recovery_saved("Кр1", request.start, request.end)
    assert len(result["rows"]) == 10
    assert result["observations"]["values"][9] is None
    workbook = load_workbook(BytesIO(api.recovery_xlsx("Кр1", request.start, request.end).body))
    assert workbook["Восстановленные данные"].max_row == 11
    ds.cache[("st", "Кр1", True)][1].loc["2025-01-10", "t_air"] = 9.0
    result = api.recovery_saved("Кр1", request.start, request.end)
    assert result["observations"]["values"][9] == 9.0
    assert all(row["has_observation"] for row in result["rows"] if row["date"] == "2025-01-10")
