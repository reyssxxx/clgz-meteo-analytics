"""Готовые для интерфейса ответы: данные графиков, карточки, таблицы — в виде JSON-совместимых dict.

Без расчётной логики: всё считает analytics.py / insights.py, здесь только сборка под конкретный экран.
"""
from __future__ import annotations

import math
from datetime import date, datetime

import numpy as np
import pandas as pd

from . import analytics as A
from . import insights as I
from .loader import station_from_filename
from .schema import STATION_PREFIXES, VARIABLES, label

# категории — по месту среди тех же периодов всех лет станции (не «климатическая норма»)
CATEGORIES = ["Рекордно низко", "Намного ниже среднего", "Ниже среднего", "Около среднего", "Выше среднего",
              "Намного выше среднего", "Рекордно высоко"]


# ------------------------------------------------------------------ JSON

def js(x):
    """Рекурсивно приводит pandas/numpy к JSON: NaN → None, даты → ISO."""
    if isinstance(x, dict):
        return {str(k): js(v) for k, v in x.items()}
    if isinstance(x, (list, tuple)):
        return [js(v) for v in x]
    if isinstance(x, pd.DataFrame):
        return [js(r) for r in x.to_dict(orient="records")]
    if isinstance(x, (pd.Series, np.ndarray, pd.Index)):
        return [js(v) for v in list(x)]
    if isinstance(x, (pd.Timestamp, datetime)):
        return None if pd.isna(x) else x.strftime("%Y-%m-%d") if x == pd.Timestamp(x).normalize() else x.isoformat()
    if isinstance(x, date):
        return x.isoformat()
    if isinstance(x, pd.Timedelta):
        return str(x)
    if isinstance(x, (np.integer,)):
        return int(x)
    if isinstance(x, (np.floating, float)):
        return None if math.isnan(x) or math.isinf(x) else round(float(x), 4)
    if isinstance(x, np.bool_):
        return bool(x)
    if x is pd.NaT or x is None:
        return None
    try:
        if pd.isna(x):
            return None
    except (TypeError, ValueError):
        pass
    return x


def var_meta(v: str) -> dict:
    m = VARIABLES[v]
    return {"id": v, "label": m.label, "unit": m.unit, "sum": m.period_agg == "sum"}


# ------------------------------------------------------------------ категория «насколько необычно»

def category(values: pd.Series, year: int) -> dict | None:
    """Класс по месту среди тех же периодов всех лет (как в NOAA Climate at a Glance):
    рекорд, крайние 10 %, крайняя треть, средняя треть."""
    v = values.dropna()
    if year not in v or len(v) < 8:
        return None
    n = len(v)
    x = v[year]
    rank_high = int((v > x).sum()) + 1
    pct = (v < x).sum() / (n - 1)           # 0 — минимум, 1 — максимум
    if rank_high == 1:
        k = 6
    elif rank_high == n:
        k = 0
    elif pct >= 0.9:
        k = 5
    elif pct <= 0.1:
        k = 1
    elif pct >= 2 / 3:
        k = 4
    elif pct <= 1 / 3:
        k = 2
    else:
        k = 3
    return {"index": k, "label": CATEGORIES[k], "rank": rank_high, "n": n}


# ------------------------------------------------------------------ экраны

def meta(raw: pd.DataFrame, dailies: dict[str, pd.DataFrame]) -> dict:
    out = []
    for name, d in dailies.items():
        vs = [v for v in d.columns if v in VARIABLES and d[v].notna().any()]
        out.append({"id": name, "vars": vs, "start": d.index.min(), "end": d.index.max(),
                    "days": int(len(d))})
    # порядок как в задании: АМЕ-60, затем точки Сокол М1; прочие (загруженные) — следом
    order = {n: i for i, n in enumerate(STATION_PREFIXES.values())}
    out.sort(key=lambda s: (order.get(s["id"], 99), s["id"]))
    for s_ in out:
        s_["device"] = "Сокол М1" if s_["id"].startswith("Кр") else ("АМЕ-60" if s_["id"] == "АМЕ-60" else "")
    all_vars = [v for v in VARIABLES if any(v in s["vars"] for s in out)]
    return js({"stations": out, "variables": [var_meta(v) for v in all_vars]})


