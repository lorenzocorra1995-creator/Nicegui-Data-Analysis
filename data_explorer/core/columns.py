"""
Calculated columns (no UI dependency).

Three ways to get the values of a column, new or overwritten:
  - format change: text → number/date, date → year/month/..., anything → text
  - formula: DuckDB SQL expression calculated row by row
  - if… then: rules (filter tree → value) evaluated in order, like CASE WHEN
"""
from __future__ import annotations

import re

import duckdb
import pandas as pd

from data_explorer.core import engine as eng
# ---------------------------------------------------------------------------
# Format change
# ---------------------------------------------------------------------------

NUMERIC_CONVERSIONS = ("Numero", "Numero intero")  # they use the decimal separator
DATE_PARTS = {"Anno": "year", "Trimestre": "quarter", "Mese": "month", "Giorno": "day"}
CONVERSIONS = [*NUMERIC_CONVERSIONS, "Testo", "Data", "Data senza ora", *DATE_PARTS, "Anno-mese"]


def _text(series: pd.Series) -> pd.Series:
    """Values as text; nulls stay null."""
    return series.astype(str).where(series.notna())


def to_number(series: pd.Series, decimal: str = ",") -> pd.Series:
    """Text → number with the chosen decimal separator (thousands, spaces and currency symbols are ignored)."""
    if pd.api.types.is_numeric_dtype(series):
        return series.astype("float64")
    thousands = "." if decimal == "," else ","
    cleaned = (_text(series)
               .str.replace(f"[\\s\u00a0€$£{thousands}]", "", regex=True)
               .str.replace(decimal, ".", regex=False))
    numbers = pd.to_numeric(cleaned, errors="coerce")
    if series.dtype == object:  # mixed columns (e.g. from Excel): numbers already there must not be read again as text
        not_text = ~series.map(lambda value: isinstance(value, str))
        numbers = numbers.mask(not_text, pd.to_numeric(series.where(not_text), errors="coerce"))
    return numbers.astype("float64")


def to_date(series: pd.Series) -> pd.Series:
    """Text → date: first the ISO format (2024-01-31), then day/month/year (31/01/2024)."""
    if pd.api.types.is_datetime64_any_dtype(series):
        return series
    text = _text(series)
    dates = pd.to_datetime(text, errors="coerce", format="ISO8601")
    others = dates.isna() & text.notna()
    if others.any():
        dates[others] = pd.to_datetime(text[others], errors="coerce", dayfirst=True)
    return dates


def convert(series: pd.Series, to: str, decimal: str = ",") -> pd.Series:
    """Converts a column to the format `to` (one of CONVERSIONS); values that cannot be converted become null."""
    if to == "Numero":
        return to_number(series, decimal)
    if to == "Numero intero":
        return to_number(series, decimal).round().astype("Int64")
    if to == "Testo":
        return _text(series)
    if to not in CONVERSIONS:
        raise ValueError(f"Conversione sconosciuta: {to}")
    dates = to_date(series)
    if to == "Data":
        return dates
    if to == "Data senza ora":
        return dates.dt.normalize()
    if to == "Anno-mese":
        return dates.dt.strftime("%Y-%m")
    return getattr(dates.dt, DATE_PARTS[to]).astype("Int64")


def lost_values(source: pd.Series, converted: pd.Series) -> pd.Series:
    """Values present in the source column (not empty) that are null after the conversion."""
    lost = source[source.notna() & converted.isna()]
    return lost[lost.astype(str).str.strip() != ""]


def default_name(column: str, to: str) -> str:
    """Proposed name for the new column, e.g. data + Anno-mese → data_anno_mese."""
    suffix = re.sub(r"\W+", "_", to.lower())
    return f"{column}_{suffix}"


# ---------------------------------------------------------------------------
# Formula (DuckDB expression)
# ---------------------------------------------------------------------------

ROW_ORDER = "__row_order__"
ONE_VALUE_PER_ROW = ("La formula deve restituire un solo valore per ogni riga "
                     "(niente FROM, WHERE o aggregazioni senza OVER).")


def formula_query(formula: str) -> str:
    """Query that calculates `formula` for each row of table df, in the original row order
    (OVER functions with PARTITION BY / ORDER BY may reorder them)."""
    return (f"SELECT {formula}\n"
            f"FROM (SELECT *, row_number() OVER () AS {ROW_ORDER} FROM df) AS df\n"
            f"ORDER BY {ROW_ORDER}")


