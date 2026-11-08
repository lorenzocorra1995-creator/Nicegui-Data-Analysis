"""
Data Explorer — local data analysis tool (NiceGUI + pandas + DuckDB).

Start:
    python main.py                       # then load the files from the interface
    python main.py sales.parquet customers.csv
    python main.py --port 8123 sales.parquet
    python main.py --db archive.duckdb      # database of the datasets (default: data/workspace.duckdb)
"""
from __future__ import annotations

import copy
import json
import sys
import time
from collections import Counter
from collections.abc import Callable
from dataclasses import replace
from pathlib import Path

import numpy as np
import pandas as pd
from nicegui import app as nicegui_app, background_tasks, run, ui

from data_explorer.ai import ai_assistant as ai
from data_explorer.plotting import chart_ai
from data_explorer.plotting import chart_options
from data_explorer.plotting import charts
from data_explorer.core import columns
from data_explorer.core import combine
from data_explorer.ui import dashboard
from data_explorer.core import engine as eng
from data_explorer.excel_io import excel_link
from data_explorer.excel_io import excel_report
from data_explorer.excel_io import excel_split
from data_explorer.excel_io import excel_writer as xw
from data_explorer.core import formula_help as fh
from data_explorer.core import import_plan
from data_explorer.core import notes
from data_explorer.core import number_format
from data_explorer.core import pivot
from data_explorer.core import stats
from data_explorer.plotting import stats_charts
from data_explorer.core import structure
from data_explorer.ui import style
from data_explorer.core import workspace
ROW_LIMITS = [50, 100, 500, 1000, 5000, 10_000]  # rows that can be shown in the table (the export is always complete)
DEFAULT_PREVIEW_ROWS = 1000
DATA_PREVIEW_ROWS = 50
GRID_HEIGHT = 520  # maximum height of the results grid (px); beyond it the grid scrolls inside
ROW_ID = "__riga__"  # position of the row in the result, in the grid data but not shown (click on a cell)
TOTAL_FLAG = "__totale__"  # in the grid data but not shown: the row is a pivot total (bold)
# row and header + pagination + horizontal scrollbar of the grid (px), to fit the height
GRID_ROW_HEIGHT, GRID_CHROME = 42, 125
GRID_MIN_COLUMN_WIDTH = 120  # narrower columns cut headers and numbers: beyond the width the grid scrolls sideways
GRID_LOCALE = {  # Italian texts of AG Grid (pagination, empty grid, column filters and menu)
    "noRowsToShow": "Nessuna riga da mostrare", "loadingOoo": "Caricamento…", "page": "Pagina", "of": "di",
    "to": "-", "more": "altre", "firstPage": "Prima pagina", "lastPage": "Ultima pagina",
    "nextPage": "Pagina successiva", "previousPage": "Pagina precedente", "pageSizeSelectorLabel": "Righe per pagina:",
    "filterOoo": "Filtra…", "equals": "Uguale a", "notEqual": "Diverso da", "contains": "Contiene",
    "notContains": "Non contiene", "startsWith": "Inizia con", "endsWith": "Finisce con", "blank": "Vuoto",
    "notBlank": "Non vuoto", "lessThan": "Minore di", "greaterThan": "Maggiore di",
    "lessThanOrEqual": "Minore o uguale a", "greaterThanOrEqual": "Maggiore o uguale a", "inRange": "Tra",
    "inRangeStart": "da", "inRangeEnd": "a", "before": "Prima del", "after": "Dopo il",
    "andCondition": "E", "orCondition": "O", "applyFilter": "Applica", "resetFilter": "Reimposta",
    "clearFilter": "Cancella", "cancelFilter": "Annulla", "dateFormatOoo": "gg/mm/aaaa",
    "selectAll": "(Seleziona tutto)", "searchOoo": "Cerca…", "blanks": "(Vuoti)",
    "pinColumn": "Blocca colonna", "pinLeft": "Blocca a sinistra", "pinRight": "Blocca a destra",
    "noPin": "Sblocca", "autosizeThisColumn": "Adatta questa colonna", "autosizeAllColumns": "Adatta tutte le colonne",
    "resetColumns": "Ripristina colonne", "sortAscending": "Ordine crescente", "sortDescending": "Ordine decrescente",
    "sortUnSort": "Togli ordinamento", "columnFilter": "Filtro colonna", "columnChooser": "Scegli colonne",
    "ariaChecked": "selezionato", "ariaUnchecked": "non selezionato",
}
SIDEBAR_BREAKPOINT = 900  # window width (px) under which the side menu covers the page instead of sitting beside it
FORM_GRID = "w-full gap-3 grid-cols-1 sm:grid-cols-2 lg:grid-cols-4"  # fields of equal width, on 4 columns


# ===========================================================================
# Global state (local single-user app)
# ===========================================================================

class AppState:
    def __init__(self) -> None:
        self.datasets: dict[str, pd.DataFrame] = {}
        self.active: str | None = None  # last dataset loaded or chosen: the default of new windows and pickers
        self.tab = "Dati"
        self.lang = "SQL"
        self.code = {
            "Python": "df.describe()",
            "SQL": "SELECT *\nFROM df\nLIMIT 100",
        }
        self.env: dict = {"pd": pd, "np": np}
        self.api_key = ""
        self.ai_model = ai.DEFAULT_MODEL
        self.ai_effort = ai.DEFAULT_EFFORT
        self.appearance = style.DEFAULT  # look preferences (saved from one start to the next)
        self.themed: list[ChartTab | StatsTab] = []  # tabs with charts: they must be redrawn if the theme changes
        self.report = excel_report.Report()  # tables collected with «Aggiungi al report»
        self.report_name = excel_report.DEFAULT_REPORT_NAME
        self.report_panel: ReportPanel | None = None  # open Report panel: it is updated when a table is added
        self.excel_folder = ""  # destination folder of the Excel files
        self.pickers: list[SourcePicker] = []  # Join / Union dataset pickers: they follow the loaded datasets
        self.panes: list[DatasetPane] = []  # work windows of the sections, each on its own dataset
        self.watchers: list[tuple[ui.element, Callable[[], None]]] = []  # (element, update): see watch_datasets
        self.number_formats: dict[str, number_format.NumberFormat] = {}  # section → format of its tables (kept when the page is rebuilt)
        self.panels: list[ResultPanel] = []  # result panels, to update together those of the same section
        self.undo: tuple[str, pd.DataFrame, str] | None = None  # (dataset, previous version, change that can be undone)
        self.origins: dict[str, str] = {}  # dataset → history (file, database, operations): basis of the notes
        self.database_path = workspace.DEFAULT_PATH
        self._database: workspace.Workspace | None = None

    @property
    def df(self) -> pd.DataFrame:
        return self.datasets[self.active] if self.active else pd.DataFrame()

    def add_themed(self, tab: ChartTab | StatsTab) -> None:
        """Registers a tab with charts (`chart_box`, `draw`): it is redrawn when the theme changes."""
        self.themed = [other for other in self.themed if not other.chart_box.is_deleted] + [tab]

    def add_panel(self, panel: ResultPanel) -> None:
        """Registers a result panel (the ones of rebuilt tabs are deleted and dropped)."""
        self.panels = [other for other in self.panels if not other.grid.is_deleted] + [panel]

    def format_of(self, section: str, precise: bool = False) -> number_format.NumberFormat:
        """Number format of the tables of a section; statistics start with more decimals."""
        decimals = number_format.PRECISE_DECIMALS if precise else number_format.DEFAULT_DECIMALS
        return self.number_formats.get(section, number_format.NumberFormat(decimals=decimals))

    @property
    def database(self) -> workspace.Workspace:
        """Database of the saved datasets, opened on first use."""
        if self._database is None or self._database.path != Path(self.database_path):
            self._database = workspace.Workspace(self.database_path)
        return self._database

    def add(self, name: str, df: pd.DataFrame, activate: bool = True, origin: str = "") -> str:
        base, i = name, 2
        while name in self.datasets:
            name = f"{base}_{i}"
            i += 1
        self.datasets[name] = df
        self.origins[name] = origin
        if activate or self.active is None:
            self.active = name
        return name

    def replace(self, name: str, df: pd.DataFrame, description: str) -> None:
        """Replaces a dataset, keeping the previous version for «Annulla ultima modifica»."""
        self.undo = (name, self.datasets[name], description)
        self.datasets[name] = df
        self.origins[name] = notes.with_change(self.origins.get(name, ""), description)

    def revert(self) -> str | None:
        """Restores the dataset as it was before the last change; returns the undone change."""
        if self.undo is None or self.undo[0] not in self.datasets:
            return None
        name, df, description = self.undo
        self.datasets[name], self.undo = df, None
        self.origins[name] = notes.without_change(self.origins.get(name, ""), description)
        return description

    def rename(self, old_name: str, new_name: str) -> None:
        """Renames a dataset, keeping the order of the datasets."""
        self.datasets = {new_name if name == old_name else name: df for name, df in self.datasets.items()}
        if self.active == old_name:
            self.active = new_name
        if self.undo and self.undo[0] == old_name:
            self.undo = (new_name, *self.undo[1:])
        self.origins[new_name] = self.origins.pop(old_name, "")

    def remove(self, name: str) -> None:
        del self.datasets[name]
        self.origins.pop(name, None)
        if self.undo and self.undo[0] == name:
            self.undo = None
        if self.active == name:
            self.active = next(iter(self.datasets), None)

    def note_for(self, operation: str, sources: list[tuple[str, dict | None]]) -> str:
        """Note of a result: operation, source datasets with their history and applied filters."""
        return notes.make_note(operation, sources, self.origins)

    def sql_tables(self, name: str | None = None) -> dict[str, pd.DataFrame]:
        """Every dataset as a SQL table, plus `df` = the dataset `name` (default: the active one)."""
        t = {eng.sql_table_name(n): d for n, d in self.datasets.items()}
        if name := name or self.active:
            t["df"] = self.datasets[name]
        return t

    @property
    def palette(self) -> style.Palette:
        return style.resolve(self.appearance)

    def prepare_env(self, name: str) -> dict:
        """Variables of the Python console with `df` = the dataset `name` (the other variables are shared)."""
        self.env["df"] = self.datasets[name]
        self.env["datasets"] = self.datasets
        self.env["sql"] = lambda q: eng.run_sql(q, self.sql_tables(name))
        self.env["Dashboard"] = dashboard.Dashboard
        return self.env

    def live_panes(self) -> list[DatasetPane]:
        """Work windows still on the page (the ones of rebuilt sections are dropped)."""
        self.panes = [pane for pane in self.panes if not pane.body.is_deleted]
        return self.panes

    def watch_datasets(self, element: ui.element, update: Callable[[], None]) -> None:
        """Registers `update`, called when the datasets change (loaded, removed, renamed, modified) while
        `element` is on the page: for texts and lists that show them without being rebuilt."""
        self.watchers = [(other, callback) for other, callback in self.watchers if not other.is_deleted]
        self.watchers.append((element, update))

    def notify_watchers(self) -> None:
        self.watchers = [(element, update) for element, update in self.watchers if not element.is_deleted]
        for _, update in self.watchers:
            update()

    def live_pickers(self) -> list[SourcePicker]:
        self.pickers = [picker for picker in self.pickers if not picker.card.is_deleted]
        return self.pickers


S = AppState()


# ===========================================================================
# Grid utilities
# ===========================================================================

def _json_rows(df: pd.DataFrame) -> list[dict]:
    return json.loads(df.to_json(orient="records", date_format="iso", date_unit="s",
                                 default_handler=str))


DATE_FORMAT = (  # ISO text from _json_rows → dd/mm/yyyy, plus the time only when it is not midnight
    "params => { const v = params.value; if (v == null) return ''; "
    "const [day, time] = String(v).split('T'); const [y, m, d] = day.split('-'); if (!d) return v; "
    "const t = (time || '').slice(0, 8); "
    "return `${d.slice(0, 2)}/${m}/${y}` + (t && t !== '00:00:00' ? ' ' + (t.endsWith(':00') ? t.slice(0, 5) : t) : ''); }"
)


def _col_defs(df: pd.DataFrame, pinned: list[str] = (), sources: dict[str, tuple[str, bool]] | None = None,
              precise: bool = False, fmt: number_format.NumberFormat = number_format.NumberFormat()) -> list[dict]:
    defs = []
    for c in df.columns:
        d = {"field": str(c), "headerName": str(c), "filter": "agTextColumnFilter"}
        if str(c) in pinned:
            d["pinned"] = "left"
        if pd.api.types.is_bool_dtype(df[c]):
            pass
        elif pd.api.types.is_numeric_dtype(df[c]):
            d["type"] = "numericColumn"
            d["filter"] = "agNumberColumnFilter"
            formatter = number_format.grid_formatter(fmt, pd.api.types.is_integer_dtype(df[c]), precise)
            if formatter:
                d[":valueFormatter"] = formatter
        elif pd.api.types.is_datetime64_any_dtype(df[c]):
            d["filter"] = "agDateColumnFilter"
            d[":valueFormatter"] = DATE_FORMAT
        defs.append(d)
    return _group_by_source(defs, sources) if sources else defs


def _group_by_source(defs: list[dict], sources: dict[str, tuple[str, bool]]) -> list[dict]:
    """A header with the dataset name above the adjacent columns that come from it; duplicates are highlighted."""
    groups: list[dict] = []
    for column_def in defs:
        source, duplicate = sources.get(column_def["field"], ("", False))
        if duplicate:
            column_def["headerClass"] = "dup-column"
            column_def["headerTooltip"] = "Il nome esiste anche nell'altro dataset"
        if not groups or groups[-1]["headerName"] != source:
            groups.append({"headerName": source, "children": []})
        groups[-1]["children"].append(column_def)
    return groups


def _unique_columns(df: pd.DataFrame) -> pd.DataFrame:
    """The grid requires unique string column names."""
    seen: dict[str, int] = {}
    new_names = []
    for c in df.columns:
        c = str(c)
        if c in seen:
            seen[c] += 1
            c = f"{c}_{seen[c]}"
        else:
            seen[c] = 1
        new_names.append(c)
    if new_names != list(df.columns):
        df = df.copy()
        df.columns = new_names
    return df


def scroll_into_view(element: ui.element) -> None:
    """Scrolls the page just enough to show the element (e.g. a result that ends up under the window)."""
    # through the element's client: also works after an await, outside the UI context of the event;
    # the delay lets the browser draw the new content first (otherwise the element has no height yet)
    element.client.run_javascript(
        f"setTimeout(() => getHtmlElement({element.id})?.scrollIntoView({{block: 'nearest'}}), 300)")


def notify_error(e: Exception | str) -> None:
    ui.notify(str(e), type="negative", multi_line=True, close_button=True, timeout=8000)


# ===========================================================================
# Result panel: grid + export (always present under every view)
# ===========================================================================

def result_action(label: str, icon: str, handler: Callable, marker: str) -> None:
    """Item of the «Esporta e salva» menu of a result."""
    with ui.item(on_click=handler).mark(marker):
        with ui.item_section().props("avatar").classes("min-w-0 pr-3"):
            ui.icon(icon, size="20px")
        ui.item_section(label)


class NumberFormatMenu:
    """«Formato numeri» button: menu to choose how numbers are written (none, or decimal with its number of decimals,
    in the Italian or American style). `on_change` receives the new `NumberFormat`."""

    def __init__(self, fmt: number_format.NumberFormat, on_change: Callable[[number_format.NumberFormat], None]) -> None:
        self.fmt = fmt
        self.on_change = on_change
        self.updating = False  # the widgets are being set from outside: their events must not reach `on_change`
        low, high = number_format.DECIMALS_RANGE
        with ui.button("Formato numeri", icon="123").props("dense outline no-caps").mark("number-format"):
            with ui.menu(), ui.column().classes("gap-1 p-3 w-64"):
                ui.label("Formato dei numeri (interi e decimali)").classes("text-sm font-medium")
                self.mode = ui.radio(number_format.MODES, value=fmt.mode, on_change=self.changed).props(
                    "dense").mark("number-format-mode")
                self.decimals = ui.number("Decimali", value=fmt.decimals, min=low, max=high, step=1, precision=0,
                                          on_change=self.changed).props("dense outlined").classes("w-full").mark(
                    "number-format-decimals")
                ui.label("Separatori di migliaia e decimali").classes("text-xs text-gray-500 mt-2")
                self.style = ui.radio({name: f"{name} ({style.example})" for name, style in number_format.STYLES.items()},
                                      value=fmt.style, on_change=self.changed).props("dense").mark("number-format-style")
        self.set_enabled()

    def set_enabled(self) -> None:
        """Decimals and style matter only with a format."""
        for control in (self.decimals, self.style):
            control.set_enabled(self.fmt.mode != number_format.NO_FORMAT)

    def changed(self, _event=None) -> None:
        if self.updating:
            return
        self.fmt = number_format.NumberFormat(self.mode.value, number_format.clamp_decimals(self.decimals.value),
                                              self.style.value)
        self.set_enabled()
        self.on_change(self.fmt)

    def show(self, fmt: number_format.NumberFormat) -> None:
        """Shows a format chosen elsewhere (another panel of the same section)."""
        if fmt == self.fmt:
            return
        self.fmt, self.updating = fmt, True
        self.mode.value, self.decimals.value, self.style.value = fmt.mode, fmt.decimals, fmt.style
        self.updating = False
        self.set_enabled()


class ResultPanel:
    """Grid of a result with its export menu. `section` is the sidebar section that shows it: the number format
    chosen in «Formato numeri» is kept per section and shared by its panels."""

    def __init__(self, base_name: str = "risultato", preview_rows: int = DEFAULT_PREVIEW_ROWS,
                 precise: bool = False, fit_height: bool = False,
                 on_cell_click: Callable[[int, str], None] | None = None, *, section: str) -> None:
        self.df: pd.DataFrame | None = None
        self.section = section
        self.on_cell_click = on_cell_click  # (position of the row in the result, column name)
        self.precise = precise  # numbers with more decimals (statistics)
        self.fit_height = fit_height  # height fitted to the rows (up to GRID_HEIGHT): for small tables
        self.pinned: list[str] = []  # columns pinned on the left (e.g. the calculated column in the preview)
        self.sources: dict[str, tuple[str, bool]] = {}  # column → (source dataset, duplicate) for the headers
        self.name = base_name
        self.note: str | None = None  # origin and filters of the result (None = only the active dataset)
        self.limit = preview_rows
        self.total_rows: set[int] = set()  # positions of the rows drawn in bold (pivot totals)
        with ui.column().classes("w-full gap-2"):
            with ui.row().classes("w-full items-center gap-2"):
                self.info = ui.label("Nessun risultato").classes("text-sm text-gray-600")
                ui.select(ROW_LIMITS, value=preview_rows, label="Righe mostrate",
                          on_change=self.set_limit).props("dense outlined").classes("w-36")
                ui.space()
                self.format_menu = NumberFormatMenu(self.fmt, self.change_format)
                with ui.dropdown_button("Esporta e salva", icon="ios_share", auto_close=True).props(
                        "dense outline no-caps").mark("result-actions") as self.actions:
                    with ui.list().props("dense").classes("min-w-[220px]"):
                        result_action("Excel", "download", self.download_xlsx, "dl-xlsx")
                        result_action("CSV", "download", self.download_csv, "dl-csv")
                        result_action("Apri in Excel", "open_in_new", self.open_excel, "open-excel")
                        ui.separator()
                        result_action("Salva come dataset", "playlist_add", self.save_as_dataset, "save-dataset")
                        result_action("Salva nel database", "save",
                                      lambda: save_to_database(self.df, self.name), "db-save")
                        result_action("Aggiungi al report", "post_add", self.add_to_report, "add-report")
                        ui.separator()
                        result_action("Nota: origine e filtri", "sticky_note_2",
                                      lambda: note_dialog(self.name, self.current_note()), "show-note")
                self.b_full = ui.button(icon="fullscreen", on_click=self.full_screen).props(
                    'dense flat aria-label="Schermo intero"').tooltip("Schermo intero").mark("full-screen")
            self.grid = ui.aggrid({
                "columnDefs": [], "rowData": [],
                # long headers (e.g. pivot «somma importo | Abbigliamento») wrap instead of being cut
                "defaultColDef": {"sortable": True, "resizable": True, "filter": True,
                                  "minWidth": GRID_MIN_COLUMN_WIDTH, "wrapHeaderText": True, "autoHeaderHeight": True},
                "localeText": GRID_LOCALE,
                ":getRowClass": f"params => params.data && params.data.{TOTAL_FLAG} ? 'total-row' : ''",
                "suppressFieldDotNotation": True,
                "enableCellTextSelection": True,
                "pagination": True, "paginationPageSize": 100,
                "paginationPageSizeSelector": [50, 100, 500, 1000],
            }, theme="quartz").classes("w-full").style(f"height: {GRID_HEIGHT}px")
            if on_cell_click:
                self.grid.on("cellClicked", self._cell_click)
        self._enable(False)
        S.add_panel(self)

    @property
    def fmt(self) -> number_format.NumberFormat:
        return S.format_of(self.section, self.precise)

    def change_format(self, fmt: number_format.NumberFormat) -> None:
        """Format chosen in this panel: it applies to every panel of the section."""
        S.number_formats[self.section] = fmt
        for panel in S.panels:
            if panel.section == self.section and not panel.grid.is_deleted:
                panel.format_menu.show(fmt)
                panel.redraw_columns()

    def redraw_columns(self) -> None:
        """Rewrites the columns (numbers formatted as chosen) without touching the rows."""
        if self.df is not None:
            self.grid.options["columnDefs"] = _col_defs(self._visible(), self.pinned, self.sources, self.precise, self.fmt)
            self.grid.update()

    def _visible(self) -> pd.DataFrame:
        return _unique_columns(self.df.head(self.limit))

    def _cell_click(self, event) -> None:
        position = (event.args.get("data") or {}).get(ROW_ID)
        if position is not None:
            self.on_cell_click(int(position), event.args.get("colId"))

    def _enable(self, on: bool) -> None:
        for element in (self.actions, self.b_full):
            element.set_enabled(on)

    def set_limit(self, event) -> None:
        self.limit = event.value
        if self.df is not None:
            self.show(self.df, pinned=self.pinned, sources=self.sources, note=self.note,
                      total_rows=self.total_rows, reveal=False)

    def show(self, df: pd.DataFrame, name: str | None = None, pinned: list[str] = (),
             sources: dict[str, tuple[str, bool]] | None = None, note: str | None = None,
             total_rows: set[int] = frozenset(), reveal: bool = True) -> None:
        """Shows the result; `total_rows` are positions drawn in bold (pivot totals), `reveal` scrolls the grid into view."""
        if name:
            self.name = name
        self.df = df
        self.note = note
        self.pinned = list(pinned)
        self.sources = dict(sources or {})
        self.total_rows = set(total_rows)
        vis = self._visible()
        self.grid.options["columnDefs"] = _col_defs(vis, self.pinned, self.sources, self.precise, self.fmt)
        rows = _json_rows(vis)
        for position, row in enumerate(rows):
            if self.on_cell_click:
                row[ROW_ID] = position
            if position in self.total_rows:
                row[TOTAL_FLAG] = True
        self.grid.options["rowData"] = rows
        if self.fit_height:
            page_rows = min(len(vis), self.grid.options["paginationPageSize"])
            self.grid.style(replace=f"height: {min(GRID_HEIGHT, GRID_CHROME + GRID_ROW_HEIGHT * max(page_rows, 1))}px")
        self.grid.update()
        info = f"{len(df):,} righe × {df.shape[1]} colonne"
        if len(df) > len(vis):  # the grid has only the first rows: the export is complete
            info += f" · in tabella le prime {len(vis):,}"
        self.info.set_text(info.replace(",", "."))
        self._enable(True)
        if reveal:
            scroll_into_view(self.grid)

    def current_note(self) -> str:
        if self.note is not None:
            return self.note
        return S.note_for("", [(S.active, None)]) if S.active else ""

    # --- actions ----------------------------------------------------------
    def full_screen(self) -> None:
        """The same rows and columns of the grid in a full-screen window."""
        with ui.dialog().props("maximized") as dlg, ui.card().classes("w-full h-full no-wrap gap-2 p-3"):
            with ui.row().classes("w-full items-center gap-3 no-wrap"):
                ui.label(self.name).classes("text-base font-medium truncate")
                ui.label(self.info.text).classes("text-sm text-gray-600 truncate")
                ui.space()
                ui.button(icon="fullscreen_exit", on_click=dlg.close).props("flat round dense").tooltip("Chiudi")
            ui.aggrid(dict(self.grid.options), theme="quartz").classes("w-full").style("height: calc(100vh - 80px)")
        dlg.on_value_change(lambda e: None if e.value else dlg.delete())  # closed: frees the memory of the copy
        dlg.open()

    def add_to_report(self) -> None:
        if not self._check():
            return
        with ui.dialog() as dlg, ui.card().classes("w-96 max-w-full"):
            ui.label("Aggiungi al report Excel").classes("text-base font-medium")
            name = ui.input("Nome del foglio", value=S.report.unique_name(self.name)).props("autofocus").classes("w-full")
            title = ui.input("Titolo sopra la tabella (facoltativo)").classes("w-full")

            def ok() -> None:
                item = S.report.add(name.value.strip() or self.name, self.df, self.name, title.value.strip())
                dlg.close()
                refresh_report()
                ui.notify(f"«{item.name}» aggiunto al report ({len(S.report)} tabelle): scheda Excel → Report",
                          type="positive")

            with ui.row().classes("w-full justify-end"):
                ui.button("Annulla", on_click=dlg.close).props("flat")
                ui.button("Aggiungi", on_click=ok).mark("report-ok")
        dlg.open()

    def _check(self) -> bool:
        if self.df is None:
            ui.notify("Nessun dato da esportare", type="warning")
            return False
        return True

    async def download_xlsx(self) -> None:
        if self._check():
            try:
                data = await run.io_bound(eng.to_xlsx_bytes, self.df, self.name)
                ui.download.content(data, f"{self.name}.xlsx")
            except Exception as e:  # noqa: BLE001
                notify_error(e)

    async def download_csv(self) -> None:
        if self._check():
            try:
                data = await run.io_bound(eng.to_csv_bytes, self.df, True)
                ui.download.content(data, f"{self.name}.csv")
            except Exception as e:  # noqa: BLE001
                notify_error(e)

    async def open_excel(self) -> None:
        if self._check():
            try:
                path = await run.io_bound(eng.open_in_excel, self.df, self.name)
                ui.notify(f"Aperto in Excel: {path}", type="positive")
            except Exception as e:  # noqa: BLE001
                notify_error(e)

    def save_as_dataset(self) -> None:
        if not self._check():
            return
        with ui.dialog() as dlg, ui.card():
            ui.label("Salva il risultato come nuovo dataset").classes("text-base font-medium")
            name_input = ui.input("Nome", value=self.name).props("autofocus")

            def ok() -> None:
                name = S.add(name_input.value.strip() or self.name, self.df.copy(), activate=False,
                                  origin=self.current_note())
                dlg.close()
                update_header()
                ui.notify(f"Dataset '{name}' aggiunto (usalo da selettore, SQL o datasets['{name}'])",
                          type="positive")

            with ui.row().classes("w-full justify-end"):
                ui.button("Annulla", on_click=dlg.close).props("flat")
                ui.button("Salva", on_click=ok).mark("salva-ok")
        dlg.open()


