import io

import openpyxl
import pandas as pd
import pytest

from data_explorer.core import engine as eng
from data_explorer.excel_io import excel_report
from data_explorer.excel_io import excel_split
from data_explorer.excel_io import excel_writer as xw
def read(content: bytes) -> openpyxl.Workbook:
    return openpyxl.load_workbook(io.BytesIO(content))


# --- writing -------------------------------------------------------------------

def test_sheet_with_title_table_totals_and_formats(df):
    style = xw.ExcelStyle(totals=True, red_negatives=True, conditional="data_bar")
    sheet = read(xw.workbook_bytes([xw.Sheet("Vendite", df.head(10), "Titolo", "Nota")], style))["Vendite"]
    assert sheet["A1"].value == "Titolo" and sheet["A2"].value == "Nota"
    assert [cell.value for cell in sheet[4]] == list(df.columns)  # empty row, then the header
    table = sheet.tables["Tabella1"]
    assert table.ref == "A4:F15" and table.totalsRowCount == 1
    assert sheet["A15"].value == xw.TOTAL_LABEL and "SUBTOTAL(109" in sheet["C15"].value
    assert sheet["C5"].number_format == "#,##0.00;[Red]-#,##0.00"
    assert sheet["E5"].number_format == xw.DATE
    assert sheet.freeze_panes == "A5"


def test_plain_range_has_centered_bold_headers_and_nothing_frozen(df):
    style = xw.ExcelStyle(table_style=xw.PLAIN, totals=True, freeze=True)
    sheet = read(xw.workbook_bytes([xw.Sheet("x", df.head(5))], style))["x"]
    assert not sheet.tables and sheet.auto_filter.ref is None and sheet.freeze_panes is None
    header = sheet["A1"]
    assert header.font.bold and header.alignment.horizontal == "center" and header.alignment.vertical == "center"
    assert sheet["C7"].value == "=SUBTOTAL(109,C2:C6)"


def test_missing_values_are_empty_cells():
    frame = pd.DataFrame({"x": [1.5, None], "d": [pd.Timestamp("2024-01-02"), pd.NaT], "t": ["a", None]})
    sheet = read(xw.workbook_bytes([xw.Sheet("x", frame)]))["x"]
    assert [cell.value for cell in sheet[3]] == [None, None, None]


def test_index_sheet_links_every_sheet(df):
    book = read(xw.workbook_bytes([xw.Sheet("Uno", df.head(2)), xw.Sheet("Due", df.head(3))], xw.ExcelStyle(index=True)))
    assert book.sheetnames == [xw.INDEX_SHEET, "Uno", "Due"]
    assert book[xw.INDEX_SHEET]["A2"].hyperlink.location == "'Uno'!A1"
    assert book[xw.INDEX_SHEET]["C3"].value == 3


def test_sheets_beyond_the_row_limit_continue(df, monkeypatch):
    monkeypatch.setattr(xw, "MAX_ROWS", 4)
    book = read(xw.workbook_bytes([xw.Sheet("Dati", df.head(10))]))
    assert book.sheetnames == ["Dati", "Dati (2)", "Dati (3)"]
    assert book["Dati (3)"].max_row == 3


def test_names_are_valid_and_unique():
    used: set[str] = set()
    assert xw.sheet_name("a/b:c", used) == "a_b_c"
    assert xw.sheet_name("A/B:C", used) == "A_B_C (2)"
    assert len(xw.sheet_name("x" * 40, used)) == xw.MAX_SHEET_NAME
    assert xw.file_name('vendite "Nord"?', set()) == "vendite _Nord__.xlsx"


def test_save_to_folder_never_overwrites(tmp_path):
    xw.save_to_folder({"a.xlsx": b"1"}, tmp_path)
    saved = xw.save_to_folder({"a.xlsx": b"2"}, tmp_path / "")
    assert saved[0].name == "a (2).xlsx" and (tmp_path / "a.xlsx").read_bytes() == b"1"


