"""Отчёты: Excel (все таблицы) и самодостаточный HTML (выводы + интерактивные графики).

Используется и интерфейсом (кнопки «Скачать»), и пакетным режимом (meteo.batch).
"""
from __future__ import annotations

import html
import io

import numpy as np
import pandas as pd
from openpyxl.utils import get_column_letter

from . import analytics as A
from . import charts as C
from . import insights as I
from .schema import VARIABLES, label


# ------------------------------------------------------------------ Excel

def _indexed(df: pd.DataFrame, col: str):
    """Пустая таблица (нет данных за период) не должна ломать выгрузку — лист просто не создаётся."""
    return df.set_index(col) if col in df.columns else None


def build_sheets(daily: pd.DataFrame, start, end, base: tuple | None, issues: pd.DataFrame | None,
                 items: list[I.Insight] | None = None) -> dict[str, pd.DataFrame | None]:
    d = daily.loc[start:end]
    month_t = A.aggregate(d, "MS")
    month_t.index = [A.period_label(i, "MS") for i in month_t.index]
    year_t = A.aggregate(d, "YS")
    year_t.index = year_t.index.year
    trends = A.trend_table(d)
    means, years = A.monthly_reference(daily, base)
    items = items if items is not None else I.generate(daily, start, end, base, issues)
    return {
        "Выводы": pd.DataFrame({"Раздел": [i.kind for i in items], "Вывод": [i.text for i in items]}).set_index("Раздел"),
        "Статистика": _indexed(A.period_summary(daily, start, end), "Параметр"),
        "Характерные дни": _indexed(A.day_counts(daily, start, end), "Показатель"),
        "Сутки": d.rename(columns={"wind_gust": "Средний порыв ветра, м/с", "wind_gust_max": "Максимальный порыв ветра, м/с"}),
        "Месяцы": month_t,
        "Годы": year_t,
        "Средние по месяцам": means,
        "Число лет в средних": years,
        "Тренды": trends.drop(columns="var").set_index("Параметр") if len(trends) else None,
        "Агроклимат": A.agro_indices(d),
        "Экстремумы": A.extreme_events(d),
        "Качество данных": issues,
    }


def to_excel(sheets: dict[str, pd.DataFrame | None]) -> bytes:
    buf = io.BytesIO()
    with pd.ExcelWriter(buf, engine="openpyxl") as w:
        for name, df in sheets.items():
            if df is None or df.empty:
                continue
            out = df.copy()
            out.columns = [label(c) for c in out.columns]
            num = out.select_dtypes("number").columns
            out[num] = out[num].round(3)
            sheet = name[:31]
            out.to_excel(w, sheet_name=sheet)
            ws = w.sheets[sheet]
            ws.freeze_panes = "B2"
            for i, col in enumerate([out.index.name or ""] + list(out.columns), start=1):
                width = 90 if name == "Выводы" and i == 2 else min(max(12, len(str(col)) + 2), 40)
                ws.column_dimensions[get_column_letter(i)].width = width
    return buf.getvalue()


# ------------------------------------------------------------------ HTML

CSS = """
:root { --fg:#1f2933; --muted:#616e7c; --line:#e4e7eb; --bg:#fff; --accent:#2c6e9e; }
* { box-sizing: border-box; }
body { margin:0; font: 15px/1.55 -apple-system, "Segoe UI", Roboto, sans-serif; color:var(--fg); background:var(--bg); }
main { max-width: 1100px; margin: 0 auto; padding: 28px 16px 64px; }
h1 { font-size: 28px; margin: 0 0 4px; } h2 { font-size: 20px; margin: 36px 0 10px; border-bottom:1px solid var(--line); padding-bottom:6px; }
.meta { color: var(--muted); margin-bottom: 20px; }
.insights { display:grid; grid-template-columns: repeat(auto-fit, minmax(320px, 1fr)); gap: 12px 28px; }
.insights h3 { font-size: 15px; margin: 8px 0 4px; color: var(--accent); }
.insights ul { margin: 0; padding-left: 18px; }
.insights li { margin: 3px 0; }
.lvl-warm::marker { color:#d6604d; } .lvl-cold::marker { color:#4393c3; } .lvl-warn::marker { color:#bf812d; }
table { border-collapse: collapse; width: 100%; font-size: 13px; margin: 8px 0; }
th, td { border-bottom: 1px solid var(--line); padding: 5px 8px; text-align: right; white-space: nowrap; }
th:first-child, td:first-child { text-align: left; }
th { background: #f5f7fa; font-weight: 600; }
.scroll { overflow-x: auto; }
.chart { margin: 8px 0 16px; }
footer { margin-top: 48px; color: var(--muted); font-size: 13px; }
"""


def _table(df: pd.DataFrame, index: bool = False) -> str:
    if df is None or df.empty:
        return "<p>Нет данных.</p>"
    out = df.copy()
    for c in out.select_dtypes("number").columns:
        col = out[c]
        if str(c).startswith("p "):
            out[c] = col.map(lambda x: "" if pd.isna(x) else "< 0,001" if x < 0.001 else I.num(x, 3))
        elif col.dropna().apply(float.is_integer if col.dtype.kind == "f" else lambda _: True).all():
            out[c] = col.map(lambda x: "" if pd.isna(x) else f"{int(x)}")
        else:
            out[c] = col.map(lambda x: "" if pd.isna(x) else I.num(x, 2 if abs(x) < 100 else 1))
    return f'<div class="scroll">{out.to_html(index=index, border=0, escape=True, na_rep="")}</div>'


