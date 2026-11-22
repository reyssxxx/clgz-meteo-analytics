"""Загрузка файлов метеостанций и приведение к канонической схеме.

Умеет: CSV/TXT (разделители ; , \\t, десятичная запятая, UTF-8/cp1251), Excel (все листы,
лист = станция, если в данных нет колонки станции), шапку не в первой строке,
дату и время в разных колонках, единицы в заголовке (м → см, мм рт. ст. → гПа, км/ч → м/с),
значения-заглушки (-9999 и т. п.).

Главная точка входа — `load(source, filename)`. Возвращает канонический DataFrame и
LoadReport, где видно, какая колонка во что превратилась: это сопоставление можно
поправить руками (параметр `mapping`) — именно его придётся подогнать под данные кейса.
"""
from __future__ import annotations

import io
import re
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np
import pandas as pd

from .schema import QC_PATTERNS, STATION_PREFIXES, TIME_PATTERNS, VARIABLES

SENTINELS = {-9999.0, 9999.0, -999.9, 999.9, -999.0, 999.0, -99.99, 99999.0}
STATION_PATTERNS = (r"станц", r"station", r"^пост", r"^site", r"^индекс", r"^wmo")
MAX_HEADER_SCAN = 30


@dataclass
class LoadReport:
    filename: str
    mapping: dict[str, str]            # исходная колонка -> каноническое имя
    time_columns: list[str]
    station_column: str | None
    unmapped: list[str]
    unit_conversions: dict[str, str] = field(default_factory=dict)
    rows_in: int = 0
    rows_out: int = 0
    bad_timestamps: int = 0
    duplicates_dropped: int = 0
    sentinels_removed: int = 0
    encoding: str | None = None
    separator: str | None = None
    decimal: str | None = None
    sheets: list[str] = field(default_factory=list)
    qc_column: str | None = None
    qc_flagged: dict[str, int] = field(default_factory=dict)   # параметр → число исключённых по флагу значений
    dup_conflict_rows: int = 0
    exclusions: list[tuple] = field(default_factory=list)

    def summary(self) -> str:
        lines = [f"Файл: {self.filename}"]
        if self.encoding:
            lines.append(f"Кодировка {self.encoding}, разделитель «{self.separator}», десятичный знак «{self.decimal}»")
        if self.sheets:
            lines.append(f"Листы: {', '.join(self.sheets)}")
        lines.append(f"Время: {', '.join(self.time_columns)}")
        for src, dst in self.mapping.items():
            conv = f" ({self.unit_conversions[src]})" if src in self.unit_conversions else ""
            lines.append(f"  {src} → {dst}{conv}")
        if self.unmapped:
            lines.append(f"Не распознаны: {', '.join(self.unmapped)}")
        if self.qc_column:
            flagged = ", ".join(f"{k} {v}" for k, v in self.qc_flagged.items() if v) or "нет"
            lines.append(f"Флаги качества ({self.qc_column}): исключено значений — {flagged}; "
                         f"строк dup_conflict — {self.dup_conflict_rows}")
        lines.append(
            f"Строк: {self.rows_in} → {self.rows_out}; битых дат {self.bad_timestamps}, "
            f"дублей {self.duplicates_dropped}, заглушек {self.sentinels_removed}"
        )
        return "\n".join(lines)


# ---------------------------------------------------------------- чтение «сырого» файла

def _decode(data: bytes) -> tuple[str, str]:
    for enc in ("utf-8-sig", "cp1251"):
        try:
            return data.decode(enc), enc
        except UnicodeDecodeError:
            continue
    return data.decode("latin-1"), "latin-1"


def _sniff_sep(text: str) -> str:
    lines = [l for l in text.splitlines()[:50] if l.strip()]
    best, best_score = ",", -1.0
    for sep in (";", "\t", ","):
        counts = [l.count(sep) for l in lines]
        if not counts or max(counts) == 0:
            continue
        mode = max(set(counts), key=counts.count)
        score = counts.count(mode) / len(counts) * (mode > 0) + mode * 1e-3
        if score > best_score:
            best, best_score = sep, score
    return best


def _find_header_row(raw: pd.DataFrame) -> int:
    """Первая строка, где есть похожая на время ячейка и хотя бы ещё одна непустая."""
    for i in range(min(MAX_HEADER_SCAN, len(raw))):
        cells = [str(c).strip().lower() for c in raw.iloc[i].tolist() if pd.notna(c) and str(c).strip()]
        if len(cells) >= 2 and any(re.search(p, c) for c in cells for p in TIME_PATTERNS):
            return i
    return 0


