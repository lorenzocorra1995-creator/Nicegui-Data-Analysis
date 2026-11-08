"""
Range selected in an Excel workbook that is open on this PC (no UI dependency).

Reads the selection of the running Excel through COM (pywin32, Windows only): the workbook file, the sheet and the
address of the selected range, e.g. «B3:F120». The file is then read from disk like any other Excel file, so the
workbook must be saved first.
"""
from __future__ import annotations

import os
from typing import NamedTuple

import pythoncom
import pywintypes
import win32com.client

from data_explorer.core import engine as eng
class Selection(NamedTuple):
    file: str  # full path of the workbook
    sheet: str
    cells: str  # address of the range, e.g. B3:F120


def selection_of(application) -> Selection:
    """Selection of an Excel application (COM object). Raises ValueError with a message for the user when it is not
    a usable range of a saved workbook."""
    try:
        selected = application.Selection
        address = selected.Address.replace("$", "")  # a property in dynamic COM: "$B$3:$F$120" becomes "B3:F120"
        sheet = selected.Worksheet.Name
        book = selected.Worksheet.Parent
    except (AttributeError, pywintypes.com_error):  # a chart or a shape is selected, not cells
        raise ValueError("Seleziona in Excel un intervallo di celle.") from None
    if "," in address:
        raise ValueError("Seleziona un solo intervallo di celle.")
    _, first_row, _, last_row = eng.cell_block(address)
    if first_row == last_row:
        raise ValueError("Seleziona almeno due righe: l'intestazione e una riga di dati.")
    if not book.Saved or not os.path.exists(book.FullName):
        raise ValueError("Salva la cartella in Excel: l'app legge il file salvato.")
    return Selection(book.FullName, sheet, address)


def _running_selection() -> Selection:
    try:
        application = win32com.client.GetActiveObject("Excel.Application")
    except pywintypes.com_error:
        raise ValueError("Excel non è aperto: apri la cartella e seleziona il range da importare.") from None
    return selection_of(application)


def current_selection() -> Selection:
    """Range selected in the running Excel. COM is initialised for the call, so it can run in a worker thread."""
    pythoncom.CoInitialize()
    try:
        return _running_selection()
    finally:
        pythoncom.CoUninitialize()