def evaluate_formula(df: pd.DataFrame, formula: str,
                     tables: dict[str, pd.DataFrame] | None = None) -> pd.Series:
    """Calculates the expression row by row on `df`; the other tables can be used in subqueries."""
    formula = formula.strip().rstrip(";")
    if not formula:
        raise ValueError("Scrivi la formula.")
    try:
        result = eng.run_sql(formula_query(formula), {**(tables or {}), "df": df})
    except duckdb.BinderException as error:
        if ROW_ORDER not in str(error):
            raise
        result = None  # aggregation without OVER: a single value for the whole dataset
    if result is None or result.shape != (len(df), 1):
        raise ValueError(ONE_VALUE_PER_ROW)
    return result.iloc[:, 0].set_axis(df.index)


# ---------------------------------------------------------------------------
# If… then (guided CASE WHEN)
# ---------------------------------------------------------------------------

def _parse_number(text: str) -> int | float | None:
    try:
        number = float(text.replace(",", "."))
    except ValueError:
        return None
    return int(number) if number.is_integer() else number


def _typed_values(texts: list[str], kept: pd.Series | None) -> list:
    """Typed values: numbers if they all are (and the kept column is not text), otherwise text; empty = null."""
    texts = [text.strip() for text in texts]
    kept_is_text = kept is not None and not pd.api.types.is_numeric_dtype(kept)
    as_numbers = not kept_is_text and all(_parse_number(text) is not None for text in texts if text)
    return [(_parse_number(text) if as_numbers else text) if text else None for text in texts]


def _uniform(values: pd.Series) -> pd.Series:
    """A single type per column: numbers or dates when possible, otherwise all text."""
    values = values.infer_objects()
    return _text(values) if values.dtype == object else values


def case_when(df: pd.DataFrame, rules: list[dict], otherwise: str = "",
              current: pd.Series | None = None) -> pd.Series:
    """
    rules: [{"filters": filter tree, "value": text}, ...] evaluated in order: the first true one wins.
    otherwise: value of the rows with no true rule; empty = leave unchanged
    (`current`, i.e. the overwritten column; null for a new column).
    """
    if not rules:
        raise ValueError("Aggiungi almeno una regola.")
    for number, rule in enumerate(rules, start=1):
        if not eng.has_conditions(df, rule["filters"]):
            raise ValueError(f"La regola {number} non ha condizioni complete.")
    kept = current if not otherwise.strip() else None
    *values, default = _typed_values([rule["value"] for rule in rules] + [otherwise], kept)
    base = kept if kept is not None else pd.Series(default, index=df.index, dtype=object)
    masks = [eng.filter_mask(df, rule["filters"]) for rule in rules]
    return _uniform(base.case_when(list(zip(masks, values))))


# ---------------------------------------------------------------------------
# Applying to the DataFrame
# ---------------------------------------------------------------------------

def with_column(df: pd.DataFrame, name: str, values: pd.Series, after: str | None = None) -> pd.DataFrame:
    """Copy of `df` with `name` overwritten, or added right after `after` (at the end if None)."""
    out = df.assign(**{name: values})
    if name not in df.columns and after in df.columns:
        out.insert(df.columns.get_loc(after) + 1, name, out.pop(name))
    return out


def _values(df: pd.DataFrame, spec: dict, target: str,
            tables: dict[str, pd.DataFrame] | None) -> pd.Series:
    mode = spec.get("mode")
    if mode == "convert":
        column = spec.get("column")
        if column not in df.columns:
            raise ValueError("Scegli la colonna da convertire.")
        return convert(df[column], spec.get("to", ""), spec.get("decimal", ","))
    if mode == "formula":
        return evaluate_formula(df, spec.get("formula", ""), tables)
    if mode == "case":
        return case_when(df, spec.get("rules", []), spec.get("otherwise", ""), df.get(target))
    raise ValueError(f"Modalità sconosciuta: {mode}")


def calculate(df: pd.DataFrame, spec: dict, tables: dict[str, pd.DataFrame] | None = None) -> pd.DataFrame:
    """
    Returns a copy of `df` with the calculated column (the original does not change).

    spec (JSON-serializable):
      mode: "convert" | "formula" | "case"
      target: result column; overwrite: True to overwrite an existing column
      convert: column, to (one of CONVERSIONS), decimal ("," or ".")
      formula: formula (DuckDB expression; `tables` can be used in subqueries)
      case: rules [{"filters": filter tree, "value": text}], otherwise (empty = leave unchanged)
    """
    target = (spec.get("target") or "").strip()
    values = _values(df, spec, target, tables)
    if not target:
        raise ValueError("Indica la colonna risultato (nome nuovo o colonna da sovrascrivere).")
    if spec.get("overwrite") and target not in df.columns:
        raise ValueError(f"La colonna '{target}' non esiste.")
    if not spec.get("overwrite") and target in df.columns:
        raise ValueError(f"La colonna '{target}' esiste già: scegli «Sovrascrivi colonna» o un altro nome.")
    after = spec.get("column") if spec.get("mode") == "convert" else None
    return with_column(df, target, values, after)
