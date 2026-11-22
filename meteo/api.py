"""HTTP API + раздача веб-интерфейса.

    uv run uvicorn meteo.api:app --port 8000      →  http://localhost:8000

Данные держатся в памяти процесса (наборы — загруженные файлы). При старте подгружаются демо-наборы.
"""
from __future__ import annotations

import threading
import uuid
from io import BytesIO
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Literal
from urllib.parse import quote

import pandas as pd
import plotly
from openpyxl import Workbook
from fastapi import APIRouter, Depends, FastAPI, File, HTTPException, Query, Request, UploadFile
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field, FiniteFloat

from . import analytics as A
from . import forecast as FC
from . import library
from . import quality as Q
from . import recovery as R
from . import tables as T
from . import views as V
from .loader import LoadReport, combine, load, station_from_filename
from .report import build_sheets, html_report, to_excel
from .schema import STATION_PREFIXES, VARIABLES

ROOT = Path(__file__).resolve().parent.parent
WEB = ROOT / "web"
DATA = ROOT / "data"
PLOTLY_JS = Path(plotly.__file__).parent / "package_data" / "plotly.min.js"

# Данные кейса: data/clgz/<станция>/<префикс>_ГГГГ.csv. Станция определяется по префиксу файла.
CASE_ID = "clgz"
CASE_NAME = "Центрально-Лесной заповедник, урочище «Красное»"
CASE_DIR = DATA / "clgz"
UPLOADS = CASE_DIR / "uploads"
RECOVERY_STORE = DATA / ".recovery" / "values.sqlite3"
ALLOWED = {".csv", ".txt", ".xlsx"}


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
                subdaily = st in STATION_PREFIXES.values() or A.is_subdaily(data)
                self.cache[key] = (data, A.to_daily(data, st), issues, subdaily)
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
UPDATE_LOCK = threading.Lock()


def _load_demos() -> None:
    """Загружает данные кейса при старте (имя функции сохранено для совместимости)."""
    files = sorted(p for p in CASE_DIR.glob("*/*.csv") if p.parent != UPLOADS)
    files.extend(sorted(p for p in UPLOADS.iterdir() if p.is_file() and p.suffix.lower() in ALLOWED)
                 if UPLOADS.is_dir() else [])
    if files and CASE_ID not in STORE:
        ds = Dataset(CASE_ID, CASE_NAME, {f"{p.parent.name}/{p.name}": p.read_bytes() for p in files})
        ds.build()
        STORE[CASE_ID] = ds


app = FastAPI(title="Библиотека Центрально-Лесного заповедника")
app.include_router(library.router)
meteo_router = APIRouter(prefix="/api", dependencies=[Depends(library.require_employee)])


@app.middleware("http")
async def _no_cache_for_ui(request, call_next):
    """Файлы интерфейса не кэшируются: после обновления сервиса браузер сразу берёт новую версию."""
    resp = await call_next(request)
    if request.url.path.startswith("/api/"):
        resp.headers["Cache-Control"] = "no-store"
    elif not request.url.path.startswith("/vendor/") and "Cache-Control" not in resp.headers:
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

@meteo_router.get("/datasets")
def datasets():
    if not STORE:
        _load_demos()
    return [{"id": d.id, "name": d.name, "demo": d.id.startswith("demo")} for d in STORE.values()]


@meteo_router.post("/datasets")
async def upload(files: list[UploadFile] = File(...)):
    content = {f.filename: await f.read() for f in files}
    ds = Dataset(uuid.uuid4().hex[:10], ", ".join(content)[:80], content)
    try:
        ds.build()
    except Exception as e:  # noqa: BLE001
        raise HTTPException(400, f"Не удалось прочитать файлы: {e}")
    STORE[ds.id] = ds
    return {"id": ds.id, "name": ds.name}


def _save_uploads(content: dict[str, bytes]) -> None:
    UPLOADS.mkdir(parents=True, exist_ok=True)
    with TemporaryDirectory(dir=UPLOADS) as tmp:
        staged = Path(tmp)
        backups = staged / "previous"
        backups.mkdir()
        for name, data in content.items():
            (staged / name).write_bytes(data)
            current = UPLOADS / name
            if current.is_file():
                (backups / name).write_bytes(current.read_bytes())
        replaced = []
        try:
            for name in content:
                (staged / name).replace(UPLOADS / name)
                replaced.append(name)
        except OSError:
            for name in reversed(replaced):
                backup = backups / name
                if backup.is_file():
                    backup.replace(UPLOADS / name)
                else:
                    (UPLOADS / name).unlink()
            raise


