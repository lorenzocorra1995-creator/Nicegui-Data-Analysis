"""
Data analysis engine (no UI dependency).

Contains:
  - file loading
  - nested AND/OR filters (JSON-serializable tree)
  - groupby with multiple aggregations
  - SQL (DuckDB) and Python console (result: table or chart)
  - Excel / CSV export and opening in Excel
"""
from __future__ import annotations

import base64
import codecs
import contextlib
import csv
import datetime as dt
import io
import operator
import os
import platform
import re
import subprocess
import sys
import tempfile
import threading
import traceback
from functools import reduce
from itertools import islice
from pathlib import Path

import duckdb
import numpy as np
from openpyxl.utils import get_column_letter
from openpyxl.utils.cell import range_boundaries
import pandas as pd
import pyarrow as pa

if int(pd.__version__.split(".")[0]) < 3:
    pd.set_option("mode.copy_on_write", True)  # default behavior since 3.0: copies only when something is modified

# ---------------------------------------------------------------------------
# Loading
# ---------------------------------------------------------------------------

CSV_DELIMITERS = ",;\t|"
CSV_ENCODINGS = ("utf-8-sig", "cp1252")  # UTF-8 (also with BOM), then Windows Western European
SNIFF_BYTES = 64 * 1024
CHUNK_BYTES = 1024 * 1024


def _decodes(path: Path, encoding: str) -> bool:
    """True if the whole file can be read with `encoding` (read in blocks, without loading it into memory)."""
    decoder = codecs.getincrementaldecoder(encoding)()
    try:
        with open(path, "rb") as file:
            while chunk := file.read(CHUNK_BYTES):
                decoder.decode(chunk)
        decoder.decode(b"", final=True)
    except UnicodeDecodeError:
        return False
    return True


def _detect_encoding(path: Path) -> str:
    """First encoding that reads the file without errors; latin-1 accepts any byte."""
    return next((encoding for encoding in CSV_ENCODINGS if _decodes(path, encoding)), "latin-1")


def _sniff_delimiter(path: Path, encoding: str) -> str:
    """Guesses the separator by reading only the start of the file."""
    with open(path, encoding=encoding, errors="replace") as file:
        sample = file.read(SNIFF_BYTES)
    try:
        return csv.Sniffer().sniff(sample, delimiters=CSV_DELIMITERS).delimiter
    except csv.Error:
        return ","


def _read_csv(path: Path) -> pd.DataFrame:
    encoding = _detect_encoding(path)
    delimiter = _sniff_delimiter(path, encoding)
    try:
        return pd.read_csv(path, sep=delimiter, engine="pyarrow", encoding=encoding)
    except (pa.ArrowException, ValueError):
        return pd.read_csv(path, sep=delimiter, encoding=encoding, low_memory=False)


EXCEL_SUFFIXES = {".xlsx", ".xlsm", ".xls"}
TEXT_SUFFIXES = {".csv", ".txt", ".tsv"}

READERS = {
    ".parquet": pd.read_parquet,
    ".pq": pd.read_parquet,
    ".pkl": pd.read_pickle,
    ".pickle": pd.read_pickle,
    ".feather": pd.read_feather,
    ".csv": _read_csv,
    ".txt": _read_csv,
    ".tsv": _read_csv,
}


def is_excel(name: str | Path) -> bool:
    return Path(name).suffix.lower() in EXCEL_SUFFIXES


def excel_sheets(source: str | Path | bytes) -> list[str]:
    """Names of the sheets of an Excel workbook (from a path or from content in memory)."""
    data = io.BytesIO(source) if isinstance(source, bytes) else source
    with pd.ExcelFile(data) as book:
        return [str(sheet) for sheet in book.sheet_names]


def cell_block(cells: str) -> tuple[int, int, int, int]:
    """Columns and rows of a range such as «B3:F120»: first column, first row, last column, last row (1-based)."""
    message = f"Intervallo non valido «{cells}»: usa la forma B3:F120"
    try:
        min_col, min_row, max_col, max_row = range_boundaries(cells.strip().upper())
    except ValueError:
        raise ValueError(message) from None
    if None in (min_col, min_row, max_col, max_row):  # «B:F» and «3:120» are whole columns or rows, not cells
        raise ValueError(message)
    return min(min_col, max_col), min(min_row, max_row), max(min_col, max_col), max(min_row, max_row)


def read_excel_sheet(path: str | Path, sheet: str | None = None, cells: str | None = None,
                     columns: list[str] | None = None, rows: int | None = None, **options) -> pd.DataFrame:
    """An Excel sheet, or only the range `cells` of it (its first row holds the headers). `columns` and `rows` limit
    the result; `options` go to pandas (e.g. dtype=str)."""
    sheet = sheet if sheet is not None else 0
    if not cells:
        return pd.read_excel(path, sheet_name=sheet, usecols=columns, nrows=rows, **options)
    min_col, min_row, max_col, max_row = cell_block(cells)
    data_rows = max_row - min_row  # the first row of the range holds the headers
    if rows is not None:
        data_rows = min(rows, data_rows)
    letters = f"{get_column_letter(min_col)}:{get_column_letter(max_col)}"
    frame = pd.read_excel(path, sheet_name=sheet, usecols=letters, skiprows=min_row - 1, nrows=data_rows, **options)
    return frame if columns is None else frame[columns]


