"""Контекстные предупреждения по наблюдениям и проверяемость расчёта."""
from __future__ import annotations

import numpy as np
import pandas as pd

from . import coverage as CV
from .schema import VARIABLES, label

IMPORTANT, REVIEW, WEATHER = "Высокий", "Проверить", "Погодный сигнал"
ISSUE_COLUMNS = ["Время", "Конец", "Параметр", "Значение", "Проблема", "Приоритет", "Контекст", "Наблюдений"]
EXCLUSION_COLUMNS = ["Станция", "Время", "Параметр", "Значение", "Причина", "Файл"]
AUDIT_SHEET = "Проверка расчёта"
IMPORTANT_SHEET = "Важные аномалии"
EXCLUSIONS_SHEET = "Исключённые измерения"
SELECTION_SHEET = "Параметры выборки"

_PEERS = {
    "t_air": ("rh", "pressure", "wind_speed", "soil_05"),
    "rh": ("t_air", "pressure", "wind_speed"),
    "pressure": ("t_air", "rh", "wind_speed"),
    "wind_speed": ("wind_gust", "pressure", "t_air"),
    "wind_gust": ("wind_speed", "pressure"),
    "soil_05": ("t_air", "soil_10"),
    "soil_10": ("soil_05", "soil_20"),
    "soil_20": ("soil_10",),
}
# Минимальная амплитуда кандидата и порог заметного изменения соседнего датчика.
_CHANGE = {"t_air": 3., "rh": 12., "pressure": 2., "wind_speed": 4., "wind_gust": 7.,
           "soil_05": 2., "soil_10": 1.5, "soil_20": 1.}
_SPRING_AUTUMN = {3, 4, 5, 9, 10, 11}


def _season(t: pd.Timestamp) -> str:
    return "межсезонье" if t.month in _SPRING_AUTUMN else "лето" if 6 <= t.month <= 8 else "зима"


def _changed_peers(df, var, start, end) -> list[str]:
    left = df.index.searchsorted(start)
    right = df.index.searchsorted(end, side="right")
    changes = []
    for name in _PEERS.get(var, ()):
        if name not in df:
            continue
        s = df[name].to_numpy()[left:right]
        s = s[np.isfinite(s)]
        if len(s) >= 2:
            lo, hi = np.quantile(s, (.1, .9))
            if hi - lo >= _CHANGE[name] / 2:
                changes.append(VARIABLES[name].label.lower())
    return changes