# ===========================================================================
# Builder of nested AND/OR filters
# ===========================================================================

MAX_CHIPS = 15  # above this number of chosen values the field shows only how many there are


def count_text(values) -> str:
    count = len(values) if isinstance(values, list) else len(eng.adapt_value("in lista", values))
    return "1 valore" if count == 1 else f"{count:,} valori".replace(",", ".")


class FilterBuilder:
    COLORS = ["border-blue-400", "border-emerald-400", "border-amber-400", "border-fuchsia-400"]

    def __init__(self, df: pd.DataFrame, on_render: Callable[[dict], None] | None = None) -> None:
        self.df = df
        self.columns = [str(c) for c in df.columns]
        self.choices_cache: dict[str, list[str] | None] = {}
        self.tree = eng.new_group("AND")
        self.on_render = on_render  # called with the tree after every redraw (e.g. the count in the box title)
        self.render()

    # --- values that can be proposed ---------------------------------------
    def _choices(self, col: str) -> list[str] | None:
        if col not in self.choices_cache:
            self.choices_cache[col] = eng.unique_values(self.df, col)
        return self.choices_cache[col]

    @ui.refreshable_method
    def render(self) -> None:
        self._group(self.tree, None, 0)
        if self.on_render:
            self.on_render(self.tree)

    def _group(self, node: dict, parent: dict | None, level: int) -> None:
        color = self.COLORS[level % len(self.COLORS)]
        with ui.column().classes(f"w-full gap-1 pl-3 py-1 border-l-4 {color}"):
            with ui.row().classes("items-center gap-2 flex-wrap"):
                ui.toggle(["AND", "OR"], value=node["logica"],
                          on_change=lambda e, n=node: n.update(logica=e.value)).props("dense no-caps")
                ui.button("Condizione", icon="add", on_click=lambda n=node: self._add(n, False)).props("dense flat")
                ui.button("Gruppo", icon="account_tree", on_click=lambda n=node: self._add(n, True)).props("dense flat")
                if parent is not None:
                    ui.button(icon="delete", on_click=lambda n=node, p=parent: self._remove(p, n)).props("dense flat size=sm color=negative")
            for child in node["condizioni"]:
                if "condizioni" in child:
                    self._group(child, node, level + 1)
                else:
                    self._condition(child, node)

    # --- condition ----------------------------------------------------------
    def _condition(self, c: dict, parent: dict) -> None:
        with ui.row().classes("items-center gap-2 flex-wrap"):
            ui.select(self.columns, value=c["col"] or None, label="Colonna", with_input=True,
                      on_change=lambda e: self._change_column(c, e.value or "")).props("dense outlined").classes("w-52 max-w-full")
            ui.select(eng.OPERATORS, value=c["op"],
                      on_change=lambda e: self._change_operator(c, e.value)).props("dense outlined").classes("w-40 max-w-full")
            if c["op"] not in eng.NO_VALUE_OPS:
                self._value(c)
            ui.button(icon="close", on_click=lambda: self._remove(parent, c)).props("dense flat size=sm color=negative")

    def _value(self, c: dict) -> None:
        values = self._choices(c["col"]) if c["op"] in eng.CHOICE_OPS else None
        if values is None:
            self._text_value(c)
        else:
            self._choice_value(c, values)
        if c["op"] in eng.LIST_OPS:
            ui.button(icon="content_paste", on_click=lambda: self._paste(c)).props("dense flat size=sm").tooltip(
                "Incolla un elenco di valori (es. una colonna copiata da Excel)").mark("paste-values")

    @staticmethod
    def _text_value(c: dict) -> None:
        hint = {"in lista": "a, b, c", "non in lista": "a, b, c", "tra": "min, max"}.get(c["op"], "")
        text = ", ".join(c["val"]) if isinstance(c["val"], list) else str(c["val"])
        ui.input(value=text, label="Valore", placeholder=hint,
                 on_change=lambda e: c.update(val=e.value)).props("dense outlined").classes("w-56 max-w-full")

    def _choice_value(self, c: dict, values: list[str]) -> None:
        multiple = c["op"] in eng.LIST_OPS
        label = "Valori" if multiple else "Valore"
        if len(values) > eng.MAX_OPTIONS:
            label += f" (cerca tra {len(values):,})".replace(",", ".")

        def change(e) -> None:
            c.update(val=e.value or ([] if multiple else ""))
            if multiple and not chips:
                select.props(f'display-value="{count_text(c["val"])}"')

        chips = not multiple or len(c["val"]) <= MAX_CHIPS  # with many values the field shows only their number
        props = "dense outlined" + (" use-chips clearable" if multiple and chips else "")
        if not chips:
            props += f' clearable display-value="{count_text(c["val"])}"'
        select = ui.select(self._options(c, values), value=c["val"] if multiple else c["val"] or None,
                           label=label, multiple=multiple, with_input=True,
                           new_value_mode="add-unique",  # also allows values that are not in the list
                           on_change=change).props(props).classes("w-80 max-w-full" if multiple else "w-56 max-w-full")

        def search(e) -> None:
            text = e.args or ""
            if not multiple and text == c["val"]:
                text = ""  # the field shows the chosen value: it is not a search
            select.set_options(self._options(c, values, text))

        select.on("input-value", search)

    def _paste(self, c: dict) -> None:
        """Window to paste a list (one per line): the values are added to the chosen ones or replace them."""
        with ui.dialog() as dlg, ui.card().classes("w-[480px] max-w-full gap-3"):
            ui.label(f"Valori per «{c['col'] or 'colonna'}» {c['op']}").classes("text-base font-medium")
            box = ui.textarea("Incolla qui i valori", placeholder="Uno per riga, come una colonna copiata da Excel").props(
                "outlined autofocus input-style='min-height: 200px'").classes("w-full font-mono text-sm").mark("paste-text")
            count = ui.label("0 valori").classes("text-xs text-gray-600")
            box.on_value_change(lambda e: count.set_text(count_text(eng.parse_pasted_values(e.value or ""))))
            replace = ui.switch("Sostituisci i valori già scelti", value=False)

            def ok() -> None:
                pasted = eng.parse_pasted_values(box.value or "")
                c["val"] = pasted if replace.value else eng.merge_values(c["val"], pasted)
                self.render.refresh()
                ui.notify(f"{count_text(c['val'])} nella condizione", type="positive")
                dlg.close()  # last: closing deletes the dialog and with it the button of this handler

            with ui.row().classes("w-full justify-end"):
                ui.button("Annulla", on_click=dlg.close).props("flat")
                ui.button("Usa i valori", icon="done", on_click=ok).mark("paste-ok")
        dlg.on_value_change(lambda e: None if e.value else dlg.delete())
        dlg.open()

    @staticmethod
    def _options(c: dict, values: list[str], text: str = "") -> list[str]:
        """Values already chosen + at most MAX_OPTIONS values that match the search."""
        selected = c["val"] if isinstance(c["val"], list) else [c["val"]] if c["val"] else []
        return selected + [v for v in eng.search_values(values, text) if v not in selected]

    def _change_column(self, c: dict, col: str) -> None:
        c.update(col=col, val=eng.adapt_value(c["op"], ""))
        self.render.refresh()

    def _change_operator(self, c: dict, op: str) -> None:
        c.update(op=op, val=eng.adapt_value(op, c["val"]))
        self.render.refresh()

    def _add(self, node: dict, group: bool) -> None:
        if group:
            node["condizioni"].append(eng.new_group("OR" if node["logica"] == "AND" else "AND"))
        else:
            node["condizioni"].append(eng.new_condition())
        self.render.refresh()

    def _remove(self, parent: dict, child: dict) -> None:
        parent["condizioni"].remove(child)
        self.render.refresh()

    def reset(self) -> None:
        self.tree = eng.new_group("AND")
        self.render.refresh()


def count_conditions(node: dict) -> int:
    """Conditions of the tree with a column chosen (the empty ones filter nothing)."""
    return sum(count_conditions(child) if "condizioni" in child else bool(child["col"])
               for child in node["condizioni"])


def filter_box(df: pd.DataFrame, expanded: bool = False) -> FilterBuilder:
    """Closed by default: the title says how many conditions are set, e.g. «Filtri (2)»."""
    with ui.expansion("Filtri", icon="filter_alt", value=expanded).classes("w-full border rounded") as box:
        def update_title(tree: dict) -> None:
            count = count_conditions(tree)
            box.set_text(f"Filtri ({count})" if count else "Filtri")

        fb = FilterBuilder(df, on_render=update_title)
        ui.button("Azzera filtri", icon="backspace", on_click=fb.reset).props("dense flat")
    return fb


# ===========================================================================
# List of aggregations (column + function), reused by GroupBy and Pivot
# ===========================================================================

class AggregationList:
    def __init__(self, columns: list[str], title: str = "Valori", aligned: bool = False, named: bool = False,
                 on_change: Callable | None = None, details: Callable[[dict], None] | None = None,
                 df: pd.DataFrame | None = None) -> None:
        self.columns = columns
        self.df = df  # if given, for each column only the functions compatible with its type are offered
        self.aligned = aligned  # True: fields on the same grid as the controls of the tab
        self.named = named  # True: «Nome» field for each measure (wins over the naming pattern)
        self.on_change = on_change  # called at every change (also async: NiceGUI awaits its result)
        self.details = details  # extra controls under each measure (e.g. «Mostra valori come» in the pivot)
        self.items: list[dict] = []
        self.title = title
        ui.label(title).classes("text-sm font-medium")
        self.render()
        ui.button("Aggiungi valore", icon="add", on_click=self.add).props("dense flat")

    @ui.refreshable_method
    def render(self) -> None:
        column_width, function_width = ("w-full", "w-full") if self.aligned else ("w-52", "w-36")
        for it in self.items:
            functions = self.adapt(it)
            row = ui.grid().classes(f"{FORM_GRID} items-center") if self.aligned else ui.row().classes("items-center gap-2 no-wrap")
            with row:
                ui.select(self.columns, value=it["col"] or None, label="Colonna", with_input=True,
                          on_change=lambda e, it=it: self.set_col(it, e.value or "")).props("dense outlined").classes(column_width)
                ui.select(functions, value=it["fn"], label="Funzione",
                          on_change=lambda e, it=it: self.set(it, fn=e.value)).props("dense outlined").classes(function_width)
                if self.named:
                    ui.input("Nome (vuoto = schema)", value=it.get("name", ""),
                             on_change=lambda e, it=it: self.set(it, name=e.value)).props("dense outlined").classes(
                        "w-56").mark("measure-name")
                ui.button(icon="close", on_click=lambda it=it: self.remove(it)).props("dense flat size=sm color=negative")
            if self.details:
                self.details(it)

    def changed(self):
        return self.on_change() if self.on_change else None

    def functions(self, col: str) -> list[str]:
        if self.df is None or col not in self.df.columns:
            return list(eng.AGG_FUNCTIONS)
        return eng.allowed_functions(self.df[col])

    def adapt(self, it: dict) -> list[str]:
        """Functions allowed for the column of the measure; if the chosen one is not allowed, it switches to the count
        (e.g. sum on a text: it would concatenate the strings)."""
        functions = self.functions(it["col"])
        if it["fn"] not in functions:
            it["fn"] = "conteggio"
        return functions

    def set(self, it: dict, **changes):
        it.update(changes)
        return self.changed()

    def set_col(self, it: dict, col: str):
        it["col"] = col
        self.adapt(it)
        self.render.refresh()  # functions offered according to the column type
        return self.changed()

    def add(self):
        self.items.append({"col": "", "fn": "somma", "name": ""})
        self.render.refresh()
        return self.changed()

    def remove(self, it: dict):
        self.items.remove(it)
        self.render.refresh()
        return self.changed()


CUSTOM_NAMING = "custom"


class NamingChoice:
    """Naming pattern of the aggregated columns (Group By, Pivot): a ready-made one or one written with placeholders."""

    def __init__(self, on_change: Callable | None = None, pivot: bool = False) -> None:
        with ui.row().classes("w-full items-center gap-3"):
            self.pattern = ui.select({**eng.NAMING_PATTERNS, CUSTOM_NAMING: "Personalizzato…"},
                                     value=eng.DEFAULT_NAMING, label="Nomi delle colonne", on_change=on_change).props(
                "dense outlined").classes("w-56").mark("naming")
            self.custom = ui.input("Schema", value=eng.DEFAULT_NAMING, on_change=on_change).props("dense outlined").classes(
                "w-64").bind_visibility_from(self.pattern, "value", value=CUSTOM_NAMING).mark("naming-custom")
            value_help = ", {valore} (valore della colonna nella pivot; se manca va in fondo dopo «|»)" if pivot else ""
            ui.label(f"Segnaposto: {{colonna}}, {{funzione}}{value_help}. "
                     "Un nome scritto sulla misura vince sullo schema.").classes("text-xs text-gray-500")

    @property
    def value(self) -> str:
        return self.custom.value if self.pattern.value == CUSTOM_NAMING else self.pattern.value


# ===========================================================================
# AI assistant settings (shared by Console and Charts)
# ===========================================================================

def ai_settings() -> None:
    with ui.row().classes("w-full items-center gap-3"):
        ui.input("API key Anthropic", password=True, password_toggle_button=True).bind_value(S, "api_key").props("dense outlined").classes("w-96")
        ui.select(ai.MODELS, label="Modello").bind_value(S, "ai_model").props("dense outlined").classes("w-72")
        ui.select(ai.EFFORTS, label="Sforzo").bind_value(S, "ai_effort").props("dense outlined").classes("w-56")


# ===========================================================================
# Work windows: each one works on a dataset chosen in it
# ===========================================================================

MAX_WINDOWS = 6  # windows of a section that can be stacked one under the other
NEW_DATASET_SECTION = "Dati"  # section whose window shows a dataset just loaded or opened
# Sections whose window has its filters once, with sub-windows under them that read the same rows:
# section → (title of a sub-window, plural for the count choice)
SUB_WINDOWS = {"Group By": ("Group By", "Group By"), "Pivot": ("Pivot", "Pivot"), "Grafici": ("Grafico", "Grafici")}


def dataset_size(df: pd.DataFrame) -> str:
    return f"{len(df):,} righe × {df.shape[1]} colonne".replace(",", ".")


class Pane:
    """Framed window of a stack: title, close button and a body that `build` fills."""

    def __init__(self, stack: Stack) -> None:
        self.stack = stack
        with ui.column().classes(stack.frame).style(stack.frame_style) as self.card:
            with ui.row().classes("w-full items-center gap-3"):
                self.title = ui.label().classes("text-sm font-medium")
                self.header()
                ui.space()
                self.close_button = ui.button(icon="close", on_click=lambda: stack.remove(self)).props(
                    'flat round dense size=sm aria-label="Chiudi finestra"').tooltip("Chiudi finestra").mark(
                    f"{stack.marker}-close")
            self.body = ui.column().classes("w-full gap-3")
        self.build()

    def header(self) -> None:
        """Controls of the header row, after the title."""

    def build(self) -> None:
        raise NotImplementedError


class DatasetPane(Pane):
    """Window of a section on a dataset chosen in it: changing the dataset rebuilds its content (filters and
    choices start again, because the columns change). Sections in SUB_WINDOWS put the filters here, once, and
    stack under them the sub-windows that read the same filtered rows."""

    def __init__(self, stack: PaneStack, name: str) -> None:
        self.initial = name
        super().__init__(stack)
        S.panes.append(self)

    def header(self) -> None:
        self.select = ui.select(list(S.datasets), value=self.initial, label="Dataset", with_input=True,
                                on_change=self.change).props("dense outlined").classes(
            "w-72 max-w-full").mark("pane-dataset")
        self.size = ui.label().classes("text-xs text-gray-500")

    @property
    def name(self) -> str:
        return self.select.value

    def show_size(self) -> None:
        self.size.set_text(dataset_size(S.datasets[self.name]))

    def build(self) -> None:
        self.show_size()
        self.body.clear()
        self.sub_stack: SubStack | None = None
        with self.body:
            if self.stack.section in SUB_WINDOWS:
                self.sub_stack = SubStack(self.stack.section, self.stack.builder, self.name,
                                          filter_box(S.datasets[self.name]))
            else:
                self.stack.builder(self.name)

    def change(self, event) -> None:
        if event.value:
            S.active = event.value  # the last choice becomes the default of new windows
            self.build()


class SubPane(Pane):
    """Sub-window: one analysis (pivot, chart…) that reads the dataset and the filters of its window."""

    def build(self) -> None:
        with self.body:
            self.stack.builder(self.stack.dataset, self.stack.filters)


class Stack:
    """Panes one under the other; with `multiple` a count choice adds them and closes the last ones."""
    marker = "pane"  # prefix of the markers of the count choice and of the close buttons
    title = "Finestra"  # «Finestra 1», «Finestra 2»…
    count_label = "Finestre"
    count_width = "w-32"
    hint = ""
    frame = "w-full gap-3"
    frame_style = ""

    def __init__(self, section: str, builder: Callable, multiple: bool) -> None:
        self.section = section
        self.builder = builder  # builds the content of a pane
        self.multiple = multiple
        self.panes: list[Pane] = []
        if multiple:
            with ui.row().classes("items-center gap-3"):
                self.count = ui.select(list(range(1, MAX_WINDOWS + 1)), value=1, label=self.count_label,
                                       on_change=self.set_count).props("dense outlined").classes(
                    self.count_width).mark(f"{self.marker}-count")
                ui.label(self.hint).classes("text-xs text-gray-500")
        self.box = ui.column().classes("w-full gap-4")
        self.add()

    def new_pane(self) -> Pane:
        raise NotImplementedError

    def add(self) -> Pane:
        with self.box:
            pane = self.new_pane()
        self.panes.append(pane)
        self.update_titles()
        return pane

    def remove(self, pane: Pane) -> None:
        self.panes.remove(pane)
        pane.card.delete()
        self.update_titles()
        self.count.value = len(self.panes)  # set_count finds nothing to change

    def set_count(self, event) -> None:
        """New panes are added at the end; the last ones are closed first."""
        while len(self.panes) < event.value:
            scroll_into_view(self.add().card)
        while len(self.panes) > event.value:
            self.remove(self.panes[-1])

    def update_titles(self) -> None:
        for number, pane in enumerate(self.panes, 1):
            pane.title.set_text(f"{self.title} {number}")
            pane.title.set_visibility(len(self.panes) > 1)
            pane.close_button.set_visibility(self.multiple)
            pane.close_button.set_enabled(len(self.panes) > 1)


class PaneStack(Stack):
    """The windows of a section: each one with its own dataset, filters and choices."""
    hint = "Ogni finestra ha il suo dataset, i suoi filtri e le sue scelte."
    frame_style = ""

    def __init__(self, section: str, builder: Callable[..., None], multiple: bool) -> None:
        self.frame = "w-full gap-3 p-3 border rounded-lg" if multiple else "w-full gap-3"
        super().__init__(section, builder, multiple)

    def new_pane(self) -> DatasetPane:
        """A new window starts on the dataset of the last one."""
        return DatasetPane(self, self.panes[-1].name if self.panes else S.active)


class SubStack(Stack):
    """Sub-windows of a window: they share its dataset and its filters, each one has its own choices."""
    marker = "subpane"
    count_width = "w-64"
    hint = "Condividono il dataset e i filtri della finestra; ognuno ha le sue scelte."
    frame = "w-full gap-3 pl-4"
    frame_style = "border-left: 3px solid var(--line)"

    def __init__(self, section: str, builder: Callable[[str, FilterBuilder], None], dataset: str,
                 filters: FilterBuilder) -> None:
        singular, plural = SUB_WINDOWS[section]
        self.title, self.count_label = singular, f"{plural} in questa finestra"
        self.dataset, self.filters = dataset, filters
        super().__init__(section, builder, multiple=True)

    def new_pane(self) -> SubPane:
        return SubPane(self)


def refresh_dataset(name: str, keep: ui.element | None = None) -> None:
    """The dataset `name` changed (filters, structure, columns, join, undo): windows and pickers on it are rebuilt,
    except the window that contains `keep`."""
    refresh_menus()  # sizes of the datasets
    for pane in S.live_panes():
        if pane.name != name:
            continue
        if keep in pane.body.descendants():
            pane.show_size()
        else:
            pane.build()
    S.notify_watchers()  # e.g. the columns of a Console that changed its own df
    for picker in S.live_pickers():
        if picker.name == name:
            picker.change()


# ===========================================================================
# Tabs
# ===========================================================================

def tab_data(name: str) -> None:
    df = S.datasets[name]
    fb = filter_box(df)

    async def apply() -> None:
        try:
            out = await run.io_bound(eng.apply_filters, df, fb.tree)
            filtered = bool(fb.tree["condizioni"])
            result.show(out, f"{name}_filtrato" if filtered else name,
                       note=S.note_for("Filtro" if filtered else "", [(name, fb.tree)]))
        except Exception as e:  # noqa: BLE001
            notify_error(e)

    async def apply_to_dataset() -> None:
        if not eng.has_conditions(df, fb.tree):
            ui.notify("Nessun filtro da applicare", type="warning")
            return
        try:
            out = await run.io_bound(eng.apply_filters, df, fb.tree)
        except Exception as e:  # noqa: BLE001
            notify_error(e)
            return
        if out.empty:
            ui.notify("Nessuna riga passa i filtri: il dataset non è stato modificato", type="warning")
            return
        S.replace(name, out, f"filtro {eng.describe_filters(fb.tree)}")
        ui.notify(f"«{name}» filtrato: {len(out):,} righe su {len(df):,}".replace(",", "."), type="positive")
        refresh_dataset(name)  # the windows on this dataset must see the filtered rows

    with ui.row().classes("items-center gap-3"):
        ui.button("Applica filtri", icon="play_arrow", on_click=apply).props("outline").mark("run-dati")
        ui.button("Applica al dataset", icon="done", on_click=apply_to_dataset).mark("filter-apply")
        ui.label("«Applica al dataset» sostituisce il dataset scelto con le sole righe filtrate "
                 "(annullabile dalla scheda Struttura).").classes("text-xs text-gray-500")

    async def load_structure(event) -> None:
        if not event.value or structure_box.default_slot.children:
            return  # structure already calculated
        with structure_box:
            spinner = ui.spinner()
        try:
            info = await run.io_bound(eng.describe_columns, df)
            with structure_box:
                ui.table(rows=info.to_dict("records"),
                         columns=[{"name": k, "label": k, "field": k, "align": "left"} for k in info.columns],
                         pagination=15).classes("w-full").props("dense flat")
        except Exception as e:  # noqa: BLE001
            notify_error(e)
        finally:
            spinner.delete()

    with ui.expansion("Struttura colonne", icon="view_column",
                      on_value_change=load_structure).classes("w-full border rounded"):
        structure_box = ui.column().classes("w-full")
    # the grid goes last, so the filter buttons and the structure stay right under the filters
    result = ResultPanel(name, preview_rows=DATA_PREVIEW_ROWS, section="Dati")
    result.show(df, name, note=S.note_for("", [(name, None)]), reveal=False)