def load_file(path: str | Path, sheet: str | None = None, cells: str | None = None) -> pd.DataFrame:
    """Loads a file; for Excel it reads the sheet `sheet` (default: the first one), or only the range `cells` of it."""
    path = Path(path)
    suffix = path.suffix.lower()
    if suffix in EXCEL_SUFFIXES:
        return read_excel_sheet(path, sheet, cells)
    reader = READERS.get(suffix)
    if reader is None:
        raise ValueError(f"Formato non supportato: {suffix}")
    return reader(path)


def load_bytes(name: str, content: bytes, sheet: str | None = None, cells: str | None = None) -> pd.DataFrame:
    """Loads from content in memory (upload from the browser)."""
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / name
        path.write_bytes(content)
        return load_file(path, sheet, cells)


def can_read_as_text(name: str | Path) -> bool:
    """CSV and Excel can be read again with the columns as text, exactly as they are written (e.g. «00123»)."""
    return Path(name).suffix.lower() in TEXT_SUFFIXES | EXCEL_SUFFIXES


def load_text_columns(name: str | Path, source: Path | bytes, columns: list[str] | None = None,
                      sheet: str | None = None, rows: int | None = None, cells: str | None = None) -> pd.DataFrame:
    """Columns of a CSV or of an Excel sheet (or of its range `cells`) read as text, exactly as they are written
    (see can_read_as_text): only `columns` (all if None) and only the first `rows` rows (all if None)."""
    if isinstance(source, bytes):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / Path(name).name
            path.write_bytes(source)
            return load_text_columns(name, path, columns, sheet, rows, cells)
    path = Path(source)
    if path.suffix.lower() in EXCEL_SUFFIXES:
        return read_excel_sheet(path, sheet, cells, columns, rows, dtype=str)
    if path.suffix.lower() not in TEXT_SUFFIXES:
        raise ValueError(f"Il formato {path.suffix} non si può rileggere come testo")
    encoding = _detect_encoding(path)
    return pd.read_csv(path, sep=_sniff_delimiter(path, encoding), encoding=encoding, usecols=columns, dtype=str,
                       nrows=rows)


# ---------------------------------------------------------------------------
# Nested AND/OR filters
# ---------------------------------------------------------------------------
# Tree:
#   {"logica": "AND"|"OR", "condizioni": [ <condition> | <group>, ... ]}
#   condition: {"col": str, "op": str, "val": str}

OPERATORS = [
    "=", "≠", ">", "<", ">=", "<=",
    "contiene", "non contiene", "inizia con", "finisce con",
    "in lista", "non in lista", "tra", "regex",
    "è nullo", "non è nullo",
]
NO_VALUE_OPS = {"è nullo", "non è nullo"}
LIST_OPS = {"in lista", "non in lista"}
CHOICE_OPS = {"=", "≠"} | LIST_OPS   # operators for which, on strings, the values are proposed
MAX_OPTIONS = 50                      # values proposed at a time in the filter selects
MAX_DESCRIBED_VALUES = 20             # values of a list written out in full in the filter summary
PASTE_SEPARATORS = re.compile(r"[\r\n\t;]+")  # rows and cells copied from Excel, semicolon


def new_group(logic: str = "AND") -> dict:
    return {"logica": logic, "condizioni": []}


def new_condition(col: str = "", op: str = "=", val: str = "") -> dict:
    return {"col": col, "op": op, "val": val}


def _is_group(n: dict) -> bool:
    return "condizioni" in n


def _split_list(val: str) -> list[str]:
    return [v.strip() for v in str(val).split(",") if v.strip()]


def parse_pasted_values(text: str) -> list[str]:
    """
    Values of a pasted list (e.g. a column copied from Excel): one per row, per cell or separated by «;».
    Only if none of these separators is there it splits on commas. Without spaces at the ends, blanks and duplicates.
    """
    parts = PASTE_SEPARATORS.split(text) if PASTE_SEPARATORS.search(text) else text.split(",")
    return list(dict.fromkeys(part.strip() for part in parts if part.strip()))


def merge_values(current, added: list[str]) -> list[str]:
    """Values already chosen (list or text) followed by the new ones, without duplicates."""
    return list(dict.fromkeys(adapt_value("in lista", current) + added))


def _describe_list(values: list[str]) -> str:
    if len(values) <= MAX_DESCRIBED_VALUES:
        return ", ".join(values)
    shown = ", ".join(values[:MAX_DESCRIBED_VALUES])
    return f"{shown}… (altri {len(values) - MAX_DESCRIBED_VALUES}, {len(values)} valori in tutto)"


