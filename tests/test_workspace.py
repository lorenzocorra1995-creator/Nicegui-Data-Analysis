import datetime as dt

import pandas as pd
import pytest

from data_explorer.core import engine as eng
from data_explorer.core import notes
from data_explorer.core import workspace
@pytest.fixture
def ws(tmp_path):
    database = workspace.Workspace(tmp_path / "test.duckdb")
    yield database
    database.close()


# --- folders -----------------------------------------------------------------

def test_projects_and_nested_folders(ws):
    project = ws.create_folder("Vendite")
    march = ws.create_folder("Marzo", project)
    week = ws.create_folder("Settimana 1", march)
    assert ws.folder_path(week) == "Vendite › Marzo › Settimana 1"
    assert list(ws.folder_choices().values()) == ["Vendite", "Vendite › Marzo", "Vendite › Marzo › Settimana 1"]
    assert sorted(ws.subtree(project)) == sorted([project, march, week])


def test_folder_names_are_unique_among_siblings(ws):
    project = ws.create_folder("Vendite")
    ws.create_folder("Marzo", project)
    with pytest.raises(ValueError, match="esiste già"):
        ws.create_folder("marzo ", project)
    ws.create_folder("Marzo")  # same name but another level: that is fine
    with pytest.raises(ValueError, match="vuoto"):
        ws.create_folder("  ")
    with pytest.raises(ValueError):
        ws.create_folder("Orfana", 999)


def test_rename_and_delete_folder_with_contents(ws, df):
    project = ws.create_folder("Vendite")
    sub = ws.create_folder("Marzo", project)
    item = ws.save_dataset(sub, "estratto", df)
    ws.rename_folder(sub, "Aprile")
    assert ws.folder_path(sub) == "Vendite › Aprile"
    ws.delete_folder(project)
    assert ws.folders() == [] and ws.datasets() == []
    with pytest.raises(ValueError):
        ws.dataset(item.id)
    tables = {row[0] for row in ws._rows("SELECT table_name FROM duckdb_tables()")}
    assert workspace.table_name(item.id) not in tables


# --- datasets ----------------------------------------------------------------

def test_save_and_load_keeps_data(ws, df):
    folder = ws.create_folder("Vendite")
    item = ws.save_dataset(folder, "estratto", df, "Origine: demo")
    assert (item.rows, item.cols, item.note) == (len(df), df.shape[1], "Origine: demo")
    assert isinstance(item.created, dt.datetime)
    loaded = ws.load_dataset(item.id)
    pd.testing.assert_frame_equal(loaded, df.reset_index(drop=True), check_dtype=False)


def test_data_survives_reopening(tmp_path, df):
    path = tmp_path / "persist.duckdb"
    first = workspace.Workspace(path)
    item = first.save_dataset(first.create_folder("P"), "d", df.head(10))
    first.close()
    second = workspace.Workspace(path)
    assert len(second.load_dataset(item.id)) == 10
    second.close()


def test_duplicate_dataset_name_and_replace(ws, df):
    folder = ws.create_folder("P")
    first = ws.save_dataset(folder, "d", df.head(5))
    with pytest.raises(ValueError, match="esiste già"):
        ws.save_dataset(folder, "D", df.head(3))
    second = ws.save_dataset(folder, "d", df.head(3), replace=True)
    assert [item.id for item in ws.datasets(folder)] == [second.id] and second.rows == 3
    with pytest.raises(ValueError):
        ws.dataset(first.id)


def test_column_names_become_unique_text():
    assert workspace.storable_columns(["a", "A", 1, "a"]) == ["a", "A_2", "1", "a_3"]  # «a_2» matches «A_2»


def test_rename_move_note_and_delete_dataset(ws, df):
    one, two = ws.create_folder("Uno"), ws.create_folder("Due")
    item = ws.save_dataset(one, "d", df.head(2))
    ws.rename_dataset(item.id, "nuovo")
    ws.move_dataset(item.id, two)
    ws.set_note(item.id, "nota corretta")
    moved = ws.dataset(item.id)
    assert (moved.name, moved.folder_id, moved.note) == ("nuovo", two, "nota corretta")
    ws.save_dataset(one, "nuovo", df.head(1))
    with pytest.raises(ValueError, match="esiste già"):
        ws.move_dataset(item.id, one)
    ws.delete_dataset(item.id)
    assert [other.folder_id for other in ws.datasets()] == [one]


def test_tree_lists_folders_then_datasets(ws, df):
    project = ws.create_folder("P")
    sub = ws.create_folder("Sotto", project)
    item = ws.save_dataset(project, "a", df.head(1))
    tree = ws.tree()
    assert [node["id"] for node in tree] == [workspace.node_id(workspace.FOLDER, project)]
    children = tree[0]["children"]
    assert [child["id"] for child in children] == [workspace.node_id(workspace.FOLDER, sub),
                                                  workspace.node_id(workspace.DATASET, item.id)]
    assert workspace.parse_node(children[1]["id"]) == (workspace.DATASET, item.id)


# --- notes -------------------------------------------------------------------

def test_note_from_filter_tree():
    tree = eng.new_group()
    tree["condizioni"] = [eng.new_condition("regione", "=", "Nord")]
    note = notes.make_note("Group By per regione", [("vendite", tree)],
                           {"vendite": notes.file_origin("vendite.xlsx [Marzo]")})
    assert note.splitlines() == ["Operazione: Group By per regione", "Origine: vendite",
                                 "    File: vendite.xlsx [Marzo]", 'Filtri: regione = "Nord"']


def test_note_with_several_sources_and_nested_history():
    created = dt.datetime(2026, 10, 28, 14, 32)
    origin = notes.database_origin("Vendite › Marzo", created, "Origine: demo\nFiltri: (nessun filtro)")
    note = notes.make_note("Join", [("a", None), ("b", None)], {"a": origin})
    assert note.splitlines() == [
        "Operazione: Join", "Origine 1: a", "    Database: Vendite › Marzo (salvato il 28/10/2026 14:32)",
        "        Origine: demo", "        Filtri: (nessun filtro)", "Filtri 1: (nessun filtro)",
        "Origine 2: b", "Filtri 2: (nessun filtro)"]


def test_changes_are_added_and_undone():
    origin = notes.file_origin("x.csv")
    changed = notes.with_change(origin, "colonna calcolata «y»")
    assert changed == "File: x.csv\nModificato: colonna calcolata «y»"
    assert notes.without_change(changed, "colonna calcolata «y»") == origin
    assert notes.without_change(notes.with_change("", "z"), "z") == ""