# ===========================================================================
# Structure tab (names, order and removal of columns; sorting of rows)
# ===========================================================================

class StructureTab:
    """Changes to the structure of the chosen dataset: they are previewed and applied directly to the dataset,
    with «Annulla ultima modifica»."""

    def __init__(self, name: str) -> None:
        self.name = name
        self.df = df = S.datasets[name]
        self.items = structure.column_items(df)
        self.levels = [structure.new_level()]
        self.names = [str(c) for c in df.columns]
        with ui.card().classes("w-full gap-2"):
            with ui.row().classes("w-full items-center gap-2"):
                section_title("Colonne")
                ui.space()
                ui.button("A → Z", icon="sort_by_alpha", on_click=lambda: self.sort_columns(False)).props("flat dense no-caps")
                ui.button("Z → A", on_click=lambda: self.sort_columns(True)).props("flat dense no-caps")
                ui.button("Tutte", icon="check_box", on_click=lambda: self.keep_all(True)).props("flat dense no-caps")
                ui.button("Nessuna", icon="check_box_outline_blank", on_click=lambda: self.keep_all(False)).props(
                    "flat dense no-caps")
                ui.button("Ripristina", icon="restart_alt", on_click=self.reset_columns).props("flat dense no-caps")
            ui.label("Rinomina scrivendo nel campo, sposta con le frecce, togli la spunta per eliminare la colonna.").classes(
                "text-xs text-gray-500")
            with ui.column().classes("w-full gap-1 max-h-[480px] overflow-auto"):
                self.column_rows()
        with ui.card().classes("w-full gap-2"):
            section_title("Ordina righe")
            self.level_rows()
            with ui.row().classes("items-center gap-4"):
                ui.button("Aggiungi livello", icon="add", on_click=self.add_level).props("dense flat")
                self.case_sensitive = ui.switch("Distingui maiuscole e minuscole")
        with ui.row().classes("items-center gap-3"):
            ui.button("Anteprima", icon="visibility", on_click=self.preview).props("outline").mark("structure-preview")
            ui.button("Applica al dataset", icon="done", on_click=self.apply).mark("structure-apply")
            self.undo_button = ui.button("Annulla ultima modifica", icon="undo", on_click=self.undo).props(
                "flat").mark("structure-undo")
            self.undo_label = ui.label().classes("text-xs text-gray-500")
        self.update_undo()
        self.result = ResultPanel(f"{name}_struttura", section="Struttura")

    # --- columns --------------------------------------------------------------
    @ui.refreshable_method
    def column_rows(self) -> None:
        last = len(self.items) - 1
        for index, item in enumerate(self.items):
            with ui.row().classes("w-full items-center gap-2 no-wrap"):
                ui.checkbox(value=item["keep"], on_change=lambda e, it=item: it.update(keep=e.value)).props("dense")
                ui.label(f"{index + 1}").classes("w-8 text-right text-xs text-gray-500")
                ui.input(value=item["name"], on_change=lambda e, it=item: it.update(name=e.value)).props(
                    "dense outlined").classes("flex-grow max-w-[480px]").mark("column-name")
                for icon, target, tooltip in (("vertical_align_top", 0, "In cima"), ("arrow_upward", index - 1, "Su"),
                                              ("arrow_downward", index + 1, "Giù"), ("vertical_align_bottom", last, "In fondo")):
                    ui.button(icon=icon, on_click=lambda i=index, t=target: self.move(i, t)).props(
                        "flat round dense size=sm").tooltip(tooltip).set_enabled(0 <= target <= last and target != index)

    def move(self, index: int, target: int) -> None:
        self.items.insert(target, self.items.pop(index))
        self.column_rows.refresh()

    def sort_columns(self, descending: bool) -> None:
        self.items.sort(key=lambda item: item["name"].casefold(), reverse=descending)
        self.column_rows.refresh()

    def keep_all(self, keep: bool) -> None:
        for item in self.items:
            item["keep"] = keep
        self.column_rows.refresh()

    def reset_columns(self) -> None:
        self.items = structure.column_items(self.df)
        self.column_rows.refresh()

    # --- row sorting -----------------------------------------------------------
    @ui.refreshable_method
    def level_rows(self) -> None:
        for index, level in enumerate(self.levels):
            with ui.row().classes("w-full items-center gap-2 no-wrap"):
                ui.label("Ordina per" if index == 0 else "Poi per").classes("w-20 text-sm")
                ui.select(self.names, value=level["column"], with_input=True, clearable=True,
                          on_change=lambda e, lv=level: lv.update(column=e.value)).props("dense outlined").classes(
                    "w-72").mark("sort-column")
                ui.toggle({False: "Crescente", True: "Decrescente"}, value=level["descending"],
                          on_change=lambda e, lv=level: lv.update(descending=e.value)).props("dense no-caps").mark(
                    "sort-direction")
                ui.button(icon="close", on_click=lambda lv=level: self.remove_level(lv)).props(
                    "dense flat size=sm color=negative").set_enabled(len(self.levels) > 1)

    def add_level(self) -> None:
        self.levels.append(structure.new_level())
        self.level_rows.refresh()

    def remove_level(self, level: dict) -> None:
        self.levels.remove(level)
        self.level_rows.refresh()

    # --- actions --------------------------------------------------------------
    async def compute(self) -> pd.DataFrame | None:
        try:
            return await run.io_bound(structure.apply, self.df, self.items, self.levels, self.case_sensitive.value)
        except Exception as e:  # noqa: BLE001
            notify_error(e)
            return None

    async def preview(self) -> None:
        if (out := await self.compute()) is not None:
            self.result.show(out, note=S.note_for("Modifica della struttura", [(self.name, None)]))

    async def apply(self) -> None:
        if (out := await self.compute()) is None:
            return
        renamed = structure.renamed(self.df, self.items)
        S.replace(self.name, out, "modifica della struttura")
        message = f"Struttura di «{self.name}» aggiornata: {out.shape[1]} colonne, {len(out):,} righe".replace(",", ".")
        ui.notify(message, type="positive")
        code = " ".join(S.code.values())
        if any(old in code for old in renamed):
            ui.notify("Il codice della Console usa nomi di colonna cambiati: aggiornalo", type="warning")
        refresh_dataset(self.name)  # the windows on this dataset must see the new structure

    def update_undo(self) -> None:
        available = S.undo is not None and S.undo[0] == self.name
        self.undo_button.set_visibility(available)
        self.undo_label.set_text(f"Ultima modifica: {S.undo[2]}" if available else "")

    def undo(self) -> None:
        if description := S.revert():
            ui.notify(f"Annullata: {description}", type="info")
            refresh_dataset(self.name)


def tab_groupby(name: str, filters: FilterBuilder) -> None:
    df = S.datasets[name]
    cols = [str(c) for c in df.columns]
    with ui.card().classes("w-full"):
        by = ui.select(cols, multiple=True, label="Raggruppa per", with_input=True).props("use-chips outlined dense").classes("w-full")
        mode = ui.toggle(["Aggregazioni", "Value counts"], value="Aggregazioni").props("no-caps")

        with ui.column().classes("w-full").bind_visibility_from(mode, "value", value="Aggregazioni"):
            aggs = AggregationList(cols, "Aggregazioni", named=True, df=df)
            naming = NamingChoice()
        with ui.column().classes("w-full gap-2").bind_visibility_from(mode, "value", value="Value counts"):
            count_cols = ui.select(cols, multiple=True, label="Colonne da contare", with_input=True).props("use-chips outlined dense").classes("w-full")
            percent = ui.switch("Mostra anche la percentuale (sul totale di ogni gruppo)", value=True)
        sort = ui.switch("Ordina per chiavi", value=True)

    async def execute() -> None:
        try:
            keys = list(by.value or [])
            operation = f"{mode.value} per {', '.join(keys)}" if keys else mode.value
            if mode.value == "Value counts":
                out = await run.io_bound(eng.run_value_counts, df, keys, list(count_cols.value or []),
                                         filters.tree, percent.value, sort.value)
            else:
                out = await run.io_bound(eng.run_groupby, df, keys, aggs.items, filters.tree, sort.value,
                                         naming.value)
            result.show(out, f"groupby_{name}", note=S.note_for(operation, [(name, filters.tree)]))
        except Exception as e:  # noqa: BLE001
            notify_error(e)

    with ui.row().classes("items-center gap-3"):
        ui.button("Esegui Group By", icon="play_arrow", on_click=execute).mark("run-groupby")
        ui.label("Aggregazioni: senza valori ottieni il conteggio righe per gruppo. "
                 "Value counts: frequenza (e %) dei valori delle colonne scelte, per ogni gruppo.").classes("text-xs text-gray-500")
    result = ResultPanel("groupby", fit_height=True, section="Group By")


LABEL_SORT = -1  # sorting of the rows by label (otherwise: index of the measure)


class PivotTab:
    """Pivot tab as a cube: changes are applied with «Crea Pivot», which recalculates the cube only if
    fields, aggregations, filters or date grouping change («Mostra valori come», totals, sorting and names
    only redraw). Click on a cell and «Righe di origine»: the dataset rows that make it up."""

    def __init__(self, name: str, filters: FilterBuilder) -> None:
        self.name = name
        self.df = df = S.datasets[name]
        columns = [str(c) for c in df.columns]
        self.date_fields = {str(c) for c in df.columns if charts.is_date(df[c])}
        self.dates: dict[str, str] = {}  # date field → chosen grouping unit
        self.items_cache: dict[tuple[str, str | None], list[str]] = {}
        self.cube: pivot.Cube | None = None
        self.table: pivot.PivotTable | None = None
        self.selected: tuple[int, str] | None = None  # cell chosen with a click: (table row, column)
        self.version = 0  # last requested calculation: results of outdated calculations are discarded

        self.filters = filters  # of the window: shared with the other sub-windows
        with ui.card().classes("w-full"):
            with ui.row().classes("w-full no-wrap gap-4"):
                self.rows = ui.select(columns, multiple=True, label="Righe", with_input=True,
                                      on_change=self.fields_changed).props(
                    "use-chips outlined dense").classes("w-1/2").mark("pivot-rows")
                self.columns = ui.select(columns, multiple=True, label="Colonne", with_input=True,
                                         on_change=self.fields_changed).props(
                    "use-chips outlined dense").classes("w-1/2").mark("pivot-columns")
            self.date_controls()
            self.values = AggregationList(columns, "Valori", named=True, on_change=self.changed,
                                   details=self.show_as_controls, df=df)
        with ui.card().classes("w-full"):
            ui.label("Layout").classes("text-sm font-medium")
            with ui.row().classes("items-center gap-6"):
                self.grand_rows = ui.switch("Riga Totale in fondo", value=True, on_change=self.changed)
                self.grand_columns = ui.switch("Colonna Totale a destra", value=True, on_change=self.changed)
                self.subtotal_rows = ui.switch("Subtotali delle righe", value=True, on_change=self.changed)
                self.subtotal_columns = ui.switch("Subtotali delle colonne", value=False, on_change=self.changed)
                self.fill_zero = ui.switch("Celle vuote = 0", value=False, on_change=self.changed)
            with ui.row().classes("items-center gap-4"):
                self.sort = ui.select({LABEL_SORT: "Etichette"}, value=LABEL_SORT, label="Ordina le righe per",
                                      on_change=self.changed).props("dense outlined").classes("w-64").mark("pivot-sort")
                self.descending = ui.switch("Decrescente", value=False, on_change=self.changed)
            self.naming = NamingChoice(on_change=self.changed, pivot=True)
        with ui.row().classes("items-center gap-3") as self.anchor:
            self.button = ui.button("Crea Pivot", icon="play_arrow", on_click=self.build).mark("run-pivot")
        self.result = ResultPanel("pivot", on_cell_click=self.select, section="Pivot")
        with ui.row().classes("items-center gap-3"):
            self.drill_button = ui.button("Righe di origine", icon="manage_search", on_click=self.drill).props(
                "outline no-caps").mark("pivot-drill")
            self.selection = ui.label().classes("text-sm text-gray-600")
        with ui.expansion("Righe di origine della cella", icon="manage_search", value=False).classes(
                "w-full border rounded") as self.drill_box:
            self.drill_label = ui.label("Scegli una cella della pivot e premi «Righe di origine»").classes(
                "text-sm text-gray-600")
            self.drill_result = ResultPanel("righe_pivot", section="Pivot")
        self.clear_selection()

    # --- state -----------------------------------------------------------
    def fields(self) -> list[str]:
        return [*(self.rows.value or []), *(self.columns.value or [])]

    def spec(self) -> dict:
        fields = self.fields()
        return {"rows": list(self.rows.value or []), "columns": list(self.columns.value or []),
                "values": self.values.items, "filters": self.filters.tree,
                "dates": {field: unit for field, unit in self.dates.items() if field in fields}}

    def layout(self) -> dict:
        return {"grand_rows": self.grand_rows.value, "grand_columns": self.grand_columns.value,
                "subtotal_rows": self.subtotal_rows.value, "subtotal_columns": self.subtotal_columns.value,
                "fill_zero": self.fill_zero.value, "naming": self.naming.value,
                "sort_by": None if self.sort.value == LABEL_SORT else self.sort.value,
                "descending": self.descending.value}

    def items(self, field: str) -> list[str]:
        """Items of a field (for the base item), with the chosen date grouping."""
        key = (field, self.dates.get(field) if field in self.date_fields else None)
        if key not in self.items_cache:
            self.items_cache[key] = pivot.field_items(self.df, *key)
        return self.items_cache[key]

    def complete_measure(self, item: dict) -> None:
        """Proposed base field and item (the first available) when the calculation needs them."""
        fields = self.fields()
        if pivot.needs_base_field(item) and item.get("base_field") not in fields:
            item["base_field"] = fields[0] if fields else ""
        if pivot.needs_base_item(item) and item.get("base_field"):
            items = self.items(item["base_field"])
            if item.get("base_item") not in items:
                item["base_item"] = items[0] if items else ""

    # --- controls --------------------------------------------------------
    @ui.refreshable_method
    def date_controls(self) -> None:
        fields = [field for field in self.fields() if field in self.date_fields]
        if not fields:
            return
        with ui.row().classes("items-center gap-4"):
            for field in fields:
                self.dates.setdefault(field, charts.DEFAULT_DATE_UNIT)
                ui.select(list(charts.DATE_UNITS), value=self.dates[field], label=f"{field}: raggruppa per",
                          on_change=lambda e, field=field: self.set_date(field, e.value)).props(
                    "dense outlined").classes("w-52").mark("pivot-date")

    def show_as_controls(self, item: dict) -> None:
        """Under each measure: «Mostra valori come» and, when needed, reference, base field and base item."""
        item.setdefault("show_as", pivot.NO_CALCULATION)
        item.setdefault("relative_to", pivot.GRAND_TOTAL)
        with ui.row().classes("items-center gap-2 pl-6 pb-2"):
            ui.select(list(pivot.CALCULATIONS), value=item["show_as"], label="Mostra valori come",
                      on_change=lambda e: self.set_measure(item, show_as=e.value)).props(
                "dense outlined").classes("w-56").mark("show-as")
            if pivot.calculation_of(item).uses_reference:
                ui.select(list(pivot.REFERENCES), value=item["relative_to"], label="rispetto a",
                          on_change=lambda e: self.set_measure(item, relative_to=e.value)).props(
                    "dense outlined").classes("w-60").mark("relative-to")
            if pivot.needs_base_field(item):
                ui.select(self.fields(), value=item.get("base_field") or None, label="Campo base",
                          on_change=lambda e: self.set_measure(item, base_field=e.value or "")).props(
                    "dense outlined").classes("w-44").mark("base-field")
            if pivot.needs_base_item(item) and item.get("base_field"):
                ui.select(self.items(item["base_field"]), value=item.get("base_item") or None, label="Elemento",
                          with_input=True, on_change=lambda e: self.set_measure(item, base_item=e.value or "")).props(
                    "dense outlined").classes("w-44").mark("base-item")

    def update_sort_options(self) -> None:
        measures = [item for item in self.values.items if item.get("col")]
        options = {LABEL_SORT: "Etichette",
                   **{i: f"{i + 1}. {eng.measure_name(item)}" for i, item in enumerate(measures)}}
        if options != self.sort.options:
            self.sort.set_options(options, value=self.sort.value if self.sort.value in options else LABEL_SORT)

    # --- events ----------------------------------------------------------
    async def fields_changed(self, *_) -> None:
        for item in self.values.items:
            self.complete_measure(item)
        self.date_controls.refresh()
        self.values.render.refresh()
        await self.changed()

    async def set_date(self, field: str, unit: str) -> None:
        self.dates[field] = unit
        for item in self.values.items:
            self.complete_measure(item)
        self.values.render.refresh()
        await self.changed()

    async def set_measure(self, item: dict, **changes) -> None:
        item.update(changes)
        self.complete_measure(item)
        self.values.render.refresh()
        await self.changed()

    async def changed(self, *_) -> None:
        """Updates only the controls: the pivot changes with «Crea Pivot»."""
        self.update_sort_options()

    async def compute(self, function, *args):
        """Calculation in a thread, with the button in loading state: the interface stays free.
        Returns None if it fails or if a more recent calculation was requested in the meantime."""
        self.version += 1
        version = self.version
        self.button.props(add="loading")
        try:
            result = await run.io_bound(function, *args)
        except Exception as e:  # noqa: BLE001
            if version == self.version:
                with self.anchor:
                    notify_error(e)
            return None
        finally:
            if version == self.version:
                self.button.props(remove="loading")
        return result if version == self.version else None

    async def build(self) -> None:
        """«Crea Pivot»: recalculates the cube if the choices are not already covered, then redraws."""
        # the choices are copied: the user can change them while the thread works
        spec = copy.deepcopy(self.spec())
        if self.cube is None or not self.cube.covers(spec):
            cube = await self.compute(pivot.build_cube, self.df, spec)
            if cube is None:
                return
            self.cube = cube
        await self.show()

    async def show(self) -> None:
        table = await self.compute(pivot.render, self.cube, copy.deepcopy(self.values.items), self.layout())
        if table is not None:
            self.table = table
            self.clear_selection()  # in the new table rows and columns may be different
            totals = np.flatnonzero((table.row_codes == pivot.TOTAL).any(axis=1).to_numpy())
            self.result.show(table.frame, f"pivot_{self.name}", total_rows=set(totals.tolist()),
                             note=S.note_for("Pivot", [(self.name, table.cube.filters)]))

    # --- source rows -------------------------------------------------------
    def clear_selection(self) -> None:
        self.selected = None
        self.drill_button.disable()
        self.selection.set_text("Clic su una cella della pivot per sceglierla")

    def select(self, row: int, column: str) -> None:
        """Click on a cell: chooses it for «Righe di origine»."""
        if self.table is None or row >= len(self.table.frame):
            return
        self.selected = (row, column)
        self.selection.set_text(f"Cella scelta: {pivot.selection_text(self.table, row, column)}")
        self.drill_button.enable()

    def drill(self) -> None:
        """The dataset rows that make up the chosen cell."""
        if self.table is None or self.selected is None:
            return
        rows = pivot.drill(self.table, *self.selected)
        count = f"{len(rows):,}".replace(",", ".")
        self.drill_label.set_text(f"{pivot.selection_text(self.table, *self.selected)} — {count} righe")
        self.drill_result.show(rows, f"{self.name}_righe_pivot", note=S.note_for(
            "Righe di una cella della pivot", [(self.name, self.table.cube.filters)]))
        self.drill_box.open()


# ===========================================================================
# Join / Union
# ===========================================================================

COMBINE_MODES = ("Join", "Union")
ERROR_STYLE, WARNING_STYLE = "text-red-600 font-medium", "text-amber-700"
SourceNote = Callable[[dict], tuple[str, str]]


def mark_notes(plan: list[dict], status: dict[int, ui.label], note: SourceNote) -> None:
    """Writes the note of each plan entry; a name repeated among the kept columns is an error."""
    counts = Counter(entry["name"] for entry in plan if entry["keep"])
    for entry in plan:
        label = status.get(id(entry))  # missing only while the column table is being redrawn
        if label is None:
            continue
        if entry["keep"] and counts[entry["name"]] > 1:
            text, style_classes = "nome ripetuto", ERROR_STYLE
        else:
            text, style_classes = note(entry)
        label.set_text(text).classes(replace=f"text-xs {style_classes}")


def column_card(status: dict[int, ui.label], entry: dict, title: str) -> ui.expansion:
    """One expander per column, closed at first: name and note in the header, its controls in the body."""
    card = ui.expansion(value=False).classes("w-full border rounded")
    with card.add_slot("header"):
        with ui.row().classes("w-full items-center gap-3 no-wrap"):
            ui.label(title).classes("font-mono text-sm truncate")
            status[id(entry)] = ui.label().classes("text-xs")
    return card


async def run_combination(button: ui.button, title: str, function, *args) -> pd.DataFrame | None:
    """Runs in a thread with the button in loading state; when done it reports rows, columns and time, or the error."""
    button.props(add="loading")
    started = time.perf_counter()
    try:
        out = await run.io_bound(function, *args)
    except Exception as e:  # noqa: BLE001
        notify_error(e)
        return None
    finally:
        button.props(remove="loading")
    rows = f"{len(out):,}".replace(",", ".")
    seconds = f"{time.perf_counter() - started:.1f}".replace(".", ",")
    ui.notify(f"{title}: {rows} righe × {out.shape[1]} colonne in {seconds} s", type="positive")
    return out


class SourcePicker:
    """Choice of a dataset with its filters; the filters are rebuilt when the dataset changes."""

    def __init__(self, title: str, name: str, on_change: Callable[[], None], on_remove: Callable | None = None) -> None:
        S.pickers = [picker for picker in S.pickers if not picker.card.is_deleted] + [self]
        self.on_change = on_change
        with ui.column().classes("w-full gap-1 p-2 border rounded-lg") as self.card:
            with ui.row().classes("w-full items-center gap-3 flex-wrap"):
                self.select = ui.select(list(S.datasets), value=name, label=title, with_input=True,
                                        on_change=self.change).props("dense outlined").classes("w-64 max-w-full")
                self.size = ui.label().classes("text-xs text-gray-500")
                ui.space()
                if on_remove is not None:
                    ui.button(icon="delete", on_click=lambda: on_remove(self)).props("dense flat size=sm color=negative")
            self.filters_box = ui.column().classes("w-full")
        self.build_filters()

    @property
    def name(self) -> str:
        return self.select.value

    @property
    def df(self) -> pd.DataFrame:
        return S.datasets[self.name]

    @property
    def columns(self) -> list[str]:
        return [str(name) for name in self.df.columns]

    @property
    def tree(self) -> dict:
        return self.filters.tree

    def build_filters(self) -> None:
        rows, width = self.df.shape
        self.size.set_text(f"{rows:,} righe × {width} colonne".replace(",", "."))
        self.filters_box.clear()
        with self.filters_box:
            self.filters = filter_box(self.df, expanded=False)

    def change(self) -> None:
        self.build_filters()
        self.on_change()


