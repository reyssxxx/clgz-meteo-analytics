"""Ядро аналитики. Работает с каноническим DataFrame одной станции (см. schema.py).

Не зависит ни от формата входных файлов, ни от интерфейса.
"""
from __future__ import annotations

import numpy as np
import pandas as pd
from scipy import stats

from . import coverage as CV
from .quality import quality_issues
from .schema import STATION_PREFIXES, VARIABLES

FREQS = {"D": "Сутки", "W": "Неделя", "MS": "Месяц", "QS-DEC": "Сезон", "YS": "Год"}
SEASON_NAMES = {12: "Зима", 3: "Весна", 6: "Лето", 9: "Осень"}


# =============================================================== служебное

def _vars(df: pd.DataFrame) -> list[str]:
    return [c for c in df.columns if c in VARIABLES]


def infer_step(df: pd.DataFrame) -> pd.Timedelta:
    idx = df.index.unique().sort_values()
    if len(idx) < 2:
        return pd.Timedelta("1D")
    return pd.Series(idx).diff().median()


def is_subdaily(df: pd.DataFrame) -> bool:
    return infer_step(df) < pd.Timedelta("20h")


def for_station(df: pd.DataFrame, station: str | None) -> pd.DataFrame:
    if station is None or "station" not in df:
        return df.drop(columns="station", errors="ignore")
    return df[df["station"] == station].drop(columns="station")


def _circmean(x: pd.Series) -> float:
    x = x.dropna()
    if x.empty:
        return np.nan
    r = np.deg2rad(x.to_numpy())
    return float(np.rad2deg(np.arctan2(np.sin(r).mean(), np.cos(r).mean())) % 360)


def _agg(resampler, how: str):
    if how == "circmean":
        return resampler.apply(_circmean)
    return getattr(resampler, how)()


def difference(value, reference, var: str):
    diff = value - reference
    return (diff + 180) % 360 - 180 if VARIABLES[var].period_agg == "circmean" else diff


# =============================================================== агрегация

def to_daily(df: pd.DataFrame, station: str | None = None) -> pd.DataFrame:
    """Срочные данные → сутки по правилам задания (см. coverage.py).

    Для каждого параметра отдельно: сутки берутся, только если валидных значений этого параметра
    достаточно (АМЕ-60 — 72 срока из 96, Сокол М1 — 18 разных часов из 24); иначе NaN.
    Суточное среднее — по всем валидным наблюдениям за сутки.
    Максимальный порыв сохраняется отдельно для экстремумов и пороговых показателей.
    Tmax/Tmin — экстремумы срочной температуры за полные сутки.
    """
    df = df.drop(columns="station", errors="ignore")
    if station not in STATION_PREFIXES.values() and not is_subdaily(df):
        out = df.copy()
        out.index = out.index.normalize()
        return out.groupby(level=0).mean(numeric_only=True)
    rule = CV.day_rule(df.index, station)
    r = df.resample("D")
    out = {}
    for v in _vars(df):
        ok = CV.valid_days(df.index[df[v].notna().to_numpy()], rule)
        val = _agg(r[v], VARIABLES[v].daily_agg)
        out[v] = val.where(ok.reindex(val.index, fill_value=False))
    if "wind_gust" in out:
        out["wind_gust_max"] = r["wind_gust"].max().where(out["wind_gust"].notna())
    out = pd.DataFrame(out)
    if "t_air" in df:  # экстремумы суток из срочной температуры, если отдельных колонок нет
        ok = CV.valid_days(df.index[df["t_air"].notna().to_numpy()], rule).reindex(out.index, fill_value=False)
        for name, how in (("t_max", "max"), ("t_min", "min")):
            if name not in out or out[name].isna().all():
                out[name] = getattr(r["t_air"], how)().where(ok)
    out.index.name = "time"
    return out


def _monthly(daily: pd.DataFrame) -> pd.DataFrame:
    """Месячные значения: только для валидных месяцев (≤ 10 неполных дней и нет 5 подряд)."""
    if daily.empty or daily.index.min() is pd.NaT:
        return pd.DataFrame(index=pd.DatetimeIndex([], name="time"))
    start = daily.index.min().to_period("M").to_timestamp()
    end = daily.index.max().to_period("M").to_timestamp(how="end").normalize()
    full = daily.reindex(pd.date_range(start, end, freq="D"))
    r = full.resample("MS")
    out = {}
    for v in _vars(full):
        val = _agg(r[v], VARIABLES[v].period_agg)
        mv = CV.monthly_valid_from_daily(full[v]).reindex(val.index, fill_value=False)
        out[v] = val.where(mv)
    out = pd.DataFrame(out)
    out.index.name = "time"
    return out