@meteo_router.post("/datasets/{ds}/files")
def add_files(ds: str, files: list[UploadFile] = File(...)):
    """Новый файл с метеостанции в том же формате дописывается к данным без доработки кода:
    kr1_2026.csv → в ряд «Кр1 (поляна)», ame60_2022.csv → «АМЕ-60», файл другой станции — новая станция.
    Файлы набора заповедника сохраняются на диск и подхватываются при следующем запуске."""
    content = {}
    for f in files:
        name = Path(f.filename or "file.csv").name
        if Path(name).suffix.lower() not in ALLOWED:
            raise HTTPException(400, f"{name}: поддерживаются CSV, TXT и XLSX.")
        if name in content:
            raise HTTPException(400, f"{name}: имя файла повторяется в загрузке.")
        content[name] = f.file.read()
    uploads = {f"uploads/{name}": data for name, data in content.items()}
    with UPDATE_LOCK:
        d = get(ds)
        updated = Dataset(d.id, d.name, d.files | uploads, dict(d.overrides))
        try:
            updated.build()
            for name in uploads:
                if not updated.reports[name].rows_out:
                    raise ValueError(f"{Path(name).name}: нет корректных дат наблюдений.")
        except Exception as e:  # noqa: BLE001
            raise HTTPException(400, f"Не удалось прочитать файл: {e}") from e
        if ds == CASE_ID:
            try:
                _save_uploads(content)
            except OSError as e:
                raise HTTPException(500, "Не удалось сохранить файлы на диск.") from e
        STORE[ds] = updated
    return {"added": list(content), "stations": sorted({station_from_filename(n) for n in content})}


class Mapping(BaseModel):
    file: str
    mapping: dict[str, str]
    time: list[str]
    station: str | None = None


@meteo_router.post("/datasets/{ds}/mapping")
def set_mapping(ds: str, m: Mapping):
    with UPDATE_LOCK:
        d = get(ds)
        if m.file not in d.files:
            raise HTTPException(404, "Файл не найден в наборе.")
        overrides = d.overrides | {m.file: {"mapping": m.mapping, "time": m.time, "station": m.station}}
        updated = Dataset(d.id, d.name, d.files, overrides)
        try:
            updated.build()
        except Exception as e:  # noqa: BLE001
            raise HTTPException(400, f"С таким сопоставлением файл не читается: {e}") from e
        STORE[ds] = updated
    return {"ok": True}


@meteo_router.get("/meta")
def meta(ds: str = CASE_ID, clean: bool = True):
    d = get(ds)
    out = V.meta(d.raw, d.dailies(clean))
    out["dataset"] = {"id": d.id, "name": d.name}
    out["all_variables"] = [V.var_meta(v) for v in VARIABLES]
    return out


# ------------------------------------------------------------------ экраны

@meteo_router.get("/overview")
def overview(var: str = "t_air", ds: str = Common["ds"], station: str | None = Common["station"],
             start: str | None = Common["start"], end: str | None = Common["end"], base: str = Common["base"],
             clean: bool = Common["clean"], fill: bool = Common["fill"]):
    d, data, daily, issues, sub, fr, s, e, b = ctx(ds, station, start, end, base, clean, fill)
    # параметр, которого у станции нет (например, давление у АМЕ-60), — подставляем первый с данными
    if var not in daily or daily[var].notna().sum() == 0:
        have = [v for v in daily.columns if daily[v].notna().any()]
        have = [v for v in have if v in VARIABLES]
        if not have:
            raise HTTPException(400, "Нет ни одного параметра с полными сутками.")
        var = "t_air" if "t_air" in have else have[0]
    return {"base": b, "years": V.years_chart(daily, var, s, e, b), "heatmap": V.anomaly_heatmap(daily, var, s, e, b),
            "ranking": V.ranking(daily, var, s, e, b), "facts": V.facts(daily, var, s, e, b, issues)}