def _daily_reference(daily: pd.DataFrame, var: str, idx: pd.DatetimeIndex, base) -> pd.DataFrame:
    return A.doy_climatology(daily, var, base).reindex(A.calendar_day(idx)).set_axis(idx)


def years_chart(daily: pd.DataFrame, var: str, start, end, base) -> dict:
    """Левый главный график. Окно до года — «все годы серыми линиями» (для сумм — нарастающим итогом);
    длиннее — помесячный ряд с нормой."""
    start, end = pd.Timestamp(start), pd.Timestamp(end)
    meta_v = VARIABLES[var]
    is_sum = meta_v.period_agg == "sum"
    if (end - start).days <= 366:
        idx = pd.date_range(start, end, freq="D")
        s = daily[var]
        first, last = s.first_valid_index(), s.last_valid_index()
        lines = []
        comparable = I.same_window(daily, var, start, end).index
        for k in range(first.year - end.year, last.year - start.year + 1):
            shifted = idx + pd.DateOffset(years=k)
            year = shifted[-1].year
            if year not in comparable or k == 0:
                continue
            vals = s.reindex(shifted).where(shifted.day == idx.day).to_numpy()
            if is_sum:
                vals = np.nancumsum(vals)
            lines.append({"year": int(year), "y": vals})
        cur = s.reindex(idx)
        cur_vals = cur.cumsum() if is_sum else cur
        ref = _daily_reference(daily, var, idx, base)
        norm = ref["norm"]
        if is_sum:
            norm = norm.cumsum()
        mark = None
        an = A.difference(cur, ref["norm"], var).dropna()
        if not an.empty and not is_sum:
            t = an.abs().idxmax()
            mark = {"x": t, "y": cur[t], "text": f"{I.date_ru(t)}: {I.num(cur[t])} {meta_v.unit}"}
        elif is_sum and cur.notna().any():
            t = cur_vals.last_valid_index()
            mark = {"x": t, "y": cur_vals[t], "text": f"итого {I.num(cur_vals[t], 0)} {meta_v.unit}"}
        return js({"mode": "years", "x": idx, "lines": lines, "current": {"year": int(end.year), "y": cur_vals},
                   "norm": norm, "base_years": ref["years"], "mark": mark, "cumulative": is_sum, "var": var_meta(var)})
    m = A.aggregate(daily.loc[start:end], "MS")
    norms, years = A.monthly_reference(daily, base)
    band = None
    if var == "t_air" and {"t_min", "t_max"} <= set(m.columns):
        band = {"lo": m["t_min"], "hi": m["t_max"]}
    return js({"mode": "series", "x": m.index, "y": m[var], "norm": norms[var].reindex(m.index.month),
               "base_years": years[var].reindex(m.index.month), "band": band, "var": var_meta(var)})


def anomaly_heatmap(daily: pd.DataFrame, var: str, start, end, base) -> dict:
    """Правый главный график: отклонения по месяцам (годы × месяцы). Для сумм — % от нормы."""
    man = A.monthly_anomalies(daily, var, base)
    is_sum = VARIABLES[var].period_agg == "sum"
    val = (man["pct_of_norm"] - 100) if is_sum else man["anomaly"]
    t = pd.DataFrame({"y": man.index.year, "m": man.index.month, "v": val}).pivot_table(index="y", columns="m", values="v")
    t = t.reindex(columns=range(1, 13))
    # пропущенные годы (у АМЕ-60 — 2000–2002) — пустые строки, иначе соседние клетки растягиваются
    if len(t):
        t = t.reindex(range(int(t.index.min()), int(t.index.max()) + 1))
    years = man.groupby(man.index.month)["years"].first().reindex(range(1, 13), fill_value=0)
    start, end = pd.Timestamp(start), pd.Timestamp(end)
    sel = [[int(p.year), int(p.month)] for p in pd.period_range(start, end, freq="M")] if (end - start).days <= 400 else []
    lim = float(np.nanpercentile(np.abs(t.to_numpy()), 97)) if t.notna().any().any() else 1.0
    return js({"years": t.index, "z": t.to_numpy(), "base_years": years, "selected": sel, "limit": max(lim, 0.5),
               "unit": "% к среднему" if is_sum else VARIABLES[var].unit, "var": var_meta(var), "inverted": is_sum or var in ("rh",)})


