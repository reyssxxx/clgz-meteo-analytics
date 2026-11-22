"""Автоматические текстовые выводы по выбранному периоду.

Только правила и расчёты из analytics.py — без нейросети, поэтому каждый вывод воспроизводим
и подкреплён числом. Список фактов (Insight) можно отдельно передать в LLM для пересказа.
"""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd

from . import analytics as A
from . import coverage as CV
from .schema import VARIABLES

MONTHS_NOM = ["январь", "февраль", "март", "апрель", "май", "июнь", "июль", "август", "сентябрь",
              "октябрь", "ноябрь", "декабрь"]
MONTHS_GEN = ["января", "февраля", "марта", "апреля", "мая", "июня", "июля", "августа", "сентября",
              "октября", "ноября", "декабря"]


@dataclass
class Insight:
    kind: str      # "main" — главное, "trend", "extreme", "quality", "agro"
    level: str     # "normal" | "warm" | "cold" | "wet" | "dry" | "warn"
    text: str


# ------------------------------------------------------------------ форматирование

def num(x: float, d: int = 1, sign: bool = False) -> str:
    s = f"{x:+.{d}f}" if sign else f"{x:.{d}f}"
    return s.replace(".", ",").replace("-", "−")


def plural(n: int, forms: tuple[str, str, str]) -> str:
    n = abs(int(n))
    if n % 10 == 1 and n % 100 != 11:
        f = forms[0]
    elif 2 <= n % 10 <= 4 and not 12 <= n % 100 <= 14:
        f = forms[1]
    else:
        f = forms[2]
    return f"{n} {f}"


DAYS = ("день", "дня", "дней")


def _p(p: float) -> str:
    return "p < 0,001" if p < 0.001 else f"p = {num(p, 3)}"


def _ru_numbers(text: str) -> str:
    import re
    return re.sub(r"(-?)(\d+)\.(\d+)", lambda m: ("−" if m.group(1) else "") + f"{m.group(2)},{m.group(3)}", text)
YEARS = ("год", "года", "лет")


def date_ru(ts: pd.Timestamp, year: bool = True) -> str:
    return f"{ts.day} {MONTHS_GEN[ts.month - 1]}" + (f" {ts.year}" if year else "")


def describe_period(start: pd.Timestamp, end: pd.Timestamp) -> str:
    """Название периода в винительном падеже — для фраз «за …»."""
    start, end = pd.Timestamp(start).normalize(), pd.Timestamp(end).normalize()
    month_end = start + pd.offsets.MonthEnd(0)
    if start.day == 1 and end == month_end:
        return f"{MONTHS_NOM[start.month - 1]} {start.year}"
    if start.month == 1 and start.day == 1 and end.month == 12 and end.day == 31:
        return f"{start.year} год" if start.year == end.year else f"{start.year}–{end.year} годы"
    seasons = {(12, 2): "зиму", (3, 5): "весну", (6, 8): "лето", (9, 11): "осень"}
    if start.day == 1 and end == end + pd.offsets.MonthEnd(0) and (start.month, end.month) in seasons:
        name = seasons[(start.month, end.month)]
        yr = f"{start.year}/{str(end.year)[2:]}" if start.month == 12 else str(start.year)
        return f"{name} {yr}"
    if start.day == 1 and end == end + pd.offsets.MonthEnd(0) and start.year == end.year:
        return f"{MONTHS_NOM[start.month - 1]}–{MONTHS_NOM[end.month - 1]} {start.year}"
    return f"период {start:%d.%m.%Y}–{end:%d.%m.%Y}"


# ------------------------------------------------------------------ тот же период в другие годы

def same_window(daily: pd.DataFrame, var: str, start, end, min_coverage: float = 0.8) -> pd.Series:
    """Значение параметра за то же календарное окно в каждый год ряда (среднее или сумма).
    Индекс — год окончания окна. Окно длиннее года не сдвигается (вернётся одно значение)."""
    start, end = pd.Timestamp(start), pd.Timestamp(end)
    how = VARIABLES[var].period_agg
    how = "mean" if how == "circmean" else how
    s = daily[var]
    if (end - start).days > 366:
        w = s.loc[start:end]
        return pd.Series({end.year: getattr(w, how)()})
    first, last = s.first_valid_index(), s.last_valid_index()
    if first is None:
        return pd.Series(dtype=float)
    length = (end - start).days + 1
    # окно из целых месяцев (месяц, сезон, год): год входит, только если каждый его месяц валиден
    # по правилу задания (≤ 10 неполных дней, нет 5 подряд); иное окно — если есть ≥ 80 % дней
    whole_months = start.day == 1 and end == end + pd.offsets.MonthEnd(0)
    out = {}
    for k in range(first.year - end.year - 1, last.year - start.year + 2):
        a, b = start + pd.DateOffset(years=k), end + pd.DateOffset(years=k)
        if whole_months:
            b = b + pd.offsets.MonthEnd(0)   # 29 февраля и т. п.
            w = s.reindex(pd.date_range(a, b, freq="D"))
            ok = CV.monthly_valid_from_daily(w)
            if len(ok) and ok.all():
                out[b.year] = getattr(w, how)()
        else:
            w = s.loc[a:b]
            if w.count() >= min_coverage * length:
                out[b.year] = getattr(w, how)()
    return pd.Series(out, dtype=float).sort_index()


