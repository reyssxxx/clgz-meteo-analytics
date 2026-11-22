"""HTTP API + раздача веб-интерфейса.

    uv run uvicorn meteo.api:app --port 8000      →  http://localhost:8000

Данные держатся в памяти процесса (наборы — загруженные файлы). При старте подгружаются демо-наборы.
"""
from __future__ import annotations

import threading
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import quote

import pandas as pd
import plotly
from fastapi import FastAPI, File, HTTPException, Query, UploadFile
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from . import analytics as A
from . import forecast as FC
from . import tables as T
from . import views as V
from .loader import LoadReport, combine, load
from .report import build_sheets, html_report, to_excel
from .schema import VARIABLES

ROOT = Path(__file__).resolve().parent.parent
WEB = ROOT / "web"
DATA = ROOT / "data"
PLOTLY_JS = Path(plotly.__file__).parent / "package_data" / "plotly.min.js"

# Данные кейса: data/clgz/<станция>/<префикс>_ГГГГ.csv. Станция определяется по префиксу файла.
CASE_ID = "clgz"
CASE_NAME = "Центрально-Лесной заповедник, урочище «Красное»"
CASE_DIR = DATA / "clgz"


@dataclass
class Dataset:
    id: str
    name: str
    files: dict[str, bytes]
    overrides: dict[str, dict] = field(default_factory=dict)
    raw: pd.DataFrame | None = None
    reports: dict[str, LoadReport] = field(default_factory=dict)
    cache: dict = field(default_factory=dict)
    lock: threading.RLock = field(default_factory=threading.RLock)  # повторно входимая: daily() → dailies() → station()

    def build(self) -> None:
        frames, reports = [], {}
        for name, data in self.files.items():
            o = self.overrides.get(name)
            df, rep = load(data, name, **({"mapping": o["mapping"], "time_cols": o["time"],
                                          "station_col": o.get("station")} if o else {}))
            frames.append(df)
            reports[name] = rep
        self.raw = combine(frames)
        self.reports = reports
        self.cache = {}

    def station(self, station: str | None, clean: bool = True):
        """(сырые данные станции, суточные, проблемы, срочные ли)"""
        names = sorted(self.raw["station"].unique())
        st = station if station in names else names[0]
        key = ("st", st, clean)
        with self.lock:
            if key not in self.cache:
                data = A.for_station(self.raw, st)
                issues = A.quality_issues(data)
                # в расчёт идут значения после официальных флагов (применены при загрузке);
                # наши дополнительные проверки (выбросы, «залипание») — только подсказки, данные не меняют
                src = data
                self.cache[key] = (data, A.to_daily(src), issues, A.is_subdaily(data))
        return self.cache[key]

    def dailies(self, clean: bool = True) -> dict[str, pd.DataFrame]:
        return {s: self.station(s, clean)[1] for s in sorted(self.raw["station"].unique())}

    def daily(self, station: str | None, clean: bool, fill: bool):
        data, daily, issues, sub = self.station(station, clean)
        fill_report = None
        # пропуски по заданию не восстанавливаются — заполнение по соседней станции отключено
        if False:
            key = ("fill", station, clean)
            with self.lock:
                if key not in self.cache:
                    self.cache[key] = A.fill_gaps(self.dailies(clean), station)
            daily, fill_report = self.cache[key]
        return data, daily, issues, sub, fill_report


STORE: dict[str, Dataset] = {}


def _load_demos() -> None:
    """Загружает данные кейса при старте (имя функции сохранено для совместимости)."""
    files = sorted(CASE_DIR.glob("*/*.csv"))
    if files and CASE_ID not in STORE:
        ds = Dataset(CASE_ID, CASE_NAME, {f"{p.parent.name}/{p.name}": p.read_bytes() for p in files})
        ds.build()
        STORE[CASE_ID] = ds


app = FastAPI(title="Метеоаналитика")


@app.middleware("http")
async def _no_cache_for_ui(request, call_next):
    """Файлы интерфейса не кэшируются: после обновления сервиса браузер сразу берёт новую версию."""
    resp = await call_next(request)
    if not request.url.path.startswith(("/api/", "/vendor/")):
        resp.headers["Cache-Control"] = "no-cache"
    return resp


@app.on_event("startup")
def _startup() -> None:
    _load_demos()


def get(ds: str) -> Dataset:
    if not STORE:
        _load_demos()
    if ds not in STORE:
        raise HTTPException(404, "Набор данных не найден — загрузите файлы заново.")
    return STORE[ds]


def base_of(daily: pd.DataFrame, base: str | None) -> tuple[str, str]:
    """Годы для «среднего за выбранный период». 'all' (по умолчанию) — все годы станции."""
    years = sorted(daily.index.year.unique())
    if not base or base == "all":
        return str(years[0]), str(years[-1])
    try:
        b0, b1 = (int(x) for x in base.split("-"))
    except ValueError:
        return str(years[0]), str(years[-1])
    b0, b1 = max(b0, years[0]), min(b1, years[-1])
    if b0 > b1:
        b0, b1 = years[0], years[-1]
    return str(b0), str(b1)


