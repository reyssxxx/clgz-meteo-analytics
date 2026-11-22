"""Посетитель работает только со своим набором из открытых станций, сотрудник — с данными заповедника."""
from fastapi.testclient import TestClient

from meteo import api

CODE = "USW00094728"


def client(monkeypatch=None) -> TestClient:
    return TestClient(api.app)


def test_guest_has_own_empty_workspace_and_no_access_to_reserve_data():
    c = client()
    ws = c.get("/api/workspace").json()
    assert ws["employee"] is False and ws["ready"] is False and ws["dataset"].startswith("guest-")
    assert c.get("/api/meta", params={"ds": "clgz"}).status_code == 403
    assert c.get("/api/meta", params={"ds": ws["dataset"]}).status_code == 404
    assert c.post("/api/datasets/clgz/files").status_code == 403
    assert c.get("/api/recovery/saved", params={"ds": ws["dataset"]}).status_code == 403


def test_guest_adds_public_station_and_gets_analytics():
    c = client()
    ds = c.get("/api/workspace").json()["dataset"]
    assert c.post(f"/api/public-stations/{CODE}/add").status_code == 200
    assert c.get("/api/workspace").json()["ready"] is True
    meta = c.get("/api/meta", params={"ds": ds})
    assert meta.status_code == 200 and meta.json()["stations"]
    assert c.get("/api/overview", params={"ds": ds, "var": "t_max"}).status_code == 200
    assert [d["id"] for d in c.get("/api/datasets").json()] == [ds]
    assert c.delete(f"/api/public-stations/{CODE}").status_code == 200
    assert c.get("/api/workspace").json()["ready"] is False


def test_guests_do_not_see_each_other():
    a, b = client(), client()
    ds_a = a.get("/api/workspace").json()["dataset"]
    b.get("/api/workspace")
    a.post(f"/api/public-stations/{CODE}/add")
    assert b.get("/api/meta", params={"ds": ds_a}).status_code == 403
    a.delete(f"/api/public-stations/{CODE}")


def test_data_request_is_public_and_saved(tmp_path, monkeypatch):
    from meteo import library
    monkeypatch.setattr(library, "REQUESTS_DB", tmp_path / "r.sqlite3")
    c = client()
    assert c.post("/data-request", data={"email": "a@b.ru", "description": "станция X"}).json() == {"ok": True}
    assert c.post("/data-request", data={"email": "bad", "description": "x"}).status_code == 400
    assert c.get("/data-request", follow_redirects=False).headers["location"] == "/meteo#data"
