import asyncio
import io
import json
import tempfile
import zipfile
from pathlib import Path

import openpyxl
import pandas as pd
import pytest
from nicegui import context, ui
from nicegui.testing.user_notify import UserNotify
from nicegui.testing import User
from nicegui.testing.user_interaction import UserInteraction
from data_explorer import app as A
from data_explorer.core import combine
from data_explorer.core import engine as eng
from demo_data import make_demo_df

DF = make_demo_df(5000)

CREATED = []
_orig_box = A.filter_box
def _box(*a, **k):
    fb = _orig_box(*a, **k)
    CREATED.append(fb)
    return fb
A.filter_box = _box

JoinPanel, UnionPanel = A.JoinPanel, A.UnionPanel
PANELS = []
def _record(cls):
    def build(*a, **k):
        panel = cls(*a, **k)
        PANELS.append(panel)
        return panel
    return build
A.JoinPanel, A.UnionPanel = _record(JoinPanel), _record(UnionPanel)
ImportDialog = A.ImportDialog
A.ImportDialog = _record(ImportDialog)
CaseRules = A.CaseRules
A.CaseRules = _record(CaseRules)

DashboardView = A.DashboardView
A.DashboardView = _record(DashboardView)

PivotTab = A.PivotTab
A.TABS["Pivot"] = (*A.TABS["Pivot"][:2], _record(PivotTab))

def last_panel(cls):
    return next(panel for panel in reversed(PANELS) if isinstance(panel, cls))

def setup_state():
    CREATED.clear()
    PANELS.clear()
    A.page_content.targets.clear()  # no refresh towards clients of earlier tests
    A.windows.clear()
    ui.page("/")(A.main_page)
    A.S.__init__()
    A.S.database_path = Path(tempfile.mkdtemp()) / "ui.duckdb"  # each test with its own empty database
    A.S.add("demo", DF.copy(), origin="File: demo.parquet")

@pytest.fixture
def deleted_contexts_fail(monkeypatch):
    """As in the real server: the element of a handler that has been deleted (the button of a dialog the handler
    closed) leaves no context, so `ui.notify` fails because it reads it. The simulated user records the messages
    without reading the context, and would hide the bug."""
    record = UserNotify.__call__

    def notify_like_the_server(self, message, **kwargs):
        context.client  # noqa: B018  (raises like the real ui.notify)
        return record(self, message, **kwargs)

    monkeypatch.setattr(UserNotify, "__call__", notify_like_the_server)


def rows_grid(user, n_expected=None):
    return [len(g.options["rowData"]) for g in user.find(kind=ui.aggrid).elements]

async def test_opening(user: User):
    setup_state()
    await user.open("/")
    for t in ["Data Explorer", "Applica filtri", "Colonne calcolate", "Group By", "Pivot", "Console", "Struttura colonne"]:
        await user.should_see(t)

async def test_nested_filters_via_ui(user: User):
    setup_state()
    await user.open("/")
    user.find(kind=ui.button, content="Condizione").click()
    user.find(kind=ui.button, content="Gruppo").click()
    await user.should_see("Colonna")
    # builds the tree as the user does (by changing the widgets): regione=Nord AND (importo>1200 OR prodotto in A,B)
    fb = CREATED[0]  # filter of the Dati tab
    assert len(fb.tree["condizioni"]) == 2, fb.tree
    fb.tree["condizioni"][0].update(col="regione", op="=", val="Nord")
    grp = fb.tree["condizioni"][1]
    grp["condizioni"] = [eng.new_condition("importo", ">", "1200"), eng.new_condition("prodotto", "in lista", "A, B")]
    fb.render.refresh()
    user.find(marker="run-dati").click()
    await asyncio.sleep(0.5)
    expected = min(len(DF[(DF.regione == "Nord") & ((DF.importo > 1200) | DF.prodotto.isin(["A", "B"]))]), 50)
    assert expected in rows_grid(user), (expected, rows_grid(user))

async def test_groupby_via_ui(user: User):
    setup_state()
    await user.open("/")
    user.find(content="Group By").click()
    await user.should_see("Raggruppa per")
    user.find(kind=ui.button, content="Aggiungi valore").click()
    user.find(marker="run-groupby").click()
    await asyncio.sleep(1)
    # no valid aggregation and no key -> 1 row n_righe=5000
    assert 1 in rows_grid(user)

async def test_pivot_error_is_handled(user: User):
    setup_state()
    await user.open("/")
    user.find(content="Pivot").click()
    user.find(marker="run-pivot").click()
    await asyncio.sleep(0.8)
    assert any("almeno" in str(m) for m in user.notify.messages), user.notify.messages

async def test_pivot_text_falls_back_to_count(user: User):
    setup_state()
    await user.open("/")
    user.find(content="Pivot").click()
    tab = last_panel(PivotTab)
    tab.rows.value = ["prodotto"]
    await tab.values.add()  # new value: «somma» by default
    await tab.values.set_col(tab.values.items[0], "regione")  # text: the sum would concatenate
    assert tab.values.items[0]["fn"] == "conteggio"
    await tab.build()
    assert "conteggio regione" in tab.table.frame.columns
    assert not any("testo" in str(m) for m in user.notify.messages), user.notify.messages

async def test_pivot_cube_via_ui(user: User):
    setup_state()
    await user.open("/")
    user.find(content="Pivot").click()
    tab = last_panel(PivotTab)
    tab.rows.value = ["regione"]
    tab.values.items.append({"col": "importo", "fn": "somma", "name": ""})
    tab.values.render.refresh()
    user.find(marker="run-pivot").click()
    await asyncio.sleep(1)
    assert 4 in rows_grid(user)  # 3 regions + Totale
    cube = tab.cube

    table = tab.table
    column = "somma importo (% di totale)"
    await tab.set_measure(tab.values.items[0], show_as="% di")  # no automatic update
    assert tab.table is table and column not in tab.table.frame.columns
    await tab.build()  # presentation only: same cube
    assert tab.cube is cube and tab.table.frame[column].iloc[-1] == 100

    tab.columns.value = ["prodotto"]  # new field: the cube is recalculated with «Crea Pivot»
    await asyncio.sleep(0.3)
    assert tab.cube is cube
    await tab.build()
    assert tab.cube is not cube and "somma importo (% di totale) | A" in tab.table.frame.columns

    assert not tab.drill_button.enabled  # no cell chosen
    tab.result._cell_click(type("Event", (), {"args": {"data": {A.ROW_ID: 0}, "colId": "somma importo (% di totale) | A"}})())
    assert tab.drill_result.df is None and tab.drill_button.enabled  # the click only chooses
    user.find(marker="pivot-drill").click()
    region = tab.table.frame.regione.iloc[0]
    assert len(tab.drill_result.df) == len(DF[(DF.regione == region) & (DF.prodotto == "A")])

async def test_console_sql_and_python(user: User):
    setup_state()
    await user.open("/")
    user.find(content="Console").click()
    await user.should_see("Esegui")
    ed = list(user.find(kind=ui.codemirror).elements)[0]
    ed.value = "SELECT regione, SUM(importo) AS tot FROM df GROUP BY 1 ORDER BY 2 DESC"
    user.find(marker="run-console").click()
    await asyncio.sleep(1.5)
    assert 3 in rows_grid(user), rows_grid(user)

    # Python: switches language and uses df + sql()
    tog = [t for t in user.find(kind=ui.toggle).elements if "Python" in str(t.options)][0]
    tog.value = "Python"
    await asyncio.sleep(0.3)
    ed.value = "x = df[df.regione=='Sud']\nprint('righe Sud:', len(x))\nsql('select prodotto, count(*) n from df group by 1')"
    user.find(marker="run-console").click()
    await asyncio.sleep(1.5)
    assert 4 in rows_grid(user), rows_grid(user)
    out = [l.text for l in user.find(kind=ui.label).elements if "righe Sud" in (l.text or "")]
    assert out, "stdout non mostrato"

    # Python: chart from the example (the last line is a dict of ECharts options)
    ed.value = ""
    user.find(marker="console-example").click()
    assert ed.value.startswith("# Grafico")
    user.find(marker="run-console").click()
    await asyncio.sleep(1.5)
    chart = user.find(marker="console-chart").elements.pop()
    assert chart.options["series"][0]["type"] == "bar" and chart.style["height"] == "480px"
    assert chart.options["series"][0]["label"]["textBorderWidth"] == 0  # labels as in the app charts

async def test_export_and_save_dataset(user: User):
    setup_state()
    await user.open("/")
    user.find(marker="save-dataset").click()
    await user.should_see("Nome")
    user.find(marker="salva-ok").click()
    await asyncio.sleep(0.3)
    assert len(A.S.datasets) == 2
    user.find(marker="dl-xlsx").click()
    await asyncio.sleep(1)
    assert len(user.notify.messages) == 1, user.notify.messages  # only the notification of 'Salva come dataset', no export error


async def test_ai_requires_key_and_writes_code(user: User, monkeypatch):
    setup_state()
    monkeypatch.setattr(A.ai, "generate_code", lambda *args: "SELECT 42")
    await user.open("/")
    user.find(content="Console").click()
    await user.should_see("Esegui")
    user.find(marker="ai-genera").click()
    await asyncio.sleep(0.3)
    assert any("API key" in str(m) for m in user.notify.messages), user.notify.messages

    A.S.api_key = "sk-test"
    textarea_by_label(user, "Descrivi cosa vuoi ottenere").value = "dammi 42"
    user.find(marker="ai-genera").click()
    await asyncio.sleep(0.8)
    assert any(editor.value == "SELECT 42" for editor in user.find(kind=ui.codemirror).elements)


