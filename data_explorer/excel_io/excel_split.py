"""
Splitting of a dataset into several Excel files and sheets (no UI dependency).

split_plan(df, spec) works out which files and sheets are created and with which rows (also used for the preview);
build_files(df, plan, spec, style) writes them with excel_writer. The spec:
    {"mode": "values" | "rows",
     "files_by": [columns], "sheets_by": [columns],            # mode "values": one file / sheet per value
     "rows_per_part": int, "rows_into": "files" | "sheets",    # mode "rows": blocks of rows
     "file_pattern": str, "sheet_pattern": str,                # names with placeholders {colonna}, {n}, {nome}
     "columns": [columns to keep] | None, "drop_keys": bool, "filters": <tree>, "name": dataset name}
"""
from __future__ import annotations

import re
from dataclasses import dataclass

import pandas as pd

from data_explorer.core import engine as eng
from data_explorer.excel_io import excel_writer as xw
MODES = {"values": "Per valori di colonna", "rows": "Per numero di righe"}
ROWS_INTO = {"files": "Un file per blocco", "sheets": "Un foglio per blocco"}
DEFAULT_ROWS_PER_PART = 100_000
DEFAULT_SHEET = "Dati"
MAX_FILES = 1000
MAX_SHEETS = 250  # sheets per file
MISSING = "(vuoto)"
PLACEHOLDER = re.compile(r"\{([^{}]+)\}")
NUMBER, NAME = "n", "nome"  # placeholders for the running number and the dataset name


@dataclass
class SheetPart:
    name: str
    rows: list[int]  # positions of the rows in the filtered dataset


@dataclass
class FilePart:
    name: str
    sheets: list[SheetPart]


# ---------------------------------------------------------------------------
# Names
# ---------------------------------------------------------------------------

def key_text(value) -> str:
    if pd.isna(value):
        return MISSING
    if isinstance(value, pd.Timestamp):
        return value.strftime("%Y-%m-%d") if value == value.normalize() else value.strftime("%Y-%m-%d %H.%M")
    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    return str(value)


def default_pattern(columns: list[str], prefix: str = "") -> str:
    parts = ([prefix] if prefix else []) + [f"{{{column}}}" for column in columns]
    return "_".join(parts) or "{" + NAME + "}"


def fill_pattern(pattern: str, values: dict[str, str]) -> str:
    """Replaces the {column} placeholders; an unknown placeholder is an error that lists the valid ones."""
    def replace(match: re.Match) -> str:
        key = match.group(1)
        if key not in values:
            valid = ", ".join("{" + name + "}" for name in values)
            raise ValueError(f"Segnaposto sconosciuto {{{key}}}: si possono usare {valid}.")
        return values[key]
    return PLACEHOLDER.sub(replace, pattern)


# ---------------------------------------------------------------------------
# Plan
# ---------------------------------------------------------------------------

def _groups(rows: pd.DataFrame, columns: list[str]) -> list[tuple[dict[str, str], list[int]]]:
    """(column values as text, row positions) for each combination, in order."""
    if not columns:
        return [({}, list(range(len(rows))))]
    groups = rows.reset_index(drop=True).groupby(columns, dropna=False, sort=True, observed=True).indices
    out = []
    for key, positions in groups.items():
        key = key if isinstance(key, tuple) else (key,)
        out.append(({column: key_text(value) for column, value in zip(columns, key)}, positions.tolist()))
    return out


def _by_values(rows: pd.DataFrame, spec: dict) -> list[FilePart]:
    files_by, sheets_by = list(spec.get("files_by") or []), list(spec.get("sheets_by") or [])
    if not files_by and not sheets_by:
        raise ValueError("Scegli almeno una colonna per dividere in file o in fogli.")
    if set(files_by) & set(sheets_by):
        raise ValueError("Una colonna non può dividere sia i file sia i fogli.")
    name = spec.get("name") or "dati"
    file_pattern = spec.get("file_pattern") or default_pattern(files_by, name)
    sheet_pattern = spec.get("sheet_pattern") or (default_pattern(sheets_by) if sheets_by else DEFAULT_SHEET)
    plan = []
    for number, (file_key, positions) in enumerate(_groups(rows, files_by), start=1):
        values = {NAME: name, NUMBER: str(number), **file_key}
        part = rows.iloc[positions]
        sheets = [SheetPart(fill_pattern(sheet_pattern, {**values, NUMBER: str(index), **sheet_key}),
                            [positions[position] for position in sheet_positions])
                  for index, (sheet_key, sheet_positions) in enumerate(_groups(part, sheets_by), start=1)]
        plan.append(FilePart(fill_pattern(file_pattern, values), sheets))
    return plan