def aggregate(daily: pd.DataFrame, freq: str = "MS", min_coverage: float = 0.8) -> pd.DataFrame:
    """Суточные → неделя/месяц/сезон/год.

    Месяц — по правилу задания; сезон и год складываются из месячных значений и считаются, только если
    валидны все их месяцы (3 и 12). Неделя — если есть ≥ min_coverage полных дней.
    """
    if freq == "D":
        return daily
    if freq == "MS":
        return _monthly(daily)
    if freq in ("QS-DEC", "YS"):
        m = _monthly(daily)
        if m.empty:
            return m
        need = 3 if freq == "QS-DEC" else 12
        r = m.resample(freq)
        counts = r.count()
        out = {}
        for v in _vars(m):
            how = VARIABLES[v].period_agg
            val = _agg(r[v], how if how in ("sum", "max", "min", "circmean") else "mean")
            out[v] = val.where(counts[v] == need)
        out = pd.DataFrame(out)
        out.index.name = "time"
        return out
    r = daily.resample(freq)
    counts = r.count()
    pad = pd.Timedelta("400D")
    cal = pd.Series(1, index=pd.date_range(daily.index.min() - pad, daily.index.max() + pad, freq="D"))
    period_days = cal.resample(freq).size().reindex(counts.index).astype(float)
    out = {}
    for v in _vars(daily):
        val = _agg(r[v], VARIABLES[v].period_agg)
        out[v] = val.where(counts[v] / period_days >= min_coverage)
    out = pd.DataFrame(out)
    out.index.name = "time"
    return out


def period_label(ts: pd.Timestamp, freq: str) -> str:
    if freq == "YS":
        return str(ts.year)
    if freq == "MS":
        return ts.strftime("%Y-%m")
    if freq == "QS-DEC":
        year = ts.year + 1 if ts.month == 12 else ts.year
        return f"{SEASON_NAMES[ts.month]} {year}"
    return ts.strftime("%Y-%m-%d")


# =============================================================== статистика за период

def period_summary(daily: pd.DataFrame, start=None, end=None) -> pd.DataFrame:
    d = daily.loc[start:end]
    n_days = max((d.index.max() - d.index.min()).days + 1, 1) if len(d) else 0
    rows = []
    for v in _vars(d):
        s = d[v].dropna()
        if s.empty:
            continue
        meta = VARIABLES[v]
        rows.append({
            "Параметр": f"{meta.label}, {meta.unit}",
            "Среднее": _circmean(s) if meta.period_agg == "circmean" else s.mean(),
            "Ст. откл.": s.std(),
            "Минимум": s.min(),
            "Дата минимума": s.idxmin().date(),
            "Максимум": s.max(),
            "Дата максимума": s.idxmax().date(),
            "Сумма": s.sum() if meta.period_agg == "sum" else np.nan,
            "Дней с данными": len(s),
            "Полнота, %": 100 * len(s) / n_days if n_days else np.nan,
        })
    return pd.DataFrame(rows)


def day_counts(daily: pd.DataFrame, start=None, end=None) -> pd.DataFrame:
    """Число характерных дней за период — то, что обычно спрашивают у климатолога."""
    d = daily.loc[start:end]
    items = []

    def add(name, mask):
        if mask is not None:
            items.append({"Показатель": name, "Дней": int(mask.sum())})

    g = d.get
    add("Дни с осадками ≥ 1 мм", g("precip") >= 1 if "precip" in d else None)
    add("Дни с сильными осадками ≥ 20 мм", g("precip") >= 20 if "precip" in d else None)
    add("Дни с заморозком (Tmin < 0 °C)", g("t_min") < 0 if "t_min" in d else None)
    add("Дни без оттепели (Tmax < 0 °C)", g("t_max") < 0 if "t_max" in d else None)
    add("Жаркие дни (Tmax ≥ 25 °C)", g("t_max") >= 25 if "t_max" in d else None)
    add("Очень жаркие дни (Tmax ≥ 30 °C)", g("t_max") >= 30 if "t_max" in d else None)
    add("Морозные дни (Tmin ≤ −20 °C)", g("t_min") <= -20 if "t_min" in d else None)
    add("Дни со снежным покровом", g("snow_depth") > 0 if "snow_depth" in d else None)
    gust_max = g("wind_gust_max", g("wind_gust"))
    add("Дни с порывами ветра ≥ 15 м/с", gust_max >= 15 if gust_max is not None else None)
    return pd.DataFrame(items)


# =============================================================== качество данных

