"""
Writing of formatted Excel files with xlsxwriter (no UI dependency).

workbook_bytes(sheets, style) writes one or more sheets: styled Excel Table, totals row with live formulas
(SUBTOTAL), conditional formatting, number and date formats, widths, frozen panes and an
index sheet with links. Sheets beyond the Excel row limit continue in sheets «(2)», «(3)», ...
At the end: valid names for files and sheets, and delivery of the files (ZIP or folder on the PC).
"""
from __future__ import annotations

import io
import math
import re
import zipfile
from dataclasses import dataclass
from pathlib import Path

import pandas as pd
import xlsxwriter

from data_explorer.core import engine as eng
# ---------------------------------------------------------------------------
# Formatting choices
# ---------------------------------------------------------------------------

PLAIN = "Nessuno (intervallo semplice)"
TABLE_STYLES = {  # displayed name → Excel Table style (None = plain range, no Table)
    "Blu": "Table Style Medium 2",
    "Arancio": "Table Style Medium 3",
    "Grigio": "Table Style Medium 4",
    "Oro": "Table Style Medium 5",
    "Verde": "Table Style Medium 7",
    PLAIN: None,  # bold centered header, no filter, no frozen row
}
CONDITIONAL = {"none": "Nessuna", "data_bar": "Barre dei dati", "color_scale": "Scala di colori"}
INDEX_SHEET = "Indice"
TOTAL_LABEL = "Totale"

MAX_ROWS = 1_048_576 - 1  # data rows per sheet (the first one is the header)
WIDTH_SAMPLE = 200  # rows read to estimate the column widths
MIN_WIDTH, MAX_WIDTH = 8, 50
INTEGER, DECIMAL = "#,##0", "#,##0.00"
DATE, DATE_TIME = "dd/mm/yyyy", "dd/mm/yyyy hh:mm"
NEGATIVE_RED = ";[Red]-"


@dataclass
class ExcelStyle:
    table_style: str = "Blu"  # key of TABLE_STYLES
    totals: bool = False  # totals row (sum of the numeric columns)
    conditional: str = "none"  # key of CONDITIONAL, on the numeric columns
    red_negatives: bool = False
    index: bool = False  # initial index sheet (only with several sheets)
    freeze: bool = True  # header always visible (only with the Table)


@dataclass
class Sheet:
    name: str
    data: pd.DataFrame
    title: str = ""  # rows above the table
    note: str = ""


# ---------------------------------------------------------------------------
# Valid names
# ---------------------------------------------------------------------------

SHEET_FORBIDDEN = re.compile(r"[\[\]:*?/\\]")
FILE_FORBIDDEN = re.compile(r'[<>:"/\\|?*\x00-\x1f]')
MAX_SHEET_NAME, MAX_FILE_NAME = 31, 150


def _deduplicated(name: str, used: set[str], limit: int) -> str:
    """`name` or, if already used (ignoring case), «name (2)», «name (3)», ... within `limit` characters."""
    candidate, number = name[:limit], 1
    while candidate.lower() in used:
        number += 1
        suffix = f" ({number})"
        candidate = name[:limit - len(suffix)] + suffix
    used.add(candidate.lower())
    return candidate


def sheet_name(name: str, used: set[str]) -> str:
    clean = SHEET_FORBIDDEN.sub("_", str(name)).strip().strip("'") or "Foglio"
    return _deduplicated(clean, used, MAX_SHEET_NAME)


def file_name(name: str, used: set[str], extension: str = ".xlsx") -> str:
    clean = FILE_FORBIDDEN.sub("_", str(name)).strip().rstrip(".") or "file"
    return _deduplicated(clean, used, MAX_FILE_NAME - len(extension)) + extension


def _unique_headers(columns) -> list[str]:
    used: set[str] = set()
    return [_deduplicated(str(column) or "colonna", used, 255) for column in columns]


# ---------------------------------------------------------------------------
# Writing
# ---------------------------------------------------------------------------

