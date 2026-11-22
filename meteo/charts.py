"""Графики Plotly. Принимают уже посчитанные таблицы из analytics.py."""
from __future__ import annotations

import numpy as np
import pandas as pd
import plotly.graph_objects as go

from .schema import VARIABLES, label

WARM, COLD, MAIN, MUTED = "#d6604d", "#4393c3", "#2c6e9e", "#9aa5b1"
MONTHS = ["янв", "фев", "мар", "апр", "май", "июн", "июл", "авг", "сен", "окт", "ноя", "дек"]


def _layout(fig: go.Figure, title: str, ytitle: str = "", height: int = 420) -> go.Figure:
    fig.update_layout(title=title, height=height, margin=dict(l=10, r=10, t=50, b=10),
                      yaxis_title=ytitle, hovermode="x unified", template="plotly_white",
                      legend=dict(orientation="h", y=-0.15))
    return fig


def timeseries(agg: pd.DataFrame, var: str, norm: pd.Series | None = None,
               years: pd.Series | None = None) -> go.Figure:
    fig = go.Figure()
    meta = VARIABLES[var]
    if var == "t_air" and {"t_min", "t_max"} <= set(agg.columns):
        fig.add_trace(go.Scatter(x=agg.index, y=agg["t_max"], line=dict(width=0), showlegend=False, hoverinfo="skip"))
        fig.add_trace(go.Scatter(x=agg.index, y=agg["t_min"], fill="tonexty", line=dict(width=0),
                                 fillcolor="rgba(44,110,158,0.15)", name="Tmin…Tmax"))
    if meta.period_agg == "sum":
        fig.add_trace(go.Bar(x=agg.index, y=agg[var], name=meta.label, marker_color=MAIN))
    else:
        fig.add_trace(go.Scatter(x=agg.index, y=agg[var], name=meta.label, line=dict(color=MAIN, width=1.6)))
    if norm is not None:
        fig.add_trace(go.Scatter(x=norm.index, y=norm, name="Среднее за выбранный период",
                                 customdata=years, line=dict(color=MUTED, dash="dash"),
                                 hovertemplate="%{y:.1f} (%{customdata} лет)<extra></extra>" if years is not None else None))
    return _layout(fig, label(var), meta.unit)


def anomaly_bars(anom: pd.DataFrame, var: str) -> go.Figure:
    meta = VARIABLES[var]
    a = anom["anomaly"]
    up_is_warm = var not in ("precip", "rh", "snow_depth", "snowfall")
    colors = np.where(a >= 0, WARM if up_is_warm else COLD, COLD if up_is_warm else WARM)
    fig = go.Figure(go.Bar(x=anom.index, y=a, marker_color=colors, name="Отклонение",
                           customdata=np.c_[anom["value"], anom["norm"], anom["years"]],
                           hovertemplate="%{x|%Y-%m}: %{y:+.2f}<br>факт %{customdata[0]:.1f}, "
                           "среднее %{customdata[1]:.1f} (%{customdata[2]} лет)<extra></extra>"))
    if len(a) > 36:
        fig.add_trace(go.Scatter(x=anom.index, y=a.rolling(12, center=True, min_periods=6).mean(),
                                 name="Скользящее среднее (12 мес.)", line=dict(color="#333", width=2)))
    return _layout(fig, f"Отклонение от среднего: {meta.label}", meta.unit)


def anomaly_heatmap(anom: pd.DataFrame, var: str) -> go.Figure:
    t = anom.assign(Год=anom.index.year, Месяц=anom.index.month).pivot_table(index="Год", columns="Месяц", values="anomaly")
    t = t.reindex(columns=range(1, 13))
    years = anom.groupby(anom.index.month)["years"].first().reindex(range(1, 13), fill_value=0)
    lim = np.nanpercentile(np.abs(t.to_numpy()), 98) if t.notna().any().any() else 1
    reverse = var in ("precip", "rh", "snow_depth", "snowfall")
    fig = go.Figure(go.Heatmap(z=t.to_numpy(), x=MONTHS, y=t.index, zmid=0, zmin=-lim, zmax=lim,
                               colorscale="RdBu" if reverse else "RdBu_r",
                               customdata=np.broadcast_to(years.to_numpy(), t.shape),
                               hovertemplate="%{y} %{x}: %{z:+.2f}<br>лет в среднем: %{customdata}<extra></extra>",
                               colorbar=dict(title=VARIABLES[var].unit)))
    fig.update_yaxes(autorange="reversed")
    return _layout(fig, f"Аномалии по месяцам: {VARIABLES[var].label}", "", height=max(320, 18 * len(t) + 120))