def _excursions(df, var, s, step, gap):
    meta = VARIABLES[var]
    if not meta.max_step_per_hour or var not in _CHANGE:
        return
    # Сначала дешёвый поиск кандидатов. Контекст оценивается только вблизи них.
    elapsed = s.index.to_series().diff()
    duration = (elapsed / pd.Timedelta("1h")).to_numpy()
    values = s.to_numpy()
    delta = np.diff(values, prepend=np.nan)
    cutoff = np.maximum(_CHANGE[var], meta.max_step_per_hour * duration)
    jumps = (elapsed <= gap).to_numpy() & (np.abs(delta) > cutoff)
    window = max(3, min(24, round(pd.Timedelta("4h") / step)))
    # Не переносим соседний фон через разрыв записи.
    blocks = (elapsed > gap).cumsum().to_numpy()
    skip_return = set()
    for i in np.flatnonzero(jumps):
        if i in skip_return:
            continue
        t = s.index[i]
        before = values[max(0, i-window):i][blocks[max(0, i-window):i] == blocks[i]]
        after = values[i+1:i+1+window][blocks[i+1:i+1+window] == blocks[i]]
        if not len(before) or not len(after):
            continue
        left, right = np.median(before), np.median(after)
        spread = max(np.median(np.abs(before - left)), np.median(np.abs(after - right)))
        amplitude = max(_CHANGE[var], 5 * spread)
        season = _season(t)
        stable = len(before) >= 2 and len(after) >= 2 and spread <= _CHANGE[var] / 4
        next_jump = i + 1 < len(s) and jumps[i+1]
        rebound = (next_jump and abs(left-right) <= amplitude
                   and min(abs(values[i]-left), abs(values[i]-right)) > amplitude
                   and delta[i] * delta[i+1] < 0)
        if rebound:
            skip_return.add(i + 1)
        peers = _changed_peers(df, var, t - gap, t + gap)
        context = f"{season.capitalize()}. Медианы до и после: {left:g} и {right:g} {meta.unit}."
        if peers:
            context += " Сопутствующие изменения: " + ", ".join(peers) + "."
        else:
            context += " Сопутствующих изменений не обнаружено по доступным рядам."
        if rebound:
            priority = IMPORTANT if (stable and not peers and
                                     (season != "межсезонье" or abs(delta[i]) >= 2 * amplitude)) else REVIEW
            problem = f"Выброс с быстрым возвратом ({delta[i]:+.1f} {meta.unit})"
        else:
            persistent_offset = stable and abs(right-left) >= 1.5 * amplitude and abs(values[i]-right) <= amplitude / 2
            if peers or season == "межсезонье":
                priority = WEATHER
            else:
                priority = IMPORTANT if persistent_offset else REVIEW
            problem = f"Скачок уровня ({delta[i]:+.1f} {meta.unit})"
        if not (meta.limits[0] <= values[i] <= meta.limits[1]):
            priority = IMPORTANT
            context += " Значение также вне диапазона проверки."
        yield t, t, var, values[i], problem, priority, context, 1


def _plateaus(df, var, s, gap, stuck_hours):
    if var not in _PEERS:
        return
    elapsed = s.index.to_series().diff()
    run = ((s != s.shift()) | (elapsed > gap)).cumsum()
    edges = np.flatnonzero(run.ne(run.shift()).to_numpy())
    for i, j in zip(edges, np.r_[edges[1:], len(s)]):
        if j - i < 4:
            continue
        t, end, value = s.index[i], s.index[j-1], s.iloc[i]
        hours = (end - t) / pd.Timedelta("1h")
        season = _season(t)
        if var.startswith("wind_") and value == 0:
            continue
        minimum = max(24, stuck_hours) if var.startswith("soil_") else stuck_hours
        if var == "rh" and value >= 95:
            minimum = max(48, minimum)
        if hours < minimum:
            continue
        peers = _changed_peers(df, var, t, end)
        if var == "rh" and value >= 95 and not peers:
            continue
        release = (i > 0 and t - s.index[i-1] <= gap and abs(s.iloc[i-1] - value) >= _CHANGE[var]) or (
            j < len(s) and s.index[j] - end <= gap and abs(s.iloc[j] - value) >= _CHANGE[var])
        priority = IMPORTANT if hours >= 2 * minimum or (peers and release) else REVIEW
        context = f"{season.capitalize()}. Длительность по отметкам времени: {hours:g} ч."
        if peers:
            context += " За это время менялись: " + ", ".join(peers) + "."
        if release:
            context += " На краю серии заметен скачок."
        yield t, end, var, value, "Одинаковое значение подряд", priority, context, j - i


def quality_issues(df: pd.DataFrame, stuck_hours: int = 6) -> pd.DataFrame:
    """Диагностика с контекстом. Пометки не меняют исходные наблюдения."""
    df = df.sort_index()
    step = CV.infer_step(df.index)
    gap = 1.75 * step
    if step >= pd.Timedelta("30min"):
        gap = max(gap, pd.Timedelta("1h"))
    issues = []
    for var in VARIABLES.keys() & df.columns:
        s = df[var].dropna()
        lo, hi = VARIABLES[var].limits
        for t, x in s[(s < lo) | (s > hi)].items():
            issues.append((t, t, var, x, f"Вне диапазона [{lo}; {hi}]", IMPORTANT,
                           "Проверка физически правдоподобного диапазона.", 1))
        if not s.empty and pd.Timedelta(0) < step < pd.Timedelta("20h"):
            issues.extend(_excursions(df, var, s, step, gap))
            issues.extend(_plateaus(df, var, s, gap, stuck_hours))
    if {"t_min", "t_max"} <= set(df.columns):
        for t, v in df.loc[df["t_min"] > df["t_max"], "t_min"].items():
            issues.append((t, t, "t_min", v, "Tmin > Tmax", IMPORTANT,
                           "Минимальная температура выше максимальной.", 1))
    return pd.DataFrame(issues, columns=ISSUE_COLUMNS).sort_values(["Время", "Параметр"]).reset_index(drop=True)