def rank_phrase(values: pd.Series, year: int) -> str | None:
    """«это максимум за 36 лет наблюдений» / «3-е место по величине из 36 лет».
    None, если место ничем не примечательно или лет слишком мало."""
    if year not in values or len(values) < 8:
        return None
    n = len(values)
    r_high = int((values > values[year]).sum()) + 1
    r_low = int((values < values[year]).sum()) + 1
    if r_high == 1:
        return f"это максимум за {plural(n, YEARS)} наблюдений"
    if r_low == 1:
        return f"это минимум за {plural(n, YEARS)} наблюдений"
    if r_high <= 3:
        return f"{r_high}-е место по величине из {n} лет"
    if r_low <= 3:
        return f"{r_low}-е место с конца из {n} лет"
    return None


# ------------------------------------------------------------------ правила

def _norm_window(daily, var, start, end, base):
    """Значения того же окна по годам и «среднее за выбранный период» по годам базы.
    Возвращает (значения по годам, среднее, подпись «за N лет»). По заданию это не «климатическая норма»:
    всегда указываем, сколько лет вошло в расчёт."""
    vals = same_window(daily, var, start, end)
    if base:
        vb = vals.loc[int(base[0]):int(base[1])]
    else:
        vb = vals
    if len(vb) >= 3:
        return vals, vb.mean(), base_phrase(vb.index)
    return vals, np.nan, ""


def base_phrase(years) -> str:
    """«за 14 лет (1993–2016)» — подпись к среднему за выбранный период."""
    ys = sorted(int(y) for y in years)
    if not ys:
        return ""
    return f"за {plural(len(ys), YEARS)} ({ys[0]}–{ys[-1]})" if len(ys) > 1 else f"за {ys[0]} год"


def _temperature(daily, start, end, base, label) -> list[Insight]:
    out = []
    d = daily.loc[start:end]
    if "t_air" not in d or d["t_air"].count() == 0:
        return out
    vals, norm, nb = _norm_window(daily, "t_air", start, end, base)
    mean = d["t_air"].mean()
    text = f"Средняя температура за {label} — {num(mean)} °C"
    level = "normal"
    if not np.isnan(norm):
        dev = mean - norm
        if abs(dev) < 0.3:
            text += f", практически как в среднем {nb} ({num(norm)} °C)"
        else:
            text += f", на {num(abs(dev))} °C {'выше' if dev > 0 else 'ниже'} среднего {nb} ({num(norm)} °C)"
            level = "warm" if dev > 0 else "cold"
        rp = rank_phrase(vals, pd.Timestamp(end).year)
        if rp:
            text += f"; {rp}"
    out.append(Insight("main", level, text + "."))

    if (pd.Timestamp(end) - pd.Timestamp(start)).days >= 59:
        m = A.monthly_anomalies(daily, "t_air", base).loc[start:end].dropna(subset=["anomaly"])
        if len(m) >= 2:
            hi, lo = m["anomaly"].idxmax(), m["anomaly"].idxmin()
            parts = []
            if m.loc[hi, "anomaly"] >= 1:
                parts.append(f"самый аномально тёплый месяц — {MONTHS_NOM[hi.month - 1]} {hi.year} "
                             f"({num(m.loc[hi, 'anomaly'], sign=True)} °C к среднему)")
            if m.loc[lo, "anomaly"] <= -1:
                parts.append(f"самый аномально холодный — {MONTHS_NOM[lo.month - 1]} {lo.year} "
                             f"({num(m.loc[lo, 'anomaly'], sign=True)} °C)")
            if parts:
                out.append(Insight("main", "normal", (parts[0][0].upper() + "; ".join(parts)[1:]) + "."))
    return out