class JoinStep:
    """A step of the chain: dataset to join on the right, join type, keys, columns of the step and dataset filters."""

    def __init__(self, panel: JoinPanel, right_name: str) -> None:
        self.panel = panel
        self.plan: list[dict] = []
        self.keys: list[dict] = [combine.new_key()]
        self.left_columns: list[str] = []  # columns of the result of the previous steps (or of the starting dataset)
        self.status: dict[int, ui.label] = {}
        self.open_sources: dict[str, bool] = {}  # expander of each dataset: open or closed, kept between redraws
        with ui.card().classes("w-full gap-3") as self.card:
            with ui.row().classes("w-full items-center gap-2"):
                self.title = ui.label().classes("text-base font-medium")
                ui.space()
                self.remove_button = ui.button(icon="delete", on_click=lambda: panel.remove_step(self)).props(
                    "dense flat size=sm color=negative")
            self.right = SourcePicker("Dataset da unire", right_name, lambda: panel.dataset_changed(self))
            with ui.grid().classes(FORM_GRID):
                self.how = ui.select({key: join_type.label for key, join_type in combine.JOIN_TYPES.items()},
                                     value="left", label="Tipo di join",
                                     on_change=lambda: self.changed()).props("dense outlined").classes("w-full")
                self.match = ui.select({key: match.label for key, match in combine.KEY_MATCHES.items()}, value="exact",
                                       label="Confronto delle chiavi",
                                       on_change=lambda: self.changed()).props("dense outlined").classes("w-full")
                self.naming = ui.select(combine.NAMING, value="duplicates", label="Suffisso dataset",
                                        on_change=lambda: self.changed()).props("dense outlined").classes("w-full")
                self.naming.tooltip("Quando aggiungere ai nomi delle colonne il nome del dataset di origine")
            self.hint = ui.label().classes("text-xs text-gray-500")
            with ui.column().classes("w-full gap-1") as self.keys_box:
                ui.label("Chiavi").classes("text-sm font-medium")
                self.render_keys()
            with ui.row().classes("w-full items-center gap-x-8 gap-y-1 flex-wrap"):
                self.null_keys = ui.switch("Le chiavi nulle si corrispondono")
                self.merge_keys = ui.switch("Unisci le chiavi «=» in una sola colonna", on_change=lambda: self.changed())
                self.indicator = ui.switch(f"Aggiungi la colonna «{combine.ORIGIN}» (sinistra / destra / entrambi)",
                                           on_change=lambda: self.changed())
            self.render_plan()
        self.update_visibility()

    def spec(self, right_index: int) -> dict:
        return {"right": right_index, "how": self.how.value, "keys": self.keys, "match": self.match.value,
                "null_keys": self.null_keys.value, "merge_keys": self.merge_keys.value,
                "naming": self.naming.value, "indicator": self.indicator.value}

    def changed(self) -> None:
        self.update_visibility()
        self.panel.refresh()

    def update_visibility(self) -> None:
        join_type = combine.JOIN_TYPES[self.how.value]
        two_sided = len(join_type.sides) == 2
        self.hint.set_text(join_type.description)
        for control, visible in ((self.keys_box, join_type.keyed), (self.match, join_type.keyed),
                                 (self.null_keys, join_type.keyed), (self.merge_keys, join_type.keyed and two_sided),
                                 (self.indicator, two_sided)):
            control.set_visibility(visible)

    def show(self, plan: list[dict], left_columns: list[str]) -> None:
        self.plan = plan
        self.left_columns = left_columns
        self.render_keys.refresh()
        self.render_plan.refresh()

    # --- keys -----------------------------------------------------------------
    @ui.refreshable_method
    def render_keys(self) -> None:
        for key in self.keys:
            with ui.grid().classes(f"{FORM_GRID} items-center"):
                ui.select(self.left_columns, value=key["left"] or None, label="Colonna di sinistra", with_input=True,
                          on_change=lambda e, key=key: self.set_key(key, left=e.value or "")).props(
                    "dense outlined").classes("w-full")
                ui.select(list(combine.KEY_OPERATORS), value=key["op"], label="Operatore",
                          on_change=lambda e, key=key: self.set_key(key, op=e.value)).props("dense outlined").classes("w-full")
                ui.select(self.right.columns, value=key["right"] or None, label="Colonna di destra", with_input=True,
                          on_change=lambda e, key=key: self.set_key(key, right=e.value or "")).props(
                    "dense outlined").classes("w-full")
                ui.button(icon="close", on_click=lambda key=key: self.remove_key(key)).props(
                    "dense flat size=sm color=negative")
        ui.button("Aggiungi chiave", icon="add", on_click=lambda: self.add_key()).props("dense flat")

    def set_key(self, key: dict, **changes) -> None:
        key.update(changes)
        self.changed()

    def add_key(self) -> None:
        self.keys.append(combine.new_key())
        self.render_keys.refresh()

    def remove_key(self, key: dict) -> None:
        self.keys.remove(key)
        self.changed()

    # --- columns of the step --------------------------------------------------
    @ui.refreshable_method
    def render_plan(self) -> None:
        self.status.clear()
        for source in dict.fromkeys(entry["source"] for entry in self.plan):
            entries = [entry for entry in self.plan if entry["source"] == source]
            kept = sum(entry["keep"] for entry in entries)
            with ui.expansion(f"Colonne di {source} · {kept}/{len(entries)} incluse", icon="view_column",
                              value=self.open_sources.get(source, True),
                              on_value_change=lambda e, source=source: self.open_sources.update({source: e.value})
                              ).classes("w-full border rounded"):
                with ui.row().classes("w-full items-center gap-2"):
                    for label, rule in (("Tutte", lambda entry: True), ("Nessuna", lambda entry: False),
                                        ("Senza duplicati", lambda entry: not entry["duplicate"])):
                        ui.button(label, on_click=lambda source=source, rule=rule: self.select_source(source, rule)
                                  ).props("dense flat size=sm")
                for entry in entries:
                    self.plan_card(entry)
        mark_notes(self.plan, self.status, self.note)

    def plan_card(self, entry: dict) -> None:
        title = entry["column"] if entry["name"] == entry["column"] else f"{entry['column']} → {entry['name']}"
        with column_card(self.status, entry, title):
            locked = combine.merged_away(entry)  # key merged with the one of the other dataset: it does not appear
            with ui.row().classes("w-full items-center gap-4 flex-wrap"):
                ui.checkbox("Includi nel risultato", value=entry["keep"],
                            on_change=lambda e, entry=entry: self.set_keep(entry, e.value)
                            ).props("dense").set_enabled(not locked)
                name = ui.input("Nome nel risultato", value=entry["name"],
                                on_change=lambda e, entry=entry: self.panel.choose(self, entry, name=e.value)
                                ).props("dense outlined").classes("w-72 max-w-full")
                name.set_enabled(not locked)
                name.on("blur", lambda: self.panel.refresh())  # the downstream columns are updated at the end of the change

    def set_keep(self, entry: dict, keep: bool) -> None:
        self.panel.choose(self, entry, keep=keep)
        self.panel.refresh()

    def select_source(self, source: str, rule) -> None:
        for entry in self.plan:
            if entry["source"] == source and not combine.merged_away(entry):
                self.panel.choose(self, entry, keep=rule(entry))
        self.panel.refresh()

    @staticmethod
    def note(entry: dict) -> tuple[str, str]:
        if entry["merged_with"]:
            return f"unita a «{entry['merged_with']}»", "text-gray-500"
        if entry["duplicate"]:
            return "duplicato", WARNING_STYLE
        return "", ""


class JoinPanel:
    """Join chain: other datasets are joined one at a time to the starting dataset (+ dataset)."""

    def __init__(self, result: ResultPanel) -> None:
        self.result = result
        self.overrides: dict[tuple[int, str, str], dict] = {}  # user choices: (step, side, column) → changes
        self.steps: list[JoinStep] = []
        with ui.card().classes("w-full"):
            self.start = SourcePicker("Dataset di partenza", S.active, self.start_changed)
        self.steps_box = ui.column().classes("w-full gap-3")
        with ui.row().classes("items-center gap-3"):
            ui.button("Aggiungi join (+ dataset)", icon="add", on_click=lambda: self.add_step()).props("dense outline")
            self.run_button = ui.button("Esegui join", icon="play_arrow", on_click=self.execute).mark("run-join")
            self.apply_button = ui.button("Applica al dataset", icon="done",
                                          on_click=self.overwrite_start).mark("join-apply")
            ui.label("«Applica al dataset» sostituisce il dataset di partenza con il risultato del join "
                     "(annullabile dalla scheda Struttura).").classes("text-xs text-gray-500")
        self.add_step()

    # --- steps ----------------------------------------------------------------
    def add_step(self, right_name: str | None = None) -> None:
        used = set(self.names())
        name = right_name or next((dataset for dataset in S.datasets if dataset not in used), self.start.name)
        with self.steps_box:
            self.steps.append(JoinStep(self, name))
        self.refresh()

    def remove_step(self, step: JoinStep) -> None:
        if len(self.steps) == 1:
            ui.notify("Serve almeno un join", type="warning")
            return
        number = self.steps.index(step)
        self.steps.remove(step)
        step.card.delete()
        S.pickers = [picker for picker in S.pickers if picker is not step.right]
        self.overrides = {(index - 1 if index > number else index, side, column): choice
                          for (index, side, column), choice in self.overrides.items() if index != number}
        self.refresh()

    def start_changed(self) -> None:
        self.overrides.clear()
        self.steps[0].keys = combine.suggest_keys(self.start.df, self.steps[0].right.df) or [combine.new_key()]
        self.refresh()

    def dataset_changed(self, step: JoinStep) -> None:
        number = self.steps.index(step)
        self.overrides = {key: value for key, value in self.overrides.items() if key[0] != number}
        if number == 0:
            step.keys = combine.suggest_keys(self.start.df, step.right.df) or [combine.new_key()]
        self.refresh()

    # --- data and plans -------------------------------------------------------
    def frames(self) -> list[pd.DataFrame]:
        return [self.start.df] + [step.right.df for step in self.steps]

    def names(self) -> list[str]:
        return [self.start.name] + [step.right.name for step in self.steps]

    def filters(self) -> list[dict]:
        return [self.start.tree] + [step.right.tree for step in self.steps]

    def step_specs(self) -> list[dict]:
        return [step.spec(number + 1) for number, step in enumerate(self.steps)]

    def choose(self, step: JoinStep, entry: dict, **choice) -> None:
        number = self.steps.index(step)
        self.overrides.setdefault((number, entry["side"], entry["column"]), {}).update(choice)
        entry.update(choice)

    def refresh(self) -> None:
        """Recalculates the plans of all the steps (each one starts from the columns of the previous one) and shows them."""
        plans = combine.plan_chain(self.frames(), self.step_specs(), self.names(), self.overrides)
        left = [str(column) for column in self.start.df.columns]
        for number, (step, plan) in enumerate(zip(self.steps, plans)):
            step.title.set_text(f"Join {number + 1}")
            step.remove_button.set_enabled(len(self.steps) > 1)
            step.show(plan, left)
            left = [entry["name"] for entry in plan if entry["keep"]]

    async def compute(self, button: ui.button) -> tuple[pd.DataFrame, list[dict]] | None:
        """Runs the chain of joins: returns the result and the column plan of its last step (None if it fails)."""
        steps, frames, names = self.step_specs(), self.frames(), self.names()
        plans = combine.plan_chain(frames, steps, names, self.overrides)
        out = await run_combination(button, "Join", combine.join_chain, frames, steps, names, self.filters(), plans)
        return None if out is None else (out, plans)

    async def execute(self) -> None:
        computed = await self.compute(self.run_button)
        if computed is not None:
            out, plans = computed
            names = self.names()
            self.result.show(out, "join_" + "_".join(names), sources=combine.column_sources(plans[-1]),
                             note=S.note_for("Join", list(zip(names, self.filters()))))

    async def overwrite_start(self) -> None:
        """Replaces the starting dataset with the result of the joins (undoable from the Struttura tab)."""
        computed = await self.compute(self.apply_button)
        if computed is None:
            return
        out, _ = computed
        start = self.start.name
        if out.empty:
            ui.notify(f"Il join non produce righe: «{start}» non è stato modificato", type="warning")
            return
        S.replace(start, out, "join di " + ", ".join(f"«{name}»" for name in self.names()))
        ui.notify(f"«{start}» sostituito dal join: {len(out):,} righe × {out.shape[1]} colonne".replace(",", "."),
                  type="positive")
        refresh_dataset(start)  # the windows on this dataset must see the new columns


class UnionPanel:
    """Union of two or more datasets: datasets with filters, type and alignment, result columns, execution."""

    def __init__(self, result: ResultPanel) -> None:
        self.result = result
        self.pickers: list[SourcePicker] = []
        self.plan: list[dict] = []
        self.kinds: dict[int, set[str]] = {}  # data types of each plan column (key: id of the entry)
        self.status: dict[int, ui.label] = {}
        self.sources_box = ui.column().classes("w-full gap-2")
        ui.button("Aggiungi dataset (+ union)", icon="add", on_click=lambda: self.add_source()).props("dense flat")
        with ui.card().classes("w-full gap-3"):
            with ui.grid().classes(FORM_GRID):
                self.how = ui.select(combine.UNION_TYPES, value="union_all", label="Tipo di union",
                                     on_change=lambda: self.change_type()).props("dense outlined").classes("w-full sm:col-span-2")
                self.align = ui.select(combine.ALIGNMENTS, value="by_name", label="Allineamento delle colonne",
                                       on_change=lambda: self.reset_plan()).props("dense outlined").classes("w-full sm:col-span-2")
            self.origin = ui.switch(f"Aggiungi la colonna «{combine.ORIGIN}» con il dataset di ogni riga")
        with ui.expansion("Colonne del risultato", icon="view_column", value=True).classes("w-full border rounded"):
            ui.label("Ogni colonna del risultato ha una voce: per ciascun dataset scegli quale colonna vi confluisce "
                     "(vuoto = valori nulli), rinominala, riordinala con le frecce o escludila.").classes("text-xs text-gray-500")
            ui.button("Ordina per nome", icon="sort_by_alpha", on_click=lambda: self.sort_plan()).props("dense flat")
            self.render_plan()
        self.run_button = ui.button("Esegui union", icon="play_arrow", on_click=self.execute).mark("run-union")
        other = next((name for name in S.datasets if name != S.active), S.active)
        for name in (S.active, other):
            self.new_picker(name)
        self.change_type()

    @property
    def frames(self) -> list[pd.DataFrame]:
        return [picker.df for picker in self.pickers]

    @property
    def names(self) -> list[str]:
        return [picker.name for picker in self.pickers]

    # --- datasets ------------------------------------------------------------
    def new_picker(self, name: str) -> None:
        with self.sources_box:
            self.pickers.append(SourcePicker("Dataset", name, lambda: self.reset_plan(), self.remove_source))

    def add_source(self) -> None:
        self.new_picker(next((name for name in S.datasets if name not in self.names), S.active))
        self.reset_plan()

    def remove_source(self, picker: SourcePicker) -> None:
        if len(self.pickers) <= 2:
            ui.notify("Servono almeno due dataset", type="warning")
            return
        self.pickers.remove(picker)
        picker.card.delete()
        self.reset_plan()

    def change_type(self) -> None:
        appending = self.how.value in combine.APPENDING
        self.origin.set_visibility(appending)
        if not appending and self.align.value == "by_name":
            self.align.value = "common"  # INTERSECT and EXCEPT compare whole rows: they need the same columns
        else:
            self.reset_plan()

    # --- column plan ----------------------------------------------------------
    def reset_plan(self) -> None:
        self.plan = combine.union_plan(self.frames, self.align.value)
        self.kinds = {id(entry): combine.entry_kinds(self.frames, entry) for entry in self.plan}
        self.render_plan.refresh()

    @ui.refreshable_method
    def render_plan(self) -> None:
        self.status.clear()
        for position, entry in enumerate(self.plan):
            self.plan_card(position, entry)
        mark_notes(self.plan, self.status, self.note)

    def plan_card(self, position: int, entry: dict) -> None:
        with column_card(self.status, entry, entry["name"]):
            with ui.row().classes("w-full items-center gap-3 flex-wrap"):
                ui.checkbox("Includi nel risultato", value=entry["keep"],
                            on_change=lambda e, entry=entry: self.choose(entry, keep=e.value)).props("dense")
                name = ui.input("Nome nel risultato", value=entry["name"],
                                on_change=lambda e, entry=entry: self.choose(entry, name=e.value)).props(
                    "dense outlined").classes("w-72 max-w-full")
                name.on("blur", lambda: self.render_plan.refresh())
                for icon, step in (("arrow_upward", -1), ("arrow_downward", 1)):
                    ui.button(icon=icon, on_click=lambda position=position, step=step: self.move(position, step)).props(
                        "flat dense")
            with ui.row().classes("w-full gap-3 flex-wrap"):
                for index, picker in enumerate(self.pickers):
                    ui.select(picker.columns, value=entry["sources"][index], label=picker.name, with_input=True,
                              clearable=True,
                              on_change=lambda e, entry=entry, index=index: self.map_column(entry, index, e.value)
                              ).props("dense outlined").classes("min-w-[12rem] flex-1 max-w-full")

    def choose(self, entry: dict, **choice) -> None:
        entry.update(choice)
        mark_notes(self.plan, self.status, self.note)

    def map_column(self, entry: dict, index: int, source: str | None) -> None:
        entry["sources"][index] = source
        self.kinds[id(entry)] = combine.entry_kinds(self.frames, entry)
        mark_notes(self.plan, self.status, self.note)

    def move(self, position: int, step: int) -> None:
        target = position + step
        if 0 <= target < len(self.plan):
            self.plan[position], self.plan[target] = self.plan[target], self.plan[position]
            self.render_plan.refresh()

    def sort_plan(self) -> None:
        self.plan.sort(key=lambda entry: entry["name"].lower())
        self.render_plan.refresh()

    def note(self, entry: dict) -> tuple[str, str]:
        mixed = entry["keep"] and len(self.kinds[id(entry)]) > 1
        return ("tipi diversi: testo", WARNING_STYLE) if mixed else ("", "")

    # --- execution --------------------------------------------------------------
    async def execute(self) -> None:
        spec = {"how": self.how.value, "align": self.align.value, "origin": self.origin.value,
                "filters": [picker.tree for picker in self.pickers], "columns": self.plan}
        out = await run_combination(self.run_button, "Union", combine.union, self.frames, spec, self.names)
        if out is not None:
            self.result.show(out, f"union_{self.names[0]}_x{len(self.pickers)}",
                               note=S.note_for("Union", list(zip(self.names, spec["filters"]))))


def tab_combine() -> None:
    mode = ui.toggle(list(COMBINE_MODES), value="Join").props("no-caps")
    join_box = ui.column().classes("w-full gap-3").bind_visibility_from(mode, "value", value="Join")
    union_box = ui.column().classes("w-full gap-3").bind_visibility_from(mode, "value", value="Union")
    result = ResultPanel("combinato", section="Join / Union")
    with join_box:
        if len(S.datasets) > 1:
            JoinPanel(result)
        else:  # a join of the dataset with itself is rarely wanted: it is built only on request
            with ui.row().classes("w-full items-center gap-3") as notice:
                ui.icon("info", size="20px").classes("text-gray-500")
                ui.label("Carica un secondo dataset per fare un join.").classes("text-sm text-gray-600")

                def show_join() -> None:
                    notice.delete()
                    with join_box:
                        JoinPanel(result)

                ui.button("Join del dataset con sé stesso", on_click=show_join).props("flat no-caps").mark("self-join")

            def on_datasets_change() -> None:  # a second dataset loaded later: the join is ready to use
                if not notice.is_deleted and len(S.datasets) > 1:
                    show_join()

            S.watch_datasets(notice, on_datasets_change)

    def show_union(event) -> None:
        if event.value == "Union" and not union_box.default_slot.children:  # built only when needed
            with union_box:
                UnionPanel(result)

    mode.on_value_change(show_union)


# ===========================================================================
# Calculated columns: format change, formula, if… then
# ===========================================================================

MODES = {"Cambio formato": "convert", "Formula": "formula", "Se… allora": "case"}
NEW_COLUMN, OVERWRITE = "Nuova colonna", "Sovrascrivi colonna"
KIND_ICONS = {"testo": "text_fields", "numero": "tag", "data": "event", "booleano": "toggle_on"}


def insert_text(editor: ui.codemirror, text: str, selection: tuple[int, int] | None = None) -> None:
    """Inserts `text` at the cursor position (in place of the selected text).
    `selection`: start and end of the part of `text` to leave selected (default: cursor after the text)."""
    start, end = selection or (len(text), len(text))
    ui.run_javascript(f"""
        const view = getElement({editor.id}).editor;
        const range = view.state.selection.main;
        view.dispatch({{
            changes: {{from: range.from, to: range.to, insert: {json.dumps(text)}}},
            selection: {{anchor: range.from + {start}, head: range.from + {end}}},
        }});
        view.focus();
    """)


def column_picker(df: pd.DataFrame, editor: ui.codemirror, quote: Callable[[str], str] = fh.quote_column,
                  target: str = "nella formula", marker: str = "column-chip") -> Callable[[pd.DataFrame], None]:
    """Expander with the dataset columns: a click inserts `quote(name)` in the editor, at the cursor.
    Returns the function that shows the columns of another version of the dataset."""
    chips: list[tuple[str, ui.chip]] = []
    shown: list[tuple[str, str]] = []  # (name, type) of the columns drawn
    with ui.expansion("", icon="view_column", value=True).classes("w-full border rounded") as box:
        search = ui.input(placeholder="Cerca colonna").props("dense outlined clearable").classes("w-72")
        ui.label(f"Clic su una colonna per inserirla {target}, alla posizione del cursore.").classes(
            "text-xs text-gray-500")
        row = ui.row().classes("w-full gap-1")

    def show(frame: pd.DataFrame) -> None:
        columns_now = [(str(column), str(dtype)) for column, dtype in frame.dtypes.items()]
        if columns_now == shown:
            return
        shown[:] = columns_now
        box.set_text(f"Colonne ({frame.shape[1]})")
        row.clear()
        chips.clear()
        with row:
            for column, dtype in frame.dtypes.items():
                name, kind = str(column), fh.column_kind(dtype)
                chip = ui.chip(name, icon=KIND_ICONS[kind], color="grey-2", text_color="grey-9",
                               on_click=lambda name=name: insert_text(editor, quote(name)))
                chip.props("dense square").tooltip(kind).mark(marker)
                chips.append((name.lower(), chip))
        filter_chips(search)

    def filter_chips(event) -> None:
        text = (event.value or "").strip().lower()
        for name, chip in chips:
            chip.set_visibility(text in name)

    search.on_value_change(filter_chips)
    show(df)
    return show


def example_view(example: str) -> None:
    """An example with its result: one value, or the sample table with the «risultato» column."""
    result = fh.example_result(example)
    with ui.row().classes("items-center gap-2"):
        ui.label(example).classes("font-mono text-xs bg-gray-100 rounded px-2 py-1")
        ui.icon("arrow_forward", size="16px").classes("text-gray-400")
        ui.label(result if isinstance(result, str) else "sulla tabella di prova:").classes(
            "font-mono text-xs font-medium" if isinstance(result, str) else "text-xs text-gray-500")
    if not isinstance(result, str):
        rows = [{column: fh.format_value(value, quote_text=False) for column, value in row.items()}
                for row in result.to_dict("records")]
        ui.table(rows=rows, columns=[{"name": column, "label": column, "field": column, "align": "left"}
                                     for column in result.columns]).props("dense flat bordered").classes("text-xs")


def function_details(function: fh.Function) -> None:
    """Guide of a function: explanation, arguments, warnings and examples."""
    with ui.column().classes("w-full gap-2 px-4 pb-3"):
        ui.label(function.details).classes("text-sm")
        if function.arguments:
            with ui.grid(columns="auto 1fr").classes("gap-x-4 gap-y-1 text-sm"):
                for name, meaning in function.arguments:
                    ui.label(name).classes("font-mono text-primary")
                    ui.label(meaning)
        if function.notes:
            with ui.row().classes("items-start gap-2 no-wrap bg-amber-50 rounded p-2 text-sm"):
                ui.icon("warning", size="18px").classes("text-amber-700")
                ui.label(function.notes)
        ui.label("Esempi").classes("text-sm font-medium")
        for example in function.examples:
            example_view(example)


def function_card(function: fh.Function, editor: ui.codemirror) -> ui.expansion:
    """Collapsible card of a function: the button on the row inserts it into the formula,
    the guide (built when first opened) explains how it works."""
    card = ui.expansion().classes("w-full border rounded").mark("function-card")
    with card.add_slot("header"):
        with ui.row().classes("w-full items-center gap-3 no-wrap py-1"):
            ui.label(function.syntax).classes("font-mono text-sm")
            ui.label(function.summary).classes("text-sm text-gray-600")
            ui.space()
            ui.button(icon="input").props("flat round dense size=sm color=primary").tooltip(
                "Inserisci nella formula").mark("insert-function").on(
                "click.stop", lambda: insert_text(editor, function.syntax, fh.first_placeholder(function.syntax)))

    def build(event) -> None:
        if event.value and not card.default_slot.children:
            with card:
                function_details(function)

    card.on_value_change(build)
    return card


def formula_guide(editor: ui.codemirror) -> None:
    """Expander with the guide: one card per function, grouped by topic, with search."""
    cards: list[tuple[str, str, ui.expansion]] = []  # (topic, search text, card)
    with ui.expansion("Guida alle formule", icon="menu_book").classes("w-full border rounded"):
        search = ui.input(placeholder="Cerca funzione").props(
            "dense outlined clearable").classes("w-96")
        with ui.tabs().props("dense no-caps align=left").classes("w-full") as topics:
            tabs = {topic: ui.tab(topic) for topic in fh.GUIDE}
        with ui.tab_panels(topics, value=next(iter(fh.GUIDE)), animated=False).classes("w-full"):
            for topic, functions in fh.GUIDE.items():
                with ui.tab_panel(topic).classes("p-0 gap-1"):
                    for function in functions:
                        cards.append((topic, fh.search_text(function), function_card(function, editor)))

    def filter_functions(event) -> None:
        text = (event.value or "").strip().lower()
        found = Counter()
        for topic, haystack, card in cards:
            card.set_visibility(text in haystack)
            found[topic] += text in haystack
        for topic, tab in tabs.items():
            tab.props["label"] = f"{topic} ({found[topic]})" if text else topic

    search.on_value_change(filter_functions)


def formula_editor(df: pd.DataFrame) -> ui.codemirror:
    """Formula editor with the list of columns and the guide."""
    ui.label("Formula SQL").classes("text-sm font-medium")
    editor = ui.codemirror(language="SQL", line_wrapping=True, theme=S.palette.editor).classes("w-full").style(
        "min-height: 80px")
    column_picker(df, editor)
    formula_guide(editor)
    return editor