def value_label(value) -> str:
    """Value as text to propose in the filters and to convert back in the comparison: numbers with a decimal comma
    and no thousands separator (integers without decimals), dates as YYYY-MM-DD (with the time if there is one)."""
    if isinstance(value, (bool, np.bool_)):
        return str(bool(value))
    if isinstance(value, pd.Timestamp):
        return f"{value:%Y-%m-%d}" if value == value.normalize() else value.isoformat(sep=" ")
    if isinstance(value, (float, np.floating)):
        number = float(value)
        return str(int(number)) if number.is_integer() else repr(number).replace(".", ",")
    return str(value)


def unique_values(df: pd.DataFrame, col: str) -> list[str] | None:
    """Distinct values as text, in the natural order of the column (text, numbers, dates); None if it does not exist."""
    if col not in df.columns:
        return None
    s = df[col].dropna()
    if pd.api.types.is_datetime64_any_dtype(s):
        return _date_labels(s.drop_duplicates().sort_values())
    if pd.api.types.is_numeric_dtype(s):
        return [value_label(value) for value in s.drop_duplicates().sort_values()]
    return sorted(s.astype(str).unique())


def _date_labels(values: pd.Series) -> list[str]:
    """value_label of each date, vectorized where the text is certain: midnights and whole seconds with
    4-digit years, without time zone. The other dates (fractions of a second, time zone) go through value_label."""
    if values.dt.tz is not None:
        return [value_label(value) for value in values]
    midnight = (values == values.dt.normalize()).to_numpy()
    whole = (values.dt.microsecond == 0).to_numpy() & (values.dt.nanosecond == 0).to_numpy()
    years = values.dt.year.to_numpy()
    plain = (years >= 1000) & (years <= 9999)
    day, second = plain & midnight, plain & whole & ~midnight
    labels = np.empty(len(values), dtype=object)
    labels[day] = values[day].dt.strftime("%Y-%m-%d").to_numpy(dtype=object)
    labels[second] = values[second].dt.strftime("%Y-%m-%d %H:%M:%S").to_numpy(dtype=object)
    rest = ~(day | second)
    labels[rest] = [value_label(value) for value in values[rest]]
    return labels.tolist()


def search_values(values: list[str], text: str = "", limit: int = MAX_OPTIONS) -> list[str]:
    """First `limit` values that contain `text` (ignoring upper/lower case)."""
    needle = text.strip().lower()
    matches = (v for v in values if needle in v.lower())
    return list(islice(matches, limit))


def adapt_value(op: str, val):
    """Converts the value to the format required by the operator (list for in/not in, text in the other cases)."""
    if op in LIST_OPS:
        return val if isinstance(val, list) else _split_list(val)
    if isinstance(val, list):
        return (val[0] if val else "") if op in CHOICE_OPS else ", ".join(val)
    return val


ISO_DATE = re.compile(r"^\s*\d{4}-")


def _to_num(x):
    """Number from text: «1,5», «1.5», «1.234,56» (Italian) and «1,234.56» (English); None if it is not a number."""
    if isinstance(x, str):
        text = x.strip().replace(" ", "")
        thousands = "." if text.rfind(",") > text.rfind(".") else ","
        if "," in text and "." in text:
            text = text.replace(thousands, "")
        x = text.replace(",", ".")
    try:
        return float(x)
    except (ValueError, TypeError):
        return None


def _to_date(text) -> pd.Timestamp:
    """Date from text: YYYY-MM-DD or with the day first (DD/MM/YYYY, as in Italian Excel); NaT if it is not one."""
    text = str(text).strip()
    return pd.to_datetime(text, errors="coerce", dayfirst=not ISO_DATE.match(text))


def _is_complete(df: pd.DataFrame, condition: dict) -> bool:
    """Conditions without a valid column or without a value are ignored."""
    op, val = condition.get("op", "="), condition.get("val", "")
    if condition.get("col") not in df.columns:
        return False
    return op in NO_VALUE_OPS or (val is not None and (bool(val) or op in ("=", "≠")))


COMPARISONS = {"=": operator.eq, "≠": operator.ne, ">": operator.gt, "<": operator.lt, ">=": operator.ge,
               "<=": operator.le}