class _Formats:
    """Formats of the file, created only once (xlsxwriter wants them shared)."""

    def __init__(self, book: xlsxwriter.Workbook, style: ExcelStyle) -> None:
        negative = NEGATIVE_RED if style.red_negatives else ";-"
        self.integer = book.add_format({"num_format": INTEGER + negative + INTEGER})
        self.decimal = book.add_format({"num_format": DECIMAL + negative + DECIMAL})
        self.date = book.add_format({"num_format": DATE})
        self.date_time = book.add_format({"num_format": DATE_TIME})
        self.header = book.add_format({"bold": True, "align": "center", "valign": "vcenter"})
        self.title = book.add_format({"bold": True, "font_size": 14})
        self.note = book.add_format({"italic": True, "font_color": "#595959"})
        self.link = book.add_format({"font_color": "#0563C1", "underline": 1})


def _kind(values: pd.Series) -> str:
    if pd.api.types.is_bool_dtype(values):
        return "bool"
    if pd.api.types.is_integer_dtype(values):
        return "integer"
    if pd.api.types.is_numeric_dtype(values):
        return "decimal"
    if pd.api.types.is_datetime64_any_dtype(values):
        clean = values.dropna()
        return "date" if (clean == clean.dt.normalize()).all() else "date_time"
    return "text"


def _cells(values: pd.Series, kind: str) -> list:
    """Python values for xlsxwriter: nulls and infinities become empty cells."""
    if kind in ("integer", "decimal"):
        convert = int if kind == "integer" else float
        return [convert(cell) if math.isfinite(cell) else None for cell in values.astype("float64").tolist()]
    if kind in ("date", "date_time"):
        return [None if pd.isna(cell) else cell.to_pydatetime() for cell in values]
    return [None if pd.isna(cell) else cell if isinstance(cell, (bool, str, int, float)) else str(cell)
            for cell in values.astype("object")]


def _width(header: str, values: pd.Series) -> float:
    sample = values.head(WIDTH_SAMPLE).astype("string").fillna("")
    longest = max([len(header)] + sample.str.len().tolist())
    return min(max(longest + 2, MIN_WIDTH), MAX_WIDTH)


def _parts(sheet: Sheet) -> list[Sheet]:
    """A sheet beyond the Excel row limit becomes several consecutive sheets."""
    if len(sheet.data) <= MAX_ROWS:
        return [sheet]
    return [Sheet(sheet.name if start == 0 else f"{sheet.name} ({start // MAX_ROWS + 1})",
                  sheet.data.iloc[start:start + MAX_ROWS], sheet.title, sheet.note)
            for start in range(0, len(sheet.data), MAX_ROWS)]


def _write_sheet(book: xlsxwriter.Workbook, formats: _Formats, style: ExcelStyle, sheet: Sheet, name: str,
                 table_number: int) -> None:
    worksheet = book.add_worksheet(name)
    data = eng._prepare_for_excel(sheet.data)
    headers = _unique_headers(data.columns)
    kinds = [_kind(data[column]) for column in data.columns]
    column_formats = [getattr(formats, kind, None) for kind in kinds]
    top = 0
    for text, text_format in ((sheet.title, formats.title), (sheet.note, formats.note)):
        if text:
            worksheet.write(top, 0, text, text_format)
            top += 1
    top += bool(top)  # an empty row between text and table
    rows = len(data)
    for position, (header, column) in enumerate(zip(headers, data.columns)):
        worksheet.write_column(top + 1, position, _cells(data[column], kinds[position]), column_formats[position])
        worksheet.set_column(position, position, _width(header, data[column]))
    numeric = [position for position, kind in enumerate(kinds) if kind in ("integer", "decimal")]
    table_style = TABLE_STYLES.get(style.table_style)
    last = top + rows + (style.totals and rows > 0)
    if headers and rows and table_style:
        columns = [{"header": header, "format": column_formats[position]} for position, header in enumerate(headers)]
        if style.totals:
            for position in numeric:
                columns[position]["total_function"] = "sum"
            if 0 not in numeric:
                columns[0]["total_string"] = TOTAL_LABEL
        worksheet.add_table(top, 0, last, len(headers) - 1, {"name": f"Tabella{table_number}", "style": table_style,
                                                            "columns": columns, "total_row": style.totals})
    else:
        worksheet.write_row(top, 0, headers, formats.header)
        if headers and rows and style.totals:
            _plain_totals(worksheet, formats, numeric, top, rows, column_formats)
    if rows and style.conditional != "none":
        for position in numeric:
            rule = {"type": "data_bar"} if style.conditional == "data_bar" else {"type": "3_color_scale"}
            worksheet.conditional_format(top + 1, position, top + rows, position, rule)
    if style.freeze and table_style:
        worksheet.freeze_panes(top + 1, 0)


