"""Пакетная обработка без интерфейса: файлы или папка → отчёты по каждой станции.

    uv run python -m meteo.batch data/sample_stations.csv --out reports
    uv run python -m meteo.batch папка_с_файлами/ --start 2024-01-01 --end 2024-12-31 --base 1991-2020

Для каждой станции создаёт <станция>.xlsx (все таблицы) и <станция>.html (выводы и графики),
плюс общий summary.md с главными выводами по всем станциям.
"""
from __future__ import annotations

import argparse
import re
import sys
import time
from pathlib import Path

import pandas as pd

from . import analytics as A
from . import insights as I
from .loader import combine, load, stations
from .report import build_sheets, html_report, to_excel

EXTS = {".csv", ".txt", ".xlsx", ".xls"}


def _files(inputs: list[str]) -> list[Path]:
    out = []
    for i in inputs:
        p = Path(i)
        out += sorted(f for f in p.rglob("*") if f.suffix.lower() in EXTS) if p.is_dir() else [p]
    return out


def _safe(name: str) -> str:
    return re.sub(r"[^\w\-]+", "_", name).strip("_") or "station"


def run(inputs: list[str], out_dir: str = "reports", start: str | None = None, end: str | None = None,
        base: str | None = "all", log=print) -> list[Path]:
    files = _files(inputs)
    if not files:
        raise SystemExit("Не найдено файлов CSV/TXT/XLSX.")
    frames = []
    for f in files:
        try:
            df, rep = load(f)
        except ValueError as e:      # служебные таблицы (например, coverage_by_month.csv) — не наблюдения
            log(f"· {f.name}: пропущен — {e}")
            continue
        frames.append(df)
        log(f"· {f.name}: {rep.rows_out} строк, параметры: {', '.join(sorted(set(rep.mapping.values())))}")
    if not frames:
        raise SystemExit("Ни один файл не удалось прочитать как наблюдения метеостанции.")
    raw = combine(frames)
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)

    prepared = {}
    for st in stations(raw):
        data = A.for_station(raw, st)
        issues = A.quality_issues(data)      # только подсказки; в расчёт идут данные после флагов качества
        prepared[st] = (A.to_daily(data, st), issues)
    dailies = {k: v[0] for k, v in prepared.items()}

    written, summary = [], ["# Сводка по станциям", ""]
    for st, (daily, issues) in prepared.items():
        t0 = time.time()
        s = max(pd.Timestamp(start), daily.index.min()) if start else daily.index.min()
        e = min(pd.Timestamp(end), daily.index.max()) if end else daily.index.max()
        if s > e or daily.loc[s:e].dropna(how="all").empty:
            log(f"– {st}: нет данных за выбранный период, отчёт не создан")
            continue
        years = sorted(daily.index.year.unique())
        b = (str(years[0]), str(years[-1]))
        if base and base != "all":
            try:
                b0, b1 = (int(x) for x in base.split("-"))
                b = (str(max(b0, years[0])), str(min(b1, years[-1])))
            except ValueError:
                pass
        items = I.generate(daily, s, e, b, issues)
        x = out / f"{_safe(st)}.xlsx"
        x.write_bytes(to_excel(build_sheets(daily, s, e, b, issues, items)))
        h = out / f"{_safe(st)}.html"
        h.write_text(html_report(daily, st, s, e, b, issues, items), encoding="utf-8")
        written += [x, h]
        summary += [f"## {st}", "", I.to_markdown([i for i in items if i.kind in ("main", "trend")]), ""]
        log(f"✓ {st}: {x.name}, {h.name} ({time.time() - t0:.1f} с)")
    md = out / "summary.md"
    md.write_text("\n".join(summary), encoding="utf-8")
    written.append(md)
    return written


def main(argv: list[str] | None = None) -> None:
    ap = argparse.ArgumentParser(prog="python -m meteo.batch", description="Пакетная обработка метеоданных")
    ap.add_argument("inputs", nargs="+", help="файлы или папки с CSV/TXT/XLSX")
    ap.add_argument("--out", default="reports", help="папка для отчётов (по умолчанию reports)")
    ap.add_argument("--start", help="начало периода, ГГГГ-ММ-ДД")
    ap.add_argument("--end", help="конец периода, ГГГГ-ММ-ДД")
    ap.add_argument("--base", default="all", help="годы для «среднего за выбранный период», например 2004-2016; по умолчанию все")
    a = ap.parse_args(argv)
    files = run(a.inputs, a.out, a.start, a.end, a.base)
    print(f"\nГотово: {len(files)} файлов в {Path(a.out).resolve()}")


if __name__ == "__main__":
    sys.exit(main())