async def test_data_preview_default_and_expand(user: User):
    setup_state()
    await user.open("/")
    assert 50 in rows_grid(user)
    limit_select = next(s for s in user.find(kind=ui.select).elements if s.props.get("label") == "Righe mostrate" and s.value == A.DATA_PREVIEW_ROWS)
    limit_select.value = 500
    await asyncio.sleep(0.3)
    assert 500 in rows_grid(user)


def value_selects(user):
    return [s for s in user.find(kind=ui.select).elements if str(s.props.get("label")).startswith(("Valore", "Valori"))]


async def test_filter_offers_unique_values(user: User):
    setup_state()
    await user.open("/")
    user.find(kind=ui.button, content="Condizione").click()
    fb = CREATED[0]
    condition = fb.tree["condizioni"][0]

    # string + "=": single select with the unique values
    fb._change_column(condition, "regione")
    await asyncio.sleep(0.2)
    (single,) = value_selects(user)
    assert not single.multiple and set(single.options) == {"Nord", "Sud", "Centro"}

    # string + "in lista": multiple select, value saved as a list
    fb._change_operator(condition, "in lista")
    await asyncio.sleep(0.2)
    (multi,) = value_selects(user)
    assert multi.multiple and condition["val"] == []
    multi.value = ["Nord", "Sud"]
    assert condition["val"] == ["Nord", "Sud"]

    # going back to "=" the value becomes single
    fb._change_operator(condition, "=")
    assert condition["val"] == "Nord"

    # numeric column: same select, with the column values in numeric order
    fb._change_column(condition, "qta")
    await asyncio.sleep(0.2)
    (numeric,) = value_selects(user)
    assert not numeric.multiple and numeric.options[:3] == ["1", "2", "3"]

    # operators without proposed values (e.g. «>»): text field
    fb._change_operator(condition, ">")
    await asyncio.sleep(0.2)
    assert value_selects(user) == []


async def test_filter_with_multiselect_applies(user: User):
    setup_state()
    await user.open("/")
    user.find(kind=ui.button, content="Condizione").click()
    fb = CREATED[0]
    fb.tree["condizioni"][0].update(col="prodotto", op="in lista", val=["A", "B"])
    user.find(marker="run-dati").click()
    await asyncio.sleep(0.5)
    expected = min(DF.prodotto.isin(["A", "B"]).sum(), 50)
    assert expected in rows_grid(user)


async def test_text_input_fallback_does_not_show_list_brackets(user: User):
    setup_state()
    await user.open("/")
    user.find(kind=ui.button, content="Condizione").click()
    fb = CREATED[0]
    condition = fb.tree["condizioni"][0]
    fb._change_column(condition, "qta")
    fb._change_operator(condition, "in lista")
    await asyncio.sleep(0.2)
    select_by_label(user, "Valori").value = ["1", "2"]
    assert len(eng.apply_filters(A.S.df, {"logica": "AND", "condizioni": [condition]})) == DF.qta.isin([1, 2]).sum()
    fb._change_operator(condition, "tra")  # text field: the list becomes «1, 2», without brackets
    await asyncio.sleep(0.2)
    (field,) = [i for i in user.find(kind=ui.input).elements if i.props.get("label") == "Valore"]
    assert field.value == "1, 2"
    assert len(eng.apply_filters(A.S.df, {"logica": "AND", "condizioni": [condition]})) == DF.qta.between(1, 2).sum()


def fire_input_value(select, text):
    """Simulates the text typed in the search field of the select (browser 'input-value' event)."""
    listener = next(l for l in select._event_listeners.values() if l.type == "inputValue")
    select._handle_event({"listener_id": listener.id, "args": text})


async def test_select_shows_max_50_values_and_searches(user: User):
    setup_state()
    A.S.datasets["demo"]["codice"] = [f"id{i}" for i in range(len(DF))]  # 5000 distinct values
    await user.open("/")
    user.find(kind=ui.button, content="Condizione").click()
    fb = CREATED[0]
    condition = fb.tree["condizioni"][0]
    fb._change_column(condition, "codice")
    fb._change_operator(condition, "in lista")
    await asyncio.sleep(0.2)
    (select,) = value_selects(user)
    assert len(select.options) == 50 and "5.000" in select.props["label"]

    fire_input_value(select, "id4999")
    assert select.options == ["id4999"]

    # values already chosen stay in the options even when searching for something else (plus at most 50 results)
    select.value = ["id4999"]
    fire_input_value(select, "id1")
    assert select.options[0] == "id4999" and 1 < len(select.options) <= 51
    assert condition["val"] == ["id4999"]


async def test_single_select_ignores_its_own_selected_label(user: User):
    setup_state()
    A.S.datasets["demo"]["codice"] = [f"id{i}" for i in range(len(DF))]
    await user.open("/")
    user.find(kind=ui.button, content="Condizione").click()
    fb = CREATED[0]
    condition = fb.tree["condizioni"][0]
    fb._change_column(condition, "codice")
    await asyncio.sleep(0.2)
    (select,) = value_selects(user)
    select.value = "id42"
    fire_input_value(select, "id42")  # the browser sends back the text of the chosen value: it is not a search
    assert select.options[0] == "id42" and len(select.options) == 51  # chosen value + 50 proposals


async def test_excel_asks_sheet(user: User, tmp_path):
    setup_state()
    path = tmp_path / "book.xlsx"
    with pd.ExcelWriter(path) as writer:
        DF.head(5).to_excel(writer, sheet_name="Primo", index=False)
        DF.head(8).to_excel(writer, sheet_name="Secondo", index=False)
    await user.open("/")
    async def drop_file() -> None:  # like a file dropped in the upload window
        with user:
            await A.load_dataset_file("book.xlsx", path.read_bytes())

    loading = asyncio.create_task(drop_file())
    await user.should_see("Quale foglio")
    next(s for s in user.find(kind=ui.select).elements if s.props.get("label") == "Foglio").value = "Secondo"
    user.find(marker="sheet-ok").click()
    await user.should_see("Importa «book.xlsx [Secondo]»")
    user.find(marker="import-ok").click()
    await loading
    assert len(A.S.datasets["book_Secondo"]) == 8


async def test_excel_range_goes_through_the_import_dialog(user: User, tmp_path):
    setup_state()
    path = tmp_path / "range.xlsx"
    DF.head(10).to_excel(path, sheet_name="Dati", index=False, startrow=2, startcol=1)  # headers in B3
    await user.open("/")

    async def drop_file() -> None:  # like a file dropped in the upload window
        with user:
            await A.load_dataset_file("range.xlsx", path.read_bytes())

    loading = asyncio.create_task(drop_file())
    await user.should_see("Quale foglio")
    user.find(marker="sheet-range").elements.pop().value = "B:D"
    user.find(marker="sheet-ok").click()
    await user.should_see("Intervallo non valido")  # the dialog stays open
    user.find(marker="sheet-range").elements.pop().value = "B3:D6"
    user.find(marker="sheet-ok").click()
    await user.should_see("Importa «range.xlsx [Dati] B3:D6»")
    user.find(marker="import-ok").click()
    await loading
    assert A.S.datasets["range"].shape == (3, 3)


async def test_excel_open_range_is_imported_from_the_selection(user: User, tmp_path, monkeypatch):
    setup_state()
    path = tmp_path / "vendite.xlsx"
    DF.head(10).to_excel(path, sheet_name="Dati", index=False, startrow=2, startcol=1)  # headers in B3
    monkeypatch.setattr(A.excel_link, "current_selection", lambda: A.excel_link.Selection(str(path), "Dati", "B3:D6"))
    await user.open("/")
    with user:
        A.upload_dialog()
    user.find(marker="excel-range").click()
    await asyncio.sleep(1.5)  # the selection of Excel is read every second
    user.find(marker="excel-range-ok").click()
    await user.should_see("Importa «vendite.xlsx [Dati] B3:D6»")
    user.find(marker="import-ok").click()
    await asyncio.sleep(0.5)
    assert A.S.datasets["vendite"].shape == (3, 3)


def select_by_label(user, label):
    return next(s for s in user.find(kind=ui.select).elements if s.props.get("label") == label)


async def test_calculated_column_preview_and_apply(user: User):
    setup_state()
    await user.open("/")
    user.find(content="Colonne calcolate").click()
    await user.should_see("Colonna da convertire")
    select_by_label(user, "Colonna da convertire").value = "data"
    select_by_label(user, "Converti in").value = "Anno"  # proposes the name data_anno

    user.find(marker="calc-preview").click()
    await asyncio.sleep(0.5)
    grids = [g.options["columnDefs"] for g in user.find(kind=ui.aggrid).elements]
    defs = next(d for d in grids if any(c["field"] == "data_anno" for c in d))  # preview grid
    assert [d["field"] for d in defs if d.get("pinned") == "left"] == ["data", "data_anno"]
    assert "data_anno" not in A.S.df.columns  # the preview does not change the dataset

    user.find(marker="calc-apply").click()
    await asyncio.sleep(0.5)
    assert A.S.df["data_anno"].tolist() == DF.data.dt.year.tolist()
    assert list(A.S.df.columns).index("data_anno") == list(DF.columns).index("data") + 1