def mask_issues(df: pd.DataFrame, issues: pd.DataFrame, kinds: tuple[str, ...] = ("Вне диапазона", "Выброс")) -> pd.DataFrame:
    """Заменяет на NaN значения с проблемами указанных типов."""
    out = df.copy()
    sel = issues[issues["Проблема"].str.startswith(kinds)]
    for var, g in sel.groupby("Параметр"):
        if var in out:
            out.loc[out.index.isin(g["Время"]), var] = np.nan
    return out


def gaps(df: pd.DataFrame, min_length: str | None = None) -> pd.DataFrame:
    """Пропуски в ряду: отсутствующие сроки дольше ожидаемого шага."""
    df = df.drop(columns="station", errors="ignore")
    step = infer_step(df)
    min_len = pd.Timedelta(min_length) if min_length else step * 1.5
    idx = df.index.unique().sort_values().to_series()
    d = idx.diff()
    m = d > min_len
    out = pd.DataFrame({
        "Начало пропуска": idx.shift()[m] + step,
        "Конец пропуска": idx[m] - step,
        "Длительность": d[m] - step,
    }).reset_index(drop=True)
    return out


def completeness(daily: pd.DataFrame) -> pd.DataFrame:
    """Полнота данных (%) по годам и параметрам."""
    g = daily.groupby(daily.index.year)
    days = g.size()
    cal = pd.Series({y: 366 if pd.Timestamp(y, 12, 31).day_of_year == 366 else 365 for y in days.index})
    out = g.count().div(cal, axis=0) * 100
    out.index.name = "Год"
    return out.round(1)


# =============================================================== климатическая норма и аномалии

def calendar_day(index: pd.DatetimeIndex):
    """Номер календарной даты на шкале високосного года (29 февраля имеет свой номер)."""
    return index.day_of_year + (~index.is_leap_year & (index.month > 2))


def doy_climatology(daily: pd.DataFrame, var: str, base: tuple | None = None) -> pd.DataFrame:
    """Среднее за выбранный период, разброс и число лет для каждой календарной даты."""
    s = daily[var].loc[slice(*base) if base else slice(None)].dropna()
    g = s.groupby(calendar_day(s.index))
    mean = g.apply(_circmean) if VARIABLES[var].daily_agg == "circmean" else g.mean()
    out = pd.DataFrame({"norm": mean, "std": g.std(), "years": g.count()}).reindex(range(1, 367))
    out["years"] = out["years"].fillna(0).astype(int)
    return out


def daily_anomalies(daily: pd.DataFrame, var: str, base: tuple | None = None) -> pd.DataFrame:
    clim = doy_climatology(daily, var, base)
    ref = clim.reindex(calendar_day(daily.index)).set_axis(daily.index)
    out = ref.assign(value=daily[var])
    out["anomaly"] = difference(out["value"], out["norm"], var)
    out["z"] = out["anomaly"] / out["std"]
    return out


def anomalous_days(daily: pd.DataFrame, var: str, base: tuple | None = None, z: float = 2.0,
                   start=None, end=None) -> pd.DataFrame:
    a = daily_anomalies(daily, var, base).loc[start:end]
    return a[a["z"].abs() >= z].dropna()


def monthly_anomalies(daily: pd.DataFrame, var: str, base: tuple | None = None) -> pd.DataFrame:
    """Отклонение каждого месяца от многолетней нормы этого месяца.
    Для сумм (осадки) дополнительно — % от нормы."""
    m = aggregate(daily[[var]], "MS")[var]
    b = m.loc[slice(*base) if base else slice(None)]
    g = b.groupby(b.index.month)
    norm_by_month = g.apply(_circmean) if VARIABLES[var].period_agg == "circmean" else g.mean()
    std_by_month = g.std()
    out = pd.DataFrame({"value": m})
    out["norm"] = norm_by_month.reindex(m.index.month).to_numpy()
    out["std"] = std_by_month.reindex(m.index.month).to_numpy()
    out["years"] = g.count().reindex(m.index.month, fill_value=0).to_numpy()
    out["anomaly"] = difference(out["value"], out["norm"], var)
    out["z"] = out["anomaly"] / out["std"]
    if VARIABLES[var].period_agg == "sum":
        out["pct_of_norm"] = 100 * out["value"] / out["norm"]
    return out


def monthly_reference(daily: pd.DataFrame, base: tuple | None = None) -> tuple[pd.DataFrame, pd.DataFrame]:
    m = aggregate(daily.loc[slice(*base) if base else slice(None)], "MS")
    g = m.groupby(m.index.month)
    mean, years = g.mean(), g.count()
    for var in _vars(m):
        if VARIABLES[var].period_agg == "circmean":
            mean[var] = g[var].apply(_circmean)
    months = pd.Index(range(1, 13), name="Месяц")
    return mean.reindex(months), years.reindex(months, fill_value=0)