def ctx(ds, station, start, end, base, clean, fill):
    d = get(ds)
    data, daily, issues, sub, fill_report = d.daily(station, clean, fill)
    if daily.empty:
        raise HTTPException(400, "У станции нет данных.")
    s = pd.Timestamp(start) if start else daily.index.min()
    e = pd.Timestamp(end) if end else daily.index.max()
    s, e = max(s, daily.index.min()), min(e, daily.index.max())
    if s > e:
        raise HTTPException(400, "Период вне диапазона данных.")
    return d, data, daily, issues, sub, fill_report, s, e, base_of(daily, base)


Common = dict(
    ds=Query(CASE_ID), station=Query(None), start=Query(None), end=Query(None),
    base=Query("all"), clean=Query(True), fill=Query(False),
)


# ------------------------------------------------------------------ наборы данных

@app.get("/api/datasets")
def datasets():
    if not STORE:
        _load_demos()
    return [{"id": d.id, "name": d.name, "demo": d.id.startswith("demo")} for d in STORE.values()]


@app.post("/api/datasets")
async def upload(files: list[UploadFile] = File(...)):
    content = {f.filename: await f.read() for f in files}
    ds = Dataset(uuid.uuid4().hex[:10], ", ".join(content)[:80], content)
    try:
        ds.build()
    except Exception as e:  # noqa: BLE001
        raise HTTPException(400, f"Не удалось прочитать файлы: {e}")
    STORE[ds.id] = ds
    return {"id": ds.id, "name": ds.name}


UPLOADS = DATA / "clgz" / "uploads"
ALLOWED = {".csv", ".txt", ".xlsx", ".xls"}


@app.post("/api/datasets/{ds}/files")
async def add_files(ds: str, files: list[UploadFile] = File(...)):
    """Новый файл с метеостанции в том же формате дописывается к данным без доработки кода:
    kr1_2026.csv → в ряд «Кр1 (поляна)», ame60_2022.csv → «АМЕ-60», файл другой станции — новая станция.
    Файлы набора заповедника сохраняются на диск и подхватываются при следующем запуске."""
    d = get(ds)
    added, old_files = [], dict(d.files)
    for f in files:
        name = Path(f.filename or "file.csv").name
        if Path(name).suffix.lower() not in ALLOWED:
            raise HTTPException(400, f"{name}: поддерживаются CSV, TXT и Excel.")
        d.files[f"uploads/{name}"] = await f.read()
        added.append(name)
    try:
        d.build()
    except Exception as e:  # noqa: BLE001
        d.files = old_files
        d.build()
        raise HTTPException(400, f"Не удалось прочитать файл: {e}")
    if ds == CASE_ID:
        UPLOADS.mkdir(parents=True, exist_ok=True)
        for name in added:
            (UPLOADS / name).write_bytes(d.files[f"uploads/{name}"])
    from .loader import station_from_filename
    return {"added": added, "stations": sorted({station_from_filename(n) for n in added})}


class Mapping(BaseModel):
    file: str
    mapping: dict[str, str]
    time: list[str]
    station: str | None = None


@app.post("/api/datasets/{ds}/mapping")
def set_mapping(ds: str, m: Mapping):
    d = get(ds)
    if m.file not in d.files:
        raise HTTPException(404, "Файл не найден в наборе.")
    old = dict(d.overrides)
    d.overrides[m.file] = {"mapping": m.mapping, "time": m.time, "station": m.station}
    try:
        d.build()
    except Exception as e:  # noqa: BLE001
        d.overrides = old
        d.build()
        raise HTTPException(400, f"С таким сопоставлением файл не читается: {e}")
    return {"ok": True}


@app.get("/api/meta")
def meta(ds: str = CASE_ID, clean: bool = True):
    d = get(ds)
    out = V.meta(d.raw, d.dailies(clean))
    out["dataset"] = {"id": d.id, "name": d.name}
    out["all_variables"] = [V.var_meta(v) for v in VARIABLES]
    return out


# ------------------------------------------------------------------ экраны

@app.get("/api/overview")
def overview(var: str = "t_air", ds: str = Common["ds"], station: str | None = Common["station"],
             start: str | None = Common["start"], end: str | None = Common["end"], base: str = Common["base"],
             clean: bool = Common["clean"], fill: bool = Common["fill"]):
    d, data, daily, issues, sub, fr, s, e, b = ctx(ds, station, start, end, base, clean, fill)
    # параметр, которого у станции нет (например, давление у АМЕ-60), — подставляем первый с данными
    if var not in daily or daily[var].notna().sum() == 0:
        have = [v for v in daily.columns if daily[v].notna().any()]
        var = "t_air" if "t_air" in have else have[0]
    return {"base": b, "years": V.years_chart(daily, var, s, e, b), "heatmap": V.anomaly_heatmap(daily, var, s, e, b),
            "ranking": V.ranking(daily, var, s, e, b), "facts": V.facts(daily, var, s, e, b, issues)}


