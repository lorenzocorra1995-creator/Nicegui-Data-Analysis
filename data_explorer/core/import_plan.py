"""
Import plan (no UI): for each column of the file read, whether to keep it, with which name and in which format.

The starting format is always the detected one: the user chooses the conversions, or asks for a proposal
(suggest_formats) for the columns read as text.
"""
from __future__ import annotations

import re

import pandas as pd

from data_explorer.core import columns
from data_explorer.core import structure
KEEP = "Come rilevato"
FORMATS = [KEEP, "Testo", "Numero", "Numero intero", "Data", "Data senza ora"]
PREVIEW_ROWS = 20
SUGGEST_ROWS = 1000  # values per column checked to propose a format (the preview still shows any losses)


def detected_format(series: pd.Series) -> str:
    """Format of the column as it was read from the file."""
    if pd.api.types.is_bool_dtype(series):
        return "Sì/No"
    if pd.api.types.is_integer_dtype(series):
        return "Numero intero"
    if pd.api.types.is_numeric_dtype(series):
        return "Numero"
    if pd.api.types.is_datetime64_any_dtype(series):
        return "Data"
    return "Testo"


def column_plan(df: pd.DataFrame) -> list[dict]:
    """Initial plan: all the columns, with the file name and the detected format."""
    return [{**item, "detected": detected_format(df.iloc[:, item["position"]]), "format": KEEP}
            for item in structure.column_items(df)]


def _proposed_format(values: pd.Series, decimal: str) -> str | None:
    """Format in which all the (non-empty) text values convert without losses; None if no conversion fits."""
    values = values[values.str.strip() != ""]
    if values.empty:
        return None
    if not values.str.match(r"^\s*0\d").any():  # codes like «00123» stay text: as numbers they would lose the zeros
        numbers = columns.to_number(values, decimal)
        if numbers.notna().all():
            return "Numero intero" if (numbers == numbers.round()).all() and not values.str.contains(
                re.escape(decimal)).any() else "Numero"
    if values.str.contains(r"\d[/\-.]\d").all():  # only values that look like dates (31/01/2024, 2024-01-31)
        dates = columns.to_date(values)
        if dates.notna().all():
            return "Data senza ora" if (dates == dates.dt.normalize()).all() else "Data"
    return None


def suggest_formats(df: pd.DataFrame, plan: list[dict], decimal: str = ",") -> int:
    """Optional: for the columns read as text, proposes «Numero», «Numero intero», «Data» or «Data senza ora»
    when the first SUGGEST_ROWS values all convert (e.g. a CSV written with ; and decimal comma).
    Changes `plan` in place and returns how many formats it changed."""
    changed = 0
    for item in plan:
        if item["detected"] != "Testo" or item["format"] != KEEP:
            continue
        values = df.iloc[:, item["position"]].dropna().head(SUGGEST_ROWS)
        if not values.map(lambda value: isinstance(value, str)).all():
            continue  # mixed columns (e.g. from Excel) stay as they are
        proposed = _proposed_format(values.astype(str), decimal)
        if proposed:
            item["format"] = proposed
            changed += 1
    return changed


def text_reload_needed(plan: list[dict]) -> list[dict]:
    """Columns read as numbers or dates for which «Testo» was chosen: they must be read again from the file as written
    (otherwise, for example, «00123» would become «123»)."""
    return [item for item in plan if item["format"] == "Testo" and item["detected"] not in ("Testo", "Sì/No")]


def with_text_columns(df: pd.DataFrame, texts: dict[int, pd.Series]) -> pd.DataFrame:
    """The dataset with the columns at the positions of `texts` replaced by their text version."""
    if not texts:
        return df
    out = df.copy(deep=False)
    for position, values in texts.items():
        out.isetitem(position, pd.Series(values.to_numpy(), index=df.index))  # same file: same rows
    return out


def apply_plan(df: pd.DataFrame, plan: list[dict], decimal: str = ",") -> pd.DataFrame:
    """Columns kept, renamed and converted to the chosen formats (values that cannot be converted become null)."""
    out = structure.apply_columns(df, plan)
    for item, column in zip([item for item in plan if item["keep"]], out.columns):
        if item["format"] != KEEP:
            out[column] = columns.convert(out[column], item["format"], decimal)
    return out


def lost_counts(df: pd.DataFrame, plan: list[dict], converted: pd.DataFrame) -> dict[str, int]:
    """For each converted column, how many non-empty values were lost in the conversion."""
    kept = [item for item in plan if item["keep"]]
    counts = {}
    for item, column in zip(kept, converted.columns):
        if item["format"] != KEEP:
            lost = len(columns.lost_values(df.iloc[:, item["position"]], converted[column]))
            if lost:
                counts[str(column)] = lost
    return counts