async def test_formula_shows_columns_and_guide(user: User):
    setup_state()
    await user.open("/")
    user.find(content="Colonne calcolate").click()
    mode = next(t for t in user.find(kind=ui.toggle).elements if "Formula" in t.options)
    mode.value = "Formula"
    await user.should_see("Guida alle formule")
    await user.should_see(f"Colonne ({DF.shape[1]})")
    chips = [chip.text for chip in user.find(marker="column-chip").elements]
    assert sorted(chips) == sorted(DF.columns)
    await user.should_not_see("Colonne: regione")  # no more text list


async def test_formula_guide_cards_open_and_search(user: User):
    setup_state()
    await user.open("/")
    user.find(content="Colonne calcolate").click()
    mode = next(t for t in user.find(kind=ui.toggle).elements if "Formula" in t.options)
    mode.value = "Formula"
    await user.should_see("Guida alle formule")
    cards = list(user.find(marker="function-card").elements)
    assert len(cards) == sum(len(functions) for functions in A.fh.GUIDE.values())
    assert not any(card.default_slot.children for card in cards)  # guide built only when opened

    cards[0].value = True
    await user.should_see("Esempi")

    search = next(i for i in user.find(kind=ui.input).elements if i.props.get("placeholder") == "Cerca funzione")
    search.value = "substring"
    assert [card.visible for card in cards].count(True) == 1
    assert any(tab.props["label"] == "Testo (1)" for tab in user.find(kind=ui.tab).elements)


async def test_function_rows_insert_formula_without_labels(user: User, monkeypatch):
    setup_state()
    await user.open("/")
    user.find(content="Colonne calcolate").click()
    mode = next(t for t in user.find(kind=ui.toggle).elements if "Formula" in t.options)
    mode.value = "Formula"
    await user.should_see("Guida alle formule")
    await user.should_not_see("Excel:")  # no more labels matching Excel
    await user.should_not_see("Operatori:")  # no more introductory text

    cards = list(user.find(marker="function-card").elements)
    assert len(user.find(marker="insert-function").elements) == len(cards)  # one button on each row

    inserted = []
    monkeypatch.setattr(A, "insert_text", lambda editor, text, selection=None: inserted.append((text, selection)))
    user.find(marker="insert-function").trigger("click.stop")  # «.stop»: the click does not open the guide of the row
    syntaxes = sorted(function.syntax for functions in A.fh.GUIDE.values() for function in functions)
    assert sorted(text for text, _ in inserted) == syntaxes  # each row inserts its own syntax
    assert all(selection == A.fh.first_placeholder(text) for text, selection in inserted)
    assert not any(card.value for card in cards)


async def test_chart_tab_has_no_helper_texts(user: User):
    setup_state()
    await user.open("/")
    user.find(content="Grafici").click()
    await user.should_see("Tipo di grafico")
    await user.should_not_see("Classifica e ordine per valore")
    await user.should_not_see("Confronta i valori tra categorie")


async def test_chart_controls_share_one_grid(user: User):
    setup_state()
    await user.open("/")
    user.find(content="Grafici").click()
    await user.should_see("Tipo di grafico")
    for label in ("Tipo di grafico", "Asse X / categorie", "Etichette dati", "Altezza"):
        assert "w-full" in select_by_label(user, label).classes  # all the fields fill their own cell


async def test_appearance_updates_page_chart_and_saved_preferences(user: User):
    setup_state()
    await user.open("/")
    user.find(content="Grafici").click()
    await user.should_see("Tipo di grafico")
    select_by_label(user, "Asse X / categorie").value = "regione"
    user.find(marker="run-chart").click()
    await asyncio.sleep(0.5)
    assert user.find(marker="chart").elements.pop().props["theme"]["color"][0] == A.style.DEFAULT.charts[0]

    user.find(marker="appearance").click()
    await user.should_see("Temi pronti")
    user.find(marker="preset-Notte").click()  # ready-made theme
    assert A.S.appearance == A.style.PRESETS["Notte"]
    chart = user.find(marker="chart").elements.pop()  # the chart is redrawn with the colors of the new look
    assert chart.props["theme"]["color"] == list(A.style.PRESETS["Notte"].charts)
    editors = [e for e in user.client.elements.values() if isinstance(e, ui.codemirror)]
    assert editors and all(e.theme == "basicDark" for e in editors)

    accent = next(c for c in user.find(kind=ui.color_input).elements if c.props.get("label") == "Personalizzato")
    accent.value = "#E3348C"  # free accent
    assert A.S.appearance.accent == "#e3348c" and A.S.appearance.dark
    series = next(c for c in user.find(kind=ui.color_input).elements if c.props.get("label") == "Serie 1")
    series.value = "#123456"
    assert A.S.appearance.charts[0] == "#123456"
    assert A.style.from_dict(A.nicegui_app.storage.general[A.APPEARANCE_KEY]) == A.S.appearance

    user.find(content="Ripristina").click()
    assert A.S.appearance == A.style.DEFAULT


def textarea_by_label(user, label):
    return next(t for t in user.find(kind=ui.textarea).elements if t.props.get("label") == label)


async def test_ai_builds_the_chart_by_filling_the_controls(user: User, monkeypatch):
    setup_state()
    seen = {}

    def fake_settings(api_key, request, df, model, effort):
        seen.update(api_key=api_key, request=request, rows=len(df))
        return A.chart_ai.normalize({"chart": "stacked", "x": "regione", "series": "prodotto", "title": "Importo",
                                     "measures": [{"col": "importo", "fn": "somma"}], "top_n": 2},
                                    [str(c) for c in df.columns])

    monkeypatch.setattr(A.chart_ai, "generate_settings", fake_settings)
    await user.open("/")
    user.find(content="Grafici").click()
    await user.should_see("Tipo di grafico")

    user.find(marker="ai-chart").click()  # without API key or request: no call
    await asyncio.sleep(0.2)
    assert not seen

    A.S.api_key = "sk-test"
    textarea_by_label(user, "Descrivi il grafico che vuoi").value = "importo per regione e prodotto, in pila"
    user.find(marker="ai-chart").click()
    await asyncio.sleep(1)
    assert seen == {"api_key": "sk-test", "request": "importo per regione e prodotto, in pila", "rows": len(DF)}
    assert select_by_label(user, "Tipo di grafico").value == "stacked"
    assert select_by_label(user, "Asse X / categorie").value == "regione"
    assert select_by_label(user, "Serie / colore").value == "prodotto"
    assert user.find(marker="chart").elements  # the chart was created
    assert user.find(marker="chart").elements.pop().options["title"]["text"] == "Importo"


# ------------------------------------------------------------------ Join / Union

REGIONS = pd.DataFrame({"regione": ["Nord", "Sud", "Centro"], "responsabile": ["Anna", "Bruno", "Carla"]})


def entry_of(step, side: str, column: str) -> dict:
    return next(entry for entry in step.plan if entry["side"] == side and entry["column"] == column)


def note_of(step, side: str, column: str) -> str:
    return step.status[id(entry_of(step, side, column))].text


async def open_combine(user: User) -> None:
    await user.open("/")
    user.find(content="Join / Union").click()
    await user.should_see("Esegui join")


async def open_union(user: User):
    await open_combine(user)
    next(toggle for toggle in user.find(kind=ui.toggle).elements if toggle.value == "Join").set_value("Union")
    return last_panel(UnionPanel)


def test_grid_headers_group_columns_by_source_and_mark_duplicates():
    df = pd.DataFrame({"a": [1], "b": [2], "c": [3]})
    defs = A._col_defs(df, sources={"a": ("x", False), "b": ("y", True), "c": ("y", False)})
    assert [group["headerName"] for group in defs] == ["x", "y"]
    assert [column["field"] for column in defs[1]["children"]] == ["b", "c"]
    assert defs[1]["children"][0]["headerClass"] == "dup-column" and "headerClass" not in defs[1]["children"][1]
    assert A._col_defs(df)[0]["field"] == "a"  # without sources the grid stays flat


def test_grid_numbers_follow_the_number_format():
    df = pd.DataFrame({"n": [1234], "x": [1234.5], "t": ["a"], "b": [True]})
    defs = {d["field"]: d for d in A._col_defs(df)}
    assert "it-IT" in defs["x"][":valueFormatter"] and "maximumFractionDigits: 0" in defs["n"][":valueFormatter"]
    assert ":valueFormatter" not in defs["t"] and ":valueFormatter" not in defs["b"]
    american = A.number_format.NumberFormat(decimals=3, style=A.number_format.AMERICAN)
    assert "'en-US'" in A._col_defs(df, fmt=american)[1][":valueFormatter"]
    none = A.number_format.NumberFormat(A.number_format.NO_FORMAT)
    assert not any(":valueFormatter" in d for d in A._col_defs(df, fmt=none))


async def test_every_section_has_a_number_format_button(user: User):
    setup_state()
    await user.open("/")
    assert {panel.section for panel in A.S.panels} == set(A.TABS) - {"Statistica"}  # its tables appear after an analysis
    assert len(user.find(marker="number-format").elements) == len(A.S.panels)