def trend_chart(series: pd.Series, var: str, tr: dict, title_suffix: str = "") -> go.Figure:
    meta = VARIABLES[var]
    s = series.dropna()
    x = np.arange(len(series))
    fig = go.Figure(go.Scatter(x=series.index, y=series, mode="lines+markers", name="Значение",
                               line=dict(color=MAIN), marker=dict(size=5)))
    if not np.isnan(tr.get("ols_slope", np.nan)):
        fig.add_trace(go.Scatter(x=series.index, y=tr["intercept"] + tr["ols_slope"] / 10 * x,
                                 name=f"Линейный тренд: {tr['ols_slope']:+.2f} {meta.unit}/10 лет",
                                 line=dict(color=WARM, dash="dash")))
        fig.add_trace(go.Scatter(x=series.index, y=s.rolling(5, center=True, min_periods=3).mean().reindex(series.index),
                                 name="Скользящее среднее (5 лет)", line=dict(color=MUTED)))
    return _layout(fig, f"{meta.label}{title_suffix}", meta.unit)


def climatology_chart(norms: pd.DataFrame, var: str) -> go.Figure:
    meta = VARIABLES[var]
    fig = go.Figure()
    if meta.period_agg == "sum":
        fig.add_trace(go.Bar(x=MONTHS, y=norms[var], marker_color=MAIN, name="Среднее"))
    else:
        fig.add_trace(go.Scatter(x=MONTHS, y=norms[var], mode="lines+markers", line=dict(color=MAIN), name="Среднее"))
    return _layout(fig, f"Годовой ход (среднее): {meta.label}", meta.unit, height=320)


def completeness_heatmap(comp: pd.DataFrame) -> go.Figure:
    fig = go.Figure(go.Heatmap(z=comp.to_numpy(), x=[label(c) for c in comp.columns], y=comp.index,
                               colorscale="Greens", zmin=0, zmax=100,
                               hovertemplate="%{y}, %{x}: %{z:.0f}%<extra></extra>", colorbar=dict(title="%")))
    fig.update_yaxes(autorange="reversed")
    return _layout(fig, "Полнота данных по годам", "", height=max(300, 18 * len(comp) + 140))


def events_timeline(ev: pd.DataFrame) -> go.Figure:
    colors = {"Волна жары": WARM, "Волна холода": COLD, "Засушливый период": "#bf812d", "Сильные осадки": MAIN}
    fig = go.Figure()
    for kind, g in ev.groupby("Явление"):
        fig.add_trace(go.Scatter(x=g["Начало"], y=[kind] * len(g), mode="markers", name=kind,
                                 marker=dict(size=np.clip(g["Дней"] * 2 + 6, 6, 30), color=colors.get(kind, MUTED),
                                             opacity=0.7),
                                 customdata=np.c_[g["Дней"], g["Характеристика"]],
                                 hovertemplate="%{x|%d.%m.%Y}: %{customdata[0]} дн., %{customdata[1]}<extra></extra>"))
    return _layout(fig, "Экстремальные явления", "", height=320)


def cycle_compare(ca: pd.DataFrame, cb: pd.DataFrame, var: str, name_a: str, name_b: str) -> go.Figure:
    meta = VARIABLES[var]
    fig = go.Figure()
    kind = go.Bar if meta.period_agg == "sum" else go.Scatter
    extra = {} if meta.period_agg == "sum" else {"mode": "lines+markers"}
    fig.add_trace(kind(x=MONTHS, y=ca[var], name=name_a, marker_color=MUTED, **extra))
    fig.add_trace(kind(x=MONTHS, y=cb[var], name=name_b, marker_color=WARM, **extra))
    fig.add_trace(go.Bar(x=MONTHS, y=cb[var] - ca[var], name="Разница (B − A)", yaxis="y2",
                         marker_color=np.where(cb[var] - ca[var] >= 0, WARM, COLD), opacity=0.35))
    fig.update_layout(yaxis2=dict(overlaying="y", side="right", showgrid=False, title="разница"),
                      barmode="group")
    return _layout(fig, f"Годовой ход: {meta.label}", meta.unit)


def window_bars(values: pd.Series, year: int, var: str, norm: float | None = None, title: str = "") -> go.Figure:
    meta = VARIABLES[var]
    colors = [WARM if y == year else MUTED for y in values.index]
    fig = go.Figure(go.Bar(x=values.index, y=values, marker_color=colors, name=meta.label,
                           hovertemplate="%{x}: %{y:.1f}<extra></extra>"))
    if norm is not None and not np.isnan(norm):
        fig.add_hline(y=norm, line_dash="dash", line_color="#333", annotation_text="среднее")
    if meta.period_agg != "sum":
        lo, hi = values.min(), values.max()
        pad = (hi - lo) * 0.15 or 1
        fig.update_yaxes(range=[lo - pad, hi + pad])
    return _layout(fig, title or meta.label, meta.unit, height=340)


PALETTE = ["#2c6e9e", "#d6604d", "#4d9221", "#8c6bb1", "#bf812d", "#35978f", "#e7298a", "#666666"]


def stations_lines(wide: pd.DataFrame, var: str) -> go.Figure:
    meta = VARIABLES[var]
    fig = go.Figure()
    for i, c in enumerate(wide.columns):
        fig.add_trace(go.Scatter(x=wide.index, y=wide[c], name=c, line=dict(width=1.5, color=PALETTE[i % 8])))
    return _layout(fig, f"{meta.label} по станциям", meta.unit)