def _plain_totals(worksheet, formats: _Formats, numeric: list[int], top: int, rows: int, column_formats) -> None:
    """Totals without an Excel Table: SUBTOTAL(109) sums only the visible rows (even with a filter set in Excel)."""
    total_row = top + rows + 1
    if 0 not in numeric:
        worksheet.write(total_row, 0, TOTAL_LABEL, formats.header)
    for position in numeric:
        letter = xlsxwriter.utility.xl_col_to_name(position)
        worksheet.write_formula(total_row, position, f"=SUBTOTAL(109,{letter}{top + 2}:{letter}{top + rows + 1})",
                                column_formats[position])


def _write_index(book: xlsxwriter.Workbook, formats: _Formats, entries: list[tuple[str, Sheet]]) -> None:
    worksheet = book.add_worksheet(INDEX_SHEET)
    worksheet.write_row(0, 0, ["Foglio", "Titolo", "Righe", "Colonne"], formats.header)
    for row, (name, sheet) in enumerate(entries, start=1):
        worksheet.write_url(row, 0, f"internal:'{name}'!A1", formats.link, string=name)
        worksheet.write(row, 1, sheet.title)
        worksheet.write_number(row, 2, len(sheet.data), formats.integer)
        worksheet.write_number(row, 3, sheet.data.shape[1], formats.integer)
    worksheet.set_column(0, 1, 32)
    worksheet.set_column(2, 3, 10)


def workbook_bytes(sheets: list[Sheet], style: ExcelStyle | None = None) -> bytes:
    """The .xlsx file with the data sheets, in the given order (plus the index first, if requested)."""
    style = style or ExcelStyle()
    if not sheets:
        raise ValueError("Nessun foglio da scrivere.")
    parts = [part for sheet in sheets for part in _parts(sheet)]
    with_index = style.index and len(parts) > 1
    used = {INDEX_SHEET.lower()} if with_index else set()
    names = [sheet_name(part.name, used) for part in parts]
    buffer = io.BytesIO()
    with xlsxwriter.Workbook(buffer, {"in_memory": True, "strings_to_numbers": False,
                                      "strings_to_formulas": False, "strings_to_urls": False}) as book:
        formats = _Formats(book, style)
        if with_index:
            _write_index(book, formats, list(zip(names, parts)))
        for number, (name, part) in enumerate(zip(names, parts), start=1):
            _write_sheet(book, formats, style, part, name, number)
    return buffer.getvalue()


# ---------------------------------------------------------------------------
# Delivery: ZIP or folder on the PC
# ---------------------------------------------------------------------------

def zip_bytes(files: dict[str, bytes]) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        for name, content in files.items():
            archive.writestr(name, content)
    return buffer.getvalue()


def save_to_folder(files: dict[str, bytes], folder: str | Path) -> list[Path]:
    """Writes the files to the folder (created if missing) without overwriting: a name already there becomes «name (2)»."""
    if not str(folder).strip():
        raise ValueError("Indica la cartella in cui salvare i file.")
    target = Path(folder).expanduser()
    target.mkdir(parents=True, exist_ok=True)
    saved = []
    for name, content in files.items():
        path, number = target / name, 1
        while path.exists():
            number += 1
            path = target / f"{Path(name).stem} ({number}){Path(name).suffix}"
        path.write_bytes(content)
        saved.append(path)
    return saved