def _frame_from_raw(raw: pd.DataFrame) -> pd.DataFrame:
    h = _find_header_row(raw)
    header = [str(c).strip() if pd.notna(c) else f"col{j}" for j, c in enumerate(raw.iloc[h].tolist())]
    # уникальные имена колонок
    seen: dict[str, int] = {}
    for j, name in enumerate(header):
        if name in seen:
            seen[name] += 1
            header[j] = f"{name}.{seen[name]}"
        else:
            seen[name] = 0
    df = raw.iloc[h + 1:].copy()
    df.columns = header
    df = df.dropna(how="all").dropna(axis=1, how="all")
    return df.reset_index(drop=True)


def read_raw(source: str | Path | bytes, filename: str | None = None) -> tuple[dict[str, pd.DataFrame], dict]:
    """Читает файл в набор «сырых» таблиц (строковые значения). Ключ — имя листа или ''."""
    if isinstance(source, (str, Path)):
        filename = filename or Path(source).name
        data = Path(source).read_bytes()
    else:
        data = source
    ext = Path(filename or "").suffix.lower()
    meta: dict = {}

    if ext in (".xlsx", ".xlsm", ".xls"):
        sheets = pd.read_excel(io.BytesIO(data), sheet_name=None, header=None, dtype=object)
        out = {}
        for name, raw in sheets.items():
            if raw.dropna(how="all").shape[0] < 2:
                continue
            out[str(name)] = _frame_from_raw(raw)
        meta["sheets"] = list(out)
        return out, meta

    text, enc = _decode(data)
    sep = _sniff_sep(text)
    body = "\n".join(text.splitlines()[MAX_HEADER_SCAN:]) or text
    decimal = "," if sep != "," and re.search(r"\d,\d", body) else "."
    # заранее задаём число колонок: иначе строка-заголовок над таблицей («Метеостанция …»)
    # задаст ширину 1, и все строки данных отбросятся как «битые»
    width = max(l.count(sep) for l in text.splitlines()[:200] if l.strip()) + 1
    raw = pd.read_csv(io.StringIO(text), sep=sep, header=None, names=range(width), dtype=str,
                      skip_blank_lines=True, engine="python", on_bad_lines="skip")
    meta.update(encoding=enc, separator=sep, decimal=decimal)
    return {"": _frame_from_raw(raw)}, meta


# ---------------------------------------------------------------- сопоставление колонок

def _norm(name: str) -> str:
    return re.sub(r"\s+", " ", str(name).strip().lower().replace("ё", "е"))


def _strip_units(name: str) -> str:
    return re.split(r"[,(\[]", name)[0].strip()


def guess_mapping(columns: list[str]) -> tuple[dict[str, str], list[str], str | None]:
    """Возвращает (колонка -> переменная, колонки времени, колонка станции)."""
    mapping: dict[str, str] = {}
    time_cols: list[str] = []
    station_col: str | None = None
    used: set[str] = set()
    qc = find_qc_column(columns)
    for col in columns:
        if col == qc:
            continue
        n = _norm(col)
        base = _strip_units(n)
        if not time_cols or len(time_cols) < 2:
            if any(re.search(p, base) for p in TIME_PATTERNS) and not any(
                    re.search(p, base) for v in VARIABLES.values() for p in v.patterns):
                time_cols.append(col)
                continue
        if station_col is None and any(re.search(p, base) for p in STATION_PATTERNS):
            station_col = col
            continue
        for var in VARIABLES.values():  # порядок важен: t_max/t_min отделены от t_air шаблонами
            if var.name in used:
                continue
            if any(re.search(p, base) or re.search(p, n.replace(" ", "_")) for p in var.patterns):
                mapping[col] = var.name
                used.add(var.name)
                break
    return mapping, time_cols, station_col


def find_qc_column(columns) -> str | None:
    for c in columns:
        n = _norm(c)
        if any(re.search(p, n) for p in QC_PATTERNS):
            return c
    return None


def station_from_filename(name: str) -> str:
    """ame60_1993.csv → «АМЕ-60», kr1_2024.csv → «Кр1 (поляна)». Для незнакомых файлов — имя без года:
    meteo_station_2026.csv → «meteo_station», чтобы годовые файлы одной станции склеивались в один ряд."""
    stem = Path(name or "Станция").stem
    base = re.sub(r"[_\-\s]*(19|20)\d{2}$", "", stem) or stem
    return STATION_PREFIXES.get(base.lower(), base)


