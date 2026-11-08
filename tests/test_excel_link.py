from types import SimpleNamespace

import pytest
import pywintypes
import win32com.client

from data_explorer.excel_io import excel_link
def application(address, file, sheet="Dati", saved=True):
    """A fake Excel application whose selection is the range `address` of the workbook `file`."""
    book = SimpleNamespace(FullName=str(file), Saved=saved)
    selected = SimpleNamespace(Address=f"${address}", Worksheet=SimpleNamespace(Name=sheet, Parent=book))  # as COM
    return SimpleNamespace(Selection=selected)


@pytest.fixture
def book(tmp_path):
    path = tmp_path / "vendite.xlsx"
    path.write_bytes(b"")  # a saved workbook is a file on disk
    return path


def test_selection_is_the_range_of_the_saved_workbook(book):
    assert excel_link.selection_of(application("B3:D6", book)) == excel_link.Selection(str(book), "Dati", "B3:D6")


@pytest.mark.parametrize("address, message", [
    ("B3:D6,F3:F9", "un solo intervallo"),
    ("B3:D3", "almeno due righe"),
    ("B:D", "Intervallo non valido"),
])
def test_selection_that_is_not_a_usable_range(book, address, message):
    with pytest.raises(ValueError, match=message):
        excel_link.selection_of(application(address, book))


def test_unsaved_workbook_is_refused(book):
    with pytest.raises(ValueError, match="Salva la cartella"):
        excel_link.selection_of(application("B3:D6", book, saved=False))


def test_chart_selected_is_refused():
    with pytest.raises(ValueError, match="intervallo di celle"):
        excel_link.selection_of(SimpleNamespace(Selection=SimpleNamespace()))  # a chart has no cell address


def test_no_running_excel_gives_a_message(monkeypatch):
    def not_running(progid):
        raise pywintypes.com_error(-2147221021, "Operazione non disponibile", None, None)

    monkeypatch.setattr(win32com.client, "GetActiveObject", not_running)
    with pytest.raises(ValueError, match="Excel non è aperto"):
        excel_link.current_selection()