def _condition_mask(df: pd.DataFrame, c: dict) -> pd.Series | None:
    if not _is_complete(df, c):
        return None  # incomplete condition: ignored
    col, op, val = c.get("col"), c.get("op", "="), c.get("val", "")
    s = df[col]
    empty = pd.Series(False, index=df.index)

    if op == "è nullo":
        return s.isna()
    if op == "non è nullo":
        return s.notna()

    # the column as text only where it is needed: converting numbers and dates costs much more than the filter
    if op == "contiene":
        return s.astype(str).str.contains(str(val), case=False, na=False, regex=False)
    if op == "non contiene":
        return ~s.astype(str).str.contains(str(val), case=False, na=False, regex=False)
    if op == "inizia con":
        return s.astype(str).str.lower().str.startswith(str(val).lower(), na=False)
    if op == "finisce con":
        return s.astype(str).str.lower().str.endswith(str(val).lower(), na=False)
    if op == "regex":
        try:
            return s.astype(str).str.contains(str(val), regex=True, na=False)
        except re.error:
            return empty
    if op in LIST_OPS:
        values = val if isinstance(val, list) else _split_list(val)
        if pd.api.types.is_datetime64_any_dtype(s):
            m = s.isin([d for d in map(_to_date, values) if not pd.isna(d)])
        elif pd.api.types.is_numeric_dtype(s) and not pd.api.types.is_bool_dtype(s):
            m = s.isin([n for n in map(_to_num, values) if n is not None])
        else:
            m = s.astype(str).isin(values)
        return m if op == "in lista" else ~m
    if op == "tra":
        parts = _split_list(val)
        if len(parts) != 2:
            return empty
        if pd.api.types.is_datetime64_any_dtype(s):
            a, b = _to_date(parts[0]), _to_date(parts[1])
            return (s >= a) & (s <= b)
        a, b = _to_num(parts[0]), _to_num(parts[1])
        if a is None or b is None:
            return empty
        sn = pd.to_numeric(s, errors="coerce")
        return (sn >= a) & (sn <= b)

    # comparisons = ≠ > < >= <=
    compare = COMPARISONS.get(op)
    if pd.api.types.is_datetime64_any_dtype(s):
        v = _to_date(val)
        if pd.isna(v):
            return empty
        if op not in (">", "<", ">=", "<="):
            s < v  # dates with a time zone against one without: = and ≠ must raise the same TypeError as > and <
        return compare(s, v).fillna(False) if compare else empty

    vn = _to_num(val)
    if pd.api.types.is_numeric_dtype(s) and vn is not None:
        return compare(s, vn).fillna(False) if compare else empty

    st, sv = s.astype(str), str(val)
    if op == "=":
        return st == sv
    if op == "≠":
        return st != sv
    # ordinal comparison on strings
    return compare(st, sv) if compare else empty


def filter_mask(df: pd.DataFrame, node: dict | None) -> pd.Series:
    """Returns the boolean mask of a filter tree (recursive)."""
    all_rows = pd.Series(True, index=df.index)
    if not node or not node.get("condizioni"):
        return all_rows
    masks = []
    for child in node["condizioni"]:
        m = filter_mask(df, child) if _is_group(child) else _condition_mask(df, child)
        if m is not None and not (_is_group(child) and not child.get("condizioni")):
            masks.append(m)
    if not masks:
        return all_rows
    op = (lambda a, b: a & b) if node.get("logica", "AND") == "AND" else (lambda a, b: a | b)
    return reduce(op, masks)


def has_conditions(df: pd.DataFrame, node: dict) -> bool:
    """True if the tree contains at least one complete condition (that is, not ignored)."""
    return any(has_conditions(df, child) if _is_group(child) else _is_complete(df, child)
               for child in node.get("condizioni", []))


def apply_filters(df: pd.DataFrame, node: dict | None, columns=None) -> pd.DataFrame:
    """Rows that pass the filters; with `columns` (names, also as text) only those columns, to copy less data.
    If all the rows pass it copies nothing: with copy-on-write, changes to the result do not touch `df`."""
    mask = filter_mask(df, node)
    if columns is not None:
        wanted = {str(name) for name in columns}
        df = df[[column for column in df.columns if str(column) in wanted]]
    return df.copy(deep=False) if mask.all() else df[mask]


def describe_filters(node: dict | None) -> str:
    """Readable form of the tree (for summaries)."""
    if not node or not node.get("condizioni"):
        return "(nessun filtro)"
    parts = []
    for f in node["condizioni"]:
        if _is_group(f):
            parts.append("(" + describe_filters(f) + ")")
        elif f.get("col"):
            val = f.get("val", "")
            val = _describe_list(val) if isinstance(val, list) else val
            v = "" if f["op"] in NO_VALUE_OPS else f' "{val}"'
            parts.append(f'{f["col"]} {f["op"]}{v}')
    return f" {node.get('logica', 'AND')} ".join(parts) or "(nessun filtro)"


# ---------------------------------------------------------------------------
# Column structure
# ---------------------------------------------------------------------------

def describe_columns(df: pd.DataFrame) -> pd.DataFrame:
    non_null = df.notna().sum()
    return pd.DataFrame({
        "colonna": df.columns.astype(str),
        "tipo": [str(dtype) for dtype in df.dtypes],
        "non nulli": non_null.values,
        "nulli": (len(df) - non_null).values,
        "distinti": df.nunique(dropna=True).values,
    })


# ---------------------------------------------------------------------------
# GroupBy
# ---------------------------------------------------------------------------

def _mode(series: pd.Series):
    modes = series.mode()
    return modes.iloc[0] if not modes.empty else None