@app.get("/api/series")
def series(var: str = "t_air", freq: str = "MS", ds: str = Common["ds"], station: str | None = Common["station"],
           start: str | None = Common["start"], end: str | None = Common["end"], base: str = Common["base"],
           clean: bool = Common["clean"], fill: bool = Common["fill"]):
    d, data, daily, issues, sub, fr, s, e, b = ctx(ds, station, start, end, base, clean, fill)
    return V.series(daily, var, freq, s, e, b)


@app.get("/api/trends")
def trends(var: str = "t_air", month: int | None = None, season: int | None = None, ds: str = Common["ds"],
           station: str | None = Common["station"], start: str | None = Common["start"], end: str | None = Common["end"],
           base: str = Common["base"], clean: bool = Common["clean"], fill: bool = Common["fill"]):
    d, data, daily, issues, sub, fr, s, e, b = ctx(ds, station, start, end, base, clean, fill)
    return V.trends(daily, var, s, e, b, month, season)


@app.get("/api/compare")
def compare(a0: str, a1: str, b0: str, b1: str, var: str = "t_air", ds: str = Common["ds"],
            station: str | None = Common["station"], base: str = Common["base"], clean: bool = Common["clean"],
            fill: bool = Common["fill"]):
    d, data, daily, issues, sub, fr, s, e, b = ctx(ds, station, None, None, base, clean, fill)
    return V.compare(daily, var, (a0, a1), (b0, b1))


@app.get("/api/ranking")
def ranking(var: str = "t_air", ds: str = Common["ds"], station: str | None = Common["station"],
            start: str | None = Common["start"], end: str | None = Common["end"], base: str = Common["base"],
            clean: bool = Common["clean"], fill: bool = Common["fill"]):
    d, data, daily, issues, sub, fr, s, e, b = ctx(ds, station, start, end, base, clean, fill)
    return V.ranking(daily, var, s, e, b)


@app.get("/api/stations")
def stations(var: str = "t_air", freq: str = "MS", ref: str | None = None, ds: str = Common["ds"],
             station: str | None = Common["station"], start: str | None = Common["start"],
             end: str | None = Common["end"], base: str = Common["base"], clean: bool = Common["clean"],
             fill: bool = Common["fill"]):
    d, data, daily, issues, sub, fr, s, e, b = ctx(ds, station, start, end, base, clean, fill)
    st = station if station in d.raw["station"].unique() else sorted(d.raw["station"].unique())[0]
    if fill and fr is None:
        _, _, _, _, fr = d.daily(st, clean, True)
    return V.stations_view(d.dailies(clean), st, var, freq, s, e, ref, fr)


@app.get("/api/pair")
def pair(var: str = "t_air", a: str | None = None, b: str | None = None, ds: str = Common["ds"],
         station: str | None = Common["station"], start: str | None = Common["start"], end: str | None = Common["end"],
         base: str = Common["base"], clean: bool = Common["clean"], fill: bool = Common["fill"]):
    """Две станции за один период. По умолчанию — Кр1 (поляна) и Кр2 (поле)."""
    d = get(ds)
    names = sorted(d.raw["station"].unique())
    a = a if a in names else ("Кр1 (поляна)" if "Кр1 (поляна)" in names else names[0])
    b = b if b in names and b != a else ("Кр2 (поле)" if "Кр2 (поле)" in names and a != "Кр2 (поле)" else
                                          next((n for n in names if n != a), a))
    ra, da, _, _ = d.station(a, clean)
    rb, db, _, _ = d.station(b, clean)
    s = pd.Timestamp(start) if start else max(da.index.min(), db.index.min())
    e = pd.Timestamp(end) if end else min(da.index.max(), db.index.max())
    out = V.pair(ra, da, rb, db, a, b, var, s, e)
    out["stations"] = names
    return out


@app.get("/api/forecast")
def forecast(ds: str = Common["ds"], station: str | None = Common["station"]):
    """Прогноз на 7 дней (Open-Meteo) + для контекста средняя температура тех же дат по станции."""
    try:
        f = FC.get_forecast(7)
    except Exception as e:  # noqa: BLE001
        raise HTTPException(503, f"Сервис прогноза недоступен: {e}")
    d = get(ds)
    names = list(d.raw["station"].unique())
    st = station if station in names else ("АМЕ-60" if "АМЕ-60" in names else names[0])
    _, daily, _, _ = d.station(st, True)
    ctx_rows = []
    if "t_air" in daily:
        s = daily["t_air"].dropna()
        for day in f["daily"]["time"]:
            t = pd.Timestamp(day)
            same = s[(s.index.month == t.month) & (s.index.day == t.day)]
            ctx_rows.append({"date": day, "mean": float(same.mean()) if len(same) else None, "years": int(len(same))})
    return V.js({**f, "context": {"station": st, "days": ctx_rows}})


