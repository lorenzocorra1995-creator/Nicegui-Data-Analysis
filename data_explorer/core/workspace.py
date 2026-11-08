"""
Database of the datasets (no UI), in a DuckDB file.

Structure: projects (folders without a parent) with optional subfolders, which can be nested.
Each dataset is saved as it is, in its own table, with a text note about its origin.
"""
from __future__ import annotations

import datetime as dt
import threading
from dataclasses import dataclass
from pathlib import Path

import duckdb
import pandas as pd

from data_explorer.core import engine as eng
DEFAULT_PATH = Path(__file__).resolve().parents[2] / "data" / "workspace.duckdb"  # <project>/data/workspace.duckdb
PATH_SEPARATOR = " › "
FOLDER, DATASET = "folder", "dataset"  # node types in the tree

SCHEMA = (
    "CREATE SEQUENCE IF NOT EXISTS ids START 1",
    """CREATE TABLE IF NOT EXISTS folders (
        id BIGINT PRIMARY KEY DEFAULT nextval('ids'),
        parent_id BIGINT,
        name VARCHAR NOT NULL,
        created TIMESTAMP DEFAULT current_localtimestamp())""",
    """CREATE TABLE IF NOT EXISTS datasets (
        id BIGINT PRIMARY KEY DEFAULT nextval('ids'),
        folder_id BIGINT NOT NULL,
        name VARCHAR NOT NULL,
        rows BIGINT,
        cols INTEGER,
        note VARCHAR DEFAULT '',
        created TIMESTAMP DEFAULT current_localtimestamp())""",
)


@dataclass(frozen=True)
class Folder:
    id: int
    parent_id: int | None
    name: str


@dataclass(frozen=True)
class SavedDataset:
    id: int
    folder_id: int
    name: str
    rows: int
    cols: int
    note: str
    created: dt.datetime


# ---------------------------------------------------------------------------
# Names
# ---------------------------------------------------------------------------

def check_name(name: str, others: list[str], what: str = "nome") -> str:
    """Name without spaces at the ends; error if empty or already used in the same folder (also by case)."""
    name = name.strip()
    if not name:
        raise ValueError(f"Il {what} non può essere vuoto")
    if any(name.casefold() == other.casefold() for other in others):
        raise ValueError(f"«{name}» esiste già in questa cartella")
    return name


def storable_columns(names) -> list[str]:
    """Column names as text and unique even ignoring case (in DuckDB «A» and «a» are the same)."""
    used, out = set(), []
    for name in map(str, names):
        candidate, number = name, 2
        while candidate.casefold() in used:
            candidate = f"{name}_{number}"
            number += 1
        used.add(candidate.casefold())
        out.append(candidate)
    return out


def table_name(dataset_id: int) -> str:
    return f"data_{dataset_id}"


def node_id(kind: str, item_id: int) -> str:
    return f"{kind}:{item_id}"


def parse_node(node: str) -> tuple[str, int]:
    kind, item_id = node.split(":")
    return kind, int(item_id)


# ---------------------------------------------------------------------------
# Database
# ---------------------------------------------------------------------------