def monthly_norms(daily: pd.DataFrame, base: tuple | None = None) -> pd.DataFrame:
    return monthly_reference(daily, base)[0]


# =============================================================== тренды

def mann_kendall(x: np.ndarray) -> dict:
    """Тест Манна–Кендалла (с поправкой на связки) + оценка наклона Сена.
    Наклон — в единицах величины на шаг ряда."""
    x = np.asarray(x, dtype=float)
    t = np.arange(len(x), dtype=float)
    ok = ~np.isnan(x)
    x, t = x[ok], t[ok]
    n = len(x)
    if n < 4:
        return {"n": n, "S": np.nan, "Z": np.nan, "p": np.nan, "tau": np.nan, "sen": np.nan}
    diff = np.sign(x[None, :] - x[:, None])
    s = np.triu(diff, 1).sum()
    _, counts = np.unique(x, return_counts=True)
    var_s = (n * (n - 1) * (2 * n + 5) - (counts * (counts - 1) * (2 * counts + 5)).sum()) / 18
    z = 0.0 if s == 0 else (s - np.sign(s)) / np.sqrt(var_s)
    p = 2 * (1 - stats.norm.cdf(abs(z)))
    i, j = np.triu_indices(n, 1)
    slopes = (x[j] - x[i]) / (t[j] - t[i])
    return {"n": n, "S": s, "Z": z, "p": p, "tau": s / (n * (n - 1) / 2), "sen": float(np.median(slopes))}


def trend(series: pd.Series, per: float = 10.0) -> dict:
    """Тренд годового (или любого регулярного) ряда. Наклоны — на `per` шагов (по умолчанию на 10 лет)."""
    s = series.dropna()
    res = {"n": len(s), "start": s.index.min(), "end": s.index.max()}
    if len(s) < 4:
        return res | {"ols_slope": np.nan, "ols_p": np.nan, "r2": np.nan, "intercept": np.nan,
                      "mk_p": np.nan, "sen_slope": np.nan, "tau": np.nan}
    t = np.arange(len(series))[series.notna().to_numpy()]
    lr = stats.linregress(t, s.to_numpy())
    mk = mann_kendall(series.to_numpy())
    return res | {
        "ols_slope": lr.slope * per, "ols_p": lr.pvalue, "r2": lr.rvalue ** 2, "intercept": lr.intercept,
        "mk_p": mk["p"], "sen_slope": mk["sen"] * per, "tau": mk["tau"],
    }


def trend_table(daily: pd.DataFrame, month: int | None = None, season: int | None = None,
                alpha: float = 0.05) -> pd.DataFrame:
    """Тренды всех параметров по годовым значениям (или по конкретному месяцу/сезону каждого года)."""
    if month:
        agg = aggregate(daily, "MS")
        agg = agg[agg.index.month == month]
    elif season:
        agg = aggregate(daily, "QS-DEC")
        agg = agg[agg.index.month == season]
    else:
        agg = aggregate(daily, "YS")
    agg = agg.set_axis(agg.index.year + (agg.index.month == 12) * (season is not None))
    agg = agg.reindex(range(agg.index.min(), agg.index.max() + 1)) if len(agg) else agg
    rows = []
    for v in _vars(agg):
        if VARIABLES[v].period_agg == "circmean":
            continue  # направление ветра — круговая величина, линейный тренд для неё не имеет смысла
        tr = trend(agg[v])
        if tr["n"] < 4:
            continue
        sig = tr["mk_p"] < alpha
        meta = VARIABLES[v]
        rows.append({
            "Параметр": f"{meta.label}, {meta.unit}",
            "var": v,
            "Лет": tr["n"],
            "Период": f"{int(agg[v].first_valid_index())}–{int(agg[v].last_valid_index())}",
            "Среднее": agg[v].mean(),
            "Наклон МНК / 10 лет": tr["ols_slope"],
            "p (МНК)": tr["ols_p"],
            "R²": tr["r2"],
            "Наклон Сена / 10 лет": tr["sen_slope"],
            "p (Манн–Кендалл)": tr["mk_p"],
            "Вывод": ("рост" if tr["sen_slope"] > 0 else "снижение") if sig else "значимого тренда нет",
        })
    return pd.DataFrame(rows)


def annual_series(daily: pd.DataFrame, var: str) -> pd.Series:
    a = aggregate(daily[[var]], "YS")[var]
    return a.set_axis(a.index.year)


# =============================================================== агроклиматические показатели