def _precip(daily, start, end, base, label) -> list[Insight]:
    d = daily.loc[start:end]
    if "precip" not in d or d["precip"].count() == 0:
        return []
    n_years = (pd.Timestamp(end) - pd.Timestamp(start)).days / 365.25
    if n_years > 2:
        annual = A.annual_series(d, "precip").dropna()
        return [Insight("main", "normal",
                        f"Среднегодовая сумма осадков — {num(annual.mean(), 0)} мм "
                        f"(от {num(annual.min(), 0)} мм в {annual.idxmin()} до {num(annual.max(), 0)} мм "
                        f"в {annual.idxmax()}); максимум за сутки — {num(d['precip'].max())} мм "
                        f"({date_ru(d['precip'].idxmax())}).")] if len(annual) else []
    vals, norm, nb = _norm_window(daily, "precip", start, end, base)
    total = d["precip"].sum()
    text = f"Выпало {num(total, 0)} мм осадков"
    level = "normal"
    if not np.isnan(norm) and norm > 0:
        pct = 100 * total / norm
        text += f" — {num(pct, 0)}% от среднего {nb}"
        if pct >= 120:
            level, text = "wet", text + ", период влажнее обычного"
        elif pct <= 80:
            level, text = "dry", text + ", период суше обычного"
        rp = rank_phrase(vals, pd.Timestamp(end).year)
        if rp:
            text += f"; {rp}"
    wet = int((d["precip"] >= 1).sum())
    text += f". Дней с осадками ≥ 1 мм: {wet}"
    mx = d["precip"].max()
    if mx >= 10:
        text += f"; максимум за сутки — {num(mx)} мм ({date_ru(d['precip'].idxmax())})"
    return [Insight("main", level, text + ".")]


def _records(daily, start, end) -> list[Insight]:
    """Суточные рекорды: значение выше/ниже, чем в этот же день во все предыдущие годы."""
    out = []
    if (pd.Timestamp(end) - pd.Timestamp(start)).days > 366:
        return out  # на многолетнем отрезке «рекордов» сотни — это не информативно
    for var, word, better in (("t_max", "тепла", "max"), ("t_min", "холода", "min")):
        if var not in daily:
            continue
        s = daily[var].dropna()
        if s.index.year.nunique() < 10:
            continue
        doy = s.index.strftime("%m-%d")
        prev = s.groupby(doy).transform(lambda x: x.shift().expanding().max() if better == "max"
                                        else x.shift().expanding().min())
        enough = s.groupby(doy).cumcount() >= 9  # рекорд имеет смысл, если до него ≥ 9 лет
        rec = (s > prev) if better == "max" else (s < prev)
        rec = rec & enough
        r = rec.loc[start:end]
        n = int(r.sum())
        if n:
            dates = ", ".join(date_ru(t) for t in r[r].index[:3]) + ("…" if n > 3 else "")
            out.append(Insight("extreme", "warm" if better == "max" else "cold",
                               f"Суточные рекорды {word}: {n} ({dates}) — в эти дни было "
                               f"{'теплее' if better == 'max' else 'холоднее'}, чем в те же даты всех предыдущих лет."))
    return out


def _extremes(daily, start, end) -> list[Insight]:
    d = daily.loc[start:end]
    out = []
    if "t_max" in d and d["t_max"].count():
        out.append(Insight("extreme", "normal",
                           f"Абсолютный максимум — {num(d['t_max'].max())} °C ({date_ru(d['t_max'].idxmax())}), "
                           + (f"минимум — {num(d['t_min'].min())} °C ({date_ru(d['t_min'].idxmin())})."
                              if "t_min" in d and d["t_min"].count() else "")))
    ev = A.extreme_events(d)
    if len(ev):
        for kind, forms, longest_word in (
                ("Волна жары", ("волна жары", "волны жары", "волн жары"), "самая долгая"),
                ("Волна холода", ("волна холода", "волны холода", "волн холода"), "самая долгая"),
                ("Засушливый период", ("засушливый период", "засушливых периода", "засушливых периодов"),
                 "самый долгий")):
            e = ev[ev["Явление"] == kind]
            if len(e):
                longest = e.loc[e["Дней"].idxmax()]
                out.append(Insight("extreme", "warm" if "жар" in kind or "Засуш" in kind else "cold",
                                   f"{plural(len(e), forms).capitalize()}; {longest_word} — "
                                   f"{plural(longest['Дней'], DAYS)} с {date_ru(longest['Начало'])} "
                                   f"({_ru_numbers(longest['Характеристика'])})."))
    return out


