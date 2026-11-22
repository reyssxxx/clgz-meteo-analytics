from datetime import datetime

import pandas as pd

from meteo import forecast as F


def scenario():
    history = {"snowfall_sum": [2] * 14}
    future = {"time": ["2025-03-01", "2025-03-02"], "rain_sum": [12, 0], "showers_sum": [1, 0],
              "temperature_2m_max": [6, 7], "temperature_mean": [3, 4],
              "temperature_2m_min": [-2, 2], "wind_gusts_10m_max": [16, 5]}
    return history, future


def test_snowmelt_requires_remaining_snow_and_weather_trigger():
    history, future = scenario()
    alert = F.recommendations(history, future, .21)[0]
    assert alert["title"] == "Возможен усиленный сток при таянии снега"
    assert "14 из 14 дней" in alert["evidence"] and "13 мм дождя" in alert["evidence"]
    assert not any("сток" in r["title"] for r in F.recommendations(history, future, 0))
    future.update(rain_sum=[0, 0], temperature_mean=[-3, -1], temperature_2m_max=[-1, 0])
    assert not any("сток" in r["title"] for r in F.recommendations(history, future, .21))


def test_missing_values_do_not_generate_recommendations():
    history, future = scenario()
    future.update(rain_sum=[None, 0], showers_sum=[30, 0], temperature_2m_min=[None, 3],
                  wind_gusts_10m_max=[None, 2])
    assert F.recommendations(history, future, None) == []


def test_history_only_informs_recommendations_and_is_not_drawn_as_forecast(monkeypatch):
    clock = datetime(2025, 3, 1, tzinfo=F.TIMEZONE)
    dates = pd.date_range("2025-02-15", periods=21, freq="D")
    hours = pd.date_range(dates[0], periods=21 * 24, freq="h")
    hourly = {"time": hours.strftime("%Y-%m-%dT%H:%M").tolist(),
              "temperature_2m": [3] * len(hours), "snow_depth": [.18] * len(hours)}
    daily = {"time": dates.strftime("%Y-%m-%d").tolist(), "snowfall_sum": [2] * len(dates),
             "rain_sum": [0] * len(dates), "showers_sum": [0] * len(dates),
             "temperature_2m_max": [6] * len(dates), "temperature_2m_min": [1] * len(dates),
             "wind_gusts_10m_max": [2] * len(dates)}
    class Response:
        def raise_for_status(self):
            pass

        def json(self):
            return {"hourly": hourly, "daily": daily}

    def get(url, *, params, timeout):
        assert params["past_days"] == 14
        return Response()

    monkeypatch.setattr(F.time, "time", clock.timestamp)
    monkeypatch.setattr(F, "_cache", {})
    monkeypatch.setattr(F.requests, "get", get)
    result = F.get_forecast()
    assert result["daily"]["time"][0] == "2025-03-01"
    assert len(result["daily"]["time"]) == 7 and len(result["hourly"]["time"]) == 168
    assert "18 см" in result["recommendations"][0]["evidence"]