def _unit_factor(col: str, var: str) -> tuple[float, str | None]:
    n = _norm(col)
    unit = n[len(_strip_units(n)):]
    if var == "snow_depth" and re.search(r"(?<![см])\bм\b|\(m\)|, m\b|_m$", unit + (" " if not unit else "")):
        return 100.0, "м → см"
    if var == "snow_depth" and re.search(r"\bмм\b|\bmm\b", unit):
        return 0.1, "мм → см"
    if var == "pressure" and re.search(r"мм рт|mmhg|мм\.? ?рт", n):
        return 1.33322, "мм рт. ст. → гПа"
    if var == "pressure" and re.search(r"кпа|kpa", unit):
        return 10.0, "кПа → гПа"
    if var in ("wind_speed", "wind_gust") and re.search(r"км/ч|km/h|kmh", unit):
        return 1 / 3.6, "км/ч → м/с"
    return 1.0, None


# ---------------------------------------------------------------- разбор значений

def _to_number(s: pd.Series, decimal: str) -> pd.Series:
    if pd.api.types.is_numeric_dtype(s):
        return s.astype(float)
    t = s.astype(str).str.strip().str.replace(" ", "", regex=False).str.replace(" ", "", regex=False)
    if decimal == ",":
        t = t.str.replace(",", ".", regex=False)
    else:
        # смешанный случай: в xlsx/csv с точкой встречается и запятая
        t = t.where(~t.str.match(r"^-?\d+,\d+$"), t.str.replace(",", ".", regex=False))
    t = t.replace({"": np.nan, "-": np.nan, "—": np.nan, "nan": np.nan, "None": np.nan, "NA": np.nan})
    return pd.to_numeric(t, errors="coerce")


def _parse_time(df: pd.DataFrame, time_cols: list[str]) -> pd.Series:
    if not time_cols:
        raise ValueError("Не нашёл колонку с датой/временем — укажите её вручную.")
    first = df[time_cols[0]]
    as_num = pd.to_numeric(first, errors="coerce")
    if pd.api.types.is_datetime64_any_dtype(first):
        ts = pd.to_datetime(first)
    elif as_num.notna().mean() > 0.9 and as_num.between(20000, 80000).mean() > 0.9:
        # дата в Excel хранится числом дней от 30.12.1899 (45292 = 01.01.2024), дробная часть — время
        ts = pd.to_datetime("1899-12-30") + pd.to_timedelta(as_num, unit="D")
        ts = ts.dt.round("min")
    else:
        s = first.astype(str).str.strip()
        sample = s.dropna().head(50)
        if sample.str.match(r"^\d{4}-\d{2}-\d{2}").all():
            ts = pd.to_datetime(s, errors="coerce")
        elif sample.str.match(r"^\d{4}-\d{2}-\d{2}").any() and sample.str.contains(r"\d{1,2}:\d{2}").any():
            ts = pd.to_datetime(s, errors="coerce")
        else:
            ts = pd.to_datetime(s, errors="coerce", dayfirst=True, format="mixed")
    if len(time_cols) > 1:
        t2 = df[time_cols[1]]
        num = pd.to_numeric(t2, errors="coerce")
        if num.notna().mean() > 0.9 and num.max() <= 24:  # «срок» в часах: 0, 3, 6…
            ts = ts.dt.normalize() + pd.to_timedelta(num, unit="h")
        else:
            tt = pd.to_timedelta(t2.astype(str).str.strip().str.replace(r"^(\d{1,2}:\d{2})$", r"\1:00", regex=True),
                                 errors="coerce")
            ts = ts.dt.normalize() + tt.fillna(pd.Timedelta(0))
    if getattr(ts.dt, "tz", None) is not None:
        ts = ts.dt.tz_convert("Europe/Moscow").dt.tz_localize(None)
    return ts


# ---------------------------------------------------------------- главная функция