def html_report(daily: pd.DataFrame, station: str, start, end, base: tuple | None,
                issues: pd.DataFrame | None = None, items: list[I.Insight] | None = None) -> str:
    start, end = pd.Timestamp(start), pd.Timestamp(end)
    d = daily.loc[start:end]
    items = items if items is not None else I.generate(daily, start, end, base, issues)
    charts: list[tuple[str, object]] = []
    first = [True]

    def fig_html(fig) -> str:
        h = fig.to_html(full_html=False, include_plotlyjs="inline" if first[0] else False,
                        config={"displaylogo": False, "responsive": True})
        first[0] = False
        return f'<div class="chart">{h}</div>'

    titles = {"main": "Главное", "extreme": "Экстремумы", "agro": "Агроклимат", "trend": "Тренды",
              "quality": "Качество данных"}
    blocks = []
    for kind, title in titles.items():
        group = [i for i in items if i.kind == kind]
        if group:
            lis = "".join(f'<li class="lvl-{i.level}">{html.escape(i.text)}</li>' for i in group)
            blocks.append(f"<div><h3>{title}</h3><ul>{lis}</ul></div>")

    base_label = f", годы для среднего {base[0]}–{base[1]}" if base else ""
    parts = [f"<h1>Метеоаналитика: {html.escape(station)}</h1>",
             f'<div class="meta">{html.escape(I.describe_period(start, end).capitalize())} · '
             f"{start:%d.%m.%Y}–{end:%d.%m.%Y}{base_label}, "
             f"сформировано {pd.Timestamp.now():%d.%m.%Y %H:%M}</div>",
             "<h2>Выводы</h2>", f'<div class="insights">{"".join(blocks)}</div>',
             "<h2>Статистика за период</h2>", _table(A.period_summary(daily, start, end)),
             _table(A.day_counts(daily, start, end))]

    norms, years = A.monthly_reference(daily, base)
    if "t_air" in norms or "precip" in norms:
        parts += ["<h2>Среднее за выбранный период</h2>", fig_html(C.climate_diagram(norms, years=years))]

    freq = "D" if (end - start).days < 400 else "MS"
    agg = A.aggregate(d, freq)
    parts.append("<h2>Ход параметров</h2>")
    for v in [v for v in ("t_air", "precip") if v in agg] or A._vars(agg)[:2]:
        ref = A.daily_anomalies(daily, v, base).loc[start:end] if freq == "D" and v != "precip" else {}
        parts.append(fig_html(C.timeseries(agg, v, ref.get("norm"), ref.get("years"))))

    if "t_air" in d:
        man = A.monthly_anomalies(daily, "t_air", base).loc[start:end]
        if man["anomaly"].notna().sum() >= 3:
            parts += ["<h2>Отклонения от среднего за выбранный период</h2>", fig_html(C.anomaly_bars(man, "t_air"))]
            if man.index.year.nunique() >= 3:
                parts.append(fig_html(C.anomaly_heatmap(man, "t_air")))

    trends = A.trend_table(d)
    if len(trends):
        parts.append("<h2>Тренды</h2>")
        if "t_air" in d:
            ann = A.annual_series(d, "t_air")
            annual_ref = A.annual_series(daily.loc[slice(*base) if base else slice(None)], "t_air").dropna()
            if ann.notna().sum() >= 10 and not annual_ref.empty:
                title = f"Полосы потепления · среднее {I.base_phrase(annual_ref.index)}"
                parts.append(fig_html(C.warming_stripes((ann - annual_ref.mean()).dropna(), title)))
            s = ann.reindex(range(ann.index.min(), ann.index.max() + 1))
            parts.append(fig_html(C.trend_chart(s, "t_air", A.trend(s), " — средняя за год")))
        parts.append(_table(trends.drop(columns="var")))
        parts.append("<p>Значимость — тест Манна–Кендалла при α = 0,05; наклон — оценка Сена.</p>")

    agro = A.agro_indices(d)
    if len(agro):
        parts += ["<h2>Агроклиматические показатели</h2>", _table(agro.reset_index())]

    ev = A.extreme_events(d)
    if len(ev):
        parts += ["<h2>Экстремальные явления</h2>", fig_html(C.events_timeline(ev)),
                  _table(ev.assign(Начало=ev["Начало"].dt.strftime("%d.%m.%Y"), Конец=ev["Конец"].dt.strftime("%d.%m.%Y")))]

    comp = A.completeness(d)
    parts += ["<h2>Качество данных</h2>", fig_html(C.completeness_heatmap(comp))]
    if issues is not None and len(issues):
        cnt = issues["Проблема"].str.split().str[0].value_counts().rename_axis("Тип").reset_index(name="Число")
        parts.append(_table(cnt))

    parts.append("<footer>Отчёт сформирован автоматически. Методика: агрегация по правилам для каждого "
                 "параметра; флаги качества исключены; неполные сутки и месяцы не учитываются (правила задания); "
                 "среднее — за выбранный период с указанием числа лет; тренды — МНК, тест Манна–Кендалла и оценка Сена.</footer>")
    return (f'<!doctype html><html lang="ru"><head><meta charset="utf-8">'
            f'<meta name="viewport" content="width=device-width, initial-scale=1">'
            f"<title>Метеоотчёт: {html.escape(station)}</title><style>{CSS}</style></head>"
            f"<body><main>{''.join(parts)}</main></body></html>")