AGG_FUNCTIONS = {
    "somma": "sum", "media": "mean", "mediana": "median",
    "conteggio": "count", "distinti": "nunique",
    "min": "min", "max": "max", "primo": "first", "ultimo": "last",
    "dev.std": "std", "varianza": "var",
    "range": lambda s: s.max() - s.min(),
    "q1": lambda s: s.quantile(0.25),
    "q3": lambda s: s.quantile(0.75),
    "moda": _mode,
    "nulli": lambda s: s.isna().sum(),
    "% nulli": lambda s: s.isna().mean() * 100,
}

# functions that make sense only on numbers (on a text «somma» would concatenate the strings)
NUMERIC_FUNCTIONS = {"somma", "media", "mediana", "dev.std", "varianza", "range", "q1", "q3"}
NO_DATE_FUNCTIONS = {"somma", "dev.std", "varianza"}


def allowed_functions(series: pd.Series) -> list[str]:
    """Aggregation functions that can be applied to the column, in the order of AGG_FUNCTIONS."""
    if pd.api.types.is_numeric_dtype(series):
        excluded = set()
    elif pd.api.types.is_datetime64_any_dtype(series):
        excluded = NO_DATE_FUNCTIONS
    else:
        excluded = NUMERIC_FUNCTIONS
    return [fn for fn in AGG_FUNCTIONS if fn not in excluded]


def check_measures(df: pd.DataFrame, measures: list[dict]) -> None:
    """Readable error if a function does not apply to the type of the column (e.g. sum of a text)."""
    for measure in measures:
        column, fn = measure["col"], measure["fn"]
        if fn in AGG_FUNCTIONS and fn not in allowed_functions(df[column]):
            kind = "una data" if pd.api.types.is_datetime64_any_dtype(df[column]) else "un testo"
            raise ValueError(f"«{fn}» non si applica a «{column}», che è {kind}: "
                             f"usa ad esempio conteggio, distinti, min o max.")


def _group_modes(groups, values: pd.Series) -> pd.Series:
    """Mode of each group in a single pass: on equal frequency the smallest value, like Series.mode()."""
    group_codes = groups.ngroup().to_numpy().astype(np.int64)
    codes, uniques = pd.factorize(values.to_numpy(), sort=True)  # codes in the order of the values; -1 = null
    present = codes >= 0
    width = max(len(uniques), 1)
    # one integer per (group, value) pair: counted with np.unique, then group ↑, count ↓, value ↑
    pairs, counts = np.unique(group_codes[present] * width + codes[present], return_counts=True)
    group_of, code_of = np.divmod(pairs, width)
    order = np.lexsort((code_of, -counts, group_of))
    firsts = order[np.unique(group_of[order], return_index=True)[1]]
    mode = pd.Series(uniques.take(code_of[firsts]), index=group_of[firsts])
    return mode.reindex(range(groups.ngroups))


def aggregate(frame: pd.DataFrame, groups, measures: dict[str, tuple[str, str]]) -> pd.DataFrame:
    """Aggregations per group, all vectorized (no Python functions called group by group).
    measures: name of the result column → (column of `frame`, function of AGG_FUNCTIONS)."""
    results = {}
    for name, (column, fn) in measures.items():
        g = groups[column]
        if fn == "range":
            results[name] = g.max() - g.min()
        elif fn in ("q1", "q3"):
            results[name] = g.quantile(0.25 if fn == "q1" else 0.75)
        elif fn == "nulli":
            results[name] = g.size() - g.count()
        elif fn == "% nulli":
            results[name] = (g.size() - g.count()) / g.size() * 100
        elif fn == "moda":
            mode = _group_modes(groups, frame[column])
            results[name] = mode.set_axis(g.size().index)
        else:
            results[name] = g.agg(AGG_FUNCTIONS.get(fn, fn))
    return pd.DataFrame(results)


# Names of the aggregated columns: pattern with placeholders, or name written for the single measure
DEFAULT_NAMING = "{funzione} {colonna}"
NAMING_PATTERNS = {
    "{funzione} {colonna}": "somma importo",
    "{colonna} ({funzione})": "importo (somma)",
    "{funzione}_{colonna}": "somma_importo",
    "{colonna}": "importo",
}


def measure_name(agg: dict, naming: str = DEFAULT_NAMING, value=None) -> str:
    """Column name of a measure: the name written for the measure or, if empty, the pattern
    ({colonna}, {funzione}). `value` = value of the pivot column: it replaces {valore},
    otherwise it goes at the end after «|»."""
    name = (agg.get("name") or "").strip() or (naming or "").strip() or DEFAULT_NAMING
    name = name.replace("{colonna}", str(agg["col"])).replace("{funzione}", agg["fn"])
    if value is None:
        return " ".join(name.replace("{valore}", "").split()).strip(" -_|/") or str(agg["col"])
    return name.replace("{valore}", str(value)) if "{valore}" in name else f"{name} | {value}"