def _trends(daily, start, end, alpha=0.05) -> list[Insight]:
    d = daily.loc[start:end]
    tt = A.trend_table(d)
    if tt.empty or tt["Лет"].max() < 10:
        return []
    out = []
    for _, r in tt.iterrows():
        if r["p (Манн–Кендалл)"] < alpha:
            meta = VARIABLES[r["var"]]
            out.append(Insight("trend", "warm" if r["var"].startswith("t_") and r["Наклон Сена / 10 лет"] > 0 else "normal",
                               f"{meta.label}: статистически значимый {'рост' if r['Наклон Сена / 10 лет'] > 0 else 'спад'} "
                               f"{num(r['Наклон Сена / 10 лет'], 2, sign=True)} {meta.unit} за 10 лет "
                               f"({r['Период']}, {_p(r['p (Манн–Кендалл)'])})."))
    insignificant = [VARIABLES[v].label.lower() for v, p in zip(tt["var"], tt["p (Манн–Кендалл)"]) if p >= alpha]
    if insignificant:
        out.append(Insight("trend", "normal", "Без значимого тренда: " + ", ".join(insignificant) + "."))
    return out


def _agro(daily, start, end) -> list[Insight]:
    a = A.agro_indices(daily)
    years = [y for y in a.index if pd.Timestamp(start) <= pd.Timestamp(y, 7, 1) <= pd.Timestamp(end)]
    if a.empty or len(years) != 1 or "Σ активных T > 10 °C" not in a:
        return []
    y, r = years[0], a.loc[years[0]]
    gdd, mean_gdd = r["Σ активных T > 10 °C"], a["Σ активных T > 10 °C"].mean()
    text = (f"Сумма активных температур в {y} году — {num(gdd, 0)} °C "
            f"({num(gdd - mean_gdd, 0, sign=True)} к среднему многолетнему)")
    if "ГТК Селянинова" in r and not np.isnan(r["ГТК Селянинова"]):
        g = r["ГТК Селянинова"]
        zone = "засушливо" if g < 1 else "достаточное увлажнение" if g <= 1.5 else "избыточное увлажнение"
        text += f"; ГТК = {num(g, 2)} — {zone}"
    if "Безморозный период, дней" in r and not pd.isna(r["Безморозный период, дней"]):
        text += f"; безморозный период {plural(r['Безморозный период, дней'], DAYS)}"
    return [Insight("agro", "normal", text + ".")]


def _quality(daily, issues, start, end) -> list[Insight]:
    d = daily.loc[start:end]
    n_days = (pd.Timestamp(end) - pd.Timestamp(start)).days + 1
    out = []
    weak = [VARIABLES[v].label.lower() for v in d.columns if v in VARIABLES and d[v].count() < 0.9 * n_days
            and d[v].count() > 0]
    if weak:
        out.append(Insight("quality", "warn", "Неполные данные (< 90% дней): " + ", ".join(weak)
                           + ". Суммы и экстремумы по ним могут быть занижены."))
    if issues is not None and len(issues):
        i = issues[(issues["Время"] >= pd.Timestamp(start)) & (issues["Время"] <= pd.Timestamp(end) + pd.Timedelta("1D"))]
        bad = i["Проблема"].str.startswith(("Вне", "Выброс")).sum()
        if bad:
            out.append(Insight("quality", "warn", f"Исключено ошибочных значений: {bad} "
                                                  "(вне физического диапазона или одиночные выбросы)."))
    return out


def generate(daily: pd.DataFrame, start, end, base: tuple | None = None,
             issues: pd.DataFrame | None = None) -> list[Insight]:
    start, end = pd.Timestamp(start), pd.Timestamp(end)
    label = describe_period(start, end)
    res: list[Insight] = []
    for f in (lambda: _temperature(daily, start, end, base, label),
              lambda: _precip(daily, start, end, base, label),
              lambda: _extremes(daily, start, end),
              lambda: _records(daily, start, end),
              lambda: _agro(daily, start, end),
              lambda: _trends(daily, start, end),
              lambda: _quality(daily, issues, start, end)):
        try:
            res += f()
        except Exception as e:  # noqa: BLE001 — один сломанный вывод не должен ронять остальные
            res.append(Insight("quality", "warn", f"Не удалось рассчитать часть выводов: {e}"))
    return res


def to_markdown(items: list[Insight]) -> str:
    titles = {"main": "Главное", "extreme": "Экстремумы", "agro": "Агроклимат", "trend": "Тренды",
              "quality": "Качество данных"}
    lines = []
    for kind, title in titles.items():
        group = [i for i in items if i.kind == kind]
        if group:
            lines.append(f"**{title}**")
            lines += [f"- {i.text}" for i in group]
            lines.append("")
    return "\n".join(lines)
