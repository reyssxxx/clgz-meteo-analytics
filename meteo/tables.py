"""Таблицы, которые заповедник сейчас сводит вручную в Excel.

• Суточная таблица за месяц: строка — сутки (неполные сутки помечены), ниже итоги по декадам
  (1–10, 11–20, 21–конец месяца), среднее за месяц (только для валидного месяца) и экстремумы.
• Декадная таблица за сезон/год/любой период: по каждому месяцу три декады и месяц, внизу итог.

Все значения — по правилам задания: флаги качества исключены, неполные сутки (< 72 из 96 сроков
у АМЕ-60, < 18 часов у Сокол М1) не входят, месяц — только если ≤ 10 неполных дней и нет 5 подряд.
"""
from __future__ import annotations

import io

import numpy as np
import pandas as pd

from . import analytics as A
from . import coverage as CV
from .schema import VARIABLES

MONTHS = ["январь", "февраль", "март", "апрель", "май", "июнь", "июль", "август", "сентябрь", "октябрь",
          "ноябрь", "декабрь"]

# колонки таблицы: ключ, группа, подпись, как сворачивать в декаду/месяц, переменная
COLUMNS = [
    ("t_air", "Температура воздуха, °C", "средняя", "mean"),
    ("t_max", "Температура воздуха, °C", "макс.", "mean"),
    ("t_min", "Температура воздуха, °C", "мин.", "mean"),
    ("rh", "Влажность, %", "средняя", "mean"),
    ("wind_speed", "Ветер", "скорость, м/с", "mean"),
    ("wind_gust", "Ветер", "порыв, м/с", "max"),
    ("wind_dir", "Ветер", "направление, °", "circmean"),
    ("soil_05", "Температура почвы, °C", "5 см", "mean"),
    ("soil_10", "Температура почвы, °C", "10 см", "mean"),
    ("soil_20", "Температура почвы, °C", "20 см", "mean"),
    ("pressure", "Давление, гПа", "среднее", "mean"),
]
RUMB = ["С", "ССВ", "СВ", "ВСВ", "В", "ВЮВ", "ЮВ", "ЮЮВ", "Ю", "ЮЮЗ", "ЮЗ", "ЗЮЗ", "З", "ЗСЗ", "СЗ", "ССЗ"]