async def test_number_format_is_shared_by_the_panels_of_a_section(user: User):
    setup_state()
    await user.open("/")
    pivot_panels = [panel for panel in A.S.panels if panel.section == "Pivot"]
    other = next(panel for panel in A.S.panels if panel.section == "Group By")
    assert len(pivot_panels) == 2
    for panel in pivot_panels + [other]:
        panel.show(DF.head(20), "prova", reveal=False)
    first, second = pivot_panels

    def formatter(panel):
        return next(d for d in panel.grid.options["columnDefs"] if d["field"] == "importo")

    first.format_menu.style.value = A.number_format.AMERICAN  # chosen in one panel...
    first.format_menu.decimals.value = 1
    for panel in pivot_panels:
        assert A.S.number_formats["Pivot"] == A.number_format.NumberFormat(decimals=1, style=A.number_format.AMERICAN)
        assert "'en-US'" in formatter(panel)[":valueFormatter"] and "maximumFractionDigits: 1" in formatter(panel)[":valueFormatter"]
    assert (second.format_menu.style.value, second.format_menu.decimals.value) == (A.number_format.AMERICAN, 1)
    assert "'it-IT'" in formatter(other)[":valueFormatter"]  # ...the other sections keep theirs

    second.format_menu.mode.value = A.number_format.NO_FORMAT
    assert ":valueFormatter" not in formatter(first)
    assert not first.format_menu.decimals.enabled and not first.format_menu.style.enabled
    assert A.S.number_formats["Pivot"].mode == A.number_format.NO_FORMAT
    assert "Group By" not in A.S.number_formats


async def test_number_format_survives_rebuilding_the_page_and_statistics_start_precise(user: User):
    setup_state()
    A.S.number_formats["Console"] = A.number_format.NumberFormat(decimals=5, style=A.number_format.AMERICAN)
    await user.open("/")
    console = next(panel for panel in A.S.panels if panel.section == "Console")
    assert (console.format_menu.decimals.value, console.format_menu.style.value) == (5, A.number_format.AMERICAN)
    assert A.S.format_of("Statistica", precise=True).decimals == A.number_format.PRECISE_DECIMALS
    assert A.S.format_of("Group By").decimals == A.number_format.DEFAULT_DECIMALS


async def test_chart_look_sets_the_number_style(user: User):
    setup_state()
    await user.open("/")
    look = next(tab for tab in A.S.themed if isinstance(tab, A.ChartTab)).look
    assert look.settings()["locale"] == "it-IT"
    look.number_style.value = A.number_format.AMERICAN
    assert look.settings()["locale"] == "en-US"


async def test_join_runs_and_names_the_sources(user: User):
    setup_state()
    A.S.add("regioni", REGIONS.copy(), activate=False)
    await open_combine(user)
    panel = last_panel(JoinPanel)
    assert (panel.start.name, panel.steps[0].right.name) == ("demo", "regioni")
    panel.steps[0].keys[0].update(left="regione", right="regione")
    panel.refresh()
    user.find(marker="run-join").click()
    await asyncio.sleep(1.5)
    assert panel.result.df.shape == (5000, 8)
    defs = panel.result.grid.options["columnDefs"]
    assert [group["headerName"] for group in defs] == ["demo", "regioni"]
    assert defs[1]["children"][0]["headerClass"] == "dup-column"
    assert user.notify.messages[0].startswith("Join: 5.000 righe × 8 colonne in "), user.notify.messages


async def test_join_can_overwrite_the_active_dataset(user: User):
    setup_state()
    A.S.add("regioni", REGIONS.copy(), activate=False)
    await open_combine(user)
    panel = last_panel(JoinPanel)
    panel.steps[0].keys[0].update(left="regione", right="regione")
    panel.refresh()
    original = A.S.df
    user.find(marker="join-apply").click()
    await asyncio.sleep(1.5)
    assert A.S.active == "demo" and A.S.df.shape == (5000, 8)
    assert "regioni" in A.S.datasets and A.S.undo[0] == "demo"
    user.find(kind=ui.tab, content="Struttura").click()
    user.find(marker="structure-undo").click()
    assert A.S.df is original


async def test_join_without_rows_does_not_overwrite_the_active_dataset(user: User):
    setup_state()
    A.S.add("regioni", REGIONS.copy(), activate=False)
    await open_combine(user)
    panel = last_panel(JoinPanel)
    step = panel.steps[0]
    step.how.set_value("inner")
    step.keys[0].update(left="regione", right="responsabile")  # no value matches: the join is empty
    panel.refresh()
    original = A.S.df
    user.find(marker="join-apply").click()
    await asyncio.sleep(1.0)
    assert A.S.df is original and A.S.undo is None
    assert any("non è stato modificato" in str(message) for message in user.notify.messages), user.notify.messages


async def test_join_column_choices_reach_the_result(user: User):
    setup_state()
    A.S.add("regioni", REGIONS.copy(), activate=False)
    await open_combine(user)
    panel = last_panel(JoinPanel)
    step = panel.steps[0]
    step.keys[0].update(left="regione", right="regione")
    panel.refresh()
    await asyncio.sleep(0.3)
    assert note_of(step, "right", "regione") == "duplicato"
    panel.choose(step, entry_of(step, "right", "regione"), keep=False)
    panel.choose(step, entry_of(step, "right", "responsabile"), name="manager")
    panel.refresh()
    await asyncio.sleep(0.3)
    user.find(marker="run-join").click()
    await asyncio.sleep(1.5)
    assert list(panel.result.df.columns)[-1] == "manager" and "regione_regioni" not in panel.result.df
    panel.choose(step, entry_of(step, "right", "responsabile"), name="note")
    panel.refresh()
    await asyncio.sleep(0.3)
    assert note_of(step, "right", "responsabile") == "nome ripetuto"
    user.find(marker="run-join").click()
    await asyncio.sleep(0.8)
    assert any("ripetuti" in str(message) for message in user.notify.messages), user.notify.messages


async def test_join_type_changes_the_plan_and_the_controls(user: User):
    setup_state()
    A.S.add("regioni", REGIONS.copy(), activate=False)
    await open_combine(user)
    panel = last_panel(JoinPanel)
    step = panel.steps[0]
    step.how.set_value("left_anti")
    assert {entry["side"] for entry in step.plan} == {"left"}
    assert not step.indicator.visible and step.keys_box.visible
    step.how.set_value("cross")
    assert not step.keys_box.visible and not step.match.visible
    step.how.set_value("full")
    step.keys[0].update(left="regione", right="regione")
    step.merge_keys.set_value(True)
    await asyncio.sleep(0.3)
    assert combine.merged_away(entry_of(step, "right", "regione"))
    assert note_of(step, "right", "regione") == "unita a «regione»"


async def test_join_without_keys_shows_the_error(user: User):
    setup_state()
    A.S.add("regioni", REGIONS.copy(), activate=False)
    await open_combine(user)
    user.find(marker="run-join").click()
    await asyncio.sleep(0.8)
    assert any("incompleta" in str(message) for message in user.notify.messages), user.notify.messages


async def test_join_that_explodes_stops_with_a_clear_message(user: User, monkeypatch):
    setup_state()
    monkeypatch.setattr(combine, "MIN_RESULT_CELLS", 1_000)
    A.S.datasets["demo"] = DF.head(300).copy()
    repeated = pd.DataFrame({"regione": ["Nord", "Sud", "Centro"] * 20, "responsabile": list("abc") * 20})
    A.S.add("ripetuto", repeated, activate=False)
    await open_combine(user)
    panel = last_panel(JoinPanel)
    panel.steps[0].keys[0].update(left="regione", right="regione")
    panel.refresh()
    user.find(marker="run-join").click()
    await asyncio.sleep(1.5)
    assert any("supera il limite" in str(message) for message in user.notify.messages), user.notify.messages
    assert panel.result.df is None
    assert not panel.run_button.props.get("loading")


async def test_join_chains_a_second_dataset_with_its_own_step(user: User):
    setup_state()
    A.S.add("regioni", REGIONS.copy(), activate=False)
    A.S.add("premi", pd.DataFrame({"responsabile": ["Anna", "Bruno"], "premio": [10, 20]}), activate=False)
    await open_combine(user)
    panel = last_panel(JoinPanel)
    panel.steps[0].keys[0].update(left="regione", right="regione")
    user.find(content="Aggiungi join").click()
    await asyncio.sleep(0.3)
    assert len(panel.steps) == 2 and panel.steps[1].right.name == "premi"
    assert "responsabile" in panel.steps[1].left_columns
    panel.steps[1].keys[0].update(left="responsabile", right="responsabile")
    panel.refresh()
    user.find(marker="run-join").click()
    await asyncio.sleep(2)
    # responsabile comes back in the second step: it is a duplicate and takes the suffix of the premi dataset
    assert panel.result.df.shape == (5000, 10)
    assert list(panel.result.df.columns)[-2:] == ["responsabile_premi", "premio"]
    assert user.notify.messages[0].startswith("Join: 5.000 righe × 10 colonne in "), user.notify.messages


async def test_removing_a_join_step_keeps_the_others(user: User):
    setup_state()
    A.S.add("regioni", REGIONS.copy(), activate=False)
    await open_combine(user)
    panel = last_panel(JoinPanel)
    user.find(content="Aggiungi join").click()
    await asyncio.sleep(0.3)
    assert len(panel.steps) == 2
    panel.remove_step(panel.steps[1])
    assert len(panel.steps) == 1 and panel.steps[0].title.text == "Join 1"