# --- split ---------------------------------------------------------------------

def test_split_by_files_and_sheets(df):
    spec = {"mode": "values", "files_by": ["regione"], "sheets_by": ["prodotto"], "name": "vendite"}
    plan = excel_split.split_plan(df, spec)
    assert [part.name for part in plan] == ["vendite_Centro.xlsx", "vendite_Nord.xlsx", "vendite_Sud.xlsx"]
    assert [sheet.name for sheet in plan[0].sheets] == ["A", "B", "C", "D"]
    table = excel_split.plan_table(plan)
    assert table["righe"].sum() == len(df)
    files = excel_split.build_files(df, plan, spec, xw.ExcelStyle())
    book = read(files["vendite_Nord.xlsx"])
    expected = ((df["regione"] == "Nord") & (df["prodotto"] == "B")).sum()
    assert book["B"].max_row == expected + 1


def test_split_patterns_filters_and_columns(df):
    tree = eng.new_group()
    tree["condizioni"].append(eng.new_condition("regione", "=", "Nord"))
    spec = {"mode": "values", "files_by": ["regione"], "file_pattern": "{n}-{regione}", "columns": ["regione", "importo"],
            "drop_keys": True, "filters": tree, "name": "x"}
    files = excel_split.split(df, spec, xw.ExcelStyle())
    assert list(files) == ["1-Nord.xlsx"]
    sheet = read(files["1-Nord.xlsx"])[excel_split.DEFAULT_SHEET]
    assert [cell.value for cell in sheet[1]] == ["importo"]


def test_unknown_placeholder_lists_the_valid_ones(df):
    with pytest.raises(ValueError, match=r"\{regione\}"):
        excel_split.split_plan(df, {"files_by": ["regione"], "file_pattern": "{area}"})


def test_split_by_rows_into_files_or_sheets(df):
    files = excel_split.split_plan(df, {"mode": "rows", "rows_per_part": 400, "name": "d"})
    assert [part.name for part in files] == ["d_1.xlsx", "d_2.xlsx", "d_3.xlsx"]
    sheets = excel_split.split_plan(df, {"mode": "rows", "rows_per_part": 400, "rows_into": "sheets", "name": "d"})
    assert len(sheets) == 1 and [len(sheet.rows) for sheet in sheets[0].sheets] == [400, 400, 200]


def test_split_needs_a_column_and_limits_the_files(df, monkeypatch):
    with pytest.raises(ValueError, match="almeno una colonna"):
        excel_split.split_plan(df, {"mode": "values"})
    monkeypatch.setattr(excel_split, "MAX_FILES", 2)
    with pytest.raises(ValueError, match="massimo 2"):
        excel_split.split_plan(df, {"files_by": ["regione"]})


def test_missing_and_date_keys_have_readable_names(df):
    plan = excel_split.split_plan(pd.DataFrame({"k": ["a", None]}), {"files_by": ["k"], "name": "n"})
    assert {part.name for part in plan} == {"n_a.xlsx", f"n_{excel_split.MISSING}.xlsx"}
    assert excel_split.key_text(pd.Timestamp("2024-05-01")) == "2024-05-01"


# --- report --------------------------------------------------------------------

def test_report_keeps_order_names_and_titles(df):
    report = excel_report.Report()
    first = report.add("Vendite", df.head(3), title="Prime righe")
    second = report.add("vendite", df.head(5))
    assert second.name == "vendite (2)"
    report.move(second, -1)
    assert report.rename(first, "Riepilogo/1") == "Riepilogo_1"
    book = read(report.workbook(xw.ExcelStyle()))
    assert book.sheetnames == ["vendite (2)", "Riepilogo_1"]
    assert book["Riepilogo_1"]["A1"].value == "Prime righe"


def test_empty_report_explains_how_to_fill_it():
    with pytest.raises(ValueError, match="Aggiungi al report"):
        excel_report.Report().workbook(xw.ExcelStyle())