def to_canonical(df: pd.DataFrame, mapping: dict[str, str], time_cols: list[str],
                 station_col: str | None = None, station: str | None = None,
                 decimal: str = ".", report: LoadReport | None = None) -> pd.DataFrame:
    ts = _parse_time(df, time_cols)
    out = pd.DataFrame(index=pd.DatetimeIndex(ts, name="time"))
    stations = df[station_col].astype(str).str.strip().to_numpy() if station_col in df.columns else (
        np.repeat(station or "Станция", len(df)))
    qc = find_qc_column(df.columns)
    flags = df[qc].fillna("").astype(str).str.replace(" ", "", regex=False) if qc else pd.Series("", index=df.index)
    dup = flags.str.contains(r"(?:^|;)dup_conflict(?:;|$)", regex=True).to_numpy()
    if report is not None and qc:
        report.qc_column = qc
        report.dup_conflict_rows += int(dup.sum())
    valid_dates = out.index.notna()
    for src, var in mapping.items():
        if src not in df.columns:
            continue
        vals = _to_number(df[src], decimal).to_numpy()
        lo, hi = VARIABLES[var].limits
        # заглушка — только если значение ещё и физически невозможно (999 гПа — нормальное давление)
        is_sent = np.isin(vals, list(SENTINELS)) & ((vals < lo) | (vals > hi))
        factor, note = _unit_factor(src, var)
        if factor != 1.0:
            vals = vals * factor
            if report is not None:
                report.unit_conversions[src] = note
        prefix = src.strip() + ":"
        hit = flags.str.contains(rf"(?:^|;){re.escape(prefix)}", regex=True).to_numpy()
        out[var] = np.where(is_sent | hit, np.nan, vals)
        if report is not None:
            report.sentinels_removed += int(is_sent.sum())
            if qc:
                report.qc_flagged[var] = report.qc_flagged.get(var, 0) + int(hit.sum())
            excluded = np.flatnonzero((is_sent | hit | dup) & valid_dates & np.isfinite(vals))
            for i in excluded:
                if dup[i]:
                    reason = "dup_conflict"
                elif hit[i]:
                    reason = "; ".join(flag for flag in flags.iloc[i].split(";") if flag.startswith(prefix))
                else:
                    reason = "Значение-заглушка"
                report.exclusions.append((stations[i], out.index[i], var, vals[i], reason, report.filename))
    out["station"] = stations
    out = out[~dup]
    bad = out.index.isna()
    out = out[~bad]
    out = out.reset_index()
    repeat = out.duplicated(subset=["time", "station"], keep="first")
    if report is not None:
        report.bad_timestamps += int(bad.sum())
        report.duplicates_dropped += int(repeat.sum())
        for row in out.loc[repeat].to_dict("records"):
            for var in (VARIABLES.keys() & row.keys()):
                if pd.notna(row[var]):
                    report.exclusions.append((row["station"], row["time"], var, row[var], "Дубль срока", report.filename))
    return out.loc[~repeat].set_index("time").sort_index()


def load(source: str | Path | bytes, filename: str | None = None,
         mapping: dict[str, str] | None = None, time_cols: list[str] | None = None,
         station_col: str | None = None, station: str | None = None) -> tuple[pd.DataFrame, LoadReport]:
    """Загружает файл целиком. Явные mapping/time_cols перекрывают автоопределение."""
    if isinstance(source, (str, Path)):
        filename = filename or Path(source).name
    frames, meta = read_raw(source, filename)
    if not frames:
        raise ValueError("В файле не нашлось таблиц с данными.")
    parts, report = [], None
    for sheet, raw in frames.items():
        g_map, g_time, g_station = guess_mapping(list(raw.columns))
        m = mapping if mapping is not None else g_map
        t = time_cols if time_cols is not None else g_time
        sc = station_col if station_col is not None else g_station
        if report is None:
            report = LoadReport(
                filename=filename or "", mapping=dict(m), time_columns=list(t), station_column=sc,
                unmapped=[c for c in raw.columns if c not in m and c not in t and c != sc and c != find_qc_column(raw.columns)],
                encoding=meta.get("encoding"), separator=meta.get("separator"),
                decimal=meta.get("decimal"), sheets=meta.get("sheets", []),
            )
        report.rows_in += len(raw)
        st = station or (sheet if sheet else station_from_filename(filename))
        parts.append(to_canonical(raw, m, t, sc, st, meta.get("decimal", "."), report))
    out = pd.concat(parts).sort_index()
    report.rows_out = len(out)
    return out, report


def combine(frames: list[pd.DataFrame]) -> pd.DataFrame:
    """Склеивает несколько загруженных таблиц. Если у одной станции параметры лежат в разных
    файлах (температура отдельно, осадки отдельно), строки с одинаковым временем объединяются;
    при пересечении значений берётся первое непустое."""
    if len(frames) == 1:
        return frames[0]
    allc = pd.concat(frames)
    allc = allc.reset_index().groupby(["station", "time"], sort=False).first()
    out = allc.reset_index().set_index("time").sort_index()
    cols = [c for c in out.columns if c != "station"] + ["station"]
    return out[cols]


def stations(df: pd.DataFrame) -> list[str]:
    return sorted(df["station"].dropna().unique().tolist()) if "station" in df else []


def variables(df: pd.DataFrame) -> list[str]:
    return [c for c in df.columns if c in VARIABLES and df[c].notna().any()]