class CaseRules:
    """«se … allora …» rules of the guided CASE WHEN: each condition is a filter tree."""

    def __init__(self, df: pd.DataFrame) -> None:
        self.df = df
        self.rules: list[dict] = []
        self.box = ui.column().classes("w-full gap-2")
        ui.button("Aggiungi regola", icon="add", on_click=self.add).props("dense flat")
        self.add()

    def add(self) -> None:
        with self.box, ui.column().classes("w-full gap-1 p-2 border rounded-lg") as card:
            rule = {"card": card}
            with ui.row().classes("w-full items-center"):
                ui.label("Se").classes("text-sm font-medium")
                ui.space()
                ui.button(icon="delete", on_click=lambda: self.remove(rule)).props("dense flat size=sm color=negative")
            rule["builder"] = FilterBuilder(self.df)
            rule["value"] = ui.input("allora (vuoto = nullo)").props("dense outlined").classes("w-72")
        self.rules.append(rule)

    def remove(self, rule: dict) -> None:
        self.rules.remove(rule)
        rule["card"].delete()

    def spec(self) -> list[dict]:
        return [{"filters": rule["builder"].tree, "value": rule["value"].value or ""} for rule in self.rules]


def tab_columns(name: str) -> None:
    df = S.datasets[name]
    column_names = [str(c) for c in df.columns]
    with ui.card().classes("w-full"):
        mode = ui.toggle(list(MODES), value="Cambio formato").props("no-caps")

        with ui.row().classes("w-full items-center gap-3").bind_visibility_from(mode, "value", value="Cambio formato"):
            source = ui.select(column_names, label="Colonna da convertire", with_input=True).props("dense outlined").classes("w-52")
            conversion = ui.select(columns.CONVERSIONS, value="Numero", label="Converti in").props("dense outlined").classes("w-44")
            with ui.row().classes("items-center gap-2").bind_visibility_from(
                    conversion, "value", backward=lambda to: to in columns.NUMERIC_CONVERSIONS):
                ui.label("Formato del numero").classes("text-sm text-gray-600")
                decimal = ui.toggle({",": "1.234,56", ".": "1,234.56"}, value=",").props("dense no-caps")

        with ui.column().classes("w-full gap-2").bind_visibility_from(mode, "value", value="Formula"):
            formula = formula_editor(df)

        with ui.column().classes("w-full gap-2").bind_visibility_from(mode, "value", value="Se… allora"):
            rules = CaseRules(df)
            otherwise = ui.input("Altrimenti (vuoto = lascia invariato)").props("dense outlined").classes("w-72")
            ui.label("Per ogni riga vale la prima regola vera. Lascia invariato: resta il valore attuale "
                     "se sovrascrivi, nullo per una nuova colonna.").classes("text-xs text-gray-500")

        ui.separator()
        with ui.row().classes("w-full items-center gap-3"):
            destination = ui.toggle([NEW_COLUMN, OVERWRITE], value=NEW_COLUMN).props("no-caps")
            new_name = ui.input("Nome nuova colonna").props("dense outlined").classes("w-64").bind_visibility_from(
                destination, "value", value=NEW_COLUMN)
            existing = ui.select(column_names, label="Colonna da sovrascrivere", with_input=True).props(
                "dense outlined").classes("w-64").bind_visibility_from(destination, "value", value=OVERWRITE)

    def suggest_target() -> None:
        if source.value:
            new_name.value = columns.default_name(source.value, conversion.value)
            existing.value = source.value

    source.on_value_change(suggest_target)
    conversion.on_value_change(suggest_target)

    def spec() -> dict:
        overwrite = destination.value == OVERWRITE
        return {
            "mode": MODES[mode.value],
            "column": source.value, "to": conversion.value, "decimal": decimal.value,
            "formula": formula.value or "",
            "rules": rules.spec(), "otherwise": otherwise.value or "",
            "target": ((existing.value if overwrite else new_name.value) or "").strip(),
            "overwrite": overwrite,
        }

    async def compute(current: dict) -> pd.DataFrame | None:
        """Calculates on the chosen dataset; warns if the conversion turned some values into nulls."""
        data = S.datasets[name]
        try:
            out = await run.io_bound(columns.calculate, data, current, S.sql_tables(name))
        except Exception as e:  # noqa: BLE001
            notify_error(e)
            return None
        if current["mode"] == "convert":
            lost = columns.lost_values(data[current["column"]], out[current["target"]])
            if len(lost):
                count = f"{len(lost):,}".replace(",", ".")
                examples = ", ".join(map(str, lost.unique()[:5]))
                ui.notify(f"{count} valori non convertiti sono diventati nulli (es.: {examples})",
                          type="warning", multi_line=True, close_button=True)
        return out

    async def preview() -> None:
        current = spec()
        out = await compute(current)
        if out is not None:
            pinned = [current["target"]]
            if current["mode"] == "convert" and not current["overwrite"]:
                pinned.insert(0, current["column"])
            result.show(out, f"{name}_calcolato", pinned=pinned,
                       note=S.note_for(f"Colonna calcolata «{current['target']}»", [(name, None)]))

    async def apply() -> None:
        current = spec()
        out = await compute(current)
        if out is None:
            return
        S.replace(name, out, f"colonna calcolata «{current['target']}»")
        action = "aggiornata" if current["overwrite"] else "aggiunta"
        ui.notify(f"Colonna '{current['target']}' {action} in '{name}': disponibile in tutte le schede",
                  type="positive")
        refresh_dataset(name)  # the windows on this dataset must see the new column

    with ui.row().classes("items-center gap-3"):
        ui.button("Anteprima", icon="visibility", on_click=preview).props("outline").mark("calc-preview")
        ui.button("Applica al dataset", icon="done", on_click=apply).mark("calc-apply")
        ui.label("L'anteprima mostra il dataset risultante, con la colonna calcolata fissata a sinistra.").classes("text-xs text-gray-500")
    result = ResultPanel("calcolato", section="Colonne calcolate")


def console_chart_example(df: pd.DataFrame) -> str:
    """Example code for an ECharts chart from the console, with columns of the dataset."""
    numbers = list(df.select_dtypes("number").columns)
    categories = [c for c in df.columns if c not in numbers]
    x = categories[0] if categories else df.columns[0]
    value = f"[{str(numbers[0])!r}].sum()" if numbers else ".size()"
    return f"""# Grafico: l'ultima riga è un dizionario di opzioni ECharts (esempi su echarts.apache.org/examples).
# Vanno bene anche una figura matplotlib o plotly, se installati.
data = df.groupby({str(x)!r}){value}.sort_values(ascending=False).head(20)
{{
    "height": 480,  # altezza in pixel (facoltativa)
    "title": {{"text": "Esempio"}},
    "tooltip": {{"trigger": "axis"}},
    "xAxis": {{"type": "category", "data": [str(v) for v in data.index]}},
    "yAxis": {{"type": "value"}},
    "series": [{{"type": "bar", "data": data.tolist(), "label": {{"show": True, "position": "top", "fontSize": 12}}}}],
}}
"""


def show_console_chart(box: ui.element, kind: str, value) -> None:
    """Draws in the box the chart returned by the console (see engine.chart_kind)."""
    box.clear()
    with box:
        if kind == "echarts":
            options, height = eng.echarts_options(value)
            options = chart_options.written_chart_defaults(options, S.palette.ink)
            ui.echart(options, theme=style.chart_theme(S.palette)).classes("w-full").style(
                f"height: {height}px").mark("console-chart")
        elif kind == "matplotlib":
            ui.image(eng.figure_image(value)).props("fit=contain").classes("w-full").mark("console-chart")
        else:
            ui.plotly(value).classes("w-full").mark("console-chart")
    box.set_visibility(True)


def console_rows(df: pd.DataFrame, tree: dict) -> pd.DataFrame | None:
    """Rows of the dataset that pass the Console filters; None without filters (df = whole dataset)."""
    return eng.apply_filters(df, tree) if eng.has_conditions(df, tree) else None


def tab_console(name: str) -> None:
    editor_ref: dict = {}

    filters = filter_box(S.datasets[name], expanded=False)
    with ui.row().classes("items-center gap-4"):
        lang = ui.toggle(["SQL", "Python"], value=S.lang).props("no-caps")
        ui.button("Esegui", icon="play_arrow", on_click=lambda: execute()).props("color=primary").mark("run-console")
        ui.button("Esempio di grafico", icon="bar_chart", on_click=lambda: insert_example(console_chart_example)).props(
            "flat no-caps").bind_visibility_from(lang, "value", value="Python").mark("console-example")
        ui.button("Esempio di dashboard", icon="dashboard", on_click=lambda: insert_example(dashboard.example_code)).props(
            "flat no-caps").bind_visibility_from(lang, "value", value="Python").mark("console-dashboard-example")
        ui.label("Ctrl+Invio per eseguire").classes("text-xs text-gray-500")
    hint = ui.label().classes("text-xs text-gray-600")
    scope = ui.label().classes("text-xs text-amber-700").mark("console-scope")  # filtered df: rows and warning

    def update_hint() -> None:
        if name not in S.datasets:  # removed: the window is about to follow another dataset
            return
        if lang.value == "SQL":
            names = ", ".join(f"`{n}`" for n in S.sql_tables(name))
            hint.set_text(f"Tabelle disponibili: {names}   (df = «{name}»)")
        else:
            names = ", ".join(f"datasets[{n!r}]" for n in S.datasets)
            hint.set_text(f"df = «{name}» (il dataset intero). Tutti i dataset: {names}. L'ultima riga può restituire "
                          "una tabella (DataFrame), un grafico (dizionario di opzioni ECharts, figura matplotlib o plotly) "
                          "o una dashboard: Dashboard(df, \"titolo\") con slicer, KPI, grafici, tabelle e pagine.")

    def insert_example(make_example: Callable[[pd.DataFrame], str]) -> None:
        editor = editor_ref["ed"]
        example = make_example(S.datasets[name])
        editor.value = f"{editor.value.rstrip()}\n\n{example}" if editor.value.strip() else example

    async def execute(_=None) -> None:
        code = editor_ref["ed"].value
        S.code[lang.value] = code
        out.set_text("")
        out.classes(remove="text-red-600")
        chart_box.set_visibility(False)
        table_box.set_visibility(True)
        df = S.datasets[name]  # read at every run: the console itself can replace it
        try:
            filtered = await run.io_bound(console_rows, df, filters.tree)
            scope.set_text("" if filtered is None else
                           f"df = {len(filtered):,} righe filtrate su {len(df):,}: ".replace(",", ".")
                           + "le modifiche a df non vengono applicate al dataset")
            if lang.value == "SQL":
                tables = S.sql_tables(name)
                if filtered is not None:
                    tables["df"] = filtered
                res = await run.io_bound(eng.run_sql, code, tables)
                if res is None:
                    out.set_text("Eseguito (nessun risultato).")
                else:
                    result.show(res, "query", note=S.note_for("Console SQL", [(name, filters.tree)]))
            else:
                env = S.prepare_env(name)
                if filtered is not None:
                    env["df"] = filtered
                stdout, val, err = await run.io_bound(eng.run_python, code, env)
                text = stdout
                if err:
                    out.classes(add="text-red-600")
                    text += err
                elif isinstance(val, dashboard.Dashboard):
                    chart_box.clear()
                    with chart_box:
                        view = DashboardView(val, name)
                    chart_box.set_visibility(True)
                    table_box.set_visibility(False)
                    await view.refresh()
                elif (kind := eng.chart_kind(val)) is not None:
                    show_console_chart(chart_box, kind, val)
                    table_box.set_visibility(False)  # with a chart the empty table is not needed
                elif (table := eng.to_display_frame(val)) is not None:
                    result.show(table, "console", note=S.note_for("Console Python", [(name, filters.tree)]))
                elif val is not None:
                    text += repr(val)
                out.set_text(text.rstrip())
                # if the user reassigned df (without filters), update the dataset of the window
                new_df = env.get("df")
                if filtered is not None:
                    if new_df is not filtered:
                        ui.notify("Con i filtri attivi df riassegnato non sostituisce il dataset", type="warning")
                    env["df"] = df  # later runs without filters start again from the dataset
                elif isinstance(new_df, pd.DataFrame) and new_df is not df:
                    S.replace(name, new_df, "df riassegnato dalla console")
                    ui.notify(f"«{name}» aggiornato dalla console", type="info")
                    refresh_dataset(name, keep=out)  # the other windows on the dataset; this one keeps its output
        except Exception as e:  # noqa: BLE001
            out.classes(add="text-red-600")
            out.set_text(str(e))

    with ui.expansion("Assistente AI", icon="auto_awesome").classes("w-full"):
        ai_settings()
        request = ui.textarea("Descrivi cosa vuoi ottenere", placeholder="es. totale importo per regione, solo per il 2024, in ordine decrescente").props("outlined autogrow").classes("w-full")
        generate_button = ui.button("Genera codice", icon="auto_awesome", on_click=lambda: generate()).mark("ai-genera")

    async def generate() -> None:
        if not S.api_key.strip() or not request.value.strip():
            ui.notify("Inserisci la API key e la richiesta", type="warning")
            return
        tables = S.sql_tables(name) if lang.value == "SQL" else {"df": S.datasets[name], **{f"datasets['{n}']": d for n, d in S.datasets.items()}}
        generate_button.props(add="loading")
        try:
            code = await run.io_bound(ai.generate_code, S.api_key.strip(), request.value, lang.value, tables,
                                        S.ai_model, S.ai_effort)
            editor_ref["ed"].value = code
        except Exception as e:  # noqa: BLE001
            notify_error(e)
        finally:
            generate_button.props(remove="loading")

    editor = ui.codemirror(S.code[S.lang], language=S.lang, theme=S.palette.editor,
                           line_wrapping=True, keymap={"Mod-Enter": execute}).classes("w-full").style("min-height: 180px")
    editor_ref["ed"] = editor
    editor_ref["lang"] = lang.value  # language of the code now in this window's editor

    def change_language(e) -> None:
        S.code[editor_ref["lang"]] = editor.value  # not S.lang: another window may have changed it
        S.lang = editor_ref["lang"] = e.value
        editor.language = e.value
        editor.value = S.code[e.value]
        update_hint()

    def quote(column: str) -> str:
        return fh.quote_column(column) if lang.value == "SQL" else repr(column)

    show_columns = column_picker(S.datasets[name], editor, quote, "nel codice", "console-column-chip")
    lang.on_value_change(change_language)
    update_hint()

    def follow_datasets_change() -> None:
        update_hint()
        if name in S.datasets:
            show_columns(S.datasets[name])

    S.watch_datasets(hint, follow_datasets_change)

    # text output (print, errors): theme background, hidden while empty
    out = ui.label().classes("w-full whitespace-pre-wrap font-mono text-sm rounded p-2").style("background: var(--soft)")
    out.bind_visibility_from(out, "text", backward=bool)
    chart_box = ui.card().classes("w-full p-2")
    chart_box.set_visibility(False)
    with ui.column().classes("w-full gap-0") as table_box:
        result = ResultPanel("query", section="Console")


# ===========================================================================
# Dashboards of the console
# ===========================================================================

DASHBOARD_SECTION = "Console"  # number format of the dashboard tables and KPIs


class DashboardView:
    """Draws a dashboard written in the console: slicers at the top, pages as tabs, visuals on a 12-column grid.
    A slicer change, a page change or a click on a chart recalculates the visuals of the page in a thread."""

    def __init__(self, dash: dashboard.Dashboard, name: str, expandable: bool = True) -> None:
        self.dash, self.name, self.expandable = dash, name, expandable
        self.page = 0
        self.version = 0  # a newer calculation discards the older ones
        self.outputs: dict[int, dashboard.Output] = {}
        self.box = ui.column().classes("w-full gap-3")
        self.build()

    def build(self) -> None:
        self.box.clear()
        with self.box:
            with ui.row().classes("w-full items-center gap-3"):
                ui.label(self.dash.title).classes("text-lg font-semibold")
                with ui.row().classes("items-center gap-1") as self.cross_box:
                    ui.icon("filter_alt", color="primary")
                    self.cross_label = ui.label().classes("text-sm")
                    ui.button(icon="close", on_click=self.clear_cross).props(
                        'dense flat round size=sm aria-label="Togli il filtro"').tooltip(
                        "Togli il filtro del grafico").mark("dashboard-cross-clear")
                self.cross_box.set_visibility(False)
                ui.space()
                if self.expandable:
                    ui.button(icon="fullscreen", on_click=self.full_screen).props(
                        'dense flat aria-label="Schermo intero"').tooltip("Schermo intero").mark("full-screen")
            if self.dash.slicers:
                with ui.row().classes("w-full items-end gap-3"):
                    for slicer in self.dash.slicers:
                        self.slicer_control(slicer)
            if len(self.dash.pages) > 1:
                with ui.tabs(value=str(self.page), on_change=self.change_page).props(
                        "dense no-caps align=left").classes("w-full").mark("dashboard-pages"):
                    for index, page in enumerate(self.dash.pages):
                        ui.tab(str(index), page.title)
            self.grid = ui.element("div").classes("dash-grid w-full")

    # --- slicers -------------------------------------------------------------
    def slicer_control(self, slicer: dashboard.Slicer) -> None:
        if slicer.kind != dashboard.RANGE:
            multiple = slicer.kind == dashboard.MULTI
            select = ui.select(slicer.options, value=slicer.value, label=slicer.label, multiple=multiple,
                               with_input=True, clearable=True).props(
                "dense outlined" + (" use-chips" if multiple else "")).classes("min-w-[220px]").mark("dashboard-slicer")

            async def choose(event) -> None:
                slicer.value = event.value or ([] if multiple else None)
                await self.slicers_changed()

            select.on_value_change(choose)
            return
        low, high = slicer.value
        with ui.row().classes("items-end gap-1 no-wrap"):
            if slicer.dates:
                fields = [ui.input(text, value=limit or "").props("dense outlined stack-label type=date")
                          for text, limit in ((f"{slicer.label} da", low), ("a", high))]
            else:
                fields = [ui.number(text, value=limit, placeholder=number_format.format_number(bound, 2))
                          .props("dense outlined").classes("w-32")
                          for text, limit, bound in ((f"{slicer.label} da", low, slicer.bounds[0]),
                                                     ("a", high, slicer.bounds[1]))]

        async def change(_=None) -> None:
            values = tuple(None if field.value in (None, "") else field.value for field in fields)
            if values != tuple(slicer.value):
                slicer.value = values
                await self.slicers_changed()

        for field in fields:
            field.mark("dashboard-slicer")
            if slicer.dates:
                field.on_value_change(change)
            else:  # a number is read when it is complete, not at every key
                field.on("blur", change)
                field.on("keydown.enter", change)

    async def slicers_changed(self) -> None:
        self.dash.clear_selection()  # the clicked element may no longer exist
        await self.refresh()

    async def change_page(self, event) -> None:
        self.page = int(event.value)
        self.dash.clear_selection()
        await self.refresh()

    # --- cross filter --------------------------------------------------------
    async def select(self, visual: dashboard.Visual, event, panel: str) -> None:
        item = getattr(event, "data", None)
        if isinstance(item, dict) and self.dash.select(visual, self.outputs[visual.id],
                                                       {**item, "p": panel} if panel else item):
            await self.refresh()

    async def clear_cross(self) -> None:
        self.dash.clear_selection()
        await self.refresh()

    # --- calculation and drawing ---------------------------------------------
    async def refresh(self) -> None:
        self.version += 1
        version = self.version
        try:
            outputs = await run.io_bound(self.dash.compute, self.page)
        except Exception as e:  # noqa: BLE001
            notify_error(e)
            return
        if version == self.version and not self.grid.is_deleted:
            self.outputs = outputs
            self.draw()

    def draw(self) -> None:
        cross = self.dash.cross
        self.cross_label.set_text(cross.text if cross else "")
        self.cross_box.set_visibility(cross is not None)
        look = {**style.chart_view(S.palette), "locale": S.format_of(DASHBOARD_SECTION).locale}
        self.grid.clear()
        with self.grid:
            for visual in self.dash.pages[self.page].visuals:
                with ui.card().classes("dash-card").style(
                        f"grid-column: span {visual.width} / span {visual.width}").mark("dashboard-visual"):
                    if visual.title:
                        ui.label(visual.title).classes("text-sm font-semibold")
                    output = self.outputs[visual.id]
                    if output.error:
                        ui.label(output.error).classes("text-sm text-red-600 whitespace-pre-wrap")
                    else:
                        DASHBOARD_DRAWERS[visual.kind](self, visual, output.value, look)

    def draw_kpi(self, visual: dashboard.Visual, value: float | None, look: dict) -> None:
        style_name = S.format_of(DASHBOARD_SECTION).style
        text = "—" if value is None else number_format.format_number(value, visual.spec["decimals"], style_name)
        ui.label(text + visual.spec["suffix"]).classes("dash-kpi").mark("dashboard-kpi")

    def draw_chart(self, visual: dashboard.Visual, data: charts.ChartData, look: dict) -> None:
        for label, options in dashboard.chart_panels(visual, data, look):
            ui.echart(options, on_point_click=lambda e, panel=label: self.select(visual, e, panel),
                      theme=style.chart_theme(S.palette)).classes("w-full").style(
                f"height: {visual.height}px").mark("dashboard-chart")
        if data.notes:
            ui.label(" · ".join(data.notes)).classes("text-xs text-gray-500")

    def table_panel(self, visual: dashboard.Visual, table: pd.DataFrame, total_rows: set[int] = frozenset()) -> None:
        panel = ResultPanel(f"dashboard_{visual.title or 'tabella'}", fit_height=True, section=DASHBOARD_SECTION)
        panel.show(table, total_rows=total_rows, reveal=False,
                   note=S.note_for(f"Dashboard «{self.dash.title}»", [(self.name, self.dash.filter_tree())]))

    def draw_table(self, visual: dashboard.Visual, table: pd.DataFrame, look: dict) -> None:
        self.table_panel(visual, table)

    def draw_pivot(self, visual: dashboard.Visual, table: pivot.PivotTable, look: dict) -> None:
        totals = np.flatnonzero((table.row_codes == pivot.TOTAL).any(axis=1).to_numpy())
        self.table_panel(visual, table.frame, set(totals.tolist()))

    def draw_python(self, visual: dashboard.Visual, result: tuple[str, object], look: dict) -> None:
        kind, value = result
        if kind == "table":
            self.table_panel(visual, value)
        elif kind == "text":
            ui.label(value).classes("w-full whitespace-pre-wrap font-mono text-sm")
        else:
            if kind == "echarts":
                value = {"height": visual.height, **value}
            show_console_chart(ui.element("div").classes("w-full"), kind, value)

    def draw_text(self, visual: dashboard.Visual, text: str, look: dict) -> None:
        ui.markdown(text).classes("w-full")

    # --- full screen ---------------------------------------------------------
    def full_screen(self) -> None:
        """The same dashboard in a window as big as the screen; on closing, this view follows its choices."""
        with ui.dialog().props("maximized") as dialog, ui.card().classes("w-full"):
            with ui.row().classes("w-full justify-end"):
                ui.button(icon="close", on_click=dialog.close).props('dense flat round aria-label="Chiudi"')
            view = DashboardView(self.dash, self.name, expandable=False)
            view.page = self.page
            view.build()

        async def closed(event) -> None:
            if not event.value:
                self.page = view.page
                self.build()
                await self.refresh()
                dialog.delete()  # last: the handler belongs to the dialog

        dialog.on_value_change(closed)
        dialog.open()
        background_tasks.create(view.refresh())


DASHBOARD_DRAWERS = {"kpi": DashboardView.draw_kpi, "chart": DashboardView.draw_chart,
                     "table": DashboardView.draw_table, "pivot": DashboardView.draw_pivot,
                     "python": DashboardView.draw_python, "text": DashboardView.draw_text}


# ===========================================================================
# Charts
# ===========================================================================

CHART_DATA, SOURCE_ROWS = "Dati del grafico", "Righe di origine"
CHART_BUILDER = "Costruzione del grafico"
PANELS_PER_ROW = {1: "1", 2: "2", 3: "3", 4: "4"}
DEFAULT_PANELS_PER_ROW = 2
CHART_HEIGHTS = {360: "Bassa", 480: "Media", 640: "Alta", 820: "Molto alta"}