def ranking(daily: pd.DataFrame, var: str, start, end, base) -> dict:
    vals = I.same_window(daily, var, start, end)
    end = pd.Timestamp(end)
    if len(vals) < 2:
        return js({"years": [], "values": [], "var": var_meta(var)})
    vb = vals.loc[int(base[0]):int(base[1])] if base else vals
    how = "circmean" if VARIABLES[var].period_agg == "circmean" else "mean"
    norm = A._agg(vb, how) if len(vb) else np.nan
    cat = category(vals, end.year) if VARIABLES[var].period_agg != "circmean" else None
    return js({"years": vals.index, "values": vals.to_numpy(), "selected": int(end.year), "norm": norm,
               "base_label": I.base_phrase(vb.index), "category": cat, "var": var_meta(var)})


def facts(daily: pd.DataFrame, var: str, start, end, base, issues=None) -> dict:
    """Короткие факты для блока «Коротко о…»: крупное число + одна строка."""
    start, end = pd.Timestamp(start), pd.Timestamp(end)
    d = daily.loc[start:end]
    out = []
    short = (end - start).days <= 366
    cats = {}
    for v in [x for x in ("t_air", "precip") if x in d] + ([var] if var not in ("t_air", "precip") else []):
        if v not in d or d[v].count() == 0:
            continue
        meta_v = VARIABLES[v]
        vals = I.same_window(daily, v, start, end) if short else pd.Series(dtype=float)
        vb = vals.loc[int(base[0]):int(base[1])] if base and len(vals) else vals
        how = meta_v.period_agg
        norm = A._agg(vb, "circmean" if how == "circmean" else "mean") if len(vb) >= 3 else np.nan
        nb = I.base_phrase(vb.index) if len(vb) >= 3 else ""
        cur = vals.get(end.year, A._agg(d[v].dropna(), how))
        cat = category(vals, end.year) if len(vals) and how != "circmean" else None
        cats[v] = cat
        if meta_v.period_agg == "sum":
            if end.year in vals and np.isfinite(norm) and norm != 0:
                out.append({"value": f"{I.num(100 * cur / norm, 0)} %", "tone": "wet" if cur > 1.2 * norm else "dry" if cur < .8 * norm else "",
                            "text": f"от среднего {nb}, {I.num(cur, 0)} {meta_v.unit}"})
            else:
                ann = A.annual_series(d, v).dropna()
                if len(ann):
                    out.append({"value": f"{I.num(ann.mean(), 0)} {meta_v.unit}", "tone": "",
                                "text": f"{meta_v.label.lower()} в среднем за год, база {I.base_phrase(ann.index)}"})
        else:
            if end.year in vals and np.isfinite(norm):
                dev = A.difference(cur, norm, v)
                rank = f", {cat['rank']}-е место из {cat['n']} по величине" if cat else ""
                out.append({"value": f"{I.num(dev, 1, sign=True)} {meta_v.unit}", "tone": "warm" if dev > 0.3 else "cold" if dev < -0.3 else "",
                            "text": f"{meta_v.label.lower()} к среднему {nb} ({I.num(cur)} {meta_v.unit}){rank}"})
            else:
                qualifier = "по полным суткам" if end.year not in vals else "за период"
                out.append({"value": f"{I.num(cur)} {meta_v.unit}", "tone": "",
                            "text": f"{meta_v.label.lower()}, среднее {qualifier}"})
    ev = A.extreme_events(d)
    for kind, tone, word in (("Волна жары", "warm", "волна жары"), ("Волна холода", "cold", "волна холода"),
                             ("Засушливый период", "dry", "без дождей")):
        e = ev[ev["Явление"] == kind] if len(ev) else ev
        if len(e):
            r = e.loc[e["Дней"].idxmax()]
            out.append({"value": I.plural(r["Дней"], I.DAYS), "tone": tone,
                        "text": f"{word} с {I.date_ru(r['Начало'], year=False)}, {I._ru_numbers(r['Характеристика'])}"})
    if short:
        for x in I._records(daily, start, end):
            n = x.text.split(":")[1].split("(")[0].strip()
            what = "тепла" if "тепла" in x.text else "холода"
            dates = x.text.split("(")[1].split(")")[0]
            word = I.plural(int(n), ("суточный рекорд", "суточных рекорда", "суточных рекордов")).split(" ", 1)[1]
            out.append({"value": n, "tone": "warm" if what == "тепла" else "cold",
                        "text": f"{word} {what}: {dates}"})
        a = A.agro_indices(daily)
        yrs = [y for y in a.index if start <= pd.Timestamp(y, 7, 15) <= end] if len(a) else []
        if yrs and "Σ активных T > 10 °C" in a:
            ref = a["Σ активных T > 10 °C"].dropna()
            g = ref.get(yrs[0])
            if g is not None:
                out.append({"value": f"{I.num(g, 0)} °C", "tone": "",
                            "text": f"сумма активных температур, {I.num(g - ref.mean(), 0, sign=True)} к среднему {I.base_phrase(ref.index)}"})
    items = I.generate(daily, start, end, base, issues)
    return js({"title": I.describe_period(start, end), "facts": out[:6], "categories": cats,
               "insights": [{"kind": i.kind, "level": i.level, "text": i.text} for i in items],
               "markdown": I.to_markdown(items)})