@meteo_router.get("/series")
def series(var: str = "t_air", freq: str = "MS", ds: str = Common["ds"], station: str | None = Common["station"],
           start: str | None = Common["start"], end: str | None = Common["end"], base: str = Common["base"],
           clean: bool = Common["clean"], fill: bool = Common["fill"]):
    d, data, daily, issues, sub, fr, s, e, b = ctx(ds, station, start, end, base, clean, fill)
    return V.series(daily, var, freq, s, e, b)


@meteo_router.get("/trends")
def trends(var: str = "t_air", month: int | None = None, season: int | None = None, ds: str = Common["ds"],
           station: str | None = Common["station"], start: str | None = Common["start"], end: str | None = Common["end"],
           base: str = Common["base"], clean: bool = Common["clean"], fill: bool = Common["fill"]):
    d, data, daily, issues, sub, fr, s, e, b = ctx(ds, station, start, end, base, clean, fill)
    return V.trends(daily, var, s, e, b, month, season)


@meteo_router.get("/compare")
def compare(a0: str, a1: str, b0: str, b1: str, var: str = "t_air", ds: str = Common["ds"],
            station: str | None = Common["station"], base: str = Common["base"], clean: bool = Common["clean"],
            fill: bool = Common["fill"]):
    d, data, daily, issues, sub, fr, s, e, b = ctx(ds, station, None, None, base, clean, fill)
    return V.compare(daily, var, (a0, a1), (b0, b1))


@meteo_router.get("/ranking")
def ranking(var: str = "t_air", ds: str = Common["ds"], station: str | None = Common["station"],
            start: str | None = Common["start"], end: str | None = Common["end"], base: str = Common["base"],
            clean: bool = Common["clean"], fill: bool = Common["fill"]):
    d, data, daily, issues, sub, fr, s, e, b = ctx(ds, station, start, end, base, clean, fill)
    return V.ranking(daily, var, s, e, b)


@meteo_router.get("/stations")
def stations(var: str = "t_air", freq: str = "MS", ref: str | None = None, ds: str = Common["ds"],
             station: str | None = Common["station"], start: str | None = Common["start"],
             end: str | None = Common["end"], base: str = Common["base"], clean: bool = Common["clean"],
             fill: bool = Common["fill"]):
    d, data, daily, issues, sub, fr, s, e, b = ctx(ds, station, start, end, base, clean, fill)
    st = station if station in d.raw["station"].unique() else sorted(d.raw["station"].unique())[0]
    if fill and fr is None:
        _, _, _, _, fr = d.daily(st, clean, True)
    return V.stations_view(d.dailies(clean), st, var, freq, s, e, ref, fr)


@meteo_router.get("/pair")
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
    if not out["common_days"] and var in da and var in db:
        common = da[var].dropna().index.intersection(db[var].dropna().index)
        if len(common):
            out["latest_common_day"] = common.max().strftime("%Y-%m-%d")
    out["stations"] = names
    return out


@meteo_router.get("/forecast")
def forecast(ds: str = Common["ds"], station: str | None = Common["station"], base: str = Common["base"]):
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
        dates = pd.DatetimeIndex(f["daily"]["time"])
        b = base_of(daily, base)
        reference = A.doy_climatology(daily, "t_air", b).reindex(A.calendar_day(dates))
        ctx_rows = [{"date": date, "mean": mean, "years": years}
                    for date, mean, years in zip(f["daily"]["time"], reference["norm"], reference["years"])]
    return V.js({**f, "context": {"station": st, "days": ctx_rows}})


@meteo_router.get("/agro")
def agro(ds: str = Common["ds"], station: str | None = Common["station"], start: str | None = Common["start"],
         end: str | None = Common["end"], base: str = Common["base"], clean: bool = Common["clean"],
         fill: bool = Common["fill"]):
    d, data, daily, issues, sub, fr, s, e, b = ctx(ds, station, start, end, base, clean, fill)
    out = V.agro(daily, s, e)
    out["wind"] = V.wind(data, daily, sub, s, e)
    return out


@meteo_router.get("/quality")
def quality(ds: str = Common["ds"], station: str | None = Common["station"], clean: bool = Common["clean"]):
    d = get(ds)
    data, daily, issues, sub = d.station(station, clean)
    return V.quality(data, daily, issues, d.reports)