def rumb(deg) -> str:
    if deg is None or (isinstance(deg, float) and np.isnan(deg)):
        return ""
    return RUMB[int(((deg % 360) + 11.25) // 22.5) % 16]


def decade(day: int) -> int:
    return 1 if day <= 10 else 2 if day <= 20 else 3


def _agg(s: pd.Series, how: str) -> float:
    s = s.dropna()
    if s.empty:
        return np.nan
    if how == "circmean":
        r = np.deg2rad(s.to_numpy())
        return float(np.rad2deg(np.arctan2(np.sin(r).mean(), np.cos(r).mean())) % 360)
    return float(getattr(s, how)())


def _cols(daily: pd.DataFrame) -> list[dict]:
    return [{"key": k, "group": g, "label": l, "how": h}
            for k, g, l, h in COLUMNS if k in daily and daily[k].notna().any()]


def _values(d: pd.DataFrame, cols: list[dict]) -> dict:
    return {c["key"]: _agg(d[c["key"]], c["how"]) for c in cols}


def _month_rows(daily: pd.DataFrame, ms: pd.Timestamp, cols: list[dict]) -> tuple[list[dict], dict]:
    """Декады и месяц для одного календарного месяца. Месячное значение — только при валидном месяце
    (для каждого параметра отдельно)."""
    me = ms + pd.offsets.MonthEnd(0)
    d = daily.reindex(pd.date_range(ms, me, freq="D"))
    rows = []
    for k in (1, 2, 3):
        part = d[[decade(x.day) == k for x in d.index]]
        n = {c["key"]: int(part[c["key"]].notna().sum()) for c in cols}
        rows.append({"kind": "decade", "label": f"{k}-я декада", "values": _values(part, cols), "days": n,
                     "of": len(part)})
    month_vals, month_ok = {}, {}
    for c in cols:
        ok = bool(CV.monthly_valid_from_daily(d[c["key"]]).iloc[0])
        month_ok[c["key"]] = ok
        month_vals[c["key"]] = _agg(d[c["key"]], c["how"]) if ok else np.nan
    rows.append({"kind": "month", "label": f"{MONTHS[ms.month - 1].capitalize()} {ms.year}", "values": month_vals,
                 "valid": month_ok, "days": {c["key"]: int(d[c["key"]].notna().sum()) for c in cols}, "of": len(d)})
    return rows, month_vals


def daily_table(raw_station: pd.DataFrame, daily: pd.DataFrame, month_start) -> dict:
    """Суточная таблица за месяц."""
    ms = pd.Timestamp(month_start).to_period("M").to_timestamp()
    me = ms + pd.offsets.MonthEnd(0)
    cols = _cols(daily)
    rule = CV.day_rule(raw_station.index)
    counts = CV.day_counts(raw_station.loc[ms:me + pd.Timedelta("1D") - pd.Timedelta("1ns")].index, rule)
    d = daily.reindex(pd.date_range(ms, me, freq="D"))
    rows = []
    for t, r in d.iterrows():
        n = int(counts.get(t, 0))
        vals = {c["key"]: r.get(c["key"], np.nan) for c in cols}
        rows.append({"kind": "day", "label": f"{t.day:02d}.{t.month:02d}", "date": t.strftime("%Y-%m-%d"),
                     "values": vals, "obs": n, "need": rule.need, "of_obs": rule.of,
                     "complete": n >= rule.need, "decade": decade(t.day)})
    summary, month_vals = _month_rows(daily, ms, cols)
    dd = d.dropna(how="all")
    ext = {}
    if "t_max" in d and d["t_max"].notna().any():
        ext["t_max"] = {"value": d["t_max"].max(), "date": d["t_max"].idxmax().strftime("%d.%m")}
    if "t_min" in d and d["t_min"].notna().any():
        ext["t_min"] = {"value": d["t_min"].min(), "date": d["t_min"].idxmin().strftime("%d.%m")}
    if "wind_gust" in d and d["wind_gust"].notna().any():
        ext["wind_gust"] = {"value": d["wind_gust"].max(), "date": d["wind_gust"].idxmax().strftime("%d.%m")}
    return {"mode": "daily", "title": f"{MONTHS[ms.month - 1].capitalize()} {ms.year}", "columns": cols,
            "rows": rows, "summary": summary, "extremes": ext, "rule": rule.describe(),
            "complete_days": int(sum(r["complete"] for r in rows)), "days": len(rows),
            "has_data": bool(len(dd))}


def decade_table(daily: pd.DataFrame, start, end) -> dict:
    """Декадная таблица: по каждому месяцу периода три декады и месяц; внизу итог за период."""
    start, end = pd.Timestamp(start), pd.Timestamp(end)
    cols = _cols(daily)
    months = pd.date_range(start.to_period("M").to_timestamp(), end, freq="MS")
    blocks, monthly = [], {}
    for ms in months:
        rows, mv = _month_rows(daily, ms, cols)
        blocks.append({"month": ms.strftime("%Y-%m"), "label": f"{MONTHS[ms.month - 1].capitalize()} {ms.year}",
                       "rows": rows})
        monthly[ms] = mv
    mdf = pd.DataFrame(monthly).T if monthly else pd.DataFrame()
    total = {}
    for c in cols:
        s = mdf[c["key"]] if c["key"] in mdf else pd.Series(dtype=float)
        # итог — по валидным месяцам; для полного года — только если валидны все 12
        if len(months) == 12 and s.notna().sum() < 12:
            total[c["key"]] = np.nan
        else:
            total[c["key"]] = _agg(s, c["how"] if c["how"] != "mean" else "mean")
    n_valid = {c["key"]: int(mdf[c["key"]].notna().sum()) if c["key"] in mdf else 0 for c in cols}
    return {"mode": "decades", "title": f"{start:%d.%m.%Y} — {end:%d.%m.%Y}", "columns": cols, "blocks": blocks,
            "total": {"label": "Год" if len(months) == 12 and start.month == 1 else "Период", "values": total,
                      "valid_months": n_valid, "months": len(months)}}


# ------------------------------------------------------------------ Excel в том же виде

def to_excel(tab: dict, station: str) -> bytes:
    from openpyxl import Workbook
    from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
    from openpyxl.utils import get_column_letter

    wb = Workbook()
    ws = wb.active
    ws.title = "Таблица"
    cols = tab["columns"]
    bold = Font(bold=True)
    grey = Font(color="8A8F8B")
    fill_dec = PatternFill("solid", fgColor="EEF2EE")
    fill_month = PatternFill("solid", fgColor="DDE8DF")
    thin = Border(bottom=Side(style="thin", color="D5D9DE"))
    ws.append([f"{station}: {tab['title']}"])
    ws["A1"].font = Font(bold=True, size=14)
    ws.append(["Расчёт по правилам задания: флаги качества исключены; неполные сутки и невалидные месяцы не учитываются."])
    ws["A2"].font = grey
    ws.append([])
    # двухуровневая шапка
    first = ["Дата" if tab["mode"] == "daily" else "Период"] + [c["group"] for c in cols] + (["Сроков"] if tab["mode"] == "daily" else [])
    second = [""] + [c["label"] for c in cols] + ([""] if tab["mode"] == "daily" else [])
    ws.append(first)
    ws.append(second)
    for row in (4, 5):
        for cell in ws[row]:
            cell.font = bold
            cell.alignment = Alignment(horizontal="center", vertical="center", wrap_text=True)
    # объединить одинаковые группы в шапке
    start = 2
    for i in range(2, len(cols) + 3):
        if i == len(cols) + 2 or cols[i - 2]["group"] != cols[start - 2]["group"]:
            if i - 1 > start:
                ws.merge_cells(start_row=4, start_column=start, end_row=4, end_column=i - 1)
            start = i

    def val(c, v):
        if v is None or (isinstance(v, float) and np.isnan(v)):
            return None
        return round(float(v)) if c["key"] == "wind_dir" else round(float(v), 1)

    def put(label, values, style=None, extra=None, greyed=False):
        ws.append([label] + [val(c, values.get(c["key"])) for c in cols] + ([extra] if extra is not None else []))
        r = ws.max_row
        for cell in ws[r]:
            if style is not None:
                cell.fill = style
            if style is fill_month:
                cell.font = bold
            if greyed:
                cell.font = grey
            cell.border = thin

    if tab["mode"] == "daily":
        for row in tab["rows"]:
            put(row["label"], row["values"], extra=f"{row['obs']} из {row['of_obs']}", greyed=not row["complete"])
            if row["date"][-2:] in ("10", "20") or row is tab["rows"][-1]:
                pass
        for s in tab["summary"]:
            put(s["label"] if s["kind"] == "decade" else f"Среднее за месяц", s["values"],
                fill_month if s["kind"] == "month" else fill_dec, extra="")
        if tab.get("extremes"):
            ex = {k: v["value"] for k, v in tab["extremes"].items()}
            put("Экстремум за месяц", ex, fill_dec, extra="")
    else:
        for b in tab["blocks"]:
            for s in b["rows"]:
                put(f"{b['label']}, {s['label']}" if s["kind"] == "decade" else b["label"], s["values"],
                    fill_month if s["kind"] == "month" else None)
        put(tab["total"]["label"], tab["total"]["values"], fill_month)
    ws.column_dimensions["A"].width = 26
    for i in range(2, len(cols) + 3):
        ws.column_dimensions[get_column_letter(i)].width = 13
    ws.row_dimensions[5].height = 30
    ws.freeze_panes = "B6"
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()