class ChartLook:
    """Choices that change only the look of the charts (labels, decimals, height, title, compact numbers,
    smooth lines, average): at every change it calls `on_change`, without recalculating the data. Used by Charts and Statistics."""

    def __init__(self, on_change: Callable[[], None], height: int = 480) -> None:
        with ui.grid().classes(f"{FORM_GRID} items-center"):
            self.labels = ui.select(list(chart_options.LABEL_MODES), value=chart_options.AUTO_LABELS,
                                    label="Etichette dati").props("dense outlined").classes("w-full")
            self.decimals = ui.select(chart_options.DECIMALS, value=chart_options.AUTO_DECIMALS,
                                      label="Decimali").props("dense outlined").classes("w-full")
            low, high = chart_options.LABEL_SIZE_RANGE
            self.label_size = ui.number("Dimensione etichette (px)", value=chart_options.DEFAULT_LABEL_SIZE, min=low,
                                        max=high, step=1, precision=0).props("dense outlined").classes("w-full")
            self.number_style = ui.select(
                {name: f"{name} ({style.example})" for name, style in number_format.STYLES.items()},
                value=number_format.ITALIAN, label="Separatori numeri").props("dense outlined").classes("w-full")
            self.height = ui.select(CHART_HEIGHTS, value=height, label="Altezza").props("dense outlined").classes("w-full")
            self.title = ui.input("Titolo del grafico").props("dense outlined").classes("w-full")
        with ui.row().classes("w-full items-center gap-x-8 gap-y-1"):
            self.compact = ui.switch("Numeri compatti (1,2 Mln)")
            self.smooth = ui.switch("Linee smussate")
            self.average = ui.switch("Linea della media")
        for control in (self.labels, self.label_size, self.decimals, self.number_style, self.compact, self.smooth,
                        self.average, self.height):
            control.on_value_change(on_change)
        self.title.on("blur", on_change)
        self.title.on("keydown.enter", on_change)

    @property
    def controls(self) -> dict[str, ui.element]:
        """Choices that the chart AI fills in too (label size and height stay the user's)."""
        return {"labels": self.labels, "decimals": self.decimals, "compact": self.compact, "smooth": self.smooth,
                "average": self.average, "title": self.title}

    def show_only(self, fields: set[str]) -> None:
        """Shows the optional choices (labels, smooth lines, average) only if the chart uses them."""
        for name in ("labels", "smooth", "average"):
            self.controls[name].set_visibility(name in fields)
        self.label_size.set_visibility("labels" in fields)

    def settings(self, title: str | None = None) -> dict:
        """Fields of chart_options.View with the current choices and the colors of the active look;
        `title` replaces the written title."""
        settings = {name: control.value for name, control in self.controls.items()}
        settings["title"] = (settings["title"] or "") if title is None else title
        return {**settings, "label_size": chart_options.label_size(self.label_size.value),
                "locale": number_format.STYLES[self.number_style.value].locale, **style.chart_view(S.palette)}


class ChartTab:
    """Charts tab: filters, choices on data and display, chart and the data behind the chart."""

    def __init__(self, name: str, filters: FilterBuilder) -> None:
        self.name = name
        self.df = df = S.datasets[name]
        self.data: charts.ChartData | None = None
        self.selection: dict | None = None  # element clicked on the chart
        self.date_columns = {str(c) for c in df.columns if pd.api.types.is_datetime64_any_dtype(df[c])}
        column_names = [str(c) for c in df.columns]

        self.filters = filters  # of the window: shared with the other sub-windows
        # all the choices in one expander: closed, only the chart (and its data) take up space
        with ui.expansion(CHART_BUILDER, icon="tune", value=True).classes(
                "w-full border rounded").mark("chart-builder") as self.builder:
            with ui.column().classes("w-full gap-3"):
                self.ai_controls()
                with ui.card().classes("w-full gap-3"):
                    ui.label("Dati").classes("text-xs font-medium uppercase tracking-wide text-gray-500")
                    self.data_controls(column_names)
                    ui.separator()
                    ui.label("Aspetto").classes("text-xs font-medium uppercase tracking-wide text-gray-500")
                    self.look = ChartLook(self.draw)
                    self.panel_look()
                with ui.row().classes("items-center gap-3"):
                    ui.button("Crea grafico", icon="insights", on_click=self.build).mark("run-chart")
                    ui.label("Tipo di grafico e visualizzazione si aggiornano subito; colonne, Top N, pannelli e "
                             "filtri con «Crea grafico». Clic su un elemento del grafico per vederne le righe.").classes(
                        "text-xs text-gray-500")
        self.chart_box = ui.card().classes("w-full p-2")
        S.add_themed(self)
        self.notes = ui.label().classes("text-xs text-amber-700")
        self.data_table()
        self.update_controls()
        self.draw()

    # --- building the interface ----------------------------------------------
    def ai_controls(self) -> None:
        with ui.expansion("Costruisci il grafico con l'AI", icon="auto_awesome").classes("w-full border rounded"):
            ai_settings()
            self.ai_request = ui.textarea("Descrivi il grafico che vuoi", placeholder="es. importo totale per regione e prodotto, "
                                          "barre in pila").props("outlined autogrow").classes("w-full")
            self.ai_button = ui.button("Costruisci grafico", icon="auto_awesome", on_click=self.ai_build).mark("ai-chart")

    @staticmethod
    def column_select(column_names: list[str], label: str) -> ui.select:
        return ui.select(column_names, label=label, with_input=True, clearable=True).props("dense outlined").classes("w-full")

    def data_controls(self, column_names: list[str]) -> None:
        with ui.grid().classes(FORM_GRID):
            self.chart_type = ui.select(charts.TYPE_OPTIONS, value="bar", label="Tipo di grafico", with_input=True,
                                        on_change=self.change_type).props("dense outlined").classes("w-full")
            self.x_column = self.column_select(column_names, "Asse X / categorie")
            self.date_unit = ui.select(list(charts.DATE_UNITS), value=charts.DEFAULT_DATE_UNIT,
                                       label="Raggruppa le date per").props("dense outlined").classes("w-full")
            self.bins = ui.number("Numero di classi", value=charts.DEFAULT_BINS, min=2, max=charts.MAX_BINS,
                                  step=1, precision=0).props("dense outlined").classes("w-full")
            self.y_column = self.column_select(column_names, "Asse Y (colonna numerica)")
            self.size_column = self.column_select(column_names, "Dimensione delle bolle")
            self.series_column = self.column_select(column_names, "Serie / colore")
            self.panel_column = self.column_select(column_names, "Dividi in pannelli per")
            self.panel_date_unit = ui.select(list(charts.DATE_UNITS), value=charts.DEFAULT_PANEL_DATE_UNIT,
                                             label="Raggruppa le date dei pannelli per").props("dense outlined").classes("w-full")
        with ui.column().classes("w-full gap-1") as self.measures_box:
            self.measures = AggregationList(column_names, "Valori (nessuno = numero di righe)", aligned=True, df=self.df)
        with ui.grid().classes(f"{FORM_GRID} items-center") as self.ranking_box:
            self.top_n = ui.number("Mostra solo i primi (0 = tutti)", value=0, min=0, step=1,
                                   precision=0).props("dense outlined").classes("w-full")
            self.top_from = ui.select([charts.TOP, charts.BOTTOM], value=charts.TOP,
                                      label="In base ai valori").props("dense outlined").classes("w-full")
            self.sort = ui.select(charts.SORT_OPTIONS, value=charts.AUTO,
                                  label="Ordine dell'asse X").props("dense outlined").classes("w-full")
            self.others = ui.switch(f"Raggruppa gli altri in «{charts.OTHERS}»")
        self.x_column.on_value_change(self.update_controls)
        self.panel_column.on_value_change(self.update_controls)

    def panel_look(self) -> None:
        """Look of a chart split into panels: panels per row and shared value scale (redrawn at once)."""
        with ui.row().classes("w-full items-center gap-x-8 gap-y-1") as self.panel_look_box:
            self.panel_columns = ui.select(PANELS_PER_ROW, value=DEFAULT_PANELS_PER_ROW, label="Pannelli per riga",
                                           on_change=self.draw).props("dense outlined").classes("w-48")
            self.same_scale = ui.switch("Stessa scala in tutti i pannelli", value=True, on_change=self.draw)

    def data_table(self) -> None:
        with ui.expansion(CHART_DATA, icon="table_view").classes("w-full border rounded") as self.data_box:
            with ui.row().classes("w-full items-center gap-3"):
                self.table_mode = ui.toggle([CHART_DATA, SOURCE_ROWS], value=CHART_DATA,
                                            on_change=self.show_table).props("no-caps dense")
                self.selection_label = ui.label().classes("text-sm text-gray-600")
                self.clear_button = ui.button("Tutte le righe", icon="clear", on_click=lambda: self.select(None)).props(
                    "dense flat size=sm")
                self.clear_button.set_visibility(False)
            self.result = ResultPanel("grafico", section="Grafici")

    # --- state of the choices -------------------------------------------------
    @property
    def chart(self) -> charts.ChartType:
        return charts.CHART_TYPES[self.chart_type.value or "bar"]

    def update_controls(self) -> None:
        """Shows only the choices that the chart type uses."""
        chart = self.chart
        fields = charts.used_fields(chart)
        self.date_unit.set_visibility("date_unit" in fields and self.x_column.value in self.date_columns)
        self.bins.set_visibility("bins" in fields)
        self.y_column.set_visibility("y" in fields)
        self.size_column.set_visibility("size" in fields)
        self.series_column.set_visibility("series" in fields)
        panels = bool(self.panel_column.value)
        self.panel_date_unit.set_visibility(panels and self.panel_column.value in self.date_columns)
        self.panel_look_box.set_visibility(panels)
        self.same_scale.set_visibility("same_scale" in fields)
        self.measures_box.set_visibility("measures" in fields)
        self.ranking_box.set_visibility("top_n" in fields)
        self.sort.set_visibility("sort" in fields)
        self.look.show_only(fields)

    def spec(self) -> dict:
        return {
            "chart": self.chart_type.value, "x": self.x_column.value, "series": self.series_column.value,
            "y": self.y_column.value, "size": self.size_column.value, "measures": self.measures.items,
            "date_unit": self.date_unit.value, "bins": self.bins.value,
            "top_n": self.top_n.value, "top_from": self.top_from.value, "others": self.others.value,
            "sort": self.sort.value, "panels": self.panel_column.value, "panel_date_unit": self.panel_date_unit.value,
            "filters": self.filters.tree,
        }

    def apply(self, settings: dict) -> None:
        """Fills the controls with choices that are already valid (chart_ai.normalize); filters and height stay as they are."""
        self.data = None  # the type change must not rebuild the chart halfway through filling in
        controls = {"x": self.x_column, "series": self.series_column, "y": self.y_column, "size": self.size_column,
                    "panels": self.panel_column, "panel_date_unit": self.panel_date_unit,
                    "date_unit": self.date_unit, "bins": self.bins, "top_n": self.top_n, "top_from": self.top_from,
                    "others": self.others, "sort": self.sort, **self.look.controls,
                    "chart": self.chart_type}  # the type last
        for key, control in controls.items():
            control.value = settings[key]
        self.measures.items[:] = settings["measures"]
        self.measures.render.refresh()

    async def ai_build(self) -> None:
        if not S.api_key.strip() or not self.ai_request.value.strip():
            ui.notify("Inserisci la API key e la richiesta", type="warning")
            return
        self.ai_button.props(add="loading")
        try:
            settings = await run.io_bound(chart_ai.generate_settings, S.api_key.strip(), self.ai_request.value,
                                          self.df, S.ai_model, S.ai_effort)
            self.apply(settings)
            await self.build()
        except Exception as e:  # noqa: BLE001
            notify_error(e)
        finally:
            self.ai_button.props(remove="loading")

    # --- actions -------------------------------------------------------------
    async def change_type(self) -> None:
        self.update_controls()
        if self.data is not None:  # with a chart already drawn the new type shows up right away
            await self.build()

    async def build(self) -> None:
        try:
            data = await run.io_bound(charts.prepare, self.df, self.spec())
        except Exception as e:  # noqa: BLE001
            notify_error(e)
            return
        self.data, self.selection = data, None
        self.builder.props["caption"] = self.summary()
        self.builder.update()
        self.notes.set_text(" · ".join(data.notes))
        self.notes.set_visibility(bool(data.notes))
        self.draw()
        self.show_table()
        scroll_into_view(self.chart_box)

    def draw(self) -> None:
        self.chart_box.clear()
        with self.chart_box:
            if self.data is None:
                with ui.row().classes("w-full items-center justify-center gap-2 py-16 text-gray-500"):
                    ui.icon("insights", size="32px")
                    ui.label("Scegli il tipo di grafico e le colonne, poi premi «Crea grafico».")
                return
            settings = self.look.settings()
            try:
                panels = chart_options.build_panels(self.data, settings, self.same_scale.value)
            except Exception as e:  # noqa: BLE001
                notify_error(e)
                return
            columns = self.panel_columns.value if self.data.panels else 1
            if self.data.panels and settings["title"].strip():  # one title above the panels
                ui.label(settings["title"].strip()).classes("text-base font-semibold px-2")
            with ui.grid().classes(f"w-full gap-2 grid-cols-1 lg:grid-cols-{columns}"):
                for label, options in panels:
                    ui.echart(options, on_point_click=lambda e, panel=label: self.select(e, panel),
                              theme=style.chart_theme(S.palette)).classes("w-full").style(
                        f"height: {self.look.height.value}px").mark("chart")

    def summary(self) -> str:
        """Short description of the drawn chart, in the title of the closed expander."""
        parts = [self.chart.label, self.data.x_name or "", self.data.series_name]
        if self.data.panels:
            parts.append(f"un pannello per {self.data.panel_name}")
        return " · ".join(part for part in parts if part)

    def select(self, event, panel: str = "") -> None:
        """Click on a chart element: its source rows in the table (None = all the rows)."""
        item = getattr(event, "data", None)
        self.selection = ({**item, "p": panel} if panel else item) if isinstance(item, dict) else None
        if self.selection is not None:
            self.data_box.open()
        if self.selection is not None and self.table_mode.value != SOURCE_ROWS:
            self.table_mode.value = SOURCE_ROWS  # the value change updates the table
        else:
            self.show_table()

    def show_table(self) -> None:
        if self.data is None:
            return
        drilled = self.table_mode.value == SOURCE_ROWS and self.selection is not None
        if self.table_mode.value == CHART_DATA:
            self.result.show(self.data.table, f"grafico_{self.name}", reveal=False,
                             note=S.note_for("Dati del grafico", [(self.name, self.filters.tree)]))
        else:
            rows = charts.drill(self.data, self.selection) if drilled else self.data.rows
            self.result.show(rows, f"{self.name}_righe_grafico", reveal=False,
                             note=S.note_for("Righe del grafico", [(self.name, self.filters.tree)]))
        text = f"Selezione: {charts.selection_text(self.data, self.selection)}" if drilled else ""
        self.selection_label.set_text(text)
        self.clear_button.set_visibility(drilled)


# ===========================================================================
# Statistics
# ===========================================================================

STAT_CHART_HEIGHT = 360  # one of the CHART_HEIGHTS


class StatsTab:
    """Statistics tab: analysis and its choices (only the ones it uses), then charts and tables of the result."""

    def __init__(self, name: str) -> None:
        self.name = name
        self.df = df = S.datasets[name]
        self.result: stats.StatResult | None = None
        self.filters = filter_box(df, expanded=False)
        with ui.card().classes("w-full gap-3"):
            self.analysis = ui.select(stats.ANALYSIS_OPTIONS, value="describe", label="Analisi", with_input=True,
                                      on_change=self.update_controls).props("dense outlined").classes("w-full sm:w-96")
            self.description = ui.label().classes("text-xs text-gray-500")
            with ui.grid().classes(FORM_GRID):
                self.controls = self.build_controls([str(c) for c in df.columns], stats.numeric_columns(df))
        self.run_button = ui.button("Calcola", icon="query_stats", on_click=self.compute).mark("run-stats")
        # under «Calcola» and shown with the result: its choices change with the charts, the button stays still
        with ui.card().classes("w-full gap-3") as self.look_box:
            ui.label("Aspetto dei grafici").classes("text-xs font-medium uppercase tracking-wide text-gray-500")
            self.look = ChartLook(self.draw, height=STAT_CHART_HEIGHT)
        self.look_box.set_visibility(False)
        self.notes = ui.label().classes("text-xs text-amber-700")
        self.notes.set_visibility(False)
        self.chart_box = ui.grid().classes("w-full gap-3 grid-cols-1 xl:grid-cols-2")
        S.add_themed(self)
        self.tables_box = ui.column().classes("w-full gap-2")
        self.look.show_only(set())  # optional choices appear with the charts that use them
        self.update_controls()

    # --- controls -------------------------------------------------------------
    @staticmethod
    def build_controls(names: list[str], numbers: list[str]) -> dict[str, ui.element]:
        """One control for each choice of stats.FIELD_LABELS; update_controls sets the labels."""
        def columns(options: list[str], multiple: bool = False) -> ui.select:
            control = ui.select(options, multiple=multiple, with_input=True, clearable=True).props("dense outlined")
            return control.classes("w-full sm:col-span-2").props("use-chips") if multiple else control.classes("w-full")

        def choice(options, value) -> ui.select:
            return ui.select(options, value=value).props("dense outlined").classes("w-full")

        def number(value: float, **limits) -> ui.number:
            return ui.number(value=value, **limits).props("dense outlined").classes("w-full")

        controls = {
            "columns": columns(names, multiple=True), "column": columns(numbers), "column2": columns(numbers),
            "group": columns(names), "category": columns(names), "category2": columns(names),
            "y": columns(names), "x": columns(names, multiple=True), "date": columns(names),
            "method": choice(stats.CORRELATION_METHODS, "pearson"),
            "outlier_method": choice({key: method.label for key, method in stats.OUTLIER_METHODS.items()}, "iqr"),
            "threshold": number(stats.OUTLIER_METHODS["iqr"].threshold, min=0, step=0.1),
            "confidence": choice(stats.CONFIDENCE_LEVELS, stats.DEFAULT_CONFIDENCE),
            "mu": number(0),
            "bins": number(stats.DEFAULT_BINS, min=2, max=stats.MAX_BINS, step=1, precision=0),
            "time_unit": choice(list(stats.TIME_UNITS), stats.DEFAULT_TIME_UNIT),
            "time_agg": choice(list(stats.TIME_AGGREGATIONS), "somma"),
            "period": number(0, min=0, step=1, precision=0),
            "lags": number(stats.DEFAULT_LAGS, min=1, step=1, precision=0),
        }
        controls["outlier_method"].on_value_change(
            lambda e: controls["threshold"].set_value(stats.OUTLIER_METHODS[e.value].threshold))
        return controls

    def update_controls(self) -> None:
        """Shows only the choices of the analysis, with its labels."""
        key = self.analysis.value or "describe"
        analysis = stats.ANALYSES[key]
        self.description.set_text(analysis.description)
        for name, control in self.controls.items():
            control.set_visibility(name in analysis.fields)
            control.props["label"] = stats.field_label(key, name)
            control.update()

    def spec(self) -> dict:
        key = self.analysis.value
        return {"analysis": key, "filters": self.filters.tree,
                **{name: self.controls[name].value for name in stats.ANALYSES[key].fields}}

    # --- calculation and result -----------------------------------------------
    async def compute(self) -> None:
        if not self.analysis.value:
            ui.notify("Scegli l'analisi", type="warning")
            return
        self.run_button.props(add="loading")
        try:
            self.result = await run.io_bound(stats.run, self.df, self.spec())
        except Exception as e:  # noqa: BLE001
            notify_error(e)
            return
        finally:
            self.run_button.props(remove="loading")
        self.notes.set_text(" · ".join(self.result.notes))
        self.notes.set_visibility(bool(self.result.notes))
        self.look.show_only(stats_charts.view_fields(self.result.plots))
        self.look_box.set_visibility(bool(self.result.plots))
        self.draw()
        self.show_tables()
        scroll_into_view(self.chart_box if self.result.plots else self.tables_box)

    def draw(self) -> None:
        self.chart_box.clear()
        if self.result is None:
            return
        plots = self.result.plots
        custom_title = (self.look.title.value or "").strip()
        with self.chart_box:
            for plot in plots:
                title = plot.title
                if custom_title:  # with several charts the written title comes before the title of each one
                    title = f"{custom_title} — {plot.title}" if len(plots) > 1 else custom_title
                view = chart_options.View(**self.look.settings(title))
                with ui.card().classes("w-full p-2" + (" xl:col-span-2" if len(plots) == 1 else "")):
                    ui.echart(stats_charts.build_options(plot, view), theme=style.chart_theme(S.palette)).classes(
                        "w-full").style(f"height: {self.look.height.value}px").mark("stat-chart")

    def show_tables(self) -> None:
        """One tab per table, each with its own result panel (export, save as dataset)."""
        self.tables_box.clear()
        names = list(self.result.tables)
        with self.tables_box:
            with ui.tabs(value=names[0]).props("dense no-caps align=left").classes("w-full") as tabs:
                for name in names:
                    ui.tab(name)
            with ui.tab_panels(tabs, value=names[0], animated=False).classes("w-full"):
                for name, table in self.result.tables.items():
                    with ui.tab_panel(name).classes("p-0"):
                        note = S.note_for(f"Statistica: {self.analysis.value} — {name}", [(self.name, self.filters.tree)])
                        ResultPanel(f"{self.analysis.value}_{self.name}_{name}", precise=True,
                                    fit_height=True, section="Statistica").show(table, note=note, reveal=False)


# ===========================================================================
# Excel: split and report
# ===========================================================================

EXCEL_MODES = ("Split", "Report")
DESTINATIONS = {"download": "Scarica (ZIP con più file)", "folder": "Salva in una cartella del PC"}


def refresh_report() -> None:
    panel = S.report_panel
    if panel is not None and not panel.card.is_deleted:
        panel.render.refresh()


async def run_excel(button: ui.button, function, *args):
    """Creates the files in a thread with the button in loading state; None if there is an error (already notified)."""
    button.props(add="loading")
    try:
        return await run.io_bound(function, *args)
    except Exception as e:  # noqa: BLE001
        notify_error(e)
        return None
    finally:
        button.props(remove="loading")


class OutputSettings:
    """Formatting and destination of the files, shared by Split and Report."""

    def __init__(self) -> None:
        with ui.card().classes("w-full gap-3"):
            ui.label("Formattazione e destinazione").classes("text-xs font-medium uppercase tracking-wide text-gray-500")
            with ui.grid().classes(FORM_GRID):
                self.table_style = ui.select(list(xw.TABLE_STYLES), value="Blu", label="Stile della tabella",
                                             on_change=self.update_freeze).props("dense outlined").classes("w-full")
                self.conditional = ui.select(xw.CONDITIONAL, value="none", label="Colonne numeriche").props(
                    "dense outlined").classes("w-full")
                self.destination = ui.select(DESTINATIONS, value="download", label="Destinazione").props(
                    "dense outlined").classes("w-full")
                self.folder = ui.input("Cartella", placeholder=r"C:\report\vendite").bind_value(S, "excel_folder").props(
                    "dense outlined").classes("w-full").bind_visibility_from(self.destination, "value", value="folder")
            with ui.row().classes("w-full items-center gap-x-8 gap-y-1"):
                self.totals = ui.switch("Riga dei totali (formule)")
                self.red_negatives = ui.switch("Negativi in rosso")
                self.index = ui.switch("Foglio indice con i link (con più fogli)")
                self.freeze = ui.switch("Blocca l'intestazione", value=True)

    def update_freeze(self) -> None:
        """The plain range never freezes the header: the choice applies only with the Table."""
        self.freeze.set_visibility(xw.TABLE_STYLES.get(self.table_style.value) is not None)

    def style(self) -> xw.ExcelStyle:
        return xw.ExcelStyle(self.table_style.value, self.totals.value, self.conditional.value,
                             self.red_negatives.value, self.index.value, self.freeze.value)

    async def deliver(self, files: dict[str, bytes], zip_name: str) -> None:
        """Downloads the file (a ZIP with several files) or saves them to the chosen folder."""
        try:
            if self.destination.value == "folder":
                saved = await run.io_bound(xw.save_to_folder, files, self.folder.value or "")
                ui.notify(f"{len(saved)} file salvati in {saved[0].parent}", type="positive")
            elif len(files) == 1:
                name, content = next(iter(files.items()))
                ui.download.content(content, name)
            else:
                ui.download.content(await run.io_bound(xw.zip_bytes, files), f"{zip_name}.zip")
                ui.notify(f"{len(files)} file nello ZIP {zip_name}.zip", type="positive")
        except Exception as e:  # noqa: BLE001
            notify_error(e)