@meteo_router.get("/quality/selection")
def quality_selection(var: str = "t_air", mode: str = "", ds: str = Common["ds"], station: str | None = Common["station"],
                      start: str | None = Common["start"], end: str | None = Common["end"],
                      base: str = Common["base"], clean: bool = Common["clean"], fill: bool = Common["fill"]):
    d, data, daily, issues, sub, fr, s, e, b = ctx(ds, station, start, end, base, clean, fill)
    st = station if station in d.raw["station"].unique() else min(d.raw["station"])
    if var not in VARIABLES or var not in daily:
        raise HTTPException(400, "Параметр недоступен для этой станции.")
    exclusions = Q.exclusions_for(d.reports.values(), st)
    source = Q.observation_var(data, var)
    selected = Q.period_records(exclusions[exclusions["Параметр"] == source], s, e)
    diagnostics = Q.period_records(issues[issues["Параметр"] == source], s, e)
    important = Q.important_events(diagnostics, selected)
    if mode == "daily":
        s = (pd.Timestamp(start) if start else s).to_period("M").to_timestamp()
        e = s + pd.offsets.MonthEnd(0)
        monthly = T.monthly_rows(T.daily_table(data, daily, s, st), s)
    elif mode == "decades":
        monthly = T.monthly_rows(T.decade_table(daily, s, e), s)
    else:
        monthly = A.aggregate(daily[[var]].loc[s:e], "MS")
    if var not in monthly:
        monthly[var] = float("nan")
    audit = Q.audit_months(data, daily, monthly, exclusions, var, s, e)
    return V.js({"audit": audit, "important": important.head(50), "important_total": len(important),
                 "exclusions": selected.head(20), "excluded_at_import": len(selected), "var": V.var_meta(var),
                 "source": V.var_meta(source)})


@meteo_router.get("/recovery/preview")
def recovery_preview(var: str = "t_air", ds: str = Common["ds"], station: str | None = Common["station"],
                     start: date | None = None, end: date | None = None, clean: bool = Common["clean"]):
    d = get(ds)
    names = sorted(d.raw["station"].unique())
    st = station or names[0]
    if st not in names:
        raise HTTPException(404, "Станция не найдена в наборе.")
    raw, daily, _, _ = d.station(st, clean)
    if daily.empty or var not in VARIABLES or var not in daily or not daily[var].notna().any():
        raise HTTPException(400, "Параметр недоступен для этой станции.")
    first = start or daily.index.min().date()
    last = end or daily.index.max().date()
    if first > last:
        raise HTTPException(400, "Дата начала должна быть не позже даты окончания.")
    return V.js(R.preview(raw, d.dailies(clean), st, var, first, last) | {"dataset": d.id})


class RecoveryEntry(BaseModel):
    date: date
    value: FiniteFloat


class RecoveryRequest(BaseModel):
    dataset: str = CASE_ID
    station: str
    parameter: str
    start: date
    end: date
    source: Literal["archive", "station"]
    resolution: Literal["day", "month"] = "day"
    donor: str = ""
    reference: str = Field(default="", max_length=500)
    entries: list[RecoveryEntry] = Field(default_factory=list, max_length=3660)
    preview_token: str = ""


def _recovery_daily(ds, station, var, start, end):
    d = get(ds)
    if start > end:
        raise HTTPException(400, "Дата начала должна быть не позже даты окончания.")
    if station not in d.raw["station"].unique():
        raise HTTPException(404, "Станция не найдена в наборе.")
    _, daily, _, _ = d.station(station)
    if var not in VARIABLES or var not in daily:
        raise HTTPException(400, "Параметр недоступен для этой станции.")
    return d, daily


