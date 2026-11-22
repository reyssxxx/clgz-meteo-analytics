"""Public source files and the separate employee workspace."""
import os
import re
import sqlite3
from contextlib import closing
from html import escape
from pathlib import Path
from secrets import compare_digest, token_urlsafe
from threading import Lock
from time import monotonic
from urllib.parse import quote, urlencode

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, RedirectResponse

router = APIRouter()
CASE = Path(__file__).resolve().parent.parent / "data" / "clgz"
REQUESTS_DB = CASE.parent / ".recovery" / "requests.sqlite3"
PUBLIC_STATIONS = {"ame60": "АМЕ-60", "kr1": "Кр1 (поляна)", "kr2": "Кр2 (поле)"}
RESERVE = "Центрально-Лесной заповедник"
COOKIE = "clgz_employee"
SESSION_TTL = 8 * 60 * 60
sessions: dict[str, tuple[str, float]] = {}
session_lock = Lock()


def session_account(request: Request) -> str | None:
    with session_lock:
        session = sessions.get(request.cookies.get(COOKIE, ""))
    if session is not None and session[1] > monotonic():
        return session[0]
    return None


def require_employee(request: Request) -> str:
    account = session_account(request)
    if account is None:
        raise HTTPException(401, "Войдите в кабинет сотрудника.")
    return account


def page(title: str, content: str, status: int = 200) -> HTMLResponse:
    return HTMLResponse(f"""<!doctype html>
<html lang="ru"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>{title}</title><link rel="stylesheet" href="/workspace.css"></head>
<body><header class="topbar"><div class="frame topbar-inner"><a class="identity" href="/">{RESERVE}</a></div></header>
<main class="frame detail"><a href="/">← Библиотека</a><h1>{title}</h1>{content}</main></body></html>""",
                        status_code=status, headers={"Cache-Control": "no-store"})


def login_page(error: str = "", status: int = 200) -> HTMLResponse:
    message = f'<p class="field-error" role="alert">{escape(error)}</p>' if error else ""
    return page("Вход для сотрудника", f"""<form class="library-form" method="post" action="/employee/login">
{message}<label>Логин <input name="username" autocomplete="username" maxlength="120" required></label>
<label>Пароль <input name="password" type="password" autocomplete="current-password" maxlength="512" required></label>
<button class="button" type="submit">Войти</button></form>""", status)


@router.post("/employee/login")
def login(request: Request, username: str = Form(max_length=120), password: str = Form(max_length=512)):
    account = os.environ.get("CLGZ_EMPLOYEE_USER")
    secret = os.environ.get("CLGZ_EMPLOYEE_PASSWORD")
    if not account or not secret:
        return login_page("Учётная запись сотрудника ещё не настроена.", 503)
    user_matches = compare_digest(username.encode(), account.encode())
    password_matches = compare_digest(password.encode(), secret.encode())
    if not (user_matches and password_matches):
        return login_page("Неверный логин или пароль.", 401)
    token = token_urlsafe(32)
    now = monotonic()
    with session_lock:
        for expired in [key for key, value in sessions.items() if value[1] <= now]:
            sessions.pop(expired)
        sessions.pop(request.cookies.get(COOKIE, ""), None)
        sessions[token] = (account, now + SESSION_TTL)
    response = RedirectResponse("/meteo#data", status_code=303, headers={"Cache-Control": "no-store"})
    response.set_cookie(COOKIE, token, max_age=SESSION_TTL, httponly=True, samesite="strict",
                        secure=request.url.scheme == "https")
    return response


@router.post("/employee/logout")
def logout(request: Request):
    with session_lock:
        sessions.pop(request.cookies.get(COOKIE, ""), None)
    response = RedirectResponse("/", status_code=303, headers={"Cache-Control": "no-store"})
    response.delete_cookie(COOKIE, httponly=True, samesite="strict")
    return response


@router.get("/api/library/session")
def employee_session(request: Request):
    return JSONResponse({"employee": session_account(request) is not None},
                        headers={"Cache-Control": "no-store"})


def public_files() -> dict[str, Path]:
    return {
        path.name: path
        for directory in ("ame60", "sokol_m1")
        for path in sorted((CASE / directory).glob("*.csv"))
        if path.stem.split("_")[0] in PUBLIC_STATIONS
    }


@router.get("/api/library/observations", dependencies=[Depends(require_employee)])
def observations():
    return [
        {"file": name, "station": PUBLIC_STATIONS[prefix], "year": year}
        for name in public_files()
        for prefix, year in [Path(name).stem.split("_", 1)]
    ]