class SplitPanel:
    """A dataset split into several files and sheets: by column values or in blocks of rows."""

    def __init__(self, output: OutputSettings) -> None:
        self.output = output
        with ui.card().classes("w-full gap-3"):
            self.source = SourcePicker("Dataset da dividere", S.active, self.dataset_changed)
            self.mode = ui.toggle(excel_split.MODES, value="values", on_change=self.update_visibility).props("no-caps")
            with ui.grid().classes(FORM_GRID) as self.values_box:
                self.files_by = self.columns_select("Un file per ogni valore di")
                self.sheets_by = self.columns_select("Un foglio per ogni valore di")
            with ui.grid().classes(FORM_GRID) as self.rows_box:
                self.rows_per_part = ui.number("Righe per blocco", value=excel_split.DEFAULT_ROWS_PER_PART, min=1,
                                               step=1000, precision=0).props("dense outlined").classes("w-full")
                self.rows_into = ui.select(excel_split.ROWS_INTO, value="files", label="Blocchi").props(
                    "dense outlined").classes("w-full")
            with ui.grid().classes(FORM_GRID):
                # short labels (longer ones are cut in the grid): the empty case is in the placeholder or tooltip
                self.file_pattern = ui.input("Nome dei file", placeholder="vuoto = automatico, es. vendite_{regione}").props(
                    "dense outlined").classes("w-full")
                self.sheet_pattern = ui.input("Nome dei fogli", placeholder="vuoto = automatico, es. {prodotto}").props(
                    "dense outlined").classes("w-full")
                self.columns = self.columns_select("Colonne da scrivere")
                self.columns.tooltip("Vuoto = tutte le colonne")
            self.drop_keys = ui.switch("Togli dai file le colonne usate per dividere")
            ui.label(f"Segnaposto nei nomi: {{colonna}} = valore della colonna, {{{excel_split.NUMBER}}} = numero "
                     f"progressivo, {{{excel_split.NAME}}} = nome del dataset.").classes("text-xs text-gray-500")
        with ui.row().classes("items-center gap-3"):
            ui.button("Anteprima", icon="preview", on_click=self.preview).props("outline").mark("split-preview")
            self.run_button = ui.button("Genera file", icon="call_split", on_click=self.execute).mark("run-split")
        self.result = ResultPanel("anteprima_split", fit_height=True, section="Excel")
        self.update_visibility()

    def columns_select(self, label: str) -> ui.select:
        return ui.select(self.source.columns, multiple=True, label=label, with_input=True, clearable=True).props(
            "dense outlined use-chips").classes("w-full sm:col-span-2")

    def dataset_changed(self) -> None:
        for control in (self.files_by, self.sheets_by, self.columns):
            control.set_options(self.source.columns, value=[])

    def update_visibility(self) -> None:
        by_values = self.mode.value == "values"
        self.values_box.set_visibility(by_values)
        self.rows_box.set_visibility(not by_values)
        self.drop_keys.set_visibility(by_values)

    def spec(self) -> dict:
        return {"mode": self.mode.value, "files_by": list(self.files_by.value or []),
                "sheets_by": list(self.sheets_by.value or []), "rows_per_part": self.rows_per_part.value,
                "rows_into": self.rows_into.value, "file_pattern": self.file_pattern.value.strip(),
                "sheet_pattern": self.sheet_pattern.value.strip(), "columns": list(self.columns.value or []),
                "drop_keys": self.drop_keys.value, "filters": self.source.tree, "name": self.source.name}

    async def preview(self) -> None:
        try:
            plan = await run.io_bound(excel_split.split_plan, self.source.df, self.spec())
        except Exception as e:  # noqa: BLE001
            notify_error(e)
            return
        self.result.show(excel_split.plan_table(plan), f"anteprima_split_{self.source.name}")
        sheets = sum(len(part.sheets) for part in plan)
        ui.notify(f"Anteprima: {len(plan)} file, {sheets} fogli", type="info")

    async def execute(self) -> None:
        files = await run_excel(self.run_button, excel_split.split, self.source.df, self.spec(), self.output.style())
        if files is not None:
            await self.output.deliver(files, f"{self.source.name}_split")


class ReportPanel:
    """Tables collected with «Aggiungi al report»: order, sheet names, titles and notes; then a single file."""

    def __init__(self, output: OutputSettings) -> None:
        S.report_panel = self
        self.output = output
        with ui.card().classes("w-full gap-3") as self.card:
            with ui.row().classes("w-full items-center gap-3"):
                ui.input("Nome del file").bind_value(S, "report_name").props("dense outlined").classes("w-64")
                ui.space()
                ui.button("Svuota", icon="delete_sweep", on_click=self.clear).props("flat color=negative")
            self.render()
        self.run_button = ui.button("Genera report", icon="summarize", on_click=self.execute).mark("run-report")

    @ui.refreshable_method
    def render(self) -> None:
        items = S.report.items
        if not items:
            ui.label("Il report è vuoto: usa «Aggiungi al report» sotto qualsiasi risultato "
                     "(Dati, Group By, Pivot, Join / Union, Statistica, …).").classes("text-sm text-gray-500")
            return
        for position, item in enumerate(items):
            with ui.row().classes("w-full items-center gap-2 no-wrap border rounded p-2"):
                ui.label(f"{position + 1}.").classes("w-6 text-gray-500")
                name = ui.input("Foglio", value=item.name).props("dense outlined").classes("w-48")
                name.on("blur", lambda _, item=item, name=name: name.set_value(S.report.rename(item, name.value)))
                ui.input("Titolo", value=item.title, on_change=lambda e, item=item: setattr(item, "title", e.value)).props(
                    "dense outlined").classes("grow")
                ui.input("Nota", value=item.note, on_change=lambda e, item=item: setattr(item, "note", e.value)).props(
                    "dense outlined").classes("grow")
                size = f"{len(item.data):,} × {item.data.shape[1]}".replace(",", ".")
                ui.label(size).classes("text-xs text-gray-500 whitespace-nowrap").tooltip(item.source)
                for icon, step, enabled in (("arrow_upward", -1, position > 0),
                                            ("arrow_downward", 1, position < len(items) - 1)):
                    ui.button(icon=icon, on_click=lambda item=item, step=step: self.move(item, step)).props(
                        "dense flat size=sm").set_enabled(enabled)
                ui.button(icon="close", on_click=lambda item=item: self.remove(item)).props(
                    "dense flat size=sm color=negative")

    def move(self, item: excel_report.ReportItem, step: int) -> None:
        S.report.move(item, step)
        self.render.refresh()

    def remove(self, item: excel_report.ReportItem) -> None:
        S.report.remove(item)
        self.render.refresh()

    def clear(self) -> None:
        S.report.clear()
        self.render.refresh()

    async def execute(self) -> None:
        name = xw.file_name(S.report_name.strip() or excel_report.DEFAULT_REPORT_NAME, set())
        content = await run_excel(self.run_button, S.report.workbook, self.output.style())
        if content is not None:
            await self.output.deliver({name: content}, name)


def tab_excel() -> None:
    mode = ui.toggle(list(EXCEL_MODES), value="Split").props("no-caps")
    split_box = ui.column().classes("w-full gap-3").bind_visibility_from(mode, "value", value="Split")
    report_box = ui.column().classes("w-full gap-3").bind_visibility_from(mode, "value", value="Report")
    output = OutputSettings()
    with split_box:
        SplitPanel(output)
    with report_box:
        ReportPanel(output)


# ===========================================================================
# Look (ready-made themes, accent, tint, corners, chart colors, advanced colors)
# ===========================================================================

APPEARANCE_KEY = "appearance"  # key in app.storage.general


def load_appearance() -> None:
    S.appearance = style.from_dict(nicegui_app.storage.general.get(APPEARANCE_KEY))


def section_title(text: str) -> None:
    ui.label(text).classes("text-xs font-semibold uppercase text-gray-500")


def valid_color(value) -> str | None:
    return value.lower() if style.is_color(value) else None


class AppearanceDialog:
    """«Aspetto» window: every change shows up right away, is passed to `on_change` and saved."""

    def __init__(self, on_change: Callable[[style.Appearance], None]) -> None:
        self.on_change = on_change
        with ui.dialog() as self.dialog, ui.card().classes("w-[600px] max-w-full gap-4"):
            with ui.row().classes("w-full items-center"):
                ui.label("Aspetto").classes("text-lg font-semibold")
                ui.space()
                ui.button("Ripristina", icon="restart_alt", on_click=lambda: self.set_all(style.DEFAULT)).props("flat dense")
                ui.button(icon="close", on_click=self.dialog.close).props("flat round dense")
            with ui.column().classes("w-full gap-4"):
                self.controls()

    def open(self) -> None:
        self.dialog.open()

    def set_all(self, appearance: style.Appearance) -> None:
        """Replaces all the preferences (ready-made theme, reset) and rebuilds the controls."""
        if appearance != S.appearance:
            self.on_change(appearance)
        self.controls.refresh()

    def set(self, **changes) -> None:
        """Changes some preferences; the controls stay, only the parts that show them are updated."""
        appearance = replace(S.appearance, **changes)
        if appearance != S.appearance:
            self.on_change(appearance)
            self.presets.refresh()
            self.accents.refresh()
            self.warning.refresh()

    @ui.refreshable_method
    def controls(self) -> None:
        current = S.appearance
        section_title("Temi pronti")
        self.presets()

        section_title("Modalità")
        ui.toggle({False: "Chiaro", True: "Scuro"}, value=current.dark, on_change=lambda e: self.set(dark=e.value))

        section_title("Colore d'accento")
        with ui.row().classes("items-center gap-3"):
            self.accents()
            self.accent_input = ui.color_input("Personalizzato", value=current.accent,
                                               on_change=self.change_accent).props("dense outlined").classes("w-40")

        with ui.grid().classes("w-full grid-cols-1 sm:grid-cols-2 gap-x-6"):
            with ui.column().classes("gap-0"):
                section_title("Tinta dello sfondo")
                ui.slider(min=0, max=100, value=round(current.tint * 100)).props("label").on(
                    "change", lambda e: self.set(tint=e.args / 100))
            with ui.column().classes("gap-0"):
                section_title("Angoli arrotondati")
                ui.slider(min=style.RADIUS_RANGE[0], max=style.RADIUS_RANGE[1], value=current.radius).props(
                    "label").on("change", lambda e: self.set(radius=int(e.args)))

        section_title("Colore del testo")
        ui.color_input("Testo", value=current.ink or "", on_change=lambda e: self.set(ink=valid_color(e.value))
                       ).props("dense outlined clearable").classes("w-56").mark("text-color")
        ui.label("Vuoto = calcolato da modalità, accento e tinta. Vale per testi, tabelle e grafici.").classes(
            "text-xs text-gray-500")
        ui.color_input("Testo dei pulsanti", value=current.button_text or "",
                       on_change=lambda e: self.set(button_text=valid_color(e.value))
                       ).props("dense outlined clearable").classes("w-56").mark("button-text-color")
        ui.label("Vuoto = leggibile sull'accento (pulsanti pieni) o accento (pulsanti con bordo).").classes(
            "text-xs text-gray-500")

        section_title("Colori dei grafici")
        chart_set = next((name for name, colors in style.CHART_PALETTES.items() if colors == current.charts), None)
        self.chart_select = ui.select(list(style.CHART_PALETTES), value=chart_set, label="Serie di colori",
                  on_change=lambda e: e.value and self.set_all(replace(S.appearance, charts=style.CHART_PALETTES[e.value]))
                  ).props("dense outlined").classes("w-56")
        with ui.grid().classes("w-full grid-cols-2 sm:grid-cols-4 gap-2"):
            for index, color in enumerate(current.charts):
                ui.color_input(f"Serie {index + 1}", value=color, preview=True,
                               on_change=lambda e, i=index: self.change_chart_color(i, e.value)).props("dense outlined")

        with ui.expansion("Colori avanzati", icon="tune").classes("w-full"):
            ui.label("Vuoto = calcolato da modalità, accento e tinta.").classes("text-xs text-gray-500")
            with ui.grid().classes("w-full grid-cols-1 sm:grid-cols-3 gap-2"):
                for key, label in style.ADVANCED.items():
                    if key == "ink":  # shown above, in «Colore del testo»
                        continue
                    ui.color_input(label, value=getattr(current, key) or "",
                                   on_change=lambda e, k=key: self.set(**{k: valid_color(e.value)})
                                   ).props("dense outlined clearable")
            self.warning()

    @ui.refreshable_method
    def presets(self) -> None:
        selected = style.preset_name(S.appearance)
        with ui.row().classes("gap-2"):
            for name, preset in style.PRESETS.items():
                palette = style.resolve(preset)
                card = ui.column().classes("preset-card items-center gap-1" + (" selected" if name == selected else ""))
                card.style(f"background: {palette.canvas}").on("click", lambda p=preset: self.set_all(p)).mark(f"preset-{name}")
                with card:
                    with ui.row().classes("gap-1 no-wrap"):
                        for color in (palette.primary, *palette.charts[:2]):
                            ui.element("div").classes("swatch-dot").style(f"background: {color}")
                    ui.label(name).classes("text-xs").style(f"color: {palette.ink}")

    @ui.refreshable_method
    def accents(self) -> None:
        with ui.row().classes("items-center gap-2"):
            for name, color in style.ACCENTS.items():
                ui.button(icon="check" if color == S.appearance.accent else None, color=None,
                          on_click=lambda c=color: self.accent_input.set_value(c)).classes("swatch").style(
                    f"background: {color}; color: {style.readable_on(color)}").props("unelevated").tooltip(name)

    @ui.refreshable_method
    def warning(self) -> None:
        if not style.is_readable(S.palette):
            ui.label("Il testo ha poco contrasto con le card: scegli colori più distanti.").classes("text-xs text-amber-700")

    def change_accent(self, event) -> None:
        if color := valid_color(event.value):
            self.set(accent=color)

    def change_chart_color(self, index: int, value) -> None:
        if color := valid_color(value):
            charts = list(S.appearance.charts)
            charts[index] = color
            self.set(charts=tuple(charts))
            self.chart_select.set_value(None)  # the colors are no longer those of a ready-made series


# ===========================================================================
# Database: projects, subfolders, saved datasets with their note
# ===========================================================================

def note_dialog(title: str, text: str, on_save: Callable[[str], None] | None = None) -> None:
    """The note in a text box: read-only, or editable if `on_save` is given."""
    with ui.dialog() as dlg, ui.card().classes("w-[640px] max-w-full gap-3"):
        ui.label(f"Nota — {title}").classes("text-base font-medium")
        box = ui.textarea(value=text or "(nessuna nota)").props(
            f"outlined autogrow {'' if on_save else 'readonly'}").classes("w-full font-mono text-sm").mark("note-text")

        def save() -> None:
            on_save(box.value)
            dlg.close()

        with ui.row().classes("w-full justify-end"):
            ui.button("Chiudi", on_click=dlg.close).props("flat")
            if on_save:
                ui.button("Salva nota", on_click=save).mark("note-save")
    dlg.on_value_change(lambda e: None if e.value else dlg.delete())
    dlg.open()


async def ask_text(title: str, label: str, value: str = "") -> str | None:
    """Asks for a text (folder or dataset name); None if the user cancels."""
    with ui.dialog() as dialog, ui.card().classes("w-[420px]"):
        ui.label(title).classes("text-base font-medium")
        field = ui.input(label, value=value).props("autofocus outlined dense").classes("w-full").mark("db-text")
        field.on("keydown.enter", lambda: dialog.submit(field.value))
        with ui.row().classes("w-full justify-end"):
            ui.button("Annulla", on_click=lambda: dialog.submit(None)).props("flat")
            ui.button("OK", on_click=lambda: dialog.submit(field.value)).mark("db-text-ok")
    text = await dialog
    dialog.delete()
    return text


async def confirm(text: str) -> bool:
    with ui.dialog() as dialog, ui.card().classes("w-[420px]"):
        ui.label(text)
        with ui.row().classes("w-full justify-end"):
            ui.button("Annulla", on_click=lambda: dialog.submit(False)).props("flat")
            ui.button("Elimina", on_click=lambda: dialog.submit(True)).props("color=negative").mark("db-confirm")
    answer = await dialog
    dialog.delete()
    return bool(answer)


def save_to_database(df: pd.DataFrame | None, name: str, folder_id: int | None = None,
                     on_saved: Callable[[workspace.SavedDataset], None] | None = None) -> None:
    """Saves a DataFrame as it is in a database folder, with the note written by the user (empty by default)."""
    if df is None:
        ui.notify("Nessun dato da salvare", type="warning")
        return
    try:
        choices = S.database.folder_choices()
    except Exception as e:  # noqa: BLE001  (e.g. database file used by another instance of the app)
        notify_error(e)
        return
    if not choices:
        ui.notify("Crea prima un progetto nel database", type="warning")
        open_database()
        return
    with ui.dialog() as dlg, ui.card().classes("w-[640px] max-w-full gap-3"):
        ui.label("Salva nel database").classes("text-base font-medium")
        folder = ui.select(choices, value=folder_id if folder_id in choices else next(iter(choices)),
                           label="Progetto / cartella", with_input=True).props("dense outlined").classes("w-full").mark("db-folder")
        dataset_name = ui.input("Nome del dataset", value=name).props("dense outlined").classes("w-full").mark("db-name")
        note_box = ui.textarea("Nota (facoltativa)").props("outlined autogrow").classes(
            "w-full font-mono text-sm").mark("db-note-text")
        replace_switch = ui.switch("Sostituisci se esiste già un dataset con questo nome", value=False)

        async def ok() -> None:
            try:
                item = await run.io_bound(S.database.save_dataset, folder.value, dataset_name.value, df,
                                          note_box.value, replace_switch.value)
            except Exception as e:  # noqa: BLE001
                notify_error(e)
                return
            if on_saved:
                on_saved(item)
            path = S.database.folder_path(item.folder_id)
            ui.notify(f"«{item.name}» salvato in {path}: {item.rows:,} righe".replace(",", "."), type="positive")
            dlg.close()  # last: closing deletes the dialog and with it the button of this handler

        with ui.row().classes("w-full justify-end"):
            ui.button("Annulla", on_click=dlg.close).props("flat")
            ui.button("Salva", icon="save", on_click=ok).mark("db-save-ok")
    dlg.on_value_change(lambda e: None if e.value else dlg.delete())
    dlg.open()


class DatabaseDialog:
    """Database management window: tree of projects and folders on the left, details and actions on the right."""

    def __init__(self) -> None:
        self.selected: str | None = None
        with ui.dialog() as self.dialog, ui.card().classes("w-[1000px] max-w-full h-[640px] no-wrap gap-3"):
            with ui.row().classes("w-full items-center gap-2 no-wrap"):
                ui.icon("storage", size="24px").classes("text-primary")
                ui.label("Database").classes("text-base font-medium")
                ui.label(str(S.database.path)).classes("text-xs text-gray-500 truncate")
                ui.space()
                ui.button("Nuovo progetto", icon="create_new_folder", on_click=self.new_project).props(
                    "dense outline").mark("db-new-project")
                ui.button(icon="close", on_click=self.dialog.close).props("flat round dense").tooltip("Chiudi")
            with ui.row().classes("w-full h-full no-wrap gap-4 overflow-hidden"):
                with ui.scroll_area().classes("w-1/3 h-full border rounded"):
                    self.tree = ui.tree([], node_key="id", on_select=self.select).props('no-transition no-nodes-label="Nessun progetto"').mark("db-tree")
                with ui.column().classes("w-2/3 h-full gap-3"):
                    self.details()
        self.dialog.on_value_change(lambda e: None if e.value else self.dialog.delete())
        self.reload()

    def open(self) -> None:
        self.dialog.open()

    # --- tree and selection ---------------------------------------------------
    def reload(self, selected: str | None = None) -> None:
        self.tree.props["nodes"] = S.database.tree()
        self.tree.update()
        self.tree.expand()
        self.selected = selected if selected in {node["id"] for node in self.tree.nodes()} else None
        self.tree.select(self.selected)
        self.details.refresh()

    def select(self, event) -> None:
        self.selected = event.value
        self.details.refresh()

    @ui.refreshable_method
    def details(self) -> None:
        if self.selected is None:
            message = "Seleziona un progetto, una cartella o un dataset" if self.tree.props["nodes"] else \
                "Il database è vuoto: crea un progetto per iniziare"
            ui.label(message).classes("text-sm text-gray-500")
            return
        kind, item_id = workspace.parse_node(self.selected)
        try:
            self.folder_details(item_id) if kind == workspace.FOLDER else self.dataset_details(item_id)
        except ValueError as e:
            ui.label(str(e)).classes("text-sm text-red-600")

    def folder_details(self, folder_id: int) -> None:
        folder = S.database.folder(folder_id)
        subfolders = sum(other.parent_id == folder_id for other in S.database.folders())
        datasets = len(S.database.datasets(folder_id))
        ui.label(S.database.folder_path(folder_id)).classes("text-lg font-medium")
        ui.label(f"{'Progetto' if folder.parent_id is None else 'Cartella'} · {subfolders} sottocartelle · "
                 f"{datasets} dataset").classes("text-sm text-gray-600")
        with ui.row().classes("items-center gap-2"):
            ui.button("Nuova sottocartella", icon="create_new_folder",
                      on_click=lambda: self.new_folder(folder_id)).props("dense outline").mark("db-new-folder")
            ui.button("Rinomina", icon="edit", on_click=lambda: self.rename_folder(folder)).props("dense flat")
            ui.button("Elimina", icon="delete", on_click=lambda: self.delete_folder(folder)).props(
                "dense flat color=negative").mark("db-delete")
        if S.datasets:
            with ui.row().classes("items-center gap-2"):
                dataset = ui.select(list(S.datasets), value=S.active, label="Dataset caricato", with_input=True).props(
                    "dense outlined").classes("w-64")
                ui.button("Salva qui", icon="save", on_click=lambda: self.save_loaded(dataset.value, folder_id)).props(
                    "dense outline").mark("db-save-active")

    def dataset_details(self, dataset_id: int) -> None:
        item = S.database.dataset(dataset_id)
        ui.label(item.name).classes("text-lg font-medium")
        ui.label(f"{S.database.folder_path(item.folder_id)} · {item.rows:,} righe × {item.cols} colonne · "
                 f"salvato il {item.created:%d/%m/%Y %H:%M}".replace(",", ".")).classes("text-sm text-gray-600")
        with ui.row().classes("gap-2"):
            ui.button("Apri", icon="open_in_browser", on_click=lambda: self.open_dataset(item)).mark("db-open")
            ui.button("Nota", icon="sticky_note_2", on_click=lambda: note_dialog(
                item.name, item.note, lambda text: self.save_note(item, text))).props("dense outline").mark("db-note")
            ui.button("Rinomina", icon="edit", on_click=lambda: self.rename_dataset(item)).props("dense flat")
            ui.button("Sposta", icon="drive_file_move", on_click=lambda: self.move_dataset(item)).props("dense flat")
            ui.button("Elimina", icon="delete", on_click=lambda: self.delete_dataset(item)).props(
                "dense flat color=negative").mark("db-delete")

    # --- actions ----------------------------------------------------------------
    def _run(self, action: Callable, *args, selected: str | None = None) -> bool:
        """Runs an action on the database and updates the tree; validation errors become notifications."""
        try:
            result = action(*args)
        except ValueError as e:
            ui.notify(str(e), type="warning")
            return False
        self.reload(selected or (workspace.node_id(workspace.FOLDER, result) if isinstance(result, int) else self.selected))
        return True

    async def new_project(self) -> None:
        if (name := await ask_text("Nuovo progetto", "Nome del progetto")) is not None:
            self._run(S.database.create_folder, name)

    async def new_folder(self, parent_id: int) -> None:
        if (name := await ask_text("Nuova sottocartella", "Nome della cartella")) is not None:
            self._run(S.database.create_folder, name, parent_id)

    async def rename_folder(self, folder: workspace.Folder) -> None:
        if (name := await ask_text("Rinomina cartella", "Nuovo nome", folder.name)) is not None:
            self._run(S.database.rename_folder, folder.id, name)

    async def delete_folder(self, folder: workspace.Folder) -> None:
        if await confirm(f"Eliminare «{folder.name}» con tutte le sottocartelle e i dataset che contiene?"):
            self._run(S.database.delete_folder, folder.id)

    def save_loaded(self, name: str, folder_id: int) -> None:
        """Saves a loaded dataset; the database window stays open and the tree then shows the new dataset."""
        save_to_database(S.datasets[name], name, folder_id,
                         on_saved=lambda item: self.reload(workspace.node_id(workspace.DATASET, item.id)))

    async def open_dataset(self, item: workspace.SavedDataset) -> None:
        try:
            df = await run.io_bound(S.database.load_dataset, item.id)
        except Exception as e:  # noqa: BLE001
            notify_error(e)
            return
        origin = notes.database_origin(S.database.folder_path(item.folder_id), item.created, item.note)
        name = S.add(item.name, df, origin=origin)
        ui.notify(f"Aperto «{name}» dal database: {len(df):,} righe".replace(",", "."), type="positive")
        show_new_dataset(name)
        self.dialog.close()  # last: closing deletes the dialog and with it the button of this handler

    def save_note(self, item: workspace.SavedDataset, text: str) -> None:
        if self._run(S.database.set_note, item.id, text):
            ui.notify("Nota aggiornata", type="positive")

    async def rename_dataset(self, item: workspace.SavedDataset) -> None:
        if (name := await ask_text("Rinomina dataset", "Nuovo nome", item.name)) is not None:
            self._run(S.database.rename_dataset, item.id, name)

    async def move_dataset(self, item: workspace.SavedDataset) -> None:
        choices = S.database.folder_choices()
        with ui.dialog() as dialog, ui.card().classes("w-[480px]"):
            ui.label(f"Sposta «{item.name}»").classes("text-base font-medium")
            target = ui.select(choices, value=item.folder_id, label="Nuova cartella", with_input=True).props(
                "dense outlined").classes("w-full")
            with ui.row().classes("w-full justify-end"):
                ui.button("Annulla", on_click=lambda: dialog.submit(None)).props("flat")
                ui.button("Sposta", on_click=lambda: dialog.submit(target.value))
        folder_id = await dialog
        dialog.delete()
        if folder_id is not None and folder_id != item.folder_id:
            self._run(S.database.move_dataset, item.id, folder_id)

    async def delete_dataset(self, item: workspace.SavedDataset) -> None:
        if await confirm(f"Eliminare il dataset «{item.name}» dal database?"):
            self._run(S.database.delete_dataset, item.id, selected=workspace.node_id(workspace.FOLDER, item.folder_id))