def series(daily: pd.DataFrame, var: str, freq: str, start, end, base) -> dict:
    agg = A.aggregate(daily.loc[start:end], freq)
    out = {"x": agg.index, "y": agg[var] if var in agg else [], "var": var_meta(var), "freq": freq}
    if var == "t_air" and {"t_min", "t_max"} <= set(agg.columns):
        out["band"] = {"lo": agg["t_min"], "hi": agg["t_max"]}
    if var in agg and freq == "D":
        ref = _daily_reference(daily, var, agg.index, base)
        out.update(norm=ref["norm"], base_years=ref["years"])
    elif var in agg and freq == "MS":
        norms, years = A.monthly_reference(daily, base)
        out.update(norm=norms[var].reindex(agg.index.month), base_years=years[var].reindex(agg.index.month))
    return js(out)


def _plain_trend(r: dict) -> dict:
    sig = r["p (Манн–Кендалл)"] < 0.05
    slope = r["Наклон Сена / 10 лет"]
    return {"var": r["var"], "label": VARIABLES[r["var"]].label, "unit": VARIABLES[r["var"]].unit,
            "years": r["Лет"], "period": r["Период"], "mean": r["Среднее"], "slope": slope,
            "ols": r["Наклон МНК / 10 лет"], "p": r["p (Манн–Кендалл)"], "r2": r["R²"],
            "verdict": ("растёт" if slope > 0 else "снижается") if sig else "значимого изменения нет",
            "significant": bool(sig)}