def _steady_transition(t: pd.Series, thr: float, run: int = 5) -> tuple[pd.Timestamp | None, pd.Timestamp | None]:
    """Даты устойчивого перехода среднесуточной температуры через порог.

    Весна: первый день выше порога после последней «холодной» серии (≥run дней ≤ thr)
    в январе–июне — так зимние оттепели не принимаются за весну.
    Осень: последний день перед первой холодной серией в июле–декабре.
    """
    cold = _runs(t <= thr, run)
    h1 = t[t.index.month <= 6]
    h2 = t[t.index.month >= 7]
    spring = autumn = None
    cold_sp = [c for c in cold if c[1].month <= 6]
    after = h1.loc[cold_sp[-1][1] + pd.Timedelta("1D"):] if cold_sp else h1
    above = after[after > thr]
    if len(above):
        spring = above.index[0]
    cold_au = [c for c in cold if c[0].month >= 7]
    if cold_au:
        before = h2.loc[:cold_au[0][0] - pd.Timedelta("1D")]
        warm = before[before > thr]
        autumn = warm.index[-1] if len(warm) else None
    elif len(h2) and (h2 > thr).any():
        autumn = h2[h2 > thr].index[-1]
    return spring, autumn


def agro_indices(daily: pd.DataFrame) -> pd.DataFrame:
    """Агроклиматические показатели по годам.

    - Сумма активных температур (Σ T > 10 °C) и эффективных (Σ (T − 5) при T > 5 °C)
    - Даты устойчивого перехода через 0, 5, 10 °C весной и осенью
    - Последний весенний и первый осенний заморозок (Tmin < 0), безморозный период
    - ГТК Селянинова = Σ осадков / (0,1 · Σ T) за период с T > 10 °C
    - Осадки за май–сентябрь, максимальная высота снега, дни со снегом
    """
    rows = []
    for year, d in daily.groupby(daily.index.year):
        if len(d) < 300:
            continue
        r: dict = {"Год": year}
        if "t_air" in d:
            t = d["t_air"]
            warm = t > 10
            r["Σ активных T > 10 °C"] = t[warm].sum()
            r["Σ эффективных T > 5 °C"] = (t[t > 5] - 5).sum()
            for thr in (0, 5, 10):
                sp, au = _steady_transition(t, thr)
                r[f"Переход через {thr} °C весной"] = sp.date() if sp is not None else None
                r[f"Переход через {thr} °C осенью"] = au.date() if au is not None else None
                if thr == 10 and sp is not None and au is not None:
                    r["Вегетационный период > 10 °C, дней"] = (au - sp).days + 1
            if "precip" in d:
                st = t[warm].sum()
                r["ГТК Селянинова"] = d.loc[warm, "precip"].sum() / (0.1 * st) if st > 0 else np.nan
        if "t_min" in d:
            tm = d["t_min"]
            spring = tm[(tm.index.month <= 6) & (tm < 0)]
            autumn = tm[(tm.index.month >= 7) & (tm < 0)]
            last_sp = spring.index.max() if len(spring) else None
            first_au = autumn.index.min() if len(autumn) else None
            r["Последний весенний заморозок"] = last_sp.date() if last_sp is not None else None
            r["Первый осенний заморозок"] = first_au.date() if first_au is not None else None
            if last_sp is not None and first_au is not None:
                r["Безморозный период, дней"] = (first_au - last_sp).days - 1
        if "precip" in d:
            r["Осадки за год, мм"] = d["precip"].sum()
            r["Осадки май–сентябрь, мм"] = d.loc[(d.index.month >= 5) & (d.index.month <= 9), "precip"].sum()
        if "snow_depth" in d and d["snow_depth"].notna().any():
            r["Макс. высота снега, см"] = d["snow_depth"].max()
            r["Дней со снежным покровом"] = int((d["snow_depth"] > 0).sum())
        rows.append(r)
    return pd.DataFrame(rows).set_index("Год") if rows else pd.DataFrame()


# =============================================================== экстремальные явления

def _runs(mask: pd.Series, min_len: int) -> list[tuple[pd.Timestamp, pd.Timestamp, int]]:
    m = mask.fillna(False).astype(int)
    grp = (m != m.shift()).cumsum()
    out = []
    for _, g in m.groupby(grp):
        if g.iloc[0] == 1 and len(g) >= min_len:
            out.append((g.index[0], g.index[-1], len(g)))
    return out