def corr_heatmap(corr: pd.DataFrame) -> go.Figure:
    fig = go.Figure(go.Heatmap(z=corr.to_numpy(), x=corr.columns, y=corr.index, zmin=0, zmax=1,
                               colorscale="Blues", text=np.round(corr.to_numpy(), 3), texttemplate="%{text}",
                               hovertemplate="%{y} ↔ %{x}: %{z:.3f}<extra></extra>"))
    fig.update_yaxes(autorange="reversed")
    return _layout(fig, "", "", height=320)


def homogeneity_chart(diff: pd.Series, r: dict, var: str, target: str, ref: str) -> go.Figure:
    meta = VARIABLES[var]
    fig = go.Figure(go.Scatter(x=diff.index, y=diff, mode="lines+markers", name=f"{target} − {ref}",
                               line=dict(color=MAIN)))
    if r.get("change") is not None:
        before = diff.loc[:r["change"]]
        after = diff.loc[r["change"]:].iloc[1:]
        fig.add_trace(go.Scatter(x=before.index, y=[r["before"]] * len(before), name="среднее до",
                                 line=dict(color=MUTED, dash="dash")))
        fig.add_trace(go.Scatter(x=after.index, y=[r["after"]] * len(after), name="среднее после",
                                 line=dict(color=WARM if r["p"] < 0.05 else MUTED, dash="dash")))
    return _layout(fig, f"Разность «{target} − {ref}» по годам", meta.unit, height=320)


def climate_diagram(norms: pd.DataFrame, title: str = "Годовой ход (среднее за выбранный период)",
                    years: pd.DataFrame | None = None) -> go.Figure:
    """Температура (линия, левая ось) и осадки (столбики, правая ось) по месяцам."""
    fig = go.Figure()
    if "precip" in norms:
        fig.add_trace(go.Bar(x=MONTHS, y=norms["precip"], name="Осадки, мм", marker_color=COLD, opacity=0.55,
                             yaxis="y2", customdata=years["precip"] if years is not None else None,
                             hovertemplate="%{x}: %{y:.0f} мм" + (" (%{customdata} лет)" if years is not None else "") + "<extra></extra>"))
    if {"t_max", "t_min"} <= set(norms.columns):
        fig.add_trace(go.Scatter(x=MONTHS, y=norms["t_max"], line=dict(width=0), showlegend=False, hoverinfo="skip"))
        fig.add_trace(go.Scatter(x=MONTHS, y=norms["t_min"], fill="tonexty", line=dict(width=0),
                                 fillcolor="rgba(214,96,77,0.15)", name="Tmin…Tmax"))
    if "t_air" in norms:
        fig.add_trace(go.Scatter(x=MONTHS, y=norms["t_air"], mode="lines+markers", name="Температура, °C",
                                 customdata=years["t_air"] if years is not None else None, line=dict(color=WARM, width=2.5),
                                 hovertemplate="%{x}: %{y:.1f} °C" + (" (%{customdata} лет)" if years is not None else "") + "<extra></extra>"))
    fig.add_hline(y=0, line_color="#999", line_width=1)
    fig.update_layout(yaxis2=dict(overlaying="y", side="right", showgrid=False, title="мм", rangemode="tozero"))
    return _layout(fig, title, "°C", height=340)


def wind_rose_chart(rose: pd.DataFrame, calm: float, title: str = "Роза ветров") -> go.Figure:
    colors = ["#c6dbef", "#6baed6", "#2171b5", "#08519c", "#08306b", "#000"]
    fig = go.Figure()
    for i, c in enumerate(rose.columns):
        fig.add_trace(go.Barpolar(r=rose[c], theta=rose.index, name=str(c), marker_color=colors[i % len(colors)],
                                  hovertemplate="%{theta}: %{r:.1f}%<extra>" + str(c) + "</extra>"))
    fig.update_layout(polar=dict(angularaxis=dict(direction="clockwise", rotation=90),
                                 radialaxis=dict(ticksuffix="%", angle=90)), barmode="stack")
    sub = f" · штиль {calm:.1f}%" if calm and not np.isnan(calm) else ""
    return _layout(fig, title + sub, "", height=420)


def warming_stripes(anom: pd.Series, title: str = "Полосы потепления") -> go.Figure:
    """Каждый год — полоса, цвет — отклонение средней годовой температуры от нормы (Эд Хокинс)."""
    lim = np.nanmax(np.abs(anom.to_numpy())) or 1
    fig = go.Figure(go.Heatmap(z=[anom.to_numpy()], x=anom.index, y=[""], colorscale="RdBu_r", zmid=0,
                               zmin=-lim, zmax=lim, colorbar=dict(title="°C", thickness=10),
                               hovertemplate="%{x}: %{z:+.2f} °C<extra></extra>"))
    fig.update_yaxes(showticklabels=False)
    fig.update_layout(hovermode="closest")
    return _layout(fig, title, "", height=200)