async def test_new_datasets_appear_in_the_pickers(user: User):
    setup_state()
    A.S.add("regioni", REGIONS.copy(), activate=False)  # with one dataset only the join is not built
    await open_combine(user)
    A.S.add("nuovo", REGIONS.copy(), activate=False)
    A.update_header()
    assert all("nuovo" in picker.select.options for picker in A.S.pickers)


async def test_union_tab_maps_columns_and_runs_the_union(user: User):
    setup_state()
    A.S.datasets["demo"] = DF.head(30).copy()
    A.S.add("altro", DF.head(20).rename(columns={"importo": "valore"}), activate=False)
    panel = await open_union(user)
    assert panel.names == ["demo", "altro"]
    names = [entry["name"] for entry in panel.plan]
    assert "importo" in names and "valore" in names          # by name: all the columns
    amount, value = (next(entry for entry in panel.plan if entry["name"] == name) for name in ("importo", "valore"))
    panel.map_column(amount, 1, "valore")                      # the second dataset flows into «importo»
    panel.map_column(value, 0, None)
    panel.choose(value, keep=False)
    user.find(marker="run-union").click()
    await asyncio.sleep(1.5)
    out = panel.result.df
    assert len(out) == 50 and "valore" not in out and out["importo"].notna().all()
    assert user.notify.messages[0].startswith("Union: 50 righe × 6 colonne in "), user.notify.messages


async def test_union_accepts_many_datasets(user: User):
    setup_state()
    A.S.add("due", DF.head(10).copy(), activate=False)
    A.S.add("tre", DF.head(5).copy(), activate=False)
    panel = await open_union(user)
    user.find(content="Aggiungi dataset").click()
    await asyncio.sleep(0.3)
    assert len(panel.pickers) == 3
    user.find(marker="run-union").click()
    await asyncio.sleep(1.5)
    assert len(panel.result.df) == 5015


async def test_union_set_operations_switch_to_common_columns_and_sources_can_be_removed(user: User):
    setup_state()
    A.S.add("altro", DF.head(10).copy(), activate=False)
    panel = await open_union(user)
    panel.how.set_value("intersect")
    assert panel.align.value == "common" and not panel.origin.visible
    panel.add_source()
    assert len(panel.pickers) == 3 and all(len(entry["sources"]) == 3 for entry in panel.plan)
    panel.remove_source(panel.pickers[-1])
    panel.remove_source(panel.pickers[-1])
    assert len(panel.pickers) == 2 and any("almeno due" in str(message) for message in user.notify.messages)


async def test_stats_tab_shows_only_the_fields_of_the_analysis(user: User):
    setup_state()
    await user.open("/")
    user.find(content="Statistica").click()
    await user.should_see("Analisi")
    analysis = select_by_label(user, "Analisi")
    labels = {s.props.get("label") for s in user.find(kind=ui.select).elements if s.visible}
    assert "Gruppo (facoltativo)" in labels and "Variabile dipendente (y)" not in labels
    analysis.value = "linear"
    labels = {s.props.get("label") for s in user.find(kind=ui.select).elements if s.visible}
    assert {"Variabile dipendente (y)", "Variabili indipendenti (x)"} <= labels


async def test_stats_tab_runs_and_shows_tables_and_charts(user: User):
    setup_state()
    await user.open("/")
    user.find(content="Statistica").click()
    await user.should_see("Analisi")
    select_by_label(user, "Analisi").value = "compare_groups"
    select_by_label(user, "Colonna numerica").value = "importo"
    select_by_label(user, "Gruppi (colonna)").value = "regione"
    user.find(marker="run-stats").click()
    await asyncio.sleep(1)
    await user.should_see("Tukey HSD")
    assert user.find(marker="stat-chart").elements

    look = next(tab for tab in A.S.themed if isinstance(tab, A.StatsTab)).look  # look as in the Grafici tab
    assert look.labels.visible and look.label_size.visible and not look.smooth.visible  # box plot: labels yes
    look.labels.value = "Valore"
    medians = [series for chart in user.find(marker="stat-chart").elements for series in chart.options["series"]
               if series["name"] == "mediana"]
    assert medians and medians[0]["label"]["show"]
    look.title.value = "Confronto"
    look.height.value = 640
    charts = user.find(marker="stat-chart").elements
    assert {chart.style["height"] for chart in charts} == {"640px"}
    assert any("'it-IT', {" in json.dumps(chart.options) for chart in charts)
    look.number_style.value = A.number_format.AMERICAN  # the separators of the charts follow the choice
    options = [json.dumps(chart.options) for chart in user.find(marker="stat-chart").elements]
    assert not any("'it-IT', {" in text for text in options) and any("'en-US', {" in text for text in options)
    tables = [panel for panel in A.S.panels if panel.section == "Statistica"]  # tables: more decimals to start with
    assert tables and {panel.format_menu.decimals.value for panel in tables} == {A.number_format.PRECISE_DECIMALS}
    assert all(chart.options["title"]["text"].startswith("Confronto") for chart in charts)

    user.find(marker="appearance").click()
    user.find(marker="preset-Notte").click()
    chart = user.find(marker="stat-chart").elements.pop()
    assert chart.props["theme"]["color"] == list(A.style.PRESETS["Notte"].charts)


async def test_stats_tab_reports_missing_choices(user: User):
    setup_state()
    await user.open("/")
    user.find(content="Statistica").click()
    await user.should_see("Analisi")
    select_by_label(user, "Analisi").value = "distribution"
    user.find(marker="run-stats").click()
    await asyncio.sleep(0.5)
    assert any("Scegli" in message for message in user.notify.messages)


async def test_result_opens_full_screen_with_the_same_rows(user: User):
    setup_state()
    await user.open("/")
    user.find(marker="run-dati").click()
    await asyncio.sleep(0.5)
    user.find(marker="full-screen").click()
    await asyncio.sleep(0.3)
    dialogs = [d for d in user.client.elements.values() if isinstance(d, ui.dialog) and d.value]
    assert dialogs
    grids = [g for g in dialogs[-1].descendants() if isinstance(g, ui.aggrid)]
    assert grids and len(grids[0].options["rowData"]) == 50


async def test_stats_tables_scroll_inside_a_sized_grid(user: User):
    setup_state()
    await user.open("/")
    user.find(content="Statistica").click()
    await user.should_see("Analisi")
    select_by_label(user, "Analisi").value = "linear"
    select_by_label(user, "Variabile dipendente (y)").value = "importo"
    select_by_label(user, "Variabili indipendenti (x)").value = ["qta"]
    user.find(marker="run-stats").click()
    await asyncio.sleep(1)
    heights = {len(g.options["rowData"]): g.style["height"] for g in user.find(kind=ui.aggrid).elements
               if g.options["rowData"]}
    assert heights[1000] == f"{A.GRID_HEIGHT}px"  # Forecasts: fixed height, scrolling inside the grid
    assert all("domLayout" not in g.options for g in user.find(kind=ui.aggrid).elements)


def open_excel_mode(user, mode):
    user.find(kind=ui.tab, content="Excel").click()
    toggle = next(t for t in user.find(kind=ui.toggle).elements if mode in t.options)
    toggle.value = mode


async def test_result_goes_to_the_report_and_the_report_downloads(user: User):
    setup_state()
    await user.open("/")
    user.find(marker="run-dati").click()
    await asyncio.sleep(0.5)
    user.find(marker="add-report").click()
    await user.should_see("Aggiungi al report Excel")
    user.find(marker="report-ok").click()
    await asyncio.sleep(0.3)
    assert [item.name for item in A.S.report.items] == ["demo"]
    open_excel_mode(user, "Report")
    await user.should_see("Genera report")
    user.find(marker="run-report").click()
    response = await user.download.next(timeout=3)
    book = openpyxl.load_workbook(io.BytesIO(response.content))
    assert book.sheetnames == ["demo"] and book["demo"].max_row == len(DF) + 1


async def test_split_preview_and_zip(user: User):
    setup_state()
    await user.open("/")
    open_excel_mode(user, "Split")
    await user.should_see("Dataset da dividere")
    select_by_label(user, "Un file per ogni valore di").value = ["regione"]
    user.find(marker="split-preview").click()
    await asyncio.sleep(0.5)
    assert 3 in rows_grid(user)
    user.find(marker="run-split").click()
    response = await user.download.next(timeout=3)
    names = zipfile.ZipFile(io.BytesIO(response.content)).namelist()
    assert names == ["demo_Centro.xlsx", "demo_Nord.xlsx", "demo_Sud.xlsx"]


async def test_plain_range_hides_the_freeze_choice(user: User):
    setup_state()
    await user.open("/")
    open_excel_mode(user, "Split")
    await user.should_see("Stile della tabella")
    freeze = next(s for s in user.find(kind=ui.switch).elements if s.text == "Blocca l'intestazione")
    assert freeze.visible
    select_by_label(user, "Stile della tabella").value = A.xw.PLAIN
    assert not freeze.visible