def trends(daily: pd.DataFrame, var: str, start, end, base, month: int | None = None, season: int | None = None) -> dict:
    d = daily.loc[start:end]
    tt = A.trend_table(d, month=month, season=season)
    if month:
        agg = A.aggregate(d, "MS"); agg = agg[agg.index.month == month]
    elif season:
        agg = A.aggregate(d, "QS-DEC"); agg = agg[agg.index.month == season]
    else:
        agg = A.aggregate(d, "YS")
    s = pd.Series(dtype=float)
    if var in agg and len(agg):
        s = agg[var].set_axis(agg.index.year + ((agg.index.month == 12) & bool(season)))
        s = s.reindex(range(int(s.index.min()), int(s.index.max()) + 1))
    tr = A.trend(s) if len(s) and VARIABLES[var].period_agg != "circmean" else {}
    stripes = None
    if "t_air" in d:
        ann = A.annual_series(d, "t_air")
        ref = A.annual_series(daily.loc[slice(*base) if base else slice(None)], "t_air").dropna()
        if not ref.empty:
            stripes = {"years": ann.index, "anomaly": ann - ref.mean(), "base_label": I.base_phrase(ref.index)}
    return js({"table": [_plain_trend(r) for r in tt.to_dict(orient="records")],
               "series": {"years": s.index, "values": s.to_numpy(), "var": var_meta(var) if var in VARIABLES else None,
                          "ols_slope": tr.get("ols_slope"), "intercept": tr.get("intercept"),
                          "sen": tr.get("sen_slope"), "p": tr.get("mk_p"),
                          "smooth": s.rolling(5, center=True, min_periods=3).mean() if len(s) else []},
               "stripes": stripes})


def compare(daily: pd.DataFrame, var: str, a: tuple, b: tuple) -> dict:
    t, unit = A.compare_periods(daily, a, b)
    ca, ya = A.monthly_reference(daily.loc[slice(*a)])
    cb, yb = A.monthly_reference(daily.loc[slice(*b)])
    rows = []
    for r in t.to_dict(orient="records"):
        rows.append({"var": r["var"], "label": r["Параметр"], "a": r["Период A"], "b": r["Период B"],
                     "diff": r["Разница (B − A)"], "pct": r["Разница, %"], "p": r["p (Манн–Уитни)"],
                     "verdict": r["Вывод"], "significant": r["p (Манн–Уитни)"] < 0.05})
    return js({"rows": rows, "unit": unit,
               "a_label": I.describe_period(pd.Timestamp(a[0]), pd.Timestamp(a[1])),
               "b_label": I.describe_period(pd.Timestamp(b[0]), pd.Timestamp(b[1])),
               "cycle": {"a": ca[var] if var in ca else [None] * 12, "b": cb[var] if var in cb else [None] * 12,
                         "years_a": ya[var] if var in ya else [0] * 12, "years_b": yb[var] if var in yb else [0] * 12},
               "var": var_meta(var)})


def stations_view(dailies: dict, station: str, var: str, freq: str, start, end, ref: str | None,
                  fill_report: pd.DataFrame | None = None) -> dict:
    sub = {k: d.loc[start:end] for k, d in dailies.items()}
    wide = A.station_matrix(sub, var, freq)
    corr = A.station_correlation(sub, var)
    summary = []
    for name, d in sub.items():
        if var in d and d[var].notna().any():
            x = d[var]
            is_sum = VARIABLES[var].period_agg == "sum"
            summary.append({"station": name, "mean": A.annual_series(d, var).mean() if is_sum else x.mean(),
                            "min": x.min(), "max": x.max(), "completeness": 100 * x.count() / len(x)})
    hom = None
    others = [k for k in wide.columns if k != station]
    if station in wide.columns and others:
        ref = ref if ref in others else others[0]
        diff, r = A.homogeneity(dailies, station, ref, var)
        step = (r["after"] - r["before"]) if r["change"] is not None else None
        small = step is not None and abs(step) < (0.3 if var.startswith("t_") else 0.1 * abs(r["before"]) + 1e-9)
        hom = {"ref": ref, "years": diff.index, "diff": diff.to_numpy(), "change": r["change"], "p": r["p"],
               "before": r["before"], "after": r["after"], "step": step,
               "verdict": "ok" if r["p"] is None or np.isnan(r["p"]) or r["p"] >= 0.05 else ("small" if small else "shift")}
    return js({"x": wide.index, "series": {c: wide[c] for c in wide.columns}, "corr": {"names": list(corr.columns),
               "z": corr.to_numpy()}, "summary": summary, "homogeneity": hom, "others": others,
               "fill": fill_report.to_dict(orient="records") if fill_report is not None else None, "var": var_meta(var)})