@router.get("/api/library/observations/{name}", dependencies=[Depends(require_employee)])
def download_observations(name: str):
    path = public_files().get(name)
    if path is None:
        raise HTTPException(404, "Файл не найден.")
    return FileResponse(path, filename=path.name, media_type="text/csv", headers={"Cache-Control": "no-store"})


def employee_files() -> dict[str, Path]:
    directory = os.environ.get("CLGZ_EMPLOYEE_DATA_DIR")
    if not directory or not Path(directory).is_dir():
        return {}
    return {
        path.name: path for path in sorted(Path(directory).iterdir())
        if path.is_file() and not path.is_symlink() and not path.name.startswith(".")
    }


def requests_connection() -> sqlite3.Connection:
    REQUESTS_DB.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(REQUESTS_DB)
    with conn:
        conn.execute("""CREATE TABLE IF NOT EXISTS requests (
            id INTEGER PRIMARY KEY, email TEXT NOT NULL, description TEXT NOT NULL,
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        )""")
    return conn


@router.get("/data-request")
def data_request():
    return RedirectResponse("/meteo#data", status_code=303)


@router.post("/data-request")
def submit_data_request(email: str = Form(max_length=254), description: str = Form(max_length=4000)):
    email, description = email.strip(), description.strip()
    if not re.fullmatch(r"[^\s@]+@[^\s@]+\.[^\s@]+", email) or not description:
        return JSONResponse({"error": "Проверьте адрес почты и описание данных."}, status_code=400)
    with closing(requests_connection()) as conn, conn:
        conn.execute("INSERT INTO requests (email, description) VALUES (?, ?)", (email, description))
    return {"ok": True}


@router.get("/employee", response_class=HTMLResponse)
def employee(request: Request):
    if session_account(request) is None:
        return login_page()
    return RedirectResponse("/meteo#data", status_code=303, headers={"Cache-Control": "no-store"})


@router.get("/employee/profile", response_class=HTMLResponse)
def employee_profile(request: Request):
    account = session_account(request)
    if account is None:
        return RedirectResponse("/employee", status_code=303, headers={"Cache-Control": "no-store"})
    years_by_station: dict[str, list[str]] = {}
    for entry in observations():
        years_by_station.setdefault(entry["station"], []).append(entry["year"])
    station_list = "".join(
        f'<li><a href="/meteo#data?{escape(urlencode({"station": station}))}">{escape(station)}</a>'
        f' <span>{min(years)}–{max(years)} · {len(years)} CSV</span></li>'
        for station, years in years_by_station.items()
    )
    meteo_panel = (
        '<section class="account-panel"><h2>Метеоданные заповедника</h2>'
        f'<ul class="profile-stations">{station_list}</ul>'
        '<a class="button" href="/meteo#data">Данные и отчёты</a></section>'
    ) if station_list else ""
    files = employee_files()
    listing = "".join(
        f'<li><a href="/employee/files/{quote(name, safe="")}">{escape(name)}</a></li>' for name in files
    )
    file_list = f"<ul>{listing}</ul>" if listing else "<p>Закрытых файлов пока нет.</p>"
    with closing(requests_connection()) as conn:
        requests = conn.execute(
            "SELECT email, description, created_at FROM requests ORDER BY id DESC LIMIT 100"
        ).fetchall()
    request_list = "".join(
        f'<li><strong>{escape(email)}</strong> · {escape(created)} UTC<p>{escape(description)}</p></li>'
        for email, description, created in requests
    )
    return page("Кабинет сотрудника", f"""<section class="account-panel">
<div class="profile-heading"><h2>Профиль</h2>
<form method="post" action="/employee/logout"><button class="button secondary" type="submit">Выйти из профиля</button></form>
</div><dl><dt>Учётная запись</dt><dd>{escape(account)}</dd><dt>Заповедник</dt><dd>{RESERVE}</dd></dl>
</section>{meteo_panel}<section class="account-panel"><h2>Закрытые файлы</h2>{file_list}</section>
<section class="account-panel"><h2>Запросы данных</h2>
{f'<ul class="request-list">{request_list}</ul>' if request_list else '<p>Новых запросов пока нет.</p>'}</section>""")


@router.get("/employee/files/{name}", dependencies=[Depends(require_employee)])
def download_employee_file(name: str):
    path = employee_files().get(name)
    if path is None:
        raise HTTPException(404, "Файл не найден.")
    return FileResponse(path, filename=path.name, media_type="application/octet-stream",
                        headers={"Cache-Control": "no-store", "X-Content-Type-Options": "nosniff"})