async def test_structure_renames_reorders_sorts_and_undoes(user: User):
    setup_state()
    await user.open("/")
    original = A.S.df
    user.find(kind=ui.tab, content="Struttura").click()
    await user.should_see("Ordina righe")
    name = next(i for i in user.find(marker="column-name").elements if i.value == "importo")
    name.value = "Importo €"
    user.find(marker="sort-column").elements.pop().value = "importo"
    user.find(marker="sort-direction").elements.pop().value = True
    user.find(marker="structure-apply").click()
    await asyncio.sleep(1)
    df = A.S.df
    assert "Importo €" in df.columns and "importo" not in df.columns
    assert df["Importo €"].is_monotonic_decreasing and len(df) == len(original)

    user.find(kind=ui.tab, content="Struttura").click()
    user.find(marker="structure-undo").click()
    assert A.S.df is original

    user.find(marker="rename-dataset").click()
    await user.should_see(marker="rename-name")
    name = user.find(marker="rename-name").elements.pop()
    name.value = "vendite 2024"
    user.find(marker="rename-ok").click()
    await asyncio.sleep(0.3)
    assert A.S.active == "vendite 2024" and "demo" not in A.S.datasets
    assert "vendite_2024" in A.S.sql_tables()


async def test_console_filters_only_df(user: User):
    setup_state()
    await user.open("/")
    user.find(kind=ui.tab, content="Console").click()
    fb = CREATED[-1]  # the Console is the last tab with filters
    fb.tree["condizioni"] = [eng.new_condition("regione", "=", "Nord")]
    ed = user.find(kind=ui.codemirror).elements.pop()
    A.S.lang = "Python"
    user.find(kind=ui.toggle, content="Python").elements.pop().value = "Python"
    ed.value = "print(len(df), len(datasets['demo']))\ndf = df.head(3)"
    user.find(marker="run-console").click()
    await asyncio.sleep(1)
    nord = int((DF.regione == "Nord").sum())
    assert any(f"{nord} {len(DF)}" in (label.text or "") for label in user.find(kind=ui.label).elements)
    assert "filtrate" in user.find(marker="console-scope").elements.pop().text
    assert len(A.S.df) == len(DF) and A.S.undo is None  # with filters a reassigned df does not touch the dataset

    user.find(kind=ui.toggle, content="SQL").elements.pop().value = "SQL"
    ed.value = "select count(*) as n from df"
    user.find(marker="run-console").click()
    await asyncio.sleep(1)
    assert any(grid.options["rowData"] == [{"n": nord}] for grid in user.find(kind=ui.aggrid).elements)


async def test_filtered_result_saved_to_database_with_manual_note(user: User, deleted_contexts_fail):
    setup_state()
    project = A.S.database.create_folder("Vendite")
    await user.open("/")
    CREATED[0].tree["condizioni"] = [eng.new_condition("regione", "=", "Nord")]
    user.find(marker="run-dati").click()
    await asyncio.sleep(0.5)
    user.find(marker="show-note").click()
    note = user.find(marker="note-text").elements.pop().value
    assert note.splitlines() == ["Operazione: Filtro", "Origine: demo", "    File: demo.parquet", 'Filtri: regione = "Nord"']
    user.find(marker="db-save").click()
    await user.should_see("Salva nel database")
    note_box = user.find(marker="db-note-text").elements.pop()
    assert note_box.value in ("", None)  # only notes written by hand
    note_box.value = "Clienti del Nord"
    user.find(marker="db-save-ok").click()
    await asyncio.sleep(0.5)
    [item] = A.S.database.datasets(project)
    assert item.name == "demo_filtrato" and item.rows == (DF.regione == "Nord").sum() and item.note == "Clienti del Nord"


async def test_database_window_creates_folders_and_opens_dataset(user: User, deleted_contexts_fail):
    setup_state()
    project = A.S.database.create_folder("Vendite")
    item = A.S.database.save_dataset(project, "marzo", DF.head(20), "Origine: demo")
    await user.open("/")
    user.find(marker="database").click()
    await user.should_see("Nuovo progetto")
    tree = user.find(marker="db-tree")
    tree.trigger("update:selected", f"folder:{project}")
    await user.should_see("Nuova sottocartella")
    user.find(marker="db-new-folder").click()
    await user.should_see("Nome della cartella")
    user.find(marker="db-text").type("Settimana 1")
    user.find(marker="db-text-ok").click()
    await asyncio.sleep(0.3)
    assert [folder.name for folder in A.S.database.folders() if folder.parent_id == project] == ["Settimana 1"]
    tree.trigger("update:selected", f"dataset:{item.id}")
    await user.should_see("20 righe")
    user.find(marker="db-open").click()
    await asyncio.sleep(0.5)
    assert A.S.active == "marzo" and len(A.S.df) == 20
    assert A.S.origins["marzo"].startswith("Database: Vendite (salvato il ")
    assert A.S.origins["marzo"].endswith("    Origine: demo")


async def set_list_condition(builder, column, values):
    """«in lista» condition already drawn in the filter builder (the refresh completes in the event loop)."""
    builder.tree["condizioni"] = [eng.new_condition(column, "in lista", values)]
    builder.render.refresh()
    await asyncio.sleep(0.1)


async def test_filter_list_pasted_from_excel(user: User, deleted_contexts_fail):
    setup_state()
    await user.open("/")
    fb = CREATED[0]  # filters of the Dati tab
    await set_list_condition(fb, "regione", ["Centro"])
    user.find(marker="paste-values").click()
    await user.should_see("Incolla qui i valori")
    user.find(marker="paste-text").type("Nord\r\nSud\r\n\r\nNord\r\n")
    user.find(marker="paste-ok").click()
    await asyncio.sleep(0.2)
    assert fb.tree["condizioni"][0]["val"] == ["Centro", "Nord", "Sud"]  # added to the ones already chosen
    user.find(marker="run-dati").click()
    await asyncio.sleep(0.5)
    assert min(int(DF.regione.isin(["Centro", "Nord", "Sud"]).sum()), 50) in rows_grid(user)


async def test_case_rule_list_pasted_replaces_values(user: User):
    setup_state()
    await user.open("/")
    user.find(content="Colonne calcolate").click()
    await user.should_see("Colonna da convertire")
    next(t for t in user.find(kind=ui.toggle).elements if "Se… allora" in t.options).value = "Se… allora"
    builder = last_panel(CaseRules).rules[0]["builder"]
    await set_list_condition(builder, "prodotto", ["A"])
    user.find(marker="paste-values").click()
    await user.should_see("Incolla qui i valori")
    user.find(marker="paste-text").type("B\tC")
    user.find(kind=ui.switch, content="Sostituisci i valori già scelti").elements.pop().value = True
    user.find(marker="paste-ok").click()
    await asyncio.sleep(0.2)
    assert builder.tree["condizioni"][0]["val"] == ["B", "C"]


async def test_numeric_column_offers_values_in_list(user: User):
    setup_state()
    await user.open("/")
    fb = CREATED[0]
    await set_list_condition(fb, "qta", [])
    choices = select_by_label(user, "Valori").options
    assert choices[:3] == ["1", "2", "3"]  # values of the numeric column, in numeric order
    select_by_label(user, "Valori").value = ["2", "3"]
    user.find(marker="run-dati").click()
    await asyncio.sleep(0.5)
    assert min(int(DF.qta.isin([2, 3]).sum()), 50) in rows_grid(user)


async def test_database_window_saves_active_dataset(user: User, deleted_contexts_fail):
    setup_state()
    project = A.S.database.create_folder("Vendite")
    await user.open("/")
    user.find(marker="database").click()
    await user.should_see("Nuovo progetto")
    user.find(marker="db-tree").trigger("update:selected", f"folder:{project}")
    await user.should_see("Salva qui")
    user.find(marker="db-save-active").click()
    await user.should_see("Nome del dataset")
    user.find(marker="db-save-ok").click()
    await asyncio.sleep(0.5)
    [item] = A.S.database.datasets(project)
    assert item.name == "demo" and item.rows == len(DF) and item.note == ""
    await user.should_see(f"{len(DF):,} righe".replace(",", "."))  # the window stays open on the new dataset


async def start_import(user: User, name: str, content: bytes) -> asyncio.Task:
    """Starts the loading as if the file were dropped, and waits for the import window."""
    async def drop_file() -> None:
        with user:
            await A.load_dataset_file(name, content)

    task = asyncio.create_task(drop_file())
    await user.should_see(f"Importa «{name}»")
    return task


CSV_TEXT = "codice;importo;giorno;nota\n00123;1.234,56;31/01/2024;a\n00456;7,5;01/02/2024;b\n".encode()


async def test_import_choose_columns_and_formats(user: User):
    setup_state()
    await user.open("/")
    loading = await start_import(user, "vendite.csv", CSV_TEXT)
    dialog = last_panel_import()
    assert [item["format"] for item in dialog.plan] == ["Come rilevato"] * 4  # no conversion proposed
    dialog.plan[1]["format"], dialog.plan[2]["format"] = "Numero", "Data"
    keeps, names = user.find(marker="import-keep").elements, user.find(marker="import-name").elements
    by_name = {field.value: (keep, field) for keep, field in zip(sorted(keeps, key=lambda e: e.id),
                                                                  sorted(names, key=lambda e: e.id))}
    by_name["codice"][0].value = False  # codice not loaded
    by_name["nota"][1].value = "commento"  # renamed
    await asyncio.sleep(0.1)
    assert dialog.plan[0]["keep"] is False and dialog.plan[3]["name"] == "commento"
    user.find(marker="import-ok").click()
    await loading
    out = A.S.datasets["vendite"]
    assert list(out.columns) == ["importo", "giorno", "commento"]
    assert out["importo"].tolist() == [1234.56, 7.5] and str(out["giorno"].iloc[0].date()) == "2024-01-31"