def extreme_events(daily: pd.DataFrame, heat_t: float = 25.0, heat_days: int = 5,
                   cold_t: float = -25.0, cold_days: int = 3, dry_mm: float = 1.0, dry_days: int = 15,
                   heavy_mm: float = 20.0) -> pd.DataFrame:
    """Волны жары и холода, засушливые периоды (тёплый сезон), сильные осадки."""
    ev = []
    if "t_max" in daily:
        for a, b, n in _runs(daily["t_max"] >= heat_t, heat_days):
            ev.append(("Волна жары", a, b, n, f"Tmax до {daily.loc[a:b, 't_max'].max():.1f} °C"))
    if "t_min" in daily:
        for a, b, n in _runs(daily["t_min"] <= cold_t, cold_days):
            ev.append(("Волна холода", a, b, n, f"Tmin до {daily.loc[a:b, 't_min'].min():.1f} °C"))
    if "precip" in daily:
        p = daily["precip"]
        warm = (p.index.month >= 5) & (p.index.month <= 9)
        for a, b, n in _runs((p < dry_mm) & warm & p.notna(), dry_days):
            ev.append(("Засушливый период", a, b, n, f"осадков {p.loc[a:b].sum():.1f} мм"))
        for t, x in p[p >= heavy_mm].items():
            ev.append(("Сильные осадки", t, t, 1, f"{x:.1f} мм за сутки"))
    out = pd.DataFrame(ev, columns=["Явление", "Начало", "Конец", "Дней", "Характеристика"])
    return out.sort_values("Начало").reset_index(drop=True)


# =============================================================== сравнение двух периодов

def compare_periods(daily: pd.DataFrame, a: tuple, b: tuple, alpha: float = 0.05) -> tuple[pd.DataFrame, str]:
    """Сравнивает два периода по всем параметрам.

    Единица сравнения выбирается так, чтобы значения были примерно независимы (иначе p-value
    занижено из-за автокорреляции суточных рядов): если оба периода ≥ 3 полных лет — годовые
    значения, иначе — средние за неделю. Тест — Уэлча (t) и Манна–Уитни; вывод по Манну–Уитни.
    Для сумм (осадки) сравниваются суммы в среднем за год (длинные периоды) или за неделю.
    Возвращает таблицу и описание единицы сравнения.
    """
    da, db = daily.loc[a[0]:a[1]], daily.loc[b[0]:b[1]]
    years_a = da.index.year.value_counts()
    years_b = db.index.year.value_counts()
    full_a, full_b = (years_a >= 330).sum(), (years_b >= 330).sum()
    if full_a >= 3 and full_b >= 3:
        freq, unit, per = "YS", "годовые значения", "за год"
    else:
        freq, unit, per = "W", "средние/суммы за неделю", "за неделю"
    ga, gb = aggregate(da, freq), aggregate(db, freq)
    rows = []
    for v in _vars(daily):
        if v not in ga or v not in gb or VARIABLES[v].period_agg == "circmean":
            continue
        xa, xb = ga[v].dropna(), gb[v].dropna()
        if len(xa) < 3 or len(xb) < 3 or (xa.std() == 0 and xb.std() == 0):
            continue
        meta = VARIABLES[v]
        t = stats.ttest_ind(xa, xb, equal_var=False)
        u = stats.mannwhitneyu(xa, xb, alternative="two-sided")
        diff = xb.mean() - xa.mean()
        rows.append({
            "Параметр": f"{meta.label}, {meta.unit}" + (f" ({per})" if meta.period_agg == "sum" else ""),
            "var": v,
            "Период A": xa.mean(),
            "Период B": xb.mean(),
            "Разница (B − A)": diff,
            "Разница, %": 100 * diff / abs(xa.mean()) if meta.period_agg == "sum" and xa.mean() else np.nan,
            "n A": len(xa),
            "n B": len(xb),
            "p (Уэлч)": t.pvalue,
            "p (Манн–Уитни)": u.pvalue,
            "Вывод": ("выше в B" if diff > 0 else "ниже в B") if u.pvalue < alpha else "различие незначимо",
        })
    return pd.DataFrame(rows), unit


def annual_cycle(daily: pd.DataFrame, start, end) -> pd.DataFrame:
    """Средний годовой ход (по месяцам) за период: для сумм — средняя месячная сумма."""
    return monthly_reference(daily.loc[start:end])[0]


# =============================================================== несколько станций

def station_matrix(dailies: dict[str, pd.DataFrame], var: str, freq: str = "D") -> pd.DataFrame:
    """Один параметр по всем станциям: колонки — станции."""
    cols = {}
    for name, d in dailies.items():
        if var in d and d[var].notna().any():
            cols[name] = aggregate(d[[var]], freq)[var] if freq != "D" else d[var]
    return pd.DataFrame(cols)


