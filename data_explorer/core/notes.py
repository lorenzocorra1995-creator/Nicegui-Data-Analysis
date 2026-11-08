"""
Origin notes of the datasets (no UI).

A note is readable text that says where a table comes from: operation, source datasets
(with their history) and applied filters, taken from the filter tree.
"""
from __future__ import annotations

import datetime as dt

from data_explorer.core import engine as eng
INDENT = "    "


def _indented(text: str) -> list[str]:
    return [INDENT + line for line in text.splitlines() if line.strip()]


def make_note(operation: str, sources: list[tuple[str, dict | None]], origins: dict[str, str]) -> str:
    """
    Note of a result. `sources` = (dataset name, filter tree) for each dataset used;
    `origins` = known history of each dataset (file, database, previous results), shown indented.
    """
    lines = [f"Operazione: {operation}"] if operation else []
    numbered = len(sources) > 1
    for number, (name, tree) in enumerate(sources, 1):
        suffix = f" {number}" if numbered else ""
        lines.append(f"Origine{suffix}: {name}")
        lines += _indented(origins.get(name, ""))
        lines.append(f"Filtri{suffix}: {eng.describe_filters(tree)}")
    return "\n".join(lines)


def file_origin(file_name: str) -> str:
    return f"File: {file_name}"


def database_origin(path: str, created: dt.datetime, note: str) -> str:
    """History of a dataset opened from the database: location, save date and saved note."""
    lines = [f"Database: {path} (salvato il {created:%d/%m/%Y %H:%M})"] + _indented(note)
    return "\n".join(lines)


def with_change(origin: str, change: str) -> str:
    """History after a change to the dataset (structure, calculated column…)."""
    return f"{origin}\nModificato: {change}" if origin else f"Modificato: {change}"


def without_change(origin: str, change: str) -> str:
    """History after undoing the change `change`."""
    return origin.removesuffix(f"\nModificato: {change}").removesuffix(f"Modificato: {change}")