async def test_import_restore_and_cancel(user: User):
    setup_state()
    await user.open("/")
    loading = await start_import(user, "vendite.csv", CSV_TEXT)
    dialog = last_panel_import()
    for item in dialog.plan:
        item["keep"], item["format"] = False, "Numero"
    dialog.preview.refresh()
    await user.should_see("Tieni almeno una colonna")
    user.find(marker="import-restore").click()
    await asyncio.sleep(0.1)
    assert all(item["keep"] and item["format"] == "Come rilevato" for item in dialog.plan)
    user.find(kind=ui.button, content="Annulla").click()
    await loading
    assert "vendite" not in A.S.datasets


def last_panel_import():
    return last_panel(ImportDialog)


async def test_import_keeps_leading_zeros_of_codes(user: User):
    setup_state()
    await user.open("/")
    loading = await start_import(user, "vendite.csv", CSV_TEXT)
    dialog = last_panel_import()
    assert dialog.plan[0]["detected"] == "Numero intero"  # the CSV reader reads it as a number
    await dialog.change_format(dialog.plan[0], "Testo")  # chosen by the user: read again from the file
    user.find(marker="import-ok").click()
    await loading
    assert A.S.datasets["vendite"]["codice"].tolist() == ["00123", "00456"]  # but the file says «00123»


async def test_filters_applied_to_dataset(user: User):
    setup_state()
    await user.open("/")
    fb = CREATED[0]
    fb.tree["condizioni"] = [eng.new_condition("regione", "=", "Nord")]
    user.find(marker="filter-apply").click()
    await asyncio.sleep(0.8)
    assert len(A.S.df) == (DF.regione == "Nord").sum()
    assert A.S.undo is not None and A.S.revert() is not None
    assert len(A.S.df) == len(DF)


async def test_groupby_names_from_pattern_and_measure(user: User):
    setup_state()
    lists = []
    original = A.AggregationList
    A.AggregationList = lambda *a, **k: lists.append(original(*a, **k)) or lists[-1]
    try:
        await user.open("/")
    finally:
        A.AggregationList = original
    user.find(content="Group By").click()
    aggs = lists[0]  # the first list created is the one of the Group By
    aggs.items[:] = [{"col": "importo", "fn": "somma", "name": ""}, {"col": "qta", "fn": "somma", "name": "Totale qta"}]
    # «naming» is also in the Pivot: the first one created is the one of the Group By
    min(user.find(marker="naming").elements, key=lambda e: e.id).value = "{colonna} ({funzione})"
    user.find(marker="run-groupby").click()
    for _ in range(30):  # the calculation runs in a thread: waits for the updated grid
        await asyncio.sleep(0.1)
        fields = [c["field"] for g in user.find(kind=ui.aggrid).elements for c in g.options["columnDefs"]]
        if "Totale qta" in fields:
            break
    assert "importo (somma)" in fields and "Totale qta" in fields, fields


async def test_join_with_one_dataset_asks_for_a_second_one(user: User):
    setup_state()
    await user.open("/")
    user.find(content="Join / Union").click()
    await user.should_see("Carica un secondo dataset per fare un join.")
    user.find(marker="self-join").click()
    await user.should_see("Esegui join")


async def test_join_appears_when_a_second_dataset_is_loaded(user: User):
    setup_state()
    await user.open("/")
    user.find(content="Join / Union").click()
    await user.should_see("Carica un secondo dataset per fare un join.")
    A.S.add("regioni", REGIONS.copy(), activate=False)
    A.update_header()
    await user.should_see("Esegui join")
    await user.should_not_see("Carica un secondo dataset per fare un join.")


async def test_each_window_chooses_its_dataset(user: User):
    setup_state()
    A.S.add("regioni", REGIONS.copy(), activate=False)
    await user.open("/")
    pivot_stack = next(pane.stack for pane in A.S.live_panes() if pane.stack.section == "Pivot")
    assert [pane.name for pane in pivot_stack.panes] == ["demo"]
    pivot_stack.count.value = 3  # three pivots, one under the other, on the dataset of the last one
    assert [pane.name for pane in pivot_stack.panes] == ["demo"] * 3
    tabs = [panel for panel in PANELS if isinstance(panel, PivotTab)]
    assert len(tabs) == 3
    pivot_stack.panes[1].select.value = "regioni"  # the second window works on another dataset
    tab = last_panel(PivotTab)
    assert tab.name == "regioni" and tab.df is A.S.datasets["regioni"] and A.S.active == "regioni"
    assert [pane.name for pane in pivot_stack.panes] == ["demo", "regioni", "demo"]
    pivot_stack.remove(pivot_stack.panes[0])
    assert [pane.name for pane in pivot_stack.panes] == ["regioni", "demo"] and pivot_stack.count.value == 2
    assert [pane.title.text for pane in pivot_stack.panes] == ["Finestra 1", "Finestra 2"]
    pivot_stack.count.value = 1
    assert [pane.name for pane in pivot_stack.panes] == ["regioni"]


async def test_changing_a_dataset_rebuilds_only_its_windows(user: User):
    setup_state()
    A.S.add("regioni", REGIONS.copy(), activate=False)
    await user.open("/")
    group_stack = next(pane.stack for pane in A.S.live_panes() if pane.stack.section == "Group By")
    group_stack.count.value = 2
    group_stack.panes[1].select.value = "regioni"
    other_body = group_stack.panes[1].body.default_slot.children[0]
    CREATED[0].tree["condizioni"] = [eng.new_condition("regione", "=", "Nord")]  # filters of the Dati window (demo)
    user.find(marker="filter-apply").click()
    await asyncio.sleep(0.8)
    assert len(A.S.datasets["demo"]) == (DF.regione == "Nord").sum()
    assert not other_body.is_deleted  # the window on «regioni» keeps its choices
    demo_pane = group_stack.panes[0]
    assert demo_pane.size.text.startswith(f"{(DF.regione == 'Nord').sum():,}".replace(",", "."))


async def test_removed_and_renamed_datasets_move_the_windows(user: User):
    setup_state()
    A.S.add("regioni", REGIONS.copy(), activate=False)
    await user.open("/")
    chart_pane = next(pane for pane in A.S.live_panes() if pane.stack.section == "Grafici")
    chart_pane.select.value = "regioni"
    A.S.rename("regioni", "aree")
    A.update_header({"regioni": "aree"})
    assert chart_pane.name == "aree" and "aree" in chart_pane.select.options
    A.remove_dataset("aree")
    assert chart_pane.name == "demo" and chart_pane.select.options == ["demo"]
    await asyncio.sleep(0.2)  # the windows that followed are redrawn before the next removal
    A.remove_dataset("demo")
    await user.should_see("Nessun dataset caricato")


async def test_console_windows_have_their_own_df(user: User):
    setup_state()
    A.S.add("regioni", REGIONS.copy(), activate=False)
    await user.open("/")
    stack = next(pane.stack for pane in A.S.live_panes() if pane.stack.section == "Console")
    stack.count.value = 2
    stack.panes[1].select.value = "regioni"
    editors = [editor for pane in stack.panes for editor in pane.body.descendants() if isinstance(editor, ui.codemirror)]
    for editor in editors:
        editor.value = "SELECT count(*) AS n FROM df"
    for pane in stack.panes:  # each window runs its query on its own df
        button = next(b for b in pane.body.descendants() if "run-console" in b._markers)
        UserInteraction(user, {button}, None).click()
    await asyncio.sleep(1.5)
    counts = {grid.options["rowData"][0]["n"] for pane in stack.panes for grid in pane.body.descendants()
              if isinstance(grid, ui.aggrid) and grid.options["rowData"]}
    assert counts == {len(DF), len(REGIONS)}, counts


async def test_console_lists_follow_the_datasets_and_offer_the_columns(user: User):
    setup_state()
    A.S.add("regioni", REGIONS.copy(), activate=False)
    await user.open("/")
    stack = next(pane.stack for pane in A.S.live_panes() if pane.stack.section == "Console")
    pane = stack.panes[0]

    def hint() -> str:
        return next(l.text for l in pane.body.descendants() if isinstance(l, ui.label) and l.text.startswith(("Tabelle", "df =")))

    def chips() -> list[str]:
        return [chip.text for chip in pane.body.descendants() if isinstance(chip, ui.chip)]

    assert "`regioni`" in hint() and chips() == list(DF.columns)  # the column box is in the window
    A.S.add("nuovo", REGIONS.copy(), activate=False)
    A.update_header()
    assert "`nuovo`" in hint()  # loaded: the list follows
    A.remove_dataset("regioni")
    assert "`regioni`" not in hint() and "`nuovo`" in hint()  # removed: the list follows
    pane.select.value = "nuovo"  # another dataset: the window is rebuilt on its columns
    pane = stack.panes[0]
    assert chips() == ["regione", "responsabile"]
    A.S.datasets["nuovo"] = REGIONS.assign(extra=1)
    A.refresh_dataset("nuovo")
    assert chips() == ["regione", "responsabile", "extra"]