def period_records(rows: pd.DataFrame, start, end) -> pd.DataFrame:
    """Интервалы включаются, если они пересекают выбранные даты."""
    if rows.empty:
        return rows
    ends = rows.get("Конец", rows["Время"])
    return rows[(ends >= pd.Timestamp(start)) & (rows["Время"] < pd.Timestamp(end) + pd.Timedelta("1D"))]


def exclusions_for(reports, station: str) -> pd.DataFrame:
    return pd.DataFrame([event for report in reports for event in report.exclusions if event[0] == station],
                        columns=EXCLUSION_COLUMNS)


def important_events(issues: pd.DataFrame, exclusions: pd.DataFrame) -> pd.DataFrame:
    detected = issues[issues["Приоритет"] == IMPORTANT].copy()
    # Для одного срока показываем диагноз с окружением, сохраняя все отдельные проверки в журнале.
    detected = detected.sort_values("Проблема").drop_duplicates(["Время", "Конец", "Параметр"], keep="last")
    detected["Статус"] = "Требуется проверка"
    found = []
    for (var, reason, file), rows in exclusions.groupby(["Параметр", "Причина", "Файл"]):
        rows = rows.sort_values("Время")
        # Близкие по времени записи с одной причиной образуют эпизод в сводке.
        for _, group in rows.groupby(rows["Время"].diff().gt(pd.Timedelta("3h")).cumsum()):
            first, last = group.iloc[0], group.iloc[-1]
            lo, hi = group["Значение"].min(), group["Значение"].max()
            found.append((first["Время"], last["Время"], var, first["Значение"], reason, IMPORTANT,
                          f"Источник: {file}. Диапазон исключённых значений: {lo:g}…{hi:g}.",
                          len(group), "Исключено при импорте"))
    confirmed = pd.DataFrame(found, columns=[*ISSUE_COLUMNS, "Статус"])
    if detected.empty:
        return confirmed.sort_values("Время")
    if confirmed.empty:
        return detected.sort_values("Время")
    return pd.concat([detected, confirmed], ignore_index=True).sort_values("Время")


def observation_var(raw, var: str) -> str:
    if var in ("t_min", "t_max") and (var not in raw or not raw[var].notna().any()):
        return "t_air"
    return var


def audit_months(raw, daily, monthly, exclusions, var, start, end) -> pd.DataFrame:
    """Числа срочных значений, вошедших в месячные расчёты, и причины исключения."""
    source = observation_var(raw, var)
    s = raw[source].loc[start:pd.Timestamp(end) + pd.Timedelta("1D") - pd.Timedelta("1ns")].dropna()
    date = s.index.normalize()
    month = date.to_period("M")
    full_dates = daily[var].dropna().index
    eligible = date.isin(full_dates)
    flags = period_records(exclusions[exclusions["Параметр"] == source], start, end)
    flag_month = flags["Время"].dt.to_period("M") if len(flags) else None
    result = []
    for ms, value in monthly[var].items():
        p = ms.to_period("M")
        observed = month == p
        valid = pd.notna(value)
        used = int((observed & eligible).sum()) if valid else 0
        incomplete = int((observed & ~eligible).sum())
        invalid_month = int((observed & eligible).sum()) if not valid else 0
        reasons = flags.loc[flag_month == p, "Причина"].value_counts() if flag_month is not None else {}
        n_flags = int(sum(n for _, n in reasons.items()))
        reason_text = ", ".join(f"{reason} × {n}" for reason, n in reasons.items())
        cal = pd.date_range(max(ms, pd.Timestamp(start)), min(ms + pd.offsets.MonthEnd(0), pd.Timestamp(end)))
        result.append({"Месяц": str(p), "Значение": value, "Число дней в выборе": len(cal),
                       "Полных дней": int(cal.isin(full_dates).sum()), "Использовано": used,
                       "Исключено": incomplete + invalid_month + n_flags, "Неполные сутки": incomplete,
                       "Неполный месяц": invalid_month, "При импорте": n_flags, "Причины при импорте": reason_text})
    return pd.DataFrame(result)