def unique_names(names: list[str], taken=()) -> list[str]:
    """Names made unique with « (2)», « (3)»… (ignoring case, as in SQL);
    `taken` = names already used, e.g. the grouping keys."""
    used = {str(name).casefold() for name in taken}
    result = []
    for name in names:
        candidate, number = name, 2
        while candidate.casefold() in used:
            candidate, number = f"{name} ({number})", number + 1
        used.add(candidate.casefold())
        result.append(candidate)
    return result


def run_groupby(df: pd.DataFrame, by: list[str], aggs: list[dict],
                filters: dict | None = None, sort: bool = True, naming: str = DEFAULT_NAMING) -> pd.DataFrame:
    """
    aggs: [{"col": "importo", "fn": "somma", "name": ""}, ...] («name» optional, wins over `naming`)
    If `by` is empty it aggregates the whole dataset into one row.
    """
    aggs = [a for a in aggs if a.get("col") in df.columns]
    check_measures(df, aggs)
    d = apply_filters(df, filters, [*by, *(a["col"] for a in aggs)])
    if not aggs:
        # without aggregations: row count per group
        if by:
            return d.groupby(by, dropna=False, sort=sort).size().reset_index(name="n_righe")
        return pd.DataFrame({"n_righe": [len(d)]})

    names = unique_names([measure_name(a, naming) for a in aggs], by)
    measures = {name: (a["col"], a["fn"]) for name, a in zip(names, aggs)}

    if by:
        return aggregate(d, d.groupby(by, dropna=False, sort=sort), measures).reset_index()
    res = {n: [d[column].agg(AGG_FUNCTIONS.get(fn, fn))] for n, (column, fn) in measures.items()}
    return pd.DataFrame(res)


def run_value_counts(df: pd.DataFrame, by: list[str], columns: list[str],
                     filters: dict | None = None, percent: bool = True,
                     sort: bool = True) -> pd.DataFrame:
    """
    Counts the rows for each value (or combination of values) of `columns`,
    within each group `by`. The percentage is calculated on the total
    of the group (if `by` is empty, on the total of the dataset).
    """
    columns = [c for c in columns if c not in by]
    if not columns:
        raise ValueError("Seleziona almeno una colonna da contare (diversa dalle chiavi di gruppo).")

    d = apply_filters(df, filters, by + columns)
    out = d.groupby(by + columns, dropna=False, sort=False, observed=True).size().reset_index(name="conteggio")

    if percent:
        totals = out.groupby(by, dropna=False)["conteggio"].transform("sum") if by else out["conteggio"].sum()
        out["percentuale"] = (out["conteggio"] / totals * 100).round(2)
    if sort:
        out = out.sort_values(by + ["conteggio"], ascending=[True] * len(by) + [False], kind="stable")
    return out.reset_index(drop=True)


# ---------------------------------------------------------------------------
# SQL console (DuckDB)
# ---------------------------------------------------------------------------

class ArrowCache:
    """
    Converts each DataFrame to Arrow only once.
    DuckDB reads Arrow without the GIL and without converting the columns again at every query
    (reading the pandas string columns directly is very slow on large datasets).
    """

    def __init__(self) -> None:
        self._items: dict[int, tuple[pd.DataFrame, object]] = {}
        self._lock = threading.Lock()

    @staticmethod
    def _mixed_to_text(df: pd.DataFrame) -> pd.DataFrame:
        """Copy of `df` where the columns with mixed types (typical of Excel) become text, as DuckDB does."""
        out = df.copy()
        for name in df.columns[df.dtypes == object]:
            try:
                pa.array(df[name])
            except (pa.ArrowException, TypeError):
                out[name] = df[name].astype(str).where(df[name].notna())
        return out

    @classmethod
    def _convert(cls, df: pd.DataFrame):
        for candidate in (lambda: df, lambda: cls._mixed_to_text(df)):
            try:
                return pa.Table.from_pandas(candidate(), preserve_index=False)
            except (pa.ArrowException, TypeError):
                continue
        return df  # conversion impossible: DuckDB reads the DataFrame directly (slow)

    def get(self, df: pd.DataFrame):
        with self._lock:
            if id(df) not in self._items:
                self._items[id(df)] = (df, self._convert(df))
            return self._items[id(df)][1]

    def keep_only(self, dataframes) -> None:
        """Frees the memory of the DataFrames no longer in use."""
        ids = {id(df) for df in dataframes}
        with self._lock:
            self._items = {key: item for key, item in self._items.items() if key in ids}

    def clear(self) -> None:
        with self._lock:
            self._items.clear()


ARROW_CACHE = ArrowCache()


def run_sql(query: str, tables: dict[str, pd.DataFrame]) -> pd.DataFrame | None:
    """
    Runs SQL with DuckDB. Each dataset is visible as a table with its own name
    (invalid names are sanitized) plus the alias `df` for the active dataset
    if passed as table 'df'.
    Returns the DataFrame of the result (or None for statements without a result).
    """
    sources = {name: ARROW_CACHE.get(df) for name, df in tables.items()}
    ARROW_CACHE.keep_only(tables.values())
    with duckdb.connect() as con:
        for name, source in sources.items():
            con.register(name, source)
        rel = con.execute(query)
        if rel.description is None:
            return None
        return rel.df()