async def test_sub_windows_share_the_filters_of_their_window(user: User):
    setup_state()
    await user.open("/")
    north = int((DF.regione == "Nord").sum())

    chart_pane = next(pane for pane in A.S.live_panes() if pane.stack.section == "Grafici")
    chart_sub = chart_pane.sub_stack
    chart_sub.count.value = 2  # two charts under the same filters
    charts_ = [tab for tab in A.S.themed if isinstance(tab, A.ChartTab)]
    assert len(charts_) == 2 and all(tab.filters is chart_sub.filters for tab in charts_)
    assert [pane.title.text for pane in chart_sub.panes] == ["Grafico 1", "Grafico 2"]
    chart_sub.filters.tree["condizioni"] = [eng.new_condition("regione", "=", "Nord")]  # set once
    for tab, column in zip(charts_, ("prodotto", "regione")):
        tab.x_column.value = column
        await tab.build()
        assert len(tab.data.rows) == north  # both charts read the filtered rows

    pivot_pane = next(pane for pane in A.S.live_panes() if pane.stack.section == "Pivot")
    pivot_sub = pivot_pane.sub_stack
    pivot_sub.count.value = 2
    pivots = [panel for panel in PANELS if isinstance(panel, PivotTab) and not panel.rows.is_deleted][-2:]
    assert all(tab.filters is pivot_sub.filters for tab in pivots)
    pivot_sub.filters.tree["condizioni"] = [eng.new_condition("regione", "=", "Sud")]
    for tab in pivots:
        tab.rows.value = ["regione"]
        tab.values.items.append({"col": "importo", "fn": "conteggio", "name": ""})
        await tab.build()
        assert tab.table.frame.iloc[0, 0] == "Sud" and tab.table.frame.iloc[0, 1] == (DF.regione == "Sud").sum()

    pivot_sub.remove(pivot_sub.panes[0])  # closing one leaves the other with the same filters
    assert [pane.title.text for pane in pivot_sub.panes] == ["Pivot 1"] and pivot_sub.count.value == 1
    assert pivots[1].filters is pivot_sub.filters and not pivots[1].rows.is_deleted


async def test_changing_the_dataset_of_a_window_rebuilds_filters_and_sub_windows(user: User):
    setup_state()
    A.S.add("regioni", REGIONS.copy(), activate=False)
    await user.open("/")
    pane = next(pane for pane in A.S.live_panes() if pane.stack.section == "Group By")
    pane.sub_stack.count.value = 3
    first_filters = pane.sub_stack.filters
    pane.select.value = "regioni"
    assert pane.sub_stack.filters is not first_filters and pane.sub_stack.dataset == "regioni"
    assert len(pane.sub_stack.panes) == 1  # new window content: the sub-windows start again


async def test_database_dataset_opens_the_dati_section_from_any_section(user: User, deleted_contexts_fail):
    setup_state()
    project = A.S.database.create_folder("Vendite")
    item = A.S.database.save_dataset(project, "marzo", DF.head(20), "Origine: demo")
    await user.open("/")
    user.find(content="Pivot").click()  # opened from another section: the dataset must come into sight
    user.find(marker="database").click()
    await user.should_see("Nuovo progetto")
    user.find(marker="db-tree").trigger("update:selected", f"dataset:{item.id}")
    await user.should_see("20 righe")
    user.find(marker="db-open").click()
    await asyncio.sleep(0.5)
    assert A.S.tab == "Dati" and A.S.active == "marzo"
    dati = next(pane for pane in A.S.live_panes() if pane.stack.section == "Dati")
    grid = next(grid for grid in dati.body.descendants() if isinstance(grid, ui.aggrid))
    assert len(grid.options["rowData"]) == 20


async def test_uploaded_file_opens_the_dati_section_from_any_section(user: User, tmp_path):
    setup_state()
    path = tmp_path / "clienti.csv"
    DF.head(12).to_csv(path, index=False)
    await user.open("/")
    user.find(content="Grafici").click()  # loaded from another section: the dataset must come into sight

    async def drop_file() -> None:  # like a file dropped in the upload window
        with user:
            await A.load_dataset_file("clienti.csv", path.read_bytes())

    loading = asyncio.create_task(drop_file())
    await user.should_see(marker="import-ok")
    user.find(marker="import-ok").click()
    await loading
    assert A.S.tab == "Dati" and A.S.active == "clienti"


async def test_database_opens_from_the_empty_start_page(user: User, deleted_contexts_fail):
    setup_state()
    project = A.S.database.create_folder("Vendite")
    item = A.S.database.save_dataset(project, "marzo", DF.head(20), "Origine: demo")
    A.S.datasets.clear()  # no dataset loaded yet: the start page offers the database
    A.S.active = None
    await user.open("/")
    await user.should_see("Apri dal database")
    user.find(content="Apri dal database").click()
    await user.should_see("Nuovo progetto")
    user.find(marker="db-tree").trigger("update:selected", f"dataset:{item.id}")
    await user.should_see("20 righe")
    user.find(marker="db-open").click()
    await asyncio.sleep(0.5)
    assert A.S.tab == "Dati" and A.S.active == "marzo"
    await user.should_see("20 righe")


async def test_chart_builder_in_an_expander_and_panels(user: User):
    setup_state()
    await user.open("/")
    tab = next(tab for tab in A.S.themed if isinstance(tab, A.ChartTab))
    assert tab.builder.value and tab.chart_box not in tab.builder.descendants()  # the chart stays outside
    assert not tab.panel_look_box.visible
    tab.x_column.value = "prodotto"
    tab.panel_column.value = "data"
    tab.top_n.value = 2
    assert tab.panel_date_unit.visible and tab.panel_look_box.visible
    await tab.build()
    years = sorted(DF.data.dt.year.astype(str).unique())
    assert list(tab.data.panels) == years
    assert len([chart for chart in tab.chart_box.descendants() if isinstance(chart, ui.echart)]) == len(years)
    assert "un pannello per data" in tab.builder.props["caption"]
    tab.select(type("Event", (), {"data": {"x": "A"}})(), "2025")
    assert tab.selection == {"x": "A", "p": "2025"}
    assert len(tab.result.df) == ((DF.data.dt.year == 2025) & (DF.prodotto == "A")).sum()
    tab.builder.value = False  # closed: the chart is still there
    assert [chart for chart in tab.chart_box.descendants() if isinstance(chart, ui.echart)]


async def test_console_python_column_chips_insert_the_quoted_name(user: User, monkeypatch):
    setup_state()
    await user.open("/")
    user.find(content="Console").click()
    await user.should_see("Esegui")
    inserted = []
    monkeypatch.setattr(A, "insert_text", lambda editor, text, selection=None: inserted.append(text))
    tog = [t for t in user.find(kind=ui.toggle).elements if "Python" in str(t.options)][0]
    user.find(marker="console-column-chip", content="regione").click()
    tog.value = "Python"
    user.find(marker="console-column-chip", content="regione").click()
    assert inserted == ["regione", "'regione'"]  # SQL quotes only names that need it


async def test_console_dashboard_with_slicers_and_cross_filter(user: User):
    setup_state()
    await user.open("/")
    user.find(content="Console").click()
    await user.should_see("Esegui")
    toggle = [t for t in user.find(kind=ui.toggle).elements if "Python" in str(t.options)][0]
    toggle.value = "Python"
    await asyncio.sleep(0.3)
    editor = list(user.find(kind=ui.codemirror).elements)[0]
    editor.value = ("dash = Dashboard(df, 'Vendite')\ndash.slicer('prodotto')\ndash.kpi('Righe')\n"
                    "dash.chart('bar', x='regione', value='importo')\ndash.table(groups=['regione'], value='importo')")
    user.find(marker="run-console").click()
    await asyncio.sleep(1.5)
    kpi = user.find(marker="dashboard-kpi").elements.pop()
    assert kpi.text == f"{len(DF):,}".replace(",", ".")
    assert len(user.find(marker="dashboard-visual").elements) == 3

    slicer = user.find(marker="dashboard-slicer").elements.pop()
    slicer.value = ["A"]
    await asyncio.sleep(1.5)
    assert kpi.is_deleted  # redrawn
    kpi = user.find(marker="dashboard-kpi").elements.pop()
    assert kpi.text == f"{(DF.prodotto == 'A').sum():,}".replace(",", ".")

    view = last_panel(DashboardView)
    chart_visual = view.dash.pages[0].visuals[1]
    await view.select(chart_visual, type("Event", (), {"data": {"x": "Nord", "value": 1}})(), "")
    await asyncio.sleep(1)
    kpi = user.find(marker="dashboard-kpi").elements.pop()
    assert kpi.text == f"{((DF.prodotto == 'A') & (DF.regione == 'Nord')).sum():,}".replace(",", ".")
    assert "Nord" in view.cross_label.text and view.cross_box.visible
    user.find(marker="dashboard-cross-clear").click()
    await asyncio.sleep(1.5)
    assert user.find(marker="dashboard-kpi").elements.pop().text == f"{(DF.prodotto == 'A').sum():,}".replace(",", ".")
    assert not view.cross_box.visible


async def test_console_dashboard_example(user: User):
    setup_state()
    await user.open("/")
    user.find(content="Console").click()
    await user.should_see("Esegui")
    toggle = [t for t in user.find(kind=ui.toggle).elements if "Python" in str(t.options)][0]
    toggle.value = "Python"
    await asyncio.sleep(0.3)
    user.find(marker="console-dashboard-example").click()
    user.find(marker="run-console").click()
    await asyncio.sleep(2)
    assert user.find(marker="dashboard-pages").elements
    assert not [label for label in user.find(kind=ui.label).elements if "text-red-600" in label.classes]