@meteo_router.post("/recovery/proposal")
def recovery_proposal(request: RecoveryRequest, save: bool = False):
    r = request
    with UPDATE_LOCK:
        d, daily = _recovery_daily(r.dataset, r.station, r.parameter, r.start, r.end)
        skipped = []
        try:
            if r.source == "archive":
                if not r.entries or not r.reference.strip():
                    raise ValueError("Укажите значения и ссылку либо том и страницу Летописи.")
                rows = R.archive_proposal(daily, r.parameter, r.start, r.end,
                                          [(str(row.date), row.value) for row in r.entries],
                                          r.reference.strip(), r.resolution)
            else:
                if r.parameter != "t_air" or r.resolution != "day":
                    raise ValueError("Оценка по соседней станции доступна для суточной температуры воздуха.")
                if r.donor == r.station or r.donor not in d.raw["station"].unique():
                    raise ValueError("Выберите другую станцию из набора.")
                _, donor, _, _ = d.station(r.donor)
                if "t_air" not in donor:
                    raise ValueError("В выбранной станции нет температуры воздуха.")
                rows, skipped = R.station_proposal(daily["t_air"], donor["t_air"], r.start, r.end, r.donor)
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from exc
        token = R.proposal_token(d.id, r.station, r.parameter, rows)
        if save and token != r.preview_token:
            raise HTTPException(409, "Предпросмотр устарел. Проверьте предложенные значения ещё раз.")
        count = R.save(RECOVERY_STORE, d.id, r.station, r.parameter, rows) if save and rows else 0
    return {"rows": rows, "skipped": skipped, "saved": count, "preview_token": token}


@meteo_router.get("/recovery/saved")
def recovery_saved(station: str, start: date, end: date, ds: str = CASE_ID, var: str = "t_air",
                   resolution: Literal["day", "month"] = "day"):
    d, daily = _recovery_daily(ds, station, var, start, end)
    rows = R.with_observation_status(R.saved(RECOVERY_STORE, d.id, station, var, start, end), daily, var)
    official = R.observations(daily, var, resolution)
    first = pd.Timestamp(start)
    if resolution == "month":
        first = first.to_period("M").start_time
    clock = pd.date_range(first, end, freq="MS" if resolution == "month" else "D")
    return V.js({"rows": rows, "observations": {"dates": clock, "values": official.reindex(clock)}})


@meteo_router.get("/recovery/report.xlsx")
def recovery_xlsx(station: str, start: date, end: date, ds: str = CASE_ID, var: str = "t_air"):
    d, daily = _recovery_daily(ds, station, var, start, end)
    rows = R.with_observation_status(R.saved(RECOVERY_STORE, d.id, station, var, start, end), daily, var)
    if not rows:
        raise HTTPException(404, "За выбранный период нет сохранённых восстановленных значений.")
    records = pd.DataFrame([{
        "Дата": row["date"], "Шаг": "Месяц" if row["resolution"] == "month" else "Сутки",
        "Станция": station, "Параметр": VARIABLES[var].label, "Единица": VARIABLES[var].unit,
        "Значение": row["value"], "Метод": "Летопись" if row["method"] == "archive" else "Поправка по станции",
        "Источник": row["source"], "MAE": row["mae"], "90% ошибок до": row["error90"],
        "Дней проверки": row["validation_days"],
        "Статус": "Есть официальное значение" if row["has_observation"] else "Отдельный ряд",
    } for row in rows])
    scope = pd.DataFrame({"Поле": ["Станция", "Показатель", "Начало", "Конец"],
                          "Значение": [station, VARIABLES[var].label, str(start), str(end)]})
    workbook = Workbook()
    workbook.remove(workbook.active)
    Q.write_review_sheets(workbook, {"Восстановленные данные": records, "Параметры выборки": scope})
    output = BytesIO()
    workbook.save(output)
    return _download(output.getvalue(), "Восстановленные данные.xlsx",
                     "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")


@meteo_router.get("/table")
def table(mode: str = "daily", ds: str = Common["ds"], station: str | None = Common["station"],
          start: str | None = Common["start"], end: str | None = Common["end"], base: str = Common["base"],
          clean: bool = Common["clean"], fill: bool = Common["fill"]):
    """mode=daily — суточная таблица за месяц (по start); иначе — декадная таблица за период."""
    d, data, daily, issues, sub, fr, s, e, b = ctx(ds, station, start, end, base, clean, fill)
    if mode == "daily":
        st = station if station in d.raw["station"].unique() else min(d.raw["station"])
        return V.js(T.daily_table(data, daily, pd.Timestamp(start) if start else s, st))
    return V.js(T.decade_table(daily, s, e))