def export_sheets(raw, daily, monthly, issues, exclusions, station, var, start, end):
    selected = period_records(exclusions, start, end)
    diagnostics = period_records(issues, start, end)
    important = important_events(diagnostics, selected)
    if important.empty:
        important = pd.DataFrame({"Результат": ["За выбранный период важных предупреждений нет."]})
    else:
        important["Параметр"] = important["Параметр"].map(label)
    source = observation_var(raw, var)
    picked = pd.DataFrame({"Поле": ["Станция", "Параметр для проверки расчёта", "Исходный показатель", "Начало", "Конец",
                                    "Подтверждённые исключения", "Контекстные предупреждения"],
                           "Значение": [station, label(var), label(source), str(pd.Timestamp(start).date()), str(pd.Timestamp(end).date()),
                                        "Флаг источника, конфликтный дубль или значение-заглушка.",
                                        "Группируются по эпизодам и сохраняются для проверки специалистом."]})
    sheets = {IMPORTANT_SHEET: important, SELECTION_SHEET: picked}
    if source in raw and var in monthly:
        sheets[AUDIT_SHEET] = audit_months(raw, daily, monthly, exclusions, var, start, end)
    if len(selected):
        sheets[EXCLUSIONS_SHEET] = selected.assign(Параметр=selected["Параметр"].map(label))
    return sheets


def write_review_sheets(workbook, sheets):
    """Оформляет одни и те же листы в обеих Excel-выгрузках."""
    from openpyxl.styles import Alignment, Font, PatternFill
    from openpyxl.utils import get_column_letter

    for name, frame in sheets.items():
        ws = workbook.create_sheet(name)
        ws.append(frame.columns.tolist())
        for row in frame.astype(object).where(frame.notna(), None).itertuples(index=False, name=None):
            ws.append(row)
            for cell in ws[ws.max_row]:
                if isinstance(cell.value, str):
                    cell.data_type = "s"
        ws.freeze_panes = "A2"
        ws.auto_filter.ref = ws.dimensions
        if name == IMPORTANT_SHEET:
            ws.sheet_properties.tabColor = "C46618"
        for cell in ws[1]:
            cell.fill = PatternFill("solid", fgColor="234535")
            cell.font = Font(color="FFFFFF", bold=True)
            cell.alignment = Alignment(wrap_text=True, vertical="center")
        ws.row_dimensions[1].height = 32
        for i, column in enumerate(frame.columns, 1):
            wide = column in ("Проблема", "Контекст", "Причины при импорте", "Параметр", "Файл")
            width = 45 if wide else 22
            if column == "Значение" and (name == SELECTION_SHEET or "Результат" in frame):
                width = 62
            if column == "Результат":
                width = 62
            ws.column_dimensions[get_column_letter(i)].width = width
            if wide or name == SELECTION_SHEET:
                for cells in ws.iter_cols(min_col=i, max_col=i, min_row=2):
                    for cell in cells:
                        cell.alignment = Alignment(vertical="center", wrap_text=True)
        if name in (IMPORTANT_SHEET, SELECTION_SHEET):
            for row in range(2, ws.max_row + 1):
                ws.row_dimensions[row].height = 43
