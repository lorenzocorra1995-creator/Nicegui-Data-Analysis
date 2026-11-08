"""
Multi-sheet Excel report (no UI dependency).

Report collects tables from any tab (snapshots: they stay the same even if the source result
changes), keeps them in order with sheet name, title and note, and writes them to a single file with excel_writer.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from itertools import count

import pandas as pd

from data_explorer.excel_io import excel_writer as xw
DEFAULT_REPORT_NAME = "report"


@dataclass
class ReportItem:
    name: str  # sheet name
    data: pd.DataFrame
    source: str = ""  # where it comes from (tab / result), for information only
    title: str = ""
    note: str = ""
    id: int = field(default_factory=count().__next__)


class Report:
    def __init__(self) -> None:
        self.items: list[ReportItem] = []

    def __len__(self) -> int:
        return len(self.items)

    def unique_name(self, name: str, ignore: ReportItem | None = None) -> str:
        """Valid sheet name, not yet used in the report."""
        used = {item.name.lower() for item in self.items if item is not ignore}
        return xw.sheet_name(name, used)

    def add(self, name: str, data: pd.DataFrame, source: str = "", title: str = "", note: str = "") -> ReportItem:
        item = ReportItem(self.unique_name(name), data, source, title, note)
        self.items.append(item)
        return item

    def rename(self, item: ReportItem, name: str) -> str:
        item.name = self.unique_name(name, ignore=item)
        return item.name

    def move(self, item: ReportItem, step: int) -> None:
        position = self.items.index(item)
        target = min(max(position + step, 0), len(self.items) - 1)
        self.items.insert(target, self.items.pop(position))

    def remove(self, item: ReportItem) -> None:
        self.items.remove(item)

    def clear(self) -> None:
        self.items.clear()

    def sheets(self) -> list[xw.Sheet]:
        return [xw.Sheet(item.name, item.data, item.title, item.note) for item in self.items]

    def workbook(self, style: xw.ExcelStyle) -> bytes:
        if not self.items:
            raise ValueError("Il report è vuoto: aggiungi tabelle con «Aggiungi al report» sotto i risultati.")
        return xw.workbook_bytes(self.sheets(), style)
