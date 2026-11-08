"""
Dashboards written in the Python console (no UI dependency).

The code of the console describes a dashboard (dataset, slicers, pages, visuals); app.py draws it:

    dash = Dashboard(df, "Vendite")
    dash.slicer("prodotto")
    dash.kpi("Fatturato", "importo", "somma")
    dash.chart("line", x="data", value="importo", date_unit="Mese", title="Andamento")
    dash.table(groups=["categoria"], value="importo", title="Per categoria")
    dash

Every visual is calculated on the rows that pass the slicers. A click on a chart element filters the other
visuals on its source rows (cross filter, as in Power BI); a second click on the same element removes it.
The builder methods return the dashboard, so the last line of the console can be any of them.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable

import numpy as np
import pandas as pd

from data_explorer.plotting import charts
from data_explorer.plotting import chart_options
from data_explorer.core import engine as eng
from data_explorer.core import pivot
GRID_COLUMNS = 12  # width of the dashboard grid; each visual takes 1–12 columns
DEFAULT_WIDTHS = {"kpi": 3, "chart": 6, "table": 12, "pivot": 12, "python": 6, "text": 12}
DEFAULT_HEIGHT = 360  # charts, in pixels
MAX_SLICER_OPTIONS = 5000  # values of a choice slicer
DEFAULT_PAGE = "Pagina 1"

MULTI, SINGLE, RANGE = "multi", "single", "range"
SLICER_KINDS = (MULTI, SINGLE, RANGE)

CHART_FIELDS = {"series", "y", "size", "bins", "date_unit", "top_n", "top_from", "others", "sort", "panels",
                "panel_date_unit"}  # charts.prepare keys accepted by Dashboard.chart besides x and the measures
VIEW_FIELDS = {"labels", "decimals", "compact", "smooth", "average"}  # chart_options.View keys
DRILL_KEYS = ("p", "x", "s", "row")  # keys of a clicked chart element that lead to its source rows (charts.drill)


# ---------------------------------------------------------------------------
# Parts of a dashboard
# ---------------------------------------------------------------------------

@dataclass
class Slicer:
    column: str
    kind: str                                         # one of SLICER_KINDS
    label: str
    options: list[str] = field(default_factory=list)  # choice slicers: the values of the column
    bounds: tuple = ()                                # range slicers: lowest and highest value
    dates: bool = False                               # range on a date column (bounds as YYYY-MM-DD)
    value: object = None                              # multi: list; single: text; range: (low, high); empty = all

    def conditions(self) -> list[dict]:
        """Filter conditions of the current choice (none = all the rows)."""
        if self.kind == MULTI:
            return [eng.new_condition(self.column, "in lista", list(self.value))] if self.value else []
        if self.kind == SINGLE:
            return [eng.new_condition(self.column, "in lista", [self.value])] if self.value else []
        low, high = self.value or (None, None)
        limits = ((">=", low), ("<=", high))
        return [eng.new_condition(self.column, op, str(limit)) for op, limit in limits if limit not in (None, "")]


@dataclass
class Visual:
    id: int
    kind: str           # kpi | chart | table | pivot | python | text
    title: str
    width: int
    spec: dict          # what to calculate (see the builder methods of Dashboard)
    height: int = DEFAULT_HEIGHT
    view: dict = field(default_factory=dict)  # charts: chart_options.View fields


@dataclass
class Page:
    title: str
    visuals: list[Visual] = field(default_factory=list)


@dataclass
class Output:
    """Result of a visual: value by kind (kpi: number, chart: ChartData, table: DataFrame, pivot: PivotTable,
    python: (engine.chart_kind or "table" or "text", value), text: str) or the error text."""
    value: object = None
    error: str = ""
    positions: np.ndarray | None = None  # dataset rows the visual was calculated on (charts: cross filter)


@dataclass
class CrossFilter:
    visual: int  # id of the clicked chart
    item: dict   # clicked ECharts data item
    text: str    # description of the selection


# ---------------------------------------------------------------------------
# Checks of the written code: clear messages with the available names
# ---------------------------------------------------------------------------

def _check_columns(df: pd.DataFrame, columns, what: str) -> None:
    for column in columns:
        if column not in df.columns:
            available = ", ".join(map(str, df.columns))
            raise ValueError(f"{what}: colonna «{column}» non trovata. Colonne disponibili: {available}")


def chart_key(name: str) -> str:
    """Key of charts.CHART_TYPES from a key ("line") or a label ("Linee"), ignoring upper/lower case."""
    wanted = str(name).strip().lower()
    for key, chart in charts.CHART_TYPES.items():
        if wanted in (key, chart.label.lower()):
            return key
    available = ", ".join(f"{key} ({chart.label})" for key, chart in charts.CHART_TYPES.items())
    raise ValueError(f"Tipo di grafico sconosciuto: «{name}». Tipi disponibili: {available}")


def measures_of(df: pd.DataFrame, value=None, fn: str = "somma", measures=None) -> list[dict]:
    """Measures [{"col", "fn"}] from `value` + `fn` or from `measures`: column names (with `fn`),
    (column, function) pairs or dicts as in Group By."""
    items = measures if measures is not None else ([value] if value is not None else [])
    result = []
    for item in [items] if isinstance(items, (str, dict)) else items:
        if isinstance(item, str):
            measure = {"col": item, "fn": fn}
        elif isinstance(item, dict) and "col" in item:
            measure = {"col": item["col"], "fn": item.get("fn", fn), "name": item.get("name", "")}
        elif isinstance(item, (tuple, list)) and len(item) == 2:
            measure = {"col": item[0], "fn": item[1]}
        else:
            raise ValueError(f"Misura non valida: {item!r} (usa \"colonna\", (\"colonna\", \"funzione\") "
                             "o {\"col\": ..., \"fn\": ...})")
        if measure["fn"] not in eng.AGG_FUNCTIONS:
            raise ValueError(f"Funzione sconosciuta: «{measure['fn']}». Funzioni: {', '.join(eng.AGG_FUNCTIONS)}")
        result.append(measure)
    _check_columns(df, [measure["col"] for measure in result], "Misura")
    eng.check_measures(df, result)
    return result


def _split_options(options: dict, accepted: set[str], what: str) -> None:
    unknown = set(options) - accepted
    if unknown:
        raise ValueError(f"{what}: opzioni sconosciute {', '.join(sorted(unknown))}. "
                         f"Opzioni ammesse: {', '.join(sorted(accepted))}")


# ---------------------------------------------------------------------------
# The dashboard
# ---------------------------------------------------------------------------

class Dashboard:
    def __init__(self, df: pd.DataFrame, title: str = "") -> None:
        if not isinstance(df, pd.DataFrame):
            raise ValueError("Dashboard(df, titolo): il primo argomento deve essere un DataFrame (es. df).")
        self.df = df
        self.title = title
        self.slicers: list[Slicer] = []
        self.pages: list[Page] = []
        self.cross: CrossFilter | None = None

    # --- builders ------------------------------------------------------------
    def page(self, title: str) -> Dashboard:
        """New page: the visuals written next go on it."""
        self.pages.append(Page(title))
        return self

    def slicer(self, column: str, kind: str | None = None, label: str = "", value=None) -> Dashboard:
        """Filter of the whole dashboard. kind: "multi" (several values, default for text), "single" (one value)
        or "range" (from–to, default for numbers and dates); value: initial choice."""
        _check_columns(self.df, [column], "Slicer")
        values = self.df[column]
        numeric = pd.api.types.is_numeric_dtype(values) and not pd.api.types.is_bool_dtype(values)
        dates = charts.is_date(values)
        kind = kind or (RANGE if numeric or dates else MULTI)
        if kind not in SLICER_KINDS:
            raise ValueError(f"Slicer: tipo «{kind}» sconosciuto (usa {', '.join(SLICER_KINDS)})")
        slicer = Slicer(column, kind, label or str(column), dates=dates)
        if kind == RANGE:
            if not (numeric or dates):
                raise ValueError(f"Slicer «{column}»: l'intervallo vale solo per numeri e date")
            low, high = values.min(), values.max()
            slicer.bounds = (f"{low:%Y-%m-%d}", f"{high:%Y-%m-%d}") if dates else (float(low), float(high))
            slicer.value = tuple(value) if value else (None, None)
        else:
            slicer.options = eng.unique_values(self.df, column)
            if len(slicer.options) > MAX_SLICER_OPTIONS:
                raise ValueError(f"Slicer «{column}»: {len(slicer.options)} valori distinti, troppi per un elenco "
                                 f"(massimo {MAX_SLICER_OPTIONS}). Usa kind=\"range\" o un'altra colonna.")
            chosen = [str(item) for item in (value if isinstance(value, (list, tuple)) else [value]) if item is not None]
            slicer.value = chosen if kind == MULTI else (chosen[0] if chosen else None)
        self.slicers.append(slicer)
        return self

    def kpi(self, title: str, column: str | None = None, fn: str | None = None, decimals: int = 0,
            suffix: str = "", width: int | None = None) -> Dashboard:
        """Big number: `fn` of `column` (default "somma"); without a column, the number of rows."""
        if column is None:
            measures = []
        else:
            measures = measures_of(self.df, column, fn or "somma")
        return self._add("kpi", title, width, {"measures": measures, "decimals": int(decimals), "suffix": suffix})

    def chart(self, chart: str = "bar", x: str | None = None, value=None, fn: str = "somma", measures=None,
              title: str = "", width: int | None = None, height: int = DEFAULT_HEIGHT, **options) -> Dashboard:
        """Chart of the app (types of charts.CHART_TYPES, key or label). Data options: series, y, size, bins,
        date_unit, top_n, top_from, others, sort, panels; look options: labels, decimals, compact, smooth, average."""
        _split_options(options, CHART_FIELDS | VIEW_FIELDS, "Grafico")
        key = chart_key(chart)
        spec = {"chart": key, "x": x, "measures": measures_of(self.df, value, fn, measures),
                **{name: item for name, item in options.items() if name in CHART_FIELDS}}
        _check_columns(self.df, [column for column in (x, spec.get("series"), spec.get("y"), spec.get("size"),
                                                       spec.get("panels")) if column], "Grafico")
        view = {name: item for name, item in options.items() if name in VIEW_FIELDS}
        return self._add("chart", title, width, spec, height, view)

    def table(self, columns: list[str] | None = None, groups: list[str] | None = None, value=None, fn: str = "somma",
              measures=None, sort_by: str | None = None, descending: bool = True, limit: int | None = None,
              title: str = "", width: int | None = None) -> Dashboard:
        """Table: with `groups` a Group By of the measures, otherwise the rows (only `columns`, if given)."""
        groups = [groups] if isinstance(groups, str) else list(groups or [])
        columns = [columns] if isinstance(columns, str) else columns
        _check_columns(self.df, [*groups, *(columns or [])], "Tabella")
        spec = {"groups": groups, "measures": measures_of(self.df, value, fn, measures) if groups else [],
                "columns": columns, "sort_by": sort_by, "descending": descending, "limit": limit}
        return self._add("table", title, width, spec)

    def pivot(self, rows, columns=None, value=None, fn: str = "somma", measures=None, dates: dict | None = None,
              totals: bool = True, title: str = "", width: int | None = None) -> Dashboard:
        """Pivot (as in the Pivot section): `dates` = {date column: grouping of charts.DATE_UNITS}."""
        rows = [rows] if isinstance(rows, str) else list(rows)
        columns = [columns] if isinstance(columns, str) else list(columns or [])
        _check_columns(self.df, [*rows, *columns], "Pivot")
        values = measures_of(self.df, value, fn, measures) or [{"col": rows[0], "fn": "conteggio"}]
        spec = {"rows": rows, "columns": columns, "values": values, "dates": dict(dates or {}), "totals": totals}
        return self._add("pivot", title, width, spec)

    def python(self, function: Callable[[pd.DataFrame], object], title: str = "", width: int | None = None,
               height: int = DEFAULT_HEIGHT) -> Dashboard:
        """Free visual: `function(rows)` receives the filtered rows and returns a table, a chart (ECharts options,
        matplotlib or plotly figure) or a value."""
        if not callable(function):
            raise ValueError("python(funzione): serve una funzione che riceve le righe filtrate, es. lambda d: ...")
        return self._add("python", title, width, {"function": function}, height)

    def text(self, text: str, width: int | None = None) -> Dashboard:
        """Text in Markdown (titles, comments)."""
        return self._add("text", "", width, {"text": str(text)})

    def _add(self, kind: str, title: str, width: int | None, spec: dict, height: int = DEFAULT_HEIGHT,
             view: dict | None = None) -> Dashboard:
        if not self.pages:
            self.pages.append(Page(DEFAULT_PAGE))
        width = DEFAULT_WIDTHS[kind] if width is None else int(width)
        if not 1 <= width <= GRID_COLUMNS:
            raise ValueError(f"width deve essere tra 1 e {GRID_COLUMNS} (colonne della griglia)")
        visual = Visual(sum(len(page.visuals) for page in self.pages), kind, str(title or ""), width, spec,
                        int(height), view or {})
        self.pages[-1].visuals.append(visual)
        return self

    # --- filters -------------------------------------------------------------
    def filter_tree(self) -> dict:
        """Filter tree of the slicers (as in the other sections)."""
        tree = eng.new_group()
        tree["condizioni"] = [condition for slicer in self.slicers for condition in slicer.conditions()]
        return tree

    def select(self, visual: Visual, output: Output, item: dict) -> bool:
        """Click on an element of a chart: cross filter on its rows; the same element again removes it.
        False if the clicked element has no source rows (e.g. the average line)."""
        item = {key: item[key] for key in DRILL_KEYS if key in item}
        if not item or output.error:
            return False
        if self.cross and self.cross.visual == visual.id and self.cross.item == item:
            self.cross = None
            return True
        name = visual.title or output.value.x_name
        self.cross = CrossFilter(visual.id, item, f"{name}: {charts.selection_text(output.value, item)}")
        return True

    def clear_selection(self) -> None:
        self.cross = None

    # --- calculation ---------------------------------------------------------
    def compute(self, page: int = 0) -> dict[int, Output]:
        """Outputs of the visuals of a page (visual id → Output). The clicked chart is calculated first, on the
        slicers only: the rows of the clicked element filter the other visuals."""
        visuals = self.pages[page].visuals
        base = eng.filter_mask(self.df, self.filter_tree()).to_numpy(dtype=bool)
        outputs, crossed = {}, base
        source = next((visual for visual in visuals if self.cross and visual.id == self.cross.visual), None)
        if source is not None:
            output = outputs[source.id] = self._output(source, base)
            if not output.error:
                crossed = np.zeros(len(self.df), dtype=bool)
                crossed[output.positions[charts.drill_positions(output.value, self.cross.item)]] = True
        for visual in visuals:
            if visual.id not in outputs:
                outputs[visual.id] = self._output(visual, crossed)
        return {visual.id: outputs[visual.id] for visual in visuals}

    def _output(self, visual: Visual, mask: np.ndarray) -> Output:
        if visual.kind == "text":
            return Output(visual.spec["text"])
        positions = np.flatnonzero(mask)
        if not len(positions):
            return Output(error="Nessuna riga con i filtri scelti.")
        rows = self.df if len(positions) == len(self.df) else self.df.iloc[positions]
        try:
            return Output(CALCULATIONS[visual.kind](rows, visual.spec), positions=positions)
        except Exception as e:  # noqa: BLE001  the other visuals are still shown
            return Output(error=str(e))


# ---------------------------------------------------------------------------
# Calculation of each kind of visual (rows already filtered)
# ---------------------------------------------------------------------------

def _kpi(rows: pd.DataFrame, spec: dict) -> float | None:
    if not spec["measures"]:
        return float(len(rows))
    value = eng.run_groupby(rows, [], spec["measures"]).iloc[0, 0]
    return None if pd.isna(value) else float(value)


def _table(rows: pd.DataFrame, spec: dict) -> pd.DataFrame:
    if spec["groups"]:
        table = eng.run_groupby(rows, spec["groups"], spec["measures"])
    else:
        table = rows[spec["columns"]] if spec["columns"] else rows
    if spec["sort_by"]:
        _check_columns(table, [spec["sort_by"]], "Ordinamento della tabella")
        table = table.sort_values(spec["sort_by"], ascending=not spec["descending"])
    return table.head(spec["limit"]) if spec["limit"] else table


def _pivot(rows: pd.DataFrame, spec: dict) -> pivot.PivotTable:
    layout = {"grand_rows": spec["totals"], "grand_columns": spec["totals"]}
    return pivot.run_pivot(rows, spec, layout)


def _python(rows: pd.DataFrame, spec: dict) -> tuple[str, object]:
    value = spec["function"](rows)
    if (kind := eng.chart_kind(value)) is not None:
        return kind, value
    if (table := eng.to_display_frame(value)) is not None:
        return "table", table
    return "text", "" if value is None else str(value)


CALCULATIONS = {"kpi": _kpi, "chart": charts.prepare, "table": _table, "pivot": _pivot, "python": _python}


def chart_panels(visual: Visual, data: charts.ChartData, look: dict) -> list[tuple[str, dict]]:
    """(panel label, ECharts options) of a dashboard chart: the written choices over the look of the app;
    the title is on the card."""
    return chart_options.build_panels(data, {**look, **visual.view, "title": ""})


# ---------------------------------------------------------------------------
# Example for the console, with the columns of the dataset
# ---------------------------------------------------------------------------

def example_code(df: pd.DataFrame) -> str:
    numbers = [column for column in df.select_dtypes("number").columns if not pd.api.types.is_bool_dtype(df[column])]
    dates = [column for column in df.columns if charts.is_date(df[column])]
    texts = [column for column in df.columns if column not in numbers and column not in dates]
    category = texts[0] if texts else df.columns[0]
    value = numbers[0] if numbers else None
    lines = ["# Dashboard: l'ultima riga restituisce la dashboard (slicer in alto, visual su una griglia di 12 colonne).",
             "# Un clic su un elemento di un grafico filtra gli altri visual; un secondo clic toglie il filtro.",
             "dash = Dashboard(df, \"Esempio\")",
             f"dash.slicer({str(category)!r})"]
    if dates:
        lines.append(f"dash.slicer({str(dates[0])!r})  # intervallo di date")
    lines.append("dash.kpi(\"Righe\")")
    if value is not None:
        lines += [f"dash.kpi(\"Totale\", {str(value)!r}, \"somma\")",
                  f"dash.kpi(\"Media\", {str(value)!r}, \"media\", decimals=2)"]
    measure = f", value={str(value)!r}" if value is not None else ""
    lines.append(f"dash.chart(\"bar\", x={str(category)!r}{measure}, title=\"Per {category}\", sort=\"Valore decrescente\")")
    if dates:
        lines.append(f"dash.chart(\"line\", x={str(dates[0])!r}{measure}, date_unit=\"Mese\", title=\"Andamento\")")
    lines.append(f"dash.table(groups=[{str(category)!r}]{measure}, title=\"Tabella\")")
    lines += ["dash.page(\"Dettaglio\")",
              "dash.python(lambda d: d.head(100), title=\"Prime righe\", width=12)",
              "dash"]
    return "\n".join(lines) + "\n"