def sql_table_name(name: str) -> str:
    n = re.sub(r"\W", "_", Path(name).stem)
    if not n or n[0].isdigit():
        n = "t_" + n
    return n


# --- SQL on Arrow tables: for the heavy operations (join) ------------------------------------------
# DuckDB `.df()` is much slower than going through Arrow (seconds against fractions of a second on millions of rows).

ARROW_BATCH_ROWS = 1_000_000


class ResultTooLarge(ValueError):
    """The result exceeds the cell limit; `rows` are the rows read before stopping."""

    def __init__(self, rows: int) -> None:
        super().__init__(f"Il risultato ha più di {rows:,} righe".replace(",", "."))
        self.rows = rows


UNCONVERTIBLE = ("Alcune colonne hanno tipi che non si possono elaborare: "
                 "convertile dalla scheda Colonne calcolate (Cambio formato).")


def _checked_arrow(table) -> pa.Table:
    if not isinstance(table, pa.Table):
        raise ValueError(UNCONVERTIBLE)
    return table


def arrow_table(df: pd.DataFrame) -> pa.Table:
    """Arrow table of the dataset (converted only once, then cached)."""
    return _checked_arrow(ARROW_CACHE.get(df))


def to_arrow(df: pd.DataFrame) -> pa.Table:
    """Arrow table of the dataset without caching it (one-off writes, e.g. to the database)."""
    return _checked_arrow(ArrowCache._convert(df))


def run_sql_arrow(query: str, tables: dict[str, pa.Table], max_cells: int | None = None) -> pa.Table:
    """
    Runs SQL on Arrow tables and returns the result as an Arrow table.
    The result is read in blocks: if rows × columns exceed `max_cells` it stops right away with
    ResultTooLarge (a join with keys repeated on both sides can produce billions of rows).
    """
    with duckdb.connect() as con:
        for name, table in tables.items():
            con.register(name, table)
        relation = con.sql(query)
        open_reader = getattr(relation, "to_arrow_reader", None) or relation.fetch_record_batch  # name in older DuckDB versions
        reader = open_reader(ARROW_BATCH_ROWS)
        width, batches, rows = len(reader.schema), [], 0
        for batch in reader:
            rows += batch.num_rows
            if max_cells is not None and rows * width > max_cells:
                raise ResultTooLarge(rows)
            batches.append(batch)
        return pa.Table.from_batches(batches, reader.schema)


def _nullable_integer(arrow_type: pa.DataType) -> pd.api.extensions.ExtensionDtype:
    return pd.UInt64Dtype() if pa.types.is_unsigned_integer(arrow_type) else pd.Int64Dtype()


def arrow_to_pandas(table: pa.Table) -> pd.DataFrame:
    """
    From Arrow to pandas column by column. Integers with null values become Int64 (not float64,
    which alters very large integers, like identifiers).
    """
    columns = []
    for column in table.columns:
        mapper = _nullable_integer if column.null_count and pa.types.is_integer(column.type) else None
        columns.append(column.to_pandas(types_mapper=mapper))
    if not columns:
        return pd.DataFrame(index=pd.RangeIndex(table.num_rows))
    out = pd.concat(columns, axis=1)
    out.columns = table.column_names
    return out


# ---------------------------------------------------------------------------
# Python console
# ---------------------------------------------------------------------------

def run_python(code: str, env: dict) -> tuple[str, object | None, str | None]:
    """
    Runs Python code in `env` (kept between runs).
    If the last statement is an expression it returns its value (like Jupyter).
    Returns (stdout, value_of_last_expression, error).
    """
    import ast

    buf = io.StringIO()
    value = None
    err = None
    try:
        tree = ast.parse(code)
        last_expr = None
        if tree.body and isinstance(tree.body[-1], ast.Expr):
            last_expr = ast.Expression(tree.body.pop().value)
        with contextlib.redirect_stdout(buf), contextlib.redirect_stderr(buf):
            exec(compile(tree, "<console>", "exec"), env)
            if last_expr is not None:
                value = eval(compile(last_expr, "<console>", "eval"), env)
    except Exception:
        err = traceback.format_exc(limit=4)
    finally:
        ARROW_CACHE.clear()  # the code may have modified the DataFrames in place
    return buf.getvalue(), value, err


os.environ.setdefault("MPLBACKEND", "Agg")  # matplotlib from the console: no windows, only images

# Charts returned by the console: ECharts options (dict with "series"), matplotlib or plotly figures
CHART_HEIGHT_KEY = "height"  # optional key in the ECharts options: chart height in pixels
DEFAULT_CONSOLE_CHART_HEIGHT = 480