class Workspace:
    def __init__(self, path: str | Path = DEFAULT_PATH) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()  # a single connection, also used by the run.io_bound threads
        self._con = duckdb.connect(str(self.path))
        for statement in SCHEMA:
            self._con.execute(statement)

    def close(self) -> None:
        self._con.close()

    def _rows(self, query: str, params: list | tuple = ()) -> list[tuple]:
        with self._lock:
            return self._con.execute(query, params).fetchall()

    # --- folders --------------------------------------------------------------
    def folders(self) -> list[Folder]:
        return [Folder(*row) for row in self._rows("SELECT id, parent_id, name FROM folders ORDER BY lower(name)")]

    def folder(self, folder_id: int) -> Folder:
        rows = self._rows("SELECT id, parent_id, name FROM folders WHERE id = ?", [folder_id])
        if not rows:
            raise ValueError("La cartella non esiste più")
        return Folder(*rows[0])

    def _folder_names(self, parent_id: int | None, exclude: int | None = None) -> list[str]:
        rows = self._rows("SELECT id, name FROM folders WHERE parent_id IS NOT DISTINCT FROM ?", [parent_id])
        return [name for item_id, name in rows if item_id != exclude]

    def create_folder(self, name: str, parent_id: int | None = None) -> int:
        """New project (without `parent_id`) or subfolder."""
        with self._lock:
            if parent_id is not None:
                self.folder(parent_id)
            name = check_name(name, self._folder_names(parent_id), "nome della cartella")
            return self._rows("INSERT INTO folders (parent_id, name) VALUES (?, ?) RETURNING id", [parent_id, name])[0][0]

    def rename_folder(self, folder_id: int, name: str) -> None:
        with self._lock:
            folder = self.folder(folder_id)
            name = check_name(name, self._folder_names(folder.parent_id, exclude=folder_id), "nome della cartella")
            self._rows("UPDATE folders SET name = ? WHERE id = ?", [name, folder_id])

    def subtree(self, folder_id: int) -> list[int]:
        """The folder and all its subfolders, at every level."""
        children: dict[int | None, list[int]] = {}
        for folder in self.folders():
            children.setdefault(folder.parent_id, []).append(folder.id)
        found, pending = [], [folder_id]
        while pending:
            current = pending.pop()
            found.append(current)
            pending += children.get(current, [])
        return found

    def delete_folder(self, folder_id: int) -> None:
        """Deletes the folder together with its subfolders and the datasets they contain."""
        with self._lock:
            ids = self.subtree(folder_id)
            marks = ", ".join("?" * len(ids))
            dataset_ids = [row[0] for row in self._rows(f"SELECT id FROM datasets WHERE folder_id IN ({marks})", ids)]
            self._con.execute("BEGIN TRANSACTION")
            try:
                for dataset_id in dataset_ids:
                    self._con.execute(f"DROP TABLE IF EXISTS {table_name(dataset_id)}")
                self._con.execute(f"DELETE FROM datasets WHERE folder_id IN ({marks})", ids)
                self._con.execute(f"DELETE FROM folders WHERE id IN ({marks})", ids)
                self._con.execute("COMMIT")
            except Exception:
                self._con.execute("ROLLBACK")
                raise

    def folder_choices(self) -> dict[int, str]:
        """All the folders with the readable path («Progetto › Sottocartella»), sorted by path."""
        by_id = {folder.id: folder for folder in self.folders()}

        def path(folder: Folder | None) -> str:
            parts = []
            while folder is not None:
                parts.append(folder.name)
                folder = by_id.get(folder.parent_id)
            return PATH_SEPARATOR.join(reversed(parts))

        paths = {folder_id: path(folder) for folder_id, folder in by_id.items()}
        return dict(sorted(paths.items(), key=lambda item: item[1].lower()))

    def folder_path(self, folder_id: int) -> str:
        return self.folder_choices().get(folder_id, "")

    # --- datasets -------------------------------------------------------------
    def datasets(self, folder_id: int | None = None) -> list[SavedDataset]:
        query = "SELECT id, folder_id, name, rows, cols, note, created FROM datasets"
        params: list = []
        if folder_id is not None:
            query += " WHERE folder_id = ?"
            params = [folder_id]
        return [SavedDataset(*row) for row in self._rows(query + " ORDER BY lower(name)", params)]

    def dataset(self, dataset_id: int) -> SavedDataset:
        rows = self._rows("SELECT id, folder_id, name, rows, cols, note, created FROM datasets WHERE id = ?",
                          [dataset_id])
        if not rows:
            raise ValueError("Il dataset non esiste più")
        return SavedDataset(*rows[0])

    def _dataset_names(self, folder_id: int, exclude: int | None = None) -> list[str]:
        return [item.name for item in self.datasets(folder_id) if item.id != exclude]

    def find_dataset(self, folder_id: int, name: str) -> SavedDataset | None:
        return next((item for item in self.datasets(folder_id) if item.name.casefold() == name.strip().casefold()), None)

    def save_dataset(self, folder_id: int, name: str, df: pd.DataFrame, note: str = "",
                     replace: bool = False) -> SavedDataset:
        """Saves the DataFrame as it is; with `replace` it replaces the dataset with the same name in the folder."""
        data = df.set_axis(storable_columns(df.columns), axis=1)
        table = eng.to_arrow(data)
        with self._lock:
            self.folder(folder_id)
            existing = self.find_dataset(folder_id, name)
            if existing is None or not replace:
                name = check_name(name, self._dataset_names(folder_id), "nome del dataset")
            self._con.execute("BEGIN TRANSACTION")
            try:
                if existing is not None:
                    self._drop_dataset(existing.id)
                dataset_id = self._con.execute(
                    "INSERT INTO datasets (folder_id, name, rows, cols, note) VALUES (?, ?, ?, ?, ?) RETURNING id",
                    [folder_id, name.strip(), len(df), df.shape[1], note]).fetchone()[0]
                self._con.register("incoming", table)
                self._con.execute(f"CREATE TABLE {table_name(dataset_id)} AS SELECT * FROM incoming")
                self._con.execute("COMMIT")
            except Exception:
                self._con.execute("ROLLBACK")
                raise
            finally:
                self._con.unregister("incoming")
            return self.dataset(dataset_id)

    def load_dataset(self, dataset_id: int) -> pd.DataFrame:
        with self._lock:
            self.dataset(dataset_id)
            table = self._con.table(table_name(dataset_id)).arrow()
        return eng.arrow_to_pandas(table.read_all() if hasattr(table, "read_all") else table)

    def rename_dataset(self, dataset_id: int, name: str) -> None:
        with self._lock:
            item = self.dataset(dataset_id)
            name = check_name(name, self._dataset_names(item.folder_id, exclude=dataset_id), "nome del dataset")
            self._rows("UPDATE datasets SET name = ? WHERE id = ?", [name, dataset_id])

    def move_dataset(self, dataset_id: int, folder_id: int) -> None:
        with self._lock:
            item = self.dataset(dataset_id)
            self.folder(folder_id)
            check_name(item.name, self._dataset_names(folder_id, exclude=dataset_id), "nome del dataset")
            self._rows("UPDATE datasets SET folder_id = ? WHERE id = ?", [folder_id, dataset_id])

    def set_note(self, dataset_id: int, note: str) -> None:
        with self._lock:
            self.dataset(dataset_id)
            self._rows("UPDATE datasets SET note = ? WHERE id = ?", [note, dataset_id])

    def _drop_dataset(self, dataset_id: int) -> None:
        self._con.execute(f"DROP TABLE IF EXISTS {table_name(dataset_id)}")
        self._con.execute("DELETE FROM datasets WHERE id = ?", [dataset_id])

    def delete_dataset(self, dataset_id: int) -> None:
        with self._lock:
            self._drop_dataset(dataset_id)

    # --- tree for the interface ----------------------------------------------
    def tree(self) -> list[dict]:
        """Nodes for a tree (id, label, icon, children): folders first, then datasets, in alphabetical order."""
        nodes = {folder.id: {"id": node_id(FOLDER, folder.id), "label": folder.name,
                             "icon": "folder", "children": []} for folder in self.folders()}
        roots = []
        for folder in self.folders():
            parent = nodes.get(folder.parent_id)
            (parent["children"] if parent else roots).append(nodes[folder.id])
        for item in self.datasets():
            if item.folder_id in nodes:
                nodes[item.folder_id]["children"].append(
                    {"id": node_id(DATASET, item.id), "label": item.name, "icon": "table_chart"})
        return roots
