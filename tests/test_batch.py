from pathlib import Path

from meteo.batch import run

DATA = Path(__file__).parent / "data"


def test_batch_writes_reports_for_each_station(tmp_path):
    files = run([str(DATA / "sample_stations.csv")], str(tmp_path), start="2020-01-01", end="2024-12-31",
                log=lambda *_: None)
    names = {f.name for f in files}
    assert {"Фёдоровское.xlsx", "Фёдоровское.html", "Нелидово.html", "Андреаполь.xlsx", "summary.md"} <= names
    html = (tmp_path / "Фёдоровское.html").read_text(encoding="utf-8")
    assert "Выводы" in html and "plotly" in html.lower() and "Climate" not in html
    assert "Средняя температура" in (tmp_path / "summary.md").read_text(encoding="utf-8")
