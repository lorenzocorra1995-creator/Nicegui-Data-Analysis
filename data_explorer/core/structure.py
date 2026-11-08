"""
Dataset structure (no UI dependency): dataset and column names, order and removal of columns,
sorting of rows on several levels (like «Ordina per» in Excel).
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from data_explorer.core import engine as eng
# ---------------------------------------------------------------------------
# Names
# ---------------------------------------------------------------------------


def check_dataset_name(name: str, others: list[str]) -> str:
    """New name of a dataset, without spaces at the ends; error if it is empty or if it matches (also as an SQL table)
    another dataset."""
    name = name.strip()
    if not name:
        raise ValueError("Il nome del dataset non può essere vuoto")
    if any(name.casefold() == other.casefold() for other in others):
        raise ValueError(f"Esiste già un dataset chiamato «{name}»")
    table = eng.sql_table_name(name)
    if table == "df" or any(table == eng.sql_table_name(other) for other in others):
        raise ValueError(f"Il nome «{name}» darebbe in SQL la tabella `{table}`, già usata: scegline un altro")
    return name


def check_column_names(names: list[str]) -> list[str]:
    """Column names without spaces at the ends; error if one is empty or two match (even only by case,
    since in SQL they are the same column)."""
    names = [name.strip() for name in names]
    if any(not name for name in names):
        raise ValueError("Ogni colonna deve avere un nome")
    seen: dict[str, str] = {}
    for name in names:
        if name.casefold() in seen:
            raise ValueError(f"Due colonne si chiamerebbero «{seen[name.casefold()]}» e «{name}»: i nomi devono essere diversi")
        seen[name.casefold()] = name
    return names


# ---------------------------------------------------------------------------
# Columns
# ---------------------------------------------------------------------------


def column_items(df: pd.DataFrame) -> list[dict]:
    """Columns in the current order: position in the dataset, name (editable) and whether to keep it."""
    return [{"position": position, "name": str(column), "keep": True} for position, column in enumerate(df.columns)]


def apply_columns(df: pd.DataFrame, items: list[dict]) -> pd.DataFrame:
    """Columns in the order of `items`, only the ones to keep, with the new names (see column_items).
    Unchanged names stay the original ones, even if they are not text."""
    kept = [item for item in items if item["keep"]]
    if not kept:
        raise ValueError("Tieni almeno una colonna")
    names = check_column_names([item["name"] for item in kept])
    positions = [item["position"] for item in kept]
    out = df.iloc[:, positions].copy()
    out.columns = [df.columns[position] if name == str(df.columns[position]) else name
                   for position, name in zip(positions, names)]
    return out


def renamed(df: pd.DataFrame, items: list[dict]) -> dict[str, str]:
    """Renamed columns: old name → new name."""
    return {str(df.columns[item["position"]]): item["name"].strip() for item in items
            if item["keep"] and item["name"].strip() != str(df.columns[item["position"]])}


# ---------------------------------------------------------------------------
# Row sorting
# ---------------------------------------------------------------------------


def new_level(column: str | None = None) -> dict:
    return {"column": column, "descending": False}


def _sort_key(series: pd.Series, case_sensitive: bool) -> pd.DataFrame:
    """Sort key of a column: blanks always last; in mixed columns numbers first, then
    text (as in Excel), text ignoring upper and lower case unless requested."""
    missing = series.isna().to_numpy()
    if not pd.api.types.is_object_dtype(series) and not pd.api.types.is_string_dtype(series):
        return pd.DataFrame({"missing": missing, "value": series.to_numpy()})
    values = series.astype(object)
    is_number = values.map(lambda v: isinstance(v, (int, float, np.number)) and not isinstance(v, (bool, np.bool_)))
    texts = values.where(~is_number & ~missing).map(str, na_action="ignore")
    if not case_sensitive:
        texts = texts.map(str.casefold, na_action="ignore")
    return pd.DataFrame({"missing": missing, "text_first": (~is_number).to_numpy(),
                         "number": pd.to_numeric(values.where(is_number), errors="coerce").to_numpy(),
                         "text": texts.to_numpy()})


def sort_rows(df: pd.DataFrame, levels: list[dict], case_sensitive: bool = False) -> pd.DataFrame:
    """Rows sorted by the levels (column, descending) in order of priority; ties keep their
    starting order."""
    levels = [level for level in levels if level["column"] is not None]
    names = {str(column): column for column in df.columns}
    order = np.arange(len(df))
    for level in reversed(levels):  # stable sorts from the last level to the first
        if level["column"] not in names:
            raise ValueError(f"Colonna «{level['column']}» non trovata")
        key = _sort_key(df[names[level["column"]]].iloc[order].reset_index(drop=True), case_sensitive)
        ascending = [True] + [not level["descending"]] * (key.shape[1] - 1)  # blanks stay last
        positions = key.sort_values(list(key.columns), ascending=ascending, kind="mergesort",
                                    na_position="last").index.to_numpy()
        order = order[positions]
    return df.iloc[order].reset_index(drop=True)


# ---------------------------------------------------------------------------
# Full change
# ---------------------------------------------------------------------------


def apply(df: pd.DataFrame, items: list[dict], levels: list[dict], case_sensitive: bool = False) -> pd.DataFrame:
    """First sorts the rows (on the current column names), then reorders, removes and renames the columns."""
    return apply_columns(sort_rows(df, levels, case_sensitive), items)