def _by_rows(rows: pd.DataFrame, spec: dict) -> list[FilePart]:
    size = int(spec.get("rows_per_part") or 0)
    if size < 1:
        raise ValueError("Indica quante righe deve avere ogni blocco.")
    name = spec.get("name") or "dati"
    blocks = [list(range(start, min(start + size, len(rows)))) for start in range(0, len(rows), size)] or [[]]
    if spec.get("rows_into") == "sheets":
        sheet_pattern = spec.get("sheet_pattern") or f"{DEFAULT_SHEET} {{{NUMBER}}}"
        file_pattern = spec.get("file_pattern") or "{" + NAME + "}"
        sheets = [SheetPart(fill_pattern(sheet_pattern, {NAME: name, NUMBER: str(number)}), block)
                  for number, block in enumerate(blocks, start=1)]
        return [FilePart(fill_pattern(file_pattern, {NAME: name, NUMBER: "1"}), sheets)]
    file_pattern = spec.get("file_pattern") or f"{{{NAME}}}_{{{NUMBER}}}"
    sheet_pattern = spec.get("sheet_pattern") or DEFAULT_SHEET
    return [FilePart(fill_pattern(file_pattern, {NAME: name, NUMBER: str(number)}),
                     [SheetPart(fill_pattern(sheet_pattern, {NAME: name, NUMBER: str(number)}), block)])
            for number, block in enumerate(blocks, start=1)]


def filtered_rows(df: pd.DataFrame, spec: dict) -> pd.DataFrame:
    return eng.apply_filters(df, spec.get("filters"))


def split_plan(df: pd.DataFrame, spec: dict) -> list[FilePart]:
    """Files and sheets that will be created, with names that are already valid and unique."""
    rows = filtered_rows(df, spec)
    if rows.empty:
        raise ValueError("Nessuna riga dopo i filtri.")
    plan = _by_rows(rows, spec) if spec.get("mode") == "rows" else _by_values(rows, spec)
    if len(plan) > MAX_FILES:
        raise ValueError(f"Lo split creerebbe {len(plan)} file (massimo {MAX_FILES}).")
    used_files: set[str] = set()
    for part in plan:
        if len(part.sheets) > MAX_SHEETS:
            raise ValueError(f"Il file «{part.name}» avrebbe {len(part.sheets)} fogli (massimo {MAX_SHEETS}).")
        part.name = xw.file_name(part.name, used_files)
        used_sheets: set[str] = set()
        for sheet in part.sheets:
            sheet.name = xw.sheet_name(sheet.name, used_sheets)
    return plan


def plan_table(plan: list[FilePart]) -> pd.DataFrame:
    """Preview: one row per sheet with file, sheet and number of rows."""
    return pd.DataFrame([{"file": part.name, "foglio": sheet.name, "righe": len(sheet.rows)}
                         for part in plan for sheet in part.sheets])


# ---------------------------------------------------------------------------
# Writing
# ---------------------------------------------------------------------------

def output_columns(df: pd.DataFrame, spec: dict) -> list:
    """Columns written to the files: the chosen ones (or all), without the split keys if requested."""
    chosen = [column for column in df.columns if str(column) in set(map(str, spec.get("columns") or []))]
    columns = chosen or list(df.columns)
    if spec.get("drop_keys") and spec.get("mode") != "rows":
        keys = set(spec.get("files_by") or []) | set(spec.get("sheets_by") or [])
        columns = [column for column in columns if str(column) not in keys]
    if not columns:
        raise ValueError("Nessuna colonna da scrivere.")
    return columns


def build_files(df: pd.DataFrame, plan: list[FilePart], spec: dict, style: xw.ExcelStyle) -> dict[str, bytes]:
    """File name → .xlsx content."""
    rows = filtered_rows(df, spec)[output_columns(df, spec)]
    return {part.name: xw.workbook_bytes([xw.Sheet(sheet.name, rows.iloc[sheet.rows]) for sheet in part.sheets], style)
            for part in plan}


def split(df: pd.DataFrame, spec: dict, style: xw.ExcelStyle) -> dict[str, bytes]:
    return build_files(df, split_plan(df, spec), spec, style)