@app.get("/api/agro")
def agro(ds: str = Common["ds"], station: str | None = Common["station"], start: str | None = Common["start"],
         end: str | None = Common["end"], base: str = Common["base"], clean: bool = Common["clean"],
         fill: bool = Common["fill"]):
    d, data, daily, issues, sub, fr, s, e, b = ctx(ds, station, start, end, base, clean, fill)
    out = V.agro(daily, s, e)
    out["wind"] = V.wind(data, daily, sub, s, e)
    return out


@app.get("/api/quality")
def quality(ds: str = Common["ds"], station: str | None = Common["station"], clean: bool = Common["clean"]):
    d = get(ds)
    data, daily, issues, sub = d.station(station, clean)
    return V.quality(data, daily, issues, d.reports)


@app.get("/api/table")
def table(mode: str = "daily", ds: str = Common["ds"], station: str | None = Common["station"],
          start: str | None = Common["start"], end: str | None = Common["end"], base: str = Common["base"],
          clean: bool = Common["clean"], fill: bool = Common["fill"]):
    """mode=daily — суточная таблица за месяц (по start); иначе — декадная таблица за период."""
    d, data, daily, issues, sub, fr, s, e, b = ctx(ds, station, start, end, base, clean, fill)
    if mode == "daily":
        return V.js(T.daily_table(data, daily, pd.Timestamp(start) if start else s))
    return V.js(T.decade_table(daily, s, e))


@app.get("/api/table.xlsx")
def table_xlsx(mode: str = "daily", ds: str = Common["ds"], station: str | None = Common["station"],
               start: str | None = Common["start"], end: str | None = Common["end"], base: str = Common["base"],
               clean: bool = Common["clean"], fill: bool = Common["fill"]):
    d, data, daily, issues, sub, fr, s, e, b = ctx(ds, station, start, end, base, clean, fill)
    st = station or sorted(d.raw["station"].unique())[0]
    tab = T.daily_table(data, daily, pd.Timestamp(start) if start else s) if mode == "daily" else T.decade_table(daily, s, e)
    name = f"{st} {tab['title']}".replace("—", "-").replace(".", "-")
    return _download(T.to_excel(tab, st), f"Таблица {name}.xlsx",
                     "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")


# ------------------------------------------------------------------ отчёты

def _download(content: bytes | str, filename: str, media: str) -> Response:
    return Response(content, media_type=media,
                    headers={"Content-Disposition": f"attachment; filename*=UTF-8''{quote(filename)}"})


@app.get("/api/report.html")
def report_html(ds: str = Common["ds"], station: str | None = Common["station"], start: str | None = Common["start"],
                end: str | None = Common["end"], base: str = Common["base"], clean: bool = Common["clean"],
                fill: bool = Common["fill"]):
    d, data, daily, issues, sub, fr, s, e, b = ctx(ds, station, start, end, base, clean, fill)
    st = station or sorted(d.raw["station"].unique())[0]
    return _download(html_report(daily, st, s, e, b, issues), f"Метеоотчёт {st} {s:%Y%m%d}-{e:%Y%m%d}.html",
                     "text/html; charset=utf-8")


@app.get("/api/report.xlsx")
def report_xlsx(ds: str = Common["ds"], station: str | None = Common["station"], start: str | None = Common["start"],
                end: str | None = Common["end"], base: str = Common["base"], clean: bool = Common["clean"],
                fill: bool = Common["fill"]):
    d, data, daily, issues, sub, fr, s, e, b = ctx(ds, station, start, end, base, clean, fill)
    st = station or sorted(d.raw["station"].unique())[0]
    return _download(to_excel(build_sheets(daily, s, e, b, issues)), f"Метеоданные {st} {s:%Y%m%d}-{e:%Y%m%d}.xlsx",
                     "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")


# ------------------------------------------------------------------ статика

@app.exception_handler(HTTPException)
def _http_error(_, exc: HTTPException):
    return JSONResponse({"error": exc.detail}, status_code=exc.status_code)


@app.get("/vendor/plotly.min.js")
def plotly_js():
    return FileResponse(PLOTLY_JS, media_type="text/javascript", headers={"Cache-Control": "max-age=86400"})


@app.get("/", response_class=HTMLResponse)
def index():
    return (WEB / "index.html").read_text(encoding="utf-8")


app.mount("/", StaticFiles(directory=WEB), name="web")