def open_database() -> None:
    try:
        DatabaseDialog().open()
    except Exception as e:  # noqa: BLE001  (e.g. database file used by another instance of the app)
        notify_error(e)


# ===========================================================================
# Header and file loading
# ===========================================================================

class DatasetMenu:
    """«Dataset» button of the header: the loaded datasets with their size, to rename or remove them
    (each window of the sections chooses its own dataset)."""

    def __init__(self) -> None:
        with ui.button(icon="dataset").props("flat dense no-caps").mark("datasets") as self.button:
            with ui.menu().props("auto-close").classes("min-w-[300px]"):
                self.render()

    @ui.refreshable_method
    def render(self) -> None:
        self.button.set_text(f"Dataset ({len(S.datasets)})")
        if not S.datasets:
            ui.label("Nessun dataset caricato").classes("text-sm text-gray-500 px-4 py-2")
        for name, df in S.datasets.items():
            with ui.row().classes("w-full items-center gap-2 no-wrap px-3 py-1"):
                with ui.column().classes("gap-0"):
                    ui.label(name).classes("text-sm font-medium")
                    ui.label(dataset_size(df)).classes("text-xs text-gray-500")
                ui.space()
                ui.button(icon="edit", on_click=lambda name=name: self.rename(name)).props(
                    'flat round dense size=sm aria-label="Rinomina dataset"').tooltip("Rinomina").mark("rename-dataset")
                ui.button(icon="delete", on_click=lambda name=name: remove_dataset(name)).props(
                    'flat round dense size=sm color=negative aria-label="Rimuovi dataset"').tooltip("Rimuovi").mark(
                    "remove-dataset")
        ui.separator()
        ui.button("Carica dataset", icon="add", on_click=upload_dialog).props("flat no-caps align=left").classes("w-full")

    async def rename(self, name: str) -> None:
        with self.button:  # the list can be rebuilt before the dialog opens: the dialog belongs to the header
            await rename_dialog(name)


def refresh_menus() -> None:
    for _, datasets_menu in windows.values():
        datasets_menu.render.refresh()


def follow_datasets(select: ui.select, renamed: dict[str, str]) -> None:
    """A dataset select follows the loaded datasets: a renamed dataset keeps its place, a removed one gives way to
    the active one (the value change rebuilds what depends on it)."""
    names = list(S.datasets)
    name = renamed.get(select.value, select.value)
    select.set_options(names, value=name if name in names else S.active)


def update_header(renamed: dict[str, str] | None = None) -> None:
    """Datasets loaded, renamed or removed: header menus, windows and pickers follow."""
    refresh_menus()
    panes = S.live_panes()
    if not S.datasets or not panes:  # first dataset or none left: the whole page changes
        page_content.refresh()
        return
    for element in [pane.select for pane in panes] + [picker.select for picker in S.live_pickers()]:
        follow_datasets(element, renamed or {})
    S.notify_watchers()  # after the windows have followed: each one on a dataset that still exists


def show_new_dataset(name: str) -> None:
    """A dataset just loaded or opened: the Dati window shows it, and the Dati section is opened in the browser
    window that loaded it (from another section the dataset would not be in sight)."""
    update_header()
    for pane in S.live_panes():
        if pane.stack.section == NEW_DATASET_SECTION:
            pane.select.value = name
    windows[ui.context.client.id][0].value = NEW_DATASET_SECTION


async def rename_dialog(old_name: str) -> None:
    with ui.dialog() as dialog, ui.card().classes("w-[420px]"):
        ui.label("Rinomina dataset").classes("text-base font-medium")
        name = ui.input("Nuovo nome", value=old_name).props("autofocus outlined dense").classes("w-full").mark("rename-name")
        ui.label().classes("text-xs text-gray-500").bind_text_from(
            name, "value", backward=lambda value: f"In SQL la tabella diventa `{eng.sql_table_name(value or '')}`")
        with ui.row().classes("w-full justify-end"):
            ui.button("Annulla", on_click=lambda: dialog.submit(None)).props("flat")
            ui.button("Rinomina", on_click=lambda: dialog.submit(name.value)).mark("rename-ok")
        name.on("keydown.enter", lambda: dialog.submit(name.value))
    new_name = await dialog
    dialog.delete()
    if new_name is None or new_name.strip() == old_name:
        return
    try:
        new_name = structure.check_dataset_name(new_name, [other for other in S.datasets if other != old_name])
    except ValueError as e:
        ui.notify(str(e), type="warning")
        return
    S.rename(old_name, new_name)
    ui.notify(f"Dataset rinominato in «{new_name}» (tabella SQL `{eng.sql_table_name(new_name)}`)", type="positive")
    update_header({old_name: new_name})


def remove_dataset(name: str) -> None:
    S.remove(name)
    ui.notify(f"Dataset «{name}» rimosso", type="info")
    update_header()


def registra_dataset(dataset_name: str, file_name: str, df: pd.DataFrame) -> None:
    name = S.add(dataset_name, df, origin=notes.file_origin(file_name))
    ui.notify(f"Caricato {file_name}: {len(df):,} righe".replace(",", "."), type="positive")
    show_new_dataset(name)


async def ask_sheet(file_name: str, sheets: list[str]) -> tuple[str, str] | None:
    """Asks the sheet and the range of cells to load (empty = whole sheet); None if the user cancels."""
    with ui.dialog() as dialog, ui.card().classes("w-[420px] max-w-full gap-3"):
        ui.label(f"Quale foglio di {file_name}?").classes("text-base font-medium")
        choice = ui.select(sheets, value=sheets[0], label="Foglio", with_input=True).props("dense outlined").classes("w-full")
        cells = ui.input("Intervallo di celle (facoltativo)", placeholder="es. B3:F120 · vuoto = tutto il foglio").props(
            "dense outlined").classes("w-full").mark("sheet-range")
        error = ui.label().classes(ERROR_STYLE)

        def ok() -> None:
            text = cells.value.strip()
            try:
                if text:
                    eng.cell_block(text)
            except ValueError as e:
                error.set_text(str(e))
                return
            dialog.submit((choice.value, text))

        with ui.row().classes("w-full justify-end"):
            ui.button("Annulla", on_click=lambda: dialog.submit(None)).props("flat")
            ui.button("Carica", on_click=ok).mark("sheet-ok")
    result = await dialog
    dialog.delete()
    return result


class ImportDialog:
    """Before loading: columns to keep, names, formats and preview; «Ripristina» goes back to the initial plan."""

    def __init__(self, file_label: str, df: pd.DataFrame,
                 text_loader: Callable[[list[str]], pd.DataFrame] | None = None) -> None:
        self.df = df
        self.text_loader = text_loader  # reads some columns again from the file as text (CSV, Excel)
        self.texts: dict[int, pd.Series] = {}  # position → column read again as text
        self.decimal = ","
        self.plan = import_plan.column_plan(df)
        self.initial = [dict(item) for item in self.plan]
        with ui.dialog().props("persistent") as self.dialog, ui.card().classes("w-[1100px] max-w-full gap-3"):
            with ui.row().classes("w-full items-baseline gap-3"):
                ui.label(f"Importa «{file_label}»").classes("text-base font-medium")
                ui.label(f"{len(df):,} righe × {df.shape[1]} colonne".replace(",", ".")).classes("text-sm text-gray-600")
            with ui.row().classes("w-full items-center gap-2"):
                ui.button("Tutte", icon="check_box", on_click=lambda: self.keep_all(True)).props("dense flat no-caps")
                ui.button("Nessuna", icon="check_box_outline_blank", on_click=lambda: self.keep_all(False)).props(
                    "dense flat no-caps")
                ui.select({",": "virgola (1.234,56)", ".": "punto (1,234.56)"}, value=self.decimal,
                          label="Decimali nel testo", on_change=self.set_decimal).props("dense outlined").classes("w-52")
                ui.button("Proponi formati", icon="auto_fix_high", on_click=self.suggest).props(
                    "dense flat no-caps").tooltip("Numeri e date scritti come testo (es. 1.234,56 e 31/01/2024)").mark(
                    "import-suggest")
                ui.space()
                self.counter = ui.label().classes("text-sm text-gray-600")
            with ui.scroll_area().classes("w-full h-[240px] border rounded"):
                self.column_rows()
            ui.label(f"Anteprima (prime {import_plan.PREVIEW_ROWS} righe)").classes("text-sm font-medium")
            self.preview()
            with ui.row().classes("w-full items-center"):
                ui.button("Ripristina", icon="restart_alt", on_click=self.restore).props("flat").mark("import-restore")
                ui.space()
                ui.button("Annulla", on_click=lambda: self.dialog.submit(None)).props("flat")
                ui.button("Carica", icon="file_download_done", on_click=self.confirm).mark("import-ok")

    async def ask(self) -> pd.DataFrame | None:
        """The dataset to load, or None if the user cancels."""
        self.dialog.open()
        result = await self.dialog
        self.dialog.delete()
        return result

    # --- plan -------------------------------------------------------------------
    @ui.refreshable_method
    def column_rows(self) -> None:
        with ui.grid(columns="auto minmax(160px, 1fr) 130px 190px").classes("w-full items-center gap-x-3 gap-y-1 p-2"):
            for text in ("", "Nome", "Rilevato", "Formato"):
                ui.label(text).classes("text-xs font-medium text-gray-500")
            for item in self.plan:
                ui.checkbox(value=item["keep"], on_change=lambda e, item=item: self.update(item, keep=e.value)).props(
                    "dense").mark("import-keep")
                ui.input(value=item["name"], on_change=lambda e, item=item: self.update(item, name=e.value)).props(
                    "dense outlined").classes("w-full").mark("import-name")
                ui.label(item["detected"]).classes("text-sm text-gray-600")
                ui.select(import_plan.FORMATS, value=item["format"],
                          on_change=lambda e, item=item: self.change_format(item, e.value)).props(
                    "dense outlined").classes("w-full").mark("import-format")
        kept = sum(item["keep"] for item in self.plan)
        self.counter.set_text(f"{kept} di {len(self.plan)} colonne da caricare")

    def update(self, item: dict, **changes) -> None:
        item.update(changes)
        kept = sum(entry["keep"] for entry in self.plan)
        self.counter.set_text(f"{kept} di {len(self.plan)} colonne da caricare")
        self.preview.refresh()

    def keep_all(self, keep: bool) -> None:
        for item in self.plan:
            item["keep"] = keep
        self.column_rows.refresh()
        self.preview.refresh()

    async def change_format(self, item: dict, value: str) -> None:
        item["format"] = value
        await self.load_texts()
        self.preview.refresh()

    async def load_texts(self) -> None:
        """Reads again from the file, only once, the numeric or date columns for which «Testo» was chosen."""
        missing = [item for item in import_plan.text_reload_needed(self.plan) if item["position"] not in self.texts]
        if not missing or self.text_loader is None:
            return
        names = [str(self.df.columns[item["position"]]) for item in missing]
        try:
            loaded = await run.io_bound(self.text_loader, names)
        except Exception as e:  # noqa: BLE001  (the conversion of the value read stays: e.g. 123 instead of 00123)
            ui.notify(f"Colonne non rilette come testo: {e}", type="warning")
            return
        for item, name in zip(missing, names):
            self.texts[item["position"]] = loaded[name]

    def source(self) -> pd.DataFrame:
        """The dataset as read, with the columns read again as text where «Testo» was chosen."""
        needed = {item["position"] for item in import_plan.text_reload_needed(self.plan)}
        return import_plan.with_text_columns(self.df, {p: v for p, v in self.texts.items() if p in needed})

    def set_decimal(self, e) -> None:
        self.decimal = e.value
        self.preview.refresh()

    def suggest(self) -> None:
        """«Proponi formati»: number and date formats for the text columns whose values all convert."""
        changed = import_plan.suggest_formats(self.df, self.plan, self.decimal)
        ui.notify(f"Formati proposti: {changed}" if changed else "Nessun formato da proporre",
                  type="positive" if changed else "info")
        self.column_rows.refresh()
        self.preview.refresh()

    def restore(self) -> None:
        self.plan = [dict(item) for item in self.initial]
        self.column_rows.refresh()
        self.preview.refresh()

    # --- preview and confirmation ----------------------------------------------------
    @ui.refreshable_method
    def preview(self) -> None:
        sample = self.source().head(import_plan.PREVIEW_ROWS)
        try:
            out = import_plan.apply_plan(sample, self.plan, self.decimal)
        except ValueError as e:
            ui.label(str(e)).classes("text-sm text-red-600").mark("import-error")
            return
        lost = import_plan.lost_counts(sample, self.plan, out)
        if lost:
            text = ", ".join(f"{column}: {count}" for column, count in lost.items())
            ui.label(f"Valori non convertibili nell'anteprima (diventano vuoti) — {text}").classes(
                "text-sm text-orange-600").mark("import-lost")
        shown = _unique_columns(out)
        ui.aggrid({"columnDefs": _col_defs(shown), "rowData": _json_rows(shown),
                   "defaultColDef": {"resizable": True, "minWidth": GRID_MIN_COLUMN_WIDTH},
                   "localeText": GRID_LOCALE, "suppressFieldDotNotation": True},
                  theme="quartz").classes("w-full").style("height: 300px").mark("import-preview")

    async def confirm(self) -> None:
        notification = ui.notification("Preparazione del dataset…", spinner=True, timeout=None)
        try:
            source = self.source()
            out = await run.io_bound(import_plan.apply_plan, source, self.plan, self.decimal)
            lost = import_plan.lost_counts(source, self.plan, out)
        except ValueError as e:
            ui.notify(str(e), type="warning")
            return
        finally:
            notification.dismiss()
        if lost:
            text = ", ".join(f"{column} ({count:,})".replace(",", ".") for column, count in lost.items())
            ui.notify(f"Valori non convertibili, rimasti vuoti: {text}", type="warning", multi_line=True)
        self.dialog.submit(out)


async def load_dataset_file(file_name: str, source: Path | bytes, choice: tuple[str, str] | None = None) -> None:
    """Loads from a path or from content in memory; for Excel it first asks for the sheet and the range of cells
    (unless `choice` gives them already), then columns and formats."""
    dataset_name = Path(file_name).stem
    sheet, cells = None, ""
    try:
        if eng.is_excel(file_name):
            sheets = await run.io_bound(eng.excel_sheets, source)
            if choice is None:
                choice = await ask_sheet(file_name, sheets)
            if choice is None:
                return
            sheet, cells = choice
            if len(sheets) > 1:
                dataset_name = f"{dataset_name}_{sheet}"
        notification = ui.notification("Caricamento in corso…", spinner=True, timeout=None)
        try:
            if isinstance(source, bytes):
                df = await run.io_bound(eng.load_bytes, file_name, source, sheet, cells)
            else:
                df = await run.io_bound(eng.load_file, source, sheet, cells)
        finally:
            notification.dismiss()
        file_label = file_name if sheet is None else f"{file_name} [{sheet}]"
        if cells:
            file_label += f" {cells}"
        text_loader = None
        if eng.can_read_as_text(file_name):
            text_loader = lambda names: eng.load_text_columns(file_name, source, names, sheet, cells=cells)  # noqa: E731
        df = await ImportDialog(file_label, df, text_loader).ask()
        if df is not None:
            registra_dataset(dataset_name, file_label, df)
    except Exception as e:  # noqa: BLE001
        notify_error(e)


async def handle_upload(e) -> None:
    # NiceGUI >= 3: e.file is an object with name and read()
    try:
        content = await e.file.read()
    except Exception as ex:  # noqa: BLE001
        notify_error(ex)
        return
    await load_dataset_file(e.file.name, content)


class ExcelRangeDialog:
    """Like a range InputBox: the range selected in the open Excel workbook is shown live, then it is imported."""

    def __init__(self, upload: ui.dialog) -> None:
        upload.close()
        self.selection: excel_link.Selection | None = None
        self.busy = False  # a reading of the selection is running
        with ui.dialog().props("persistent") as self.dialog, ui.card().classes("w-[560px] max-w-full gap-3"):
            ui.label("Range Excel").classes("text-base font-medium")
            ui.label("Seleziona in Excel le celle da importare, con le intestazioni nella prima riga: "
                     "qui compare la selezione attuale.").classes("text-sm text-gray-600")
            self.current = ui.label().classes("font-mono text-sm break-all")
            self.message = ui.label().classes(ERROR_STYLE)
            with ui.row().classes("w-full justify-end"):
                ui.button("Chiudi", on_click=self.close).props("flat")
                self.import_button = ui.button("Importa", icon="done", on_click=self.import_range).mark("excel-range-ok")
                self.import_button.set_enabled(False)
            self.timer = ui.timer(1.0, self.refresh)
        self.dialog.open()

    def close(self) -> None:
        self.timer.cancel()
        self.dialog.close()
        self.dialog.delete()

    async def refresh(self) -> None:
        """Reads the selection of Excel (every second while the dialog is open)."""
        if self.busy:
            return
        self.busy = True
        try:
            self.selection = await run.io_bound(excel_link.current_selection)
        except ValueError as e:
            self.selection = None
            self.current.set_text("")
            self.message.set_text(str(e))
        except Exception as e:  # noqa: BLE001
            self.selection = None
            self.current.set_text("")
            self.message.set_text(f"Impossibile leggere la selezione di Excel: {e}")
        else:
            self.current.set_text(f"{Path(self.selection.file).name} › {self.selection.sheet} › {self.selection.cells}")
            self.message.set_text("")
        finally:
            self.busy = False
            self.import_button.set_enabled(self.selection is not None)

    async def import_range(self) -> None:
        selection = self.selection
        if selection is None:
            return
        self.timer.cancel()  # the import dialog opens over this one, which closes when the import is over
        await load_dataset_file(Path(selection.file).name, Path(selection.file), (selection.sheet, selection.cells))
        self.close()


def upload_dialog() -> None:
    with ui.dialog() as dlg, ui.card().classes("w-[520px] gap-3"):
        ui.label("Carica un dataset").classes("text-base font-medium")
        ui.upload(label="Trascina qui i file", on_upload=handle_upload, auto_upload=True, multiple=True,
                  on_rejected=lambda: ui.notify("File rifiutato", type="warning")).props("flat bordered").classes(
            "w-full upload-box")
        with ui.row().classes("w-full items-center justify-between no-wrap"):
            ui.label("Oppure il range selezionato in un Excel aperto:").classes("text-sm text-gray-600")
            ui.button("Range Excel", icon="table_view", on_click=lambda: ExcelRangeDialog(dlg)).props(
                "outline no-caps").mark("excel-range")
        with ui.row().classes("w-full justify-end"):
            ui.button("Chiudi", on_click=dlg.close).props("flat")
    dlg.open()


# Sections in the sidebar: name → (group, icon, builder); the order is the display order
TABS = {
    "Dati": ("Prepara", "table_chart", tab_data),
    "Struttura": ("Prepara", "view_column", StructureTab),
    "Colonne calcolate": ("Prepara", "calculate", tab_columns),
    "Join / Union": ("Prepara", "call_merge", tab_combine),
    "Group By": ("Analizza", "functions", tab_groupby),
    "Pivot": ("Analizza", "pivot_table_chart", PivotTab),
    "Grafici": ("Analizza", "bar_chart", ChartTab),
    "Statistica": ("Analizza", "query_stats", StatsTab),
    "Excel": ("Esporta", "grid_on", tab_excel),
    "Console": ("Strumenti", "terminal", tab_console),
}
# Sections where windows can be stacked, each on its own dataset
MULTI_WINDOW = {"Group By", "Pivot", "Grafici", "Console"}
# Sections that choose their datasets in their own pickers (the others: a dataset per window)
OWN_SOURCES = {"Join / Union", "Excel"}


def hide_if_narrow(drawer: ui.left_drawer) -> str:
    """JavaScript that closes the side menu when the window is narrow (there it would cover the page)."""
    return f"if (window.innerWidth < {SIDEBAR_BREAKPOINT}) getElement({drawer.id})?.hide();"


def sidebar() -> tuple[ui.left_drawer, ui.tabs]:
    """Side menu of the sections, grouped as in TABS: returns the drawer and the tabs."""
    def select_tab(event) -> None:
        S.tab = event.value
        # a new section starts from its top; on a narrow window the menu covers the page, so it closes
        ui.run_javascript(f"window.scrollTo(0, 0); setTimeout(() => {{ {hide_if_narrow(drawer)} }}, 100)")

    with ui.left_drawer(value=True).props(f"width=232 breakpoint={SIDEBAR_BREAKPOINT}").classes("app-sidebar") as drawer:
        ui.button("Database", icon="storage", on_click=open_database).props(
            "flat no-caps align=left").classes("w-full").mark("database")
        with ui.tabs(value=S.tab, on_change=select_tab).props(
                "vertical inline-label no-caps indicator-color=transparent").classes("w-full") as tabs:
            group = None
            for name, (section, icon, _) in TABS.items():
                if section != group:
                    group = section
                    ui.label(section).classes("sidebar-group")
                ui.tab(name, icon=icon)
    return drawer, tabs


@ui.refreshable
def page_content() -> None:
    S.themed.clear()  # the rebuilt tabs register again: the old ones can be freed
    menu = windows[ui.context.client.id][0]  # the refresh rebuilds each window in the context of its own client
    for item in menu.descendants():
        if isinstance(item, ui.tab):
            item.set_enabled(bool(S.active))  # without a dataset the sections cannot be selected
    if not S.active:
        with ui.column().classes("w-full items-center mt-24 gap-3"):
            ui.icon("upload_file", size="64px").classes("text-gray-400")
            ui.label("Nessun dataset caricato").classes("text-lg text-gray-600")
            with ui.row().classes("gap-2"):
                ui.button("Carica un file", icon="folder_open", on_click=upload_dialog)
                ui.button("Apri dal database", icon="storage", on_click=open_database).props("outline")
        return

    ui.label().bind_text_from(menu, "value").classes("page-title")
    with ui.tab_panels(menu, value=S.tab, animated=False).classes("w-full app-panels"):
        for name, (_, _, builder) in TABS.items():
            with ui.tab_panel(name).classes("gap-3"):
                if name in OWN_SOURCES:
                    builder()
                else:
                    PaneStack(name, builder, multiple=name in MULTI_WINDOW)


def main_page() -> None:
    dark_mode = style.apply(S.appearance)

    def change_appearance(appearance: style.Appearance) -> None:
        S.appearance = appearance
        style.switch(appearance, dark_mode)
        nicegui_app.storage.general[APPEARANCE_KEY] = style.to_dict(appearance)
        for tab in S.themed:
            if not tab.chart_box.is_deleted:
                tab.draw()  # chart colors are set when the charts are created

    appearance_dialog = AppearanceDialog(change_appearance)

    with ui.header().classes("app-header items-center gap-3 py-2"):
        menu_button = ui.button(icon="menu").props('flat round dense aria-label="Mostra/nascondi menu"').tooltip(
            "Mostra/nascondi menu")
        ui.icon("analytics", size="28px").classes("text-primary")
        ui.label("Data Explorer").classes("text-lg font-semibold")
        ui.space()
        ui.button("Aspetto", icon="palette", on_click=appearance_dialog.open).props("flat dense").mark("appearance")
        datasets_menu = DatasetMenu()
        ui.button(icon="add", on_click=upload_dialog).props('flat round dense aria-label="Carica dataset"').tooltip(
            "Carica dataset")
    drawer, menu = sidebar()
    client = ui.context.client
    windows[client.id] = (menu, datasets_menu)
    client.on_delete(lambda: windows.pop(client.id, None))  # window closed
    menu_button.on_click(drawer.toggle)
    # on a narrow window the menu starts closed (closed in the browser: the server follows through the v-model)
    client.on_connect(lambda: client.run_javascript(f"setTimeout(() => {{ {hide_if_narrow(drawer)} }}, 500)"))
    with ui.column().classes("w-full px-6 py-4 gap-4"):
        page_content()


# Each open browser window has its own sections menu and datasets menu: client id → (sections, datasets)
windows: dict[str, tuple[ui.tabs, DatasetMenu]] = {}


def main() -> None:
    args = sys.argv[1:]
    port = 8090
    if "--port" in args:
        i = args.index("--port")
        port = int(args[i + 1])
        del args[i:i + 2]
    if "--db" in args:
        i = args.index("--db")
        S.database_path = Path(args[i + 1])
        del args[i:i + 2]
    for arg in args:
        try:
            p = Path(arg)
            S.add(p.stem, eng.load_file(p), origin=notes.file_origin(p.name))
            print(f"Caricato {p.name}")
        except Exception as e:  # noqa: BLE001
            print(f"Impossibile caricare {arg}: {e}")
    nicegui_app.on_startup(load_appearance)
    ui.page("/")(main_page)
    ui.run(title="Data Explorer", reload=False, show=True, port=port, favicon="📊")


if __name__ in {"__main__", "__mp_main__"}:
    main()