def agro(daily: pd.DataFrame, start, end) -> dict:
    d = daily.loc[start:end]
    # агропоказатели считаются по полным годам — для сезона или месяца берём годы, в которые он попадает
    a = A.agro_indices(daily.loc[str(pd.Timestamp(start).year):str(pd.Timestamp(end).year)])
    ev = A.extreme_events(d)
    return js({"columns": list(a.columns), "rows": [{"year": int(y), **{c: a.loc[y, c] for c in a.columns}} for y in a.index],
               "events": ev})


def wind(df_raw: pd.DataFrame, daily: pd.DataFrame, subdaily: bool, start, end) -> dict:
    speed = next((v for v in ("wind_speed", "wind_gust") if v in daily), None)
    required = ["wind_dir"] + ([speed] if speed else [])
    if not set(required) <= set(daily.columns):
        return {"available": False}
    src = daily.loc[start:end].dropna(subset=required)
    if subdaily:
        raw = df_raw.loc[start:pd.Timestamp(end) + pd.Timedelta("1D") - pd.Timedelta("1ns")]
        src = raw[raw.index.normalize().isin(src.index)]
    rose, calm = A.wind_rose(src, speed)
    if rose.empty:
        return {"available": False}
    return js({"available": True, "sectors": list(rose.index), "classes": list(rose.columns),
               "values": {c: rose[c] for c in rose.columns}, "calm": calm, "from_daily": not subdaily})


def quality(raw_station: pd.DataFrame, daily: pd.DataFrame, issues: pd.DataFrame, reports: dict) -> dict:
    comp = A.completeness(daily)
    g = A.gaps(raw_station)
    kinds = issues["Проблема"].str.split().str[0].value_counts().to_dict() if len(issues) else {}
    return js({"completeness": {"years": comp.index, "vars": [label(c) for c in comp.columns], "z": comp.to_numpy()},
               "gaps": g.head(200), "gaps_total": len(g), "issue_kinds": kinds,
               "issues": issues.head(300).assign(Параметр=issues["Параметр"].map(label)) if len(issues) else [],
               "issues_total": len(issues),
               "files": [{"name": n, "station": station_from_filename(n), "summary": r.summary(), "mapping": r.mapping,
                          "time": r.time_columns, "station_column": r.station_column, "unmapped": r.unmapped,
                          "rows": r.rows_out, "flagged": {k: v for k, v in r.qc_flagged.items() if v},
                          "dup": r.dup_conflict_rows, "encoding": r.encoding} for n, r in reports.items()]})


def pair(raw_a, daily_a, raw_b, daily_b, name_a: str, name_b: str, var: str, start, end) -> dict:
    """Сравнение двух станций (например, Кр1 «поляна» и Кр2 «поле») за один период."""
    r = A.pair_compare(raw_a, daily_a, raw_b, daily_b, var, start, end)
    m = r["months"]
    # месяцы, где разница станций резко выбивается из обычной (робастный z по медиане и MAD ≥ 6):
    # соседние станции меняются синхронно, такой скачок — признак сбоя датчика, а не погоды
    suspicious = []
    dd = m["diff"].dropna()
    if len(dd) >= 4:
        med = dd.median()
        mad = 1.4826 * (dd - med).abs().median()
        floor = {"pressure": 2.0, "rh": 5.0}.get(var, 0.5)
        scale = max(mad, floor / 3)
        for t, v in dd.items():
            z = abs(v - med) / scale
            if z >= 6:
                suspicious.append({"month": t, "diff": v, "typical": med, "z": z})
    return js({"a": name_a, "b": name_b, "var": var_meta(var), "common_days": r["common_days"],
               "suspicious": suspicious,
               "mean_diff": r["mean_diff"], "days_a_higher": r["days_a_warmer"],
               "days": {"x": r["days"].index, "a": r["days"]["a"], "b": r["days"]["b"], "diff": r["days"]["diff"]},
               "months": {"x": m.index, "a": m["a"], "b": m["b"], "diff": m["diff"]},
               "diurnal": {"hours": list(range(24)), "a": r["diurnal_a"]["value"], "b": r["diurnal_b"]["value"],
                           "n": r["diurnal_a"]["n"]}})