def deseasonalize(wide: pd.DataFrame) -> pd.DataFrame:
    """Убирает годовой ход (среднее по дню года) — иначе корреляция станций завышена сезонностью."""
    doy = np.minimum(wide.index.day_of_year, 365)
    return wide - wide.groupby(doy).transform("mean")


def station_correlation(dailies: dict[str, pd.DataFrame], var: str) -> pd.DataFrame:
    wide = station_matrix(dailies, var)
    if wide.empty:
        return pd.DataFrame()
    return deseasonalize(wide).corr(min_periods=60)


def pettitt(x: pd.Series) -> dict:
    """Тест Петтитта на одну точку смены среднего. Возвращает индекс последней точки «до», p-value
    и средние до/после."""
    s = x.dropna()
    n = len(s)
    if n < 8:
        return {"n": n, "change": None, "p": np.nan, "before": np.nan, "after": np.nan}
    v = s.to_numpy()
    sgn = np.sign(v[None, :] - v[:, None])          # sgn[i, j] = sign(x_j − x_i)
    u = np.array([sgn[:t + 1, t + 1:].sum() for t in range(n - 1)])
    t = int(np.argmax(np.abs(u)))
    k = abs(u[t])
    p = min(1.0, 2 * np.exp(-6 * k ** 2 / (n ** 3 + n ** 2)))
    return {"n": n, "change": s.index[t], "p": p, "before": v[:t + 1].mean(), "after": v[t + 1:].mean()}


def homogeneity(dailies: dict[str, pd.DataFrame], target: str, reference: str, var: str,
                freq: str = "YS") -> tuple[pd.Series, dict]:
    """Разность «станция − соседняя станция» по годам (или месяцам) и тест Петтитта на ступеньку.
    Климат у соседей меняется одинаково, поэтому ступенька в разности — признак смены прибора,
    переноса станции или ошибки обработки, а не изменения климата."""
    w = station_matrix(dailies, var, freq)
    diff = (w[target] - w[reference]).dropna()
    if freq == "YS":
        diff.index = diff.index.year
    return diff, pettitt(diff)