def chart_kind(value) -> str | None:
    """"echarts", "matplotlib" or "plotly" if the value is a chart, otherwise None."""
    if isinstance(value, dict) and "series" in value:
        return "echarts"
    module = type(value).__module__
    if module.startswith("matplotlib") and (hasattr(value, "savefig") or hasattr(getattr(value, "figure", None), "savefig")):
        return "matplotlib"  # figure or axes
    if module.startswith("plotly") and hasattr(value, "to_plotly_json"):
        return "plotly"
    return None


def _browser_value(value):
    """Value ready for the browser: numpy/pandas types to Python types, NaN and infinities to None."""
    if isinstance(value, dict):
        return {str(key): _browser_value(item) for key, item in value.items()}
    if isinstance(value, (list, tuple, np.ndarray, pd.Series, pd.Index)):
        return [_browser_value(item) for item in value]
    if isinstance(value, np.generic):
        value = value.item()
    if isinstance(value, float):
        return value if np.isfinite(value) else None
    if isinstance(value, (pd.Timestamp, dt.date)):
        return None if pd.isna(value) else value.isoformat()
    if value is pd.NaT:
        return None
    if value is None or isinstance(value, (str, int, bool)):
        return value
    return str(value)


def echarts_options(options: dict) -> tuple[dict, int]:
    """ECharts options ready for the browser and chart height (optional key CHART_HEIGHT_KEY)."""
    options = dict(options)
    height = int(options.pop(CHART_HEIGHT_KEY, DEFAULT_CONSOLE_CHART_HEIGHT))
    return _browser_value(options), height


def figure_image(figure) -> str:
    """Matplotlib figure (or axes) as an SVG image (data URI); the figure is then closed."""
    figure = figure if hasattr(figure, "savefig") else figure.figure
    buffer = io.BytesIO()
    figure.savefig(buffer, format="svg", bbox_inches="tight")
    if pyplot := sys.modules.get("matplotlib.pyplot"):
        pyplot.close(figure)
    return "data:image/svg+xml;base64," + base64.b64encode(buffer.getvalue()).decode()


def to_display_frame(value) -> pd.DataFrame | None:
    """
    Converts the result of a console (DataFrame or Series) into a flat table
    with the index visible as a column. For other types it returns None.
    """
    if isinstance(value, pd.Series):
        value = value.to_frame(name=value.name if value.name is not None else "valore")
    if not isinstance(value, pd.DataFrame):
        return None
    if isinstance(value.columns, pd.MultiIndex):
        value = value.copy()
        value.columns = [" | ".join(map(str, c)) for c in value.columns]
    if not (isinstance(value.index, pd.RangeIndex) and value.index.name is None):
        value = value.reset_index()
    return value


# ---------------------------------------------------------------------------
# Export
# ---------------------------------------------------------------------------

def _prepare_for_excel(df: pd.DataFrame) -> pd.DataFrame:
    """Excel does not support datetimes with a timezone."""
    d = df.copy()
    for c in d.columns:
        if isinstance(d[c].dtype, pd.DatetimeTZDtype):
            d[c] = d[c].dt.tz_localize(None)
    if isinstance(d.columns, pd.MultiIndex):
        d.columns = [" | ".join(map(str, c)) for c in d.columns]
    return d


def to_xlsx_bytes(df: pd.DataFrame, sheet: str = "Dati") -> bytes:
    buf = io.BytesIO()
    d = _prepare_for_excel(df)
    with pd.ExcelWriter(buf, engine="openpyxl") as w:
        d.to_excel(w, sheet_name=sheet[:31], index=False)
        ws = w.sheets[sheet[:31]]
        ws.freeze_panes = "A2"
        ws.auto_filter.ref = ws.dimensions
        for i, col in enumerate(d.columns, start=1):
            sample = d.iloc[:200, i - 1].astype(str).fillna("")  # NaN stays NaN with the pandas 3 string dtypes
            width = max([len(str(col))] + sample.str.len().tolist()) + 2
            ws.column_dimensions[get_column_letter(i)].width = min(width, 50)
    return buf.getvalue()


def to_csv_bytes(df: pd.DataFrame, italian: bool = True) -> bytes:
    """Italian CSV: separator ';', decimal ',', BOM for Excel."""
    d = _prepare_for_excel(df)
    if italian:
        return d.to_csv(index=False, sep=";", decimal=",").encode("utf-8-sig")
    return d.to_csv(index=False).encode("utf-8")


def open_in_excel(df: pd.DataFrame, name: str = "export") -> str:
    """
    Saves a temporary .xlsx and opens it with the default application (Excel).
    Returns the file path.
    """
    name = re.sub(r"[^\w\-]+", "_", name) or "export"
    path = Path(tempfile.gettempdir()) / f"{name}_{pd.Timestamp.now():%H%M%S}.xlsx"
    path.write_bytes(to_xlsx_bytes(df))
    system = platform.system()
    if system == "Windows":
        os.startfile(str(path))  # type: ignore[attr-defined]
    elif system == "Darwin":
        subprocess.Popen(["open", str(path)])
    else:
        subprocess.Popen(["xdg-open", str(path)])
    return str(path)