@meteo_router.get("/table.xlsx")
def table_xlsx(mode: str = "daily", var: str = "t_air", ds: str = Common["ds"], station: str | None = Common["station"],
               start: str | None = Common["start"], end: str | None = Common["end"], base: str = Common["base"],
               clean: bool = Common["clean"], fill: bool = Common["fill"]):
    d, data, daily, issues, sub, fr, s, e, b = ctx(ds, station, start, end, base, clean, fill)
    st = station if station in d.raw["station"].unique() else min(d.raw["station"])
    tab = (T.daily_table(data, daily, pd.Timestamp(start) if start else s, st)
           if mode == "daily" else T.decade_table(daily, s, e))
    if mode == "daily":
        s = (pd.Timestamp(start) if start else s).to_period("M").to_timestamp()
        e = s + pd.offsets.MonthEnd(0)
    monthly = T.monthly_rows(tab, s)
    review = Q.export_sheets(data, daily, monthly, issues, Q.exclusions_for(d.reports.values(), st), st, var, s, e)
    name = f"{st} {tab['title']}".replace("—", "-").replace(".", "-")
    return _download(T.to_excel(tab, st, review), f"Таблица {name}.xlsx",
                     "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")


# ------------------------------------------------------------------ отчёты

def _download(content: bytes | str, filename: str, media: str) -> Response:
    return Response(content, media_type=media,
                    headers={"Content-Disposition": f"attachment; filename*=UTF-8''{quote(filename)}"})


@meteo_router.get("/report.html")
def report_html(var: str = "t_air", ds: str = Common["ds"], station: str | None = Common["station"], start: str | None = Common["start"],
                end: str | None = Common["end"], base: str = Common["base"], clean: bool = Common["clean"],
                fill: bool = Common["fill"]):
    d, data, daily, issues, sub, fr, s, e, b = ctx(ds, station, start, end, base, clean, fill)
    st = station if station in d.raw["station"].unique() else min(d.raw["station"])
    review = Q.export_sheets(data, daily, A.aggregate(daily.loc[s:e], "MS"), issues,
                             Q.exclusions_for(d.reports.values(), st), st, var, s, e)
    return _download(html_report(daily, st, s, e, b, issues, review=review), f"Метеоотчёт {st} {s:%Y%m%d}-{e:%Y%m%d}.html",
                     "text/html; charset=utf-8")


@meteo_router.get("/report.xlsx")
def report_xlsx(var: str = "t_air", ds: str = Common["ds"], station: str | None = Common["station"], start: str | None = Common["start"],
                end: str | None = Common["end"], base: str = Common["base"], clean: bool = Common["clean"],
                fill: bool = Common["fill"]):
    d, data, daily, issues, sub, fr, s, e, b = ctx(ds, station, start, end, base, clean, fill)
    st = station if station in d.raw["station"].unique() else min(d.raw["station"])
    review = Q.export_sheets(data, daily, A.aggregate(daily.loc[s:e], "MS"), issues,
                             Q.exclusions_for(d.reports.values(), st), st, var, s, e)
    return _download(to_excel(build_sheets(daily, s, e, b, issues), review), f"Метеоданные {st} {s:%Y%m%d}-{e:%Y%m%d}.xlsx",
                     "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")


# ------------------------------------------------------------------ статика

@app.exception_handler(HTTPException)
def _http_error(_, exc: HTTPException):
    return JSONResponse({"error": exc.detail}, status_code=exc.status_code)


@app.get("/vendor/plotly.min.js")
def plotly_js():
    return FileResponse(PLOTLY_JS, media_type="text/javascript", headers={"Cache-Control": "max-age=86400"})


@app.get("/", response_class=HTMLResponse)
def workspace():
    return (WEB / "workspace.html").read_text(encoding="utf-8")


@app.get("/index.html", response_class=HTMLResponse, include_in_schema=False)
@app.get("/meteo", response_class=HTMLResponse)
def index(request: Request):
    html = (WEB / "index.html").read_text(encoding="utf-8")
    if library.session_account(request) is None:
        html = html.replace("<body>", "<body data-guest>", 1)
    return HTMLResponse(html, headers={"Cache-Control": "no-store"})


app.include_router(meteo_router)
app.mount("/", StaticFiles(directory=WEB), name="web")