def fill_gaps(dailies: dict[str, pd.DataFrame], target: str, min_r2: float = 0.8,
              max_gap_days: int | None = None) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Заполняет пропуски суточных значений станции target по лучшей соседней станции.

    Для каждого параметра берётся соседняя станция с наибольшим R² на общих днях; если R² ≥ min_r2,
    строится линейная регрессия отдельно для каждого месяца (учитывает сезонную разницу) и ей
    заполняются пропуски. Направление ветра не заполняется. Возвращает заполненную таблицу и отчёт.
    """
    d = dailies[target].copy()
    rows = []
    for v in _vars(d):
        if v == "wind_dir" or d[v].notna().all():
            continue
        y = d[v]
        best = None
        for name, other in dailies.items():
            if name == target or v not in other:
                continue
            x = other[v].reindex(d.index)
            both = y.notna() & x.notna()
            if both.sum() < 365:
                continue
            r2 = np.corrcoef(y[both], x[both])[0, 1] ** 2
            if best is None or r2 > best[1]:
                best = (name, r2, x)
        if best is None or best[1] < min_r2:
            rows.append({"Параметр": v, "Донор": best[0] if best else "—", "R²": best[1] if best else np.nan,
                         "Заполнено дней": 0, "Осталось пропусков": int(y.isna().sum())})
            continue
        name, r2, x = best
        filled = y.copy()
        for m in range(1, 13):
            mm = d.index.month == m
            both = mm & y.notna() & x.notna()
            if both.sum() < 30:
                continue
            b, a = np.polyfit(x[both], y[both], 1)
            todo = mm & y.isna() & x.notna()
            filled[todo] = a + b * x[todo]
        if VARIABLES[v].limits[0] >= 0:
            filled = filled.clip(lower=0)
        n = int((y.isna() & filled.notna()).sum())
        d[v] = filled
        d[f"{v}__filled"] = y.isna() & filled.notna()
        rows.append({"Параметр": v, "Донор": name, "R²": r2, "Заполнено дней": n,
                     "Осталось пропусков": int(filled.isna().sum())})
    flags = [c for c in d.columns if c.endswith("__filled")]
    report = pd.DataFrame(rows, columns=["Параметр", "Донор", "R²", "Заполнено дней", "Осталось пропусков"])
    return d.drop(columns=flags), report


# =============================================================== роза ветров

RUMBS = ["С", "ССВ", "СВ", "ВСВ", "В", "ВЮВ", "ЮВ", "ЮЮВ", "Ю", "ЮЮЗ", "ЮЗ", "ЗЮЗ", "З", "ЗСЗ", "СЗ", "ССЗ"]


def wind_rose(df: pd.DataFrame, speed_var: str | None = None,
              bins: tuple[float, ...] = (0, 2, 5, 8, 12, np.inf), calm: float = 0.5) -> tuple[pd.DataFrame, float]:
    """Повторяемость (%) направлений ветра по 16 румбам и градациям скорости.
    Штиль (скорость < calm) считается отдельно и возвращается вторым значением."""
    if "wind_dir" not in df:
        return pd.DataFrame(), np.nan
    speed_var = speed_var or next((v for v in ("wind_speed", "wind_gust") if v in df), None)
    d = df[["wind_dir"] + ([speed_var] if speed_var else [])].dropna()
    if d.empty:
        return pd.DataFrame(), np.nan
    n = len(d)
    calm_pct = 100 * (d[speed_var] < calm).sum() / n if speed_var else 0.0
    if speed_var:
        d = d[d[speed_var] >= calm]
    sector = ((d["wind_dir"] % 360 + 11.25) // 22.5).astype(int) % 16
    if speed_var:
        labels = [f"{a:g}–{b:g} м/с" if np.isfinite(b) else f"≥ {a:g} м/с" for a, b in zip(bins[:-1], bins[1:])]
        cls = pd.cut(d[speed_var], bins=list(bins), labels=labels, right=False)
        t = pd.crosstab(sector, cls).reindex(index=range(16), columns=labels, fill_value=0)
    else:
        t = sector.value_counts().reindex(range(16), fill_value=0).to_frame("все скорости")
    t = 100 * t / n
    t.index = RUMBS
    return t, calm_pct


# =============================================================== суточный ход и сравнение двух станций

def diurnal(raw: pd.DataFrame, daily: pd.DataFrame, var: str, start, end, days=None) -> pd.DataFrame:
    """Средний суточный ход: среднее значение по каждому часу суток (0–23) за период.
    Берутся только валидные значения (флаги исключены) и только полные сутки; время — как записано
    на станции, без сдвига. days — необязательный набор дней (например, общих для двух станций)."""
    start, end = pd.Timestamp(start), pd.Timestamp(end)
    raw = raw.drop(columns="station", errors="ignore")
    if var not in raw:
        return pd.DataFrame(columns=["value", "n"])
    s = raw[var].loc[start:end + pd.Timedelta("1D") - pd.Timedelta("1ns")].dropna()
    ok = daily[var].loc[start:end].dropna().index if var in daily else pd.DatetimeIndex([])
    if days is not None:
        ok = ok.intersection(days)
    s = s[s.index.normalize().isin(ok)]
    if s.empty:
        return pd.DataFrame(columns=["value", "n"])
    how = "circmean" if VARIABLES[var].daily_agg == "circmean" else "mean"
    by_day_hour = _agg(s.groupby([s.index.normalize(), s.index.hour]), how)
    by_hour = by_day_hour.groupby(level=1)
    out = pd.DataFrame({"value": _agg(by_hour, how), "n": by_hour.count()})
    return out.reindex(range(24))


def pair_compare(raw_a, daily_a, raw_b, daily_b, var: str, start, end) -> dict:
    """Две станции за один период: месячные средние (только валидные месяцы обеих),
    разница A − B и средний суточный ход по общим полным дням."""
    start, end = pd.Timestamp(start), pd.Timestamp(end)
    ma = aggregate(daily_a.loc[start:end], "MS")
    mb = aggregate(daily_b.loc[start:end], "MS")
    a = ma[var] if var in ma else pd.Series(dtype=float)
    b = mb[var] if var in mb else pd.Series(dtype=float)
    months = pd.DataFrame({"a": a, "b": b})
    months["diff"] = difference(months["a"], months["b"], var)
    da = daily_a[var].loc[start:end].dropna() if var in daily_a else pd.Series(dtype=float)
    db = daily_b[var].loc[start:end].dropna() if var in daily_b else pd.Series(dtype=float)
    common = da.index.intersection(db.index)
    ca = diurnal(raw_a, daily_a, var, start, end, common)
    cb = diurnal(raw_b, daily_b, var, start, end, common)
    dd = difference(da.reindex(common), db.reindex(common), var)
    days = pd.DataFrame({"a": da, "b": db}).reindex(pd.date_range(start, end, freq="D"))
    days["diff"] = difference(days["a"], days["b"], var)
    return {"days": days, "months": months, "diurnal_a": ca, "diurnal_b": cb, "common_days": len(common),
            "mean_diff": dd.mean() if len(dd) else np.nan, "days_a_warmer": int((dd > 0).sum())}
