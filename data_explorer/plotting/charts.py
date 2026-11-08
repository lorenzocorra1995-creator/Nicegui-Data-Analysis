"""
Chart data (no UI dependency).

prepare(df, spec) applies filters, categories, Top N, aggregation and sorting and returns ChartData:
the drawn table, the source rows and the keys to trace back to the rows of each element.
Of the source rows ChartData keeps only the position in the dataset (no copy): `rows` reads them when needed.
The drawing (ECharts options) is in chart_options.py: changing labels or title does not recalculate the data.

Each drawn element carries its own keys ("x" = category, "s" = series, "row" = row):
a click on the chart returns them and drill() finds the matching source rows.

With a «panels» column the chart is split into one chart per value of that column: each panel is prepared
on its own rows (Top N, «others» and sorting inside the panel) and ChartData.panels holds them; the key "p"
(panel label) leads the drill-down to the right panel.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field

import numpy as np
import pandas as pd

from data_explorer.core import engine as eng
# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

OTHERS = "(altri)"
EMPTY = "(vuoto)"
ROW_COUNT = "numero righe"
CUMULATIVE = "% cumulata"
X_KEY, SERIES_KEY, PANEL_KEY = "__x__", "__serie__", "__pannello__"
ROW_KEY = "__riga_origine__"  # temporary column: position in the dataset of each row, during the preparation

MAX_CATEGORIES = 5000
MAX_SERIES = 30
MAX_POINTS = 10_000      # points drawn in the scatter plot (beyond that: random sample)
MAX_BINS = 200
DEFAULT_BINS = 20
MAX_PANELS = 24

DATE_UNITS = {"Giorno": "D", "Settimana": "W", "Mese": "M", "Trimestre": "Q", "Anno": "Y", "Nessuno": None}
DATE_FORMATS = {"Giorno": "%Y-%m-%d", "Settimana": "%Y-%m-%d", "Mese": "%Y-%m", "Anno": "%Y"}
DEFAULT_DATE_UNIT = "Mese"
DEFAULT_PANEL_DATE_UNIT = "Anno"

AUTO = "Automatico"
VALUE_DESC, VALUE_ASC = "Valore decrescente", "Valore crescente"
LABEL_ASC, LABEL_DESC = "Categorie crescenti (A→Z)", "Categorie decrescenti (Z→A)"
SORT_OPTIONS = [AUTO, VALUE_DESC, VALUE_ASC, LABEL_ASC, LABEL_DESC]
TOP, BOTTOM = "più alti", "più bassi"

BOX_STATS = ("baffo inferiore", "q1", "mediana", "q3", "baffo superiore")  # order required by ECharts
ADDITIVE = {"somma", "conteggio", "distinti", "nulli"}  # functions for which «no rows» means zero

# ---------------------------------------------------------------------------
# Catalog of the chart types
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ChartType:
    label: str
    group: str
    kind: str = "aggregate"       # data preparation: aggregate | histogram | boxplot | scatter
    draw: str = "cartesian"       # drawing: cartesian | pie | funnel | treemap | heatmap | radar | boxplot | scatter
    series_type: str = "bar"      # cartesian: bar | line
    horizontal: bool = False
    stack: bool = False
    percent: bool = False         # each bar adds up to 100%
    area: bool = False
    step: bool = False
    variant: str = ""             # donut | rose | combo | pareto | bubble
    uses_series: bool = True      # uses the «Serie / colore» column
    multi_value: bool = True      # several values become several series (otherwise only the first one is used)
    labels: bool = True           # available data labels
    auto_labels: str = "Nessuna"  # labels when the choice is «Automatiche»
    max_categories: int = MAX_CATEGORIES
    max_series: int = MAX_SERIES


CHART_TYPES = {
    "bar": ChartType("Barre verticali", "Confronto"),
    "hbar": ChartType("Barre orizzontali", "Confronto", horizontal=True),
    "stacked": ChartType("Barre in pila", "Confronto", stack=True),
    "hstacked": ChartType("Barre orizzontali in pila", "Confronto", horizontal=True, stack=True),
    "stacked100": ChartType("Barre in pila 100%", "Confronto", stack=True, percent=True),
    "hstacked100": ChartType("Barre orizzontali in pila 100%", "Confronto",
                             horizontal=True, stack=True, percent=True),
    "combo": ChartType("Barre + linee (doppio asse)", "Confronto",
                       variant="combo", uses_series=False),
    "pareto": ChartType("Pareto", "Confronto",
                        variant="pareto", uses_series=False, multi_value=False),
    "line": ChartType("Linee", "Andamento", series_type="line"),
    "step": ChartType("Linee a gradini", "Andamento", series_type="line", step=True),
    "area": ChartType("Area", "Andamento", series_type="line", area=True),
    "stacked_area": ChartType("Area in pila", "Andamento", series_type="line", area=True, stack=True),
    "pie": ChartType("Torta", "Composizione",
                     draw="pie", uses_series=False, multi_value=False,
                     auto_labels="Categoria e percentuale", max_categories=100),
    "donut": ChartType("Ciambella", "Composizione", draw="pie", variant="donut", uses_series=False,
                       multi_value=False, auto_labels="Categoria e percentuale", max_categories=100),
    "rose": ChartType("Rosa (Nightingale)", "Composizione", draw="pie", variant="rose", uses_series=False,
                      multi_value=False, auto_labels="Categoria e valore", max_categories=100),
    "treemap": ChartType("Treemap", "Composizione",
                         draw="treemap", multi_value=False, auto_labels="Categoria e valore", max_series=200),
    "funnel": ChartType("Imbuto", "Composizione",
                        draw="funnel", uses_series=False, multi_value=False,
                        auto_labels="Categoria e percentuale", max_categories=100),
    "histogram": ChartType("Istogramma", "Distribuzione",
                           kind="histogram", stack=True),
    "boxplot": ChartType("Box plot", "Distribuzione",
                         kind="boxplot", draw="boxplot", uses_series=False, labels=False),
    "scatter": ChartType("Dispersione", "Distribuzione",
                         kind="scatter", draw="scatter", labels=False),
    "bubble": ChartType("Bolle", "Distribuzione",
                        kind="scatter", draw="scatter", variant="bubble", labels=False),
    "heatmap": ChartType("Mappa di calore", "Relazioni",
                         draw="heatmap", multi_value=False, max_categories=500, max_series=200),
    "radar": ChartType("Radar", "Relazioni",
                       draw="radar", labels=False, max_categories=50),
}
SCALED_DRAWS = {"cartesian", "boxplot", "scatter", "heatmap", "radar"}  # drawings with a value scale


def used_fields(chart: ChartType) -> frozenset[str]:
    """Choices that a chart type uses (the others have no effect): columns, Top N, order, labels."""
    ranked = chart.kind in ("aggregate", "boxplot")
    conditions = {
        "x": True,
        "date_unit": ranked,
        "bins": chart.kind == "histogram",
        "y": chart.kind in ("boxplot", "scatter"),
        "size": chart.variant == "bubble",
        "series": chart.uses_series,
        "measures": chart.kind == "aggregate",
        "top_n": ranked, "top_from": ranked, "others": ranked,
        "sort": ranked and chart.variant != "pareto",
        "labels": chart.labels,
        "smooth": (chart.series_type == "line" and not chart.step) or chart.variant == "combo",
        "average": chart.draw == "cartesian" and not chart.stack,
        "panels": True, "panel_date_unit": True,
        "same_scale": chart.draw in SCALED_DRAWS and not chart.percent,
    }
    return frozenset(name for name, used in conditions.items() if used)


TYPE_OPTIONS = {key: chart.label for key, chart in CHART_TYPES.items()}


@dataclass
class ChartData:
    chart: ChartType
    table: pd.DataFrame                   # drawn data: first column = categories
    prepared_rows: pd.DataFrame | None    # rows used during the preparation; then None (see `rows`)
    x_name: str = ""
    series_name: str = ""
    value_name: str = ""                  # name of the value axis
    size_name: str = ""
    x_keys: pd.Series | None = None       # category of each source row
    series_keys: pd.Series | None = None  # series of each source row
    series_of: dict[str, str] = field(default_factory=dict)  # table column → series
    points: pd.DataFrame | None = None    # single points (scatter, outliers), with the row position
    notes: list[str] = field(default_factory=list)
    source: pd.DataFrame | None = None    # starting dataset (the same object, not a copy)
    positions: np.ndarray | None = None   # position in `source` of each source row
    panel_name: str = ""                  # column that splits the chart into panels
    panels: dict[str, ChartData] = field(default_factory=dict)  # panel label → its data (empty = one chart)

    def rows_at(self, selector=slice(None)) -> pd.DataFrame:
        """Source rows (filtered, with Top N applied) picked by `selector`, read from the dataset."""
        if self.source is None:
            return self.prepared_rows.drop(columns=ROW_KEY, errors="ignore").iloc[selector]
        return self.source.iloc[self.positions[selector]]  # with the dataset index

    @property
    def rows(self) -> pd.DataFrame:
        return self.rows_at()


# ---------------------------------------------------------------------------
# Utilities
# ---------------------------------------------------------------------------

def _count(number: int) -> str:
    return f"{number:,}".replace(",", ".")


def _is_number(values: pd.Series) -> bool:
    return pd.api.types.is_numeric_dtype(values) and not pd.api.types.is_bool_dtype(values)


def is_date(values: pd.Series) -> bool:
    return pd.api.types.is_datetime64_any_dtype(values)


def _column(rows: pd.DataFrame, name: str | None, missing: str) -> str:
    if not name:
        raise ValueError(missing)
    if name not in rows.columns:
        raise ValueError(f"La colonna «{name}» non esiste.")
    return name


def _numeric_column(rows: pd.DataFrame, name: str | None, missing: str) -> str:
    name = _column(rows, name, missing)
    if not _is_number(rows[name]):
        raise ValueError(f"La colonna «{name}» deve essere numerica.")
    return name


def _number_text(value: float, decimals: int) -> str:
    """Italian-style number without useless trailing zeros: 1.234,5"""
    text = f"{value:,.{decimals}f}".replace(",", "_").replace(".", ",").replace("_", ".")
    if "," in text:
        text = text.rstrip("0").rstrip(",")
    return "0" if text == "-0" else text


def _value_text(value) -> str:
    if isinstance(value, (bool, np.bool_)):
        return "vero" if value else "falso"
    if isinstance(value, (float, np.floating)):
        return str(int(value)) if float(value).is_integer() else f"{value:.10g}".replace(".", ",")
    return str(value)


def _drop_missing(rows: pd.DataFrame, columns: list[str], notes: list[str]) -> pd.DataFrame:
    """Drops the rows with null (or infinite) values in the columns used."""
    valid = np.ones(len(rows), dtype=bool)
    for column in dict.fromkeys(columns):
        values = rows[column]
        valid &= values.notna().to_numpy()
        if _is_number(values):
            valid &= np.isfinite(values.to_numpy(dtype="float64", na_value=np.nan))
    if not valid.any():
        raise ValueError("Nessuna riga con valori validi nelle colonne scelte.")
    if not valid.all():
        notes.append(f"{_count(int((~valid).sum()))} righe senza valore escluse.")
    return rows[valid].reset_index(drop=True)


# ---------------------------------------------------------------------------
# Categories: labels in the natural order of the values
# ---------------------------------------------------------------------------

def _labels(uniques: pd.Index, date_unit: str | None) -> list[str]:
    if isinstance(uniques, pd.DatetimeIndex):
        if date_unit == "Trimestre":
            return [f"{day.year}-T{day.quarter}" for day in uniques]
        midnight = (uniques == uniques.normalize()).all()
        fmt = DATE_FORMATS.get(date_unit) or ("%Y-%m-%d" if midnight else "%Y-%m-%d %H:%M:%S")
        return list(uniques.strftime(fmt))
    return [_value_text(value) for value in uniques]


def _factorize(values: pd.Series, date_unit: str | None) -> tuple[np.ndarray, list[str]]:
    """Code of each value and sorted labels; with mixed types the text is sorted."""
    try:
        codes, uniques = pd.factorize(values, sort=True)
        labels = _labels(uniques, date_unit)
        if len(set(labels)) == len(labels):
            return codes, labels
    except TypeError:
        pass
    codes, uniques = pd.factorize(values.astype(str).where(values.notna()), sort=True)
    return codes, [str(value) for value in uniques]


def category_keys(values: pd.Series, date_unit: str | None = None, name: str = X_KEY) -> pd.Series:
    """Label of each value as a sorted category (numbers and dates in order, nulls last).
    Dates are grouped by `date_unit` (one of the keys of DATE_UNITS)."""
    unit = DATE_UNITS.get(date_unit) if is_date(values) else None
    if unit:
        values = values.dt.tz_localize(None) if values.dt.tz is not None else values
        values = values.dt.to_period(unit).dt.start_time
    codes, labels = _factorize(values, date_unit if unit else None)
    if (codes < 0).any():
        codes = np.where(codes < 0, len(labels), codes)
        labels = [*labels, EMPTY]
    return pd.Series(pd.Categorical.from_codes(codes, categories=labels), index=values.index, name=name)


def _bins(values: pd.Series, count: int) -> tuple[list[str], pd.Series]:
    """Bins of equal width: «from – to» labels and the bin of each value."""
    count = min(max(int(count), 1), MAX_BINS)
    edges = np.histogram_bin_edges(values.to_numpy(dtype="float64"), bins=count)
    start = max(0, 1 - math.floor(math.log10(edges[1] - edges[0])))
    for decimals in range(start, start + 8):  # enough decimals to tell the edges apart
        texts = [_number_text(edge, decimals) for edge in edges]
        if len(set(texts)) == len(texts):
            break
    labels = [f"{low} – {high}" for low, high in zip(texts, texts[1:])]
    keys = pd.cut(values, edges, labels=labels, include_lowest=True).rename(X_KEY)
    return labels, keys


# ---------------------------------------------------------------------------
# Top N, «others» and sorting
# ---------------------------------------------------------------------------

def _labelled(values: pd.Series) -> pd.Series:
    """Index as text (the category labels)."""
    values.index = values.index.astype(str)
    return values


def _ranked(ranking: pd.Series, count: int, ascending: bool = False) -> list[str]:
    """The `count` categories with the highest (or lowest) value; ties follow the natural order."""
    ordered = ranking.sort_values(ascending=ascending, kind="stable", na_position="last")
    return ordered.index[:count].tolist()


def _group_others(keys: pd.Series, kept: list[str]) -> pd.Series:
    """Categories not in `kept` become OTHERS, at the end of the order."""
    grouped = keys.cat.add_categories([OTHERS])
    return grouped.where(keys.isin(kept), OTHERS).cat.remove_unused_categories()


def _apply_top(rows: pd.DataFrame, keys: pd.Series, ranking: pd.Series, spec: dict,
               notes: list[str], x_name: str, ranking_name: str) -> tuple[pd.DataFrame, pd.Series]:
    """Only the first N categories: the other rows leave the data or are merged into OTHERS."""
    count, total = int(spec.get("top_n") or 0), len(ranking)
    if count <= 0 or count >= total:
        return rows, keys
    top_from = spec.get("top_from") or TOP
    kept = _ranked(ranking, count, ascending=top_from == BOTTOM)
    note = f"Mostrati i {count} valori {top_from} di «{x_name}» su {_count(total)} (per {ranking_name})"
    if spec.get("others"):
        notes.append(f"{note}; gli altri {_count(total - count)} sono in «{OTHERS}».")
        return rows, _group_others(keys, kept)
    notes.append(f"{note}.")
    inside = keys.isin(kept).to_numpy()
    return rows[inside].reset_index(drop=True), keys[inside].cat.remove_unused_categories().reset_index(drop=True)


def _limit_series(keys: pd.Series, ranking: pd.Series, limit: int, notes: list[str]) -> pd.Series:
    if len(ranking) <= limit:
        return keys
    notes.append(f"Serie: le {limit - 1} principali su {_count(len(ranking))}, le altre in «{OTHERS}».")
    return _group_others(keys, _ranked(ranking, limit - 1))


def _resolved_sort(sort: str | None, values: pd.Series | None) -> str:
    """«Automatico»: numbers and dates in ascending order, texts from the highest value."""
    if sort and sort != AUTO:
        return sort
    ordered = values is not None and (pd.api.types.is_numeric_dtype(values) or is_date(values))
    return LABEL_ASC if ordered else VALUE_DESC


def _order(labels: list[str], ranking: pd.Series, sort: str) -> list[str]:
    """Order of the categories; OTHERS always stays last."""
    main = [label for label in labels if label != OTHERS]
    if sort in (VALUE_DESC, VALUE_ASC):
        main = _ranked(ranking.reindex(main), len(main), ascending=sort == VALUE_ASC)
    elif sort == LABEL_DESC:
        main.reverse()
    return main + [OTHERS] * (OTHERS in labels)


# ---------------------------------------------------------------------------
# Aggregation
# ---------------------------------------------------------------------------

def measure_label(measure: dict) -> str:
    return f"{measure['fn']} di {measure['col']}"


def _ranking_name(measures: list[dict]) -> str:
    return measure_label(measures[0]) if measures else ROW_COUNT


def _measures(rows: pd.DataFrame, spec: dict, chart: ChartType, notes: list[str]) -> list[dict]:
    """Valid values (existing column and known function); none = number of rows."""
    measures = [measure for measure in spec.get("measures") or []
                if measure.get("col") in rows.columns and measure.get("fn") in eng.AGG_FUNCTIONS]
    if len(measures) > 1 and not chart.multi_value:
        notes.append(f"«{chart.label}» usa solo il primo valore ({measure_label(measures[0])}).")
        measures = measures[:1]
    if chart.variant == "combo" and len(measures) < 2:
        raise ValueError("Il grafico combinato richiede almeno due valori: il primo come barre, gli altri come linee.")
    return measures


def _numeric(values: pd.DataFrame) -> pd.DataFrame:
    try:
        return values.apply(lambda column: pd.to_numeric(column) if column.dtype == object else column).astype("float64")
    except (TypeError, ValueError) as error:
        raise ValueError("I valori del grafico devono essere numeri: con colonne di testo o date "
                         "usa «conteggio» o «distinti».") from error


def _aggregate(rows: pd.DataFrame, keys: list[pd.Series], measures: list[dict]) -> pd.DataFrame:
    """One row per key combination, one column per value (number of rows if there are none)."""
    grouped = rows.groupby(keys, observed=True, sort=True)
    if not measures:
        return grouped.size().to_frame(ROW_COUNT).astype("float64")
    named = {measure_label(m): (m["col"], m["fn"]) for m in measures}
    return _numeric(eng.aggregate(rows, grouped, named))


def _ranking(rows: pd.DataFrame, keys: pd.Series, measures: list[dict]) -> pd.Series:
    """First value (or number of rows) per category: decides Top N, series limit and order."""
    return _labelled(_aggregate(rows, [keys], measures[:1]).iloc[:, 0])


def _series_keys(rows: pd.DataFrame, spec: dict, chart: ChartType, measures: list[dict],
                 notes: list[str]) -> pd.Series | None:
    column = spec.get("series")
    if not chart.uses_series or not column or column == spec.get("x"):
        return None
    _column(rows, column, "")
    keys = category_keys(rows[column], name=SERIES_KEY)
    return _limit_series(keys, _ranking(rows, keys, measures), chart.max_series, notes)


def _wide_table(rows: pd.DataFrame, x_keys: pd.Series, series_keys: pd.Series | None,
                measures: list[dict]) -> tuple[pd.DataFrame, dict[str, str]]:
    """One row per category, one column per series (or per value). Also returns column → series."""
    if series_keys is None:
        return _labelled(_aggregate(rows, [x_keys], measures)), {}
    values = _aggregate(rows, [x_keys, series_keys], measures).unstack(SERIES_KEY)
    additive = {measure_label(m) for m in measures if m["fn"] in ADDITIVE} if measures else {ROW_COUNT}
    filled = values.columns.get_level_values(0).isin(additive)
    values.loc[:, filled] = values.loc[:, filled].fillna(0)  # no rows = sum and counts at zero
    single = values.columns.get_level_values(0).nunique() == 1
    names = [str(series) if single else f"{measure} | {series}" for measure, series in values.columns]
    series_of = {name: str(series) for name, (_, series) in zip(names, values.columns)}
    values.columns = names
    return _labelled(values), series_of


def _with_categories(values: pd.DataFrame, name: str) -> pd.DataFrame:
    """Table to show: the categories become the first column."""
    table = values.reset_index(drop=True)
    table.insert(0, name, values.index.tolist(), allow_duplicates=True)
    return table


def _row_percent(values: pd.DataFrame) -> pd.DataFrame:
    totals = values.sum(axis=1).replace(0, np.nan)
    return values.div(totals, axis=0) * 100


def _cumulative_percent(values: pd.Series) -> pd.Series:
    total = values.sum()
    return values.cumsum() / total * 100 if total else values * np.nan


# ---------------------------------------------------------------------------
# Data preparation, per chart family
# ---------------------------------------------------------------------------

def _prepare_aggregate(rows: pd.DataFrame, spec: dict, chart: ChartType) -> ChartData:
    notes: list[str] = []
    x = _column(rows, spec.get("x"), "Scegli la colonna dell'asse X.")
    measures = _measures(rows, spec, chart, notes)
    x_keys = category_keys(rows[x], spec.get("date_unit", DEFAULT_DATE_UNIT))
    ranking = _ranking(rows, x_keys, measures)
    rows, x_keys = _apply_top(rows, x_keys, ranking, spec, notes, x, _ranking_name(measures))
    count = len(x_keys.cat.categories)
    if count > chart.max_categories:
        raise ValueError(f"Troppe categorie sull'asse X per «{chart.label}»: {_count(count)} "
                         f"(massimo {_count(chart.max_categories)}). "
                         "Usa «Mostra solo i primi» oppure raggruppa le date.")
    series_keys = _series_keys(rows, spec, chart, measures, notes)
    if chart.draw == "heatmap" and series_keys is None:
        raise ValueError("La mappa di calore richiede la colonna «Serie / colore» (le righe della mappa).")

    values, series_of = _wide_table(rows, x_keys, series_keys, measures)
    sort = VALUE_DESC if chart.variant == "pareto" else _resolved_sort(spec.get("sort"), rows[x])
    values = values.loc[_order(values.index.tolist(), ranking, sort)]
    if chart.draw in ("pie", "funnel", "treemap") and (skipped := int((values.to_numpy() <= 0).sum())):
        notes.append(f"{_count(skipped)} valori nulli o negativi non disegnati.")
    if chart.percent:
        values = _row_percent(values)
    if chart.variant == "pareto":
        values[CUMULATIVE] = _cumulative_percent(values.iloc[:, 0])

    value_name = "% sul totale della categoria" if chart.percent else (
        _ranking_name(measures) if len(measures) <= 1 else "")
    return ChartData(chart, _with_categories(values, x), rows, x_name=x, series_name=spec.get("series") or "",
                     value_name=value_name, x_keys=x_keys, series_keys=series_keys, series_of=series_of,
                     notes=notes)


def _prepare_histogram(rows: pd.DataFrame, spec: dict, chart: ChartType) -> ChartData:
    notes: list[str] = []
    x = _numeric_column(rows, spec.get("x"), "Scegli la colonna numerica da dividere in classi (asse X).")
    rows = _drop_missing(rows, [x], notes)
    labels, x_keys = _bins(rows[x], spec.get("bins") or DEFAULT_BINS)
    series_keys = _series_keys(rows, spec, chart, [], notes)
    values, series_of = _wide_table(rows, x_keys, series_keys, [])
    values = values.reindex(labels).fillna(0)  # empty bins too
    name = f"{x} (classi)"
    return ChartData(chart, _with_categories(values, name), rows, x_name=name,
                     series_name=spec.get("series") if series_keys is not None else "",
                     value_name=ROW_COUNT, x_keys=x_keys, series_keys=series_keys, series_of=series_of,
                     notes=notes)


def _box_stats(values: pd.Series, keys: pd.Series) -> tuple[pd.DataFrame, np.ndarray]:
    """Statistics per category (whiskers at 1.5 × IQR) and mask of the outliers."""
    grouped = values.groupby(keys, observed=True)
    q1, q3 = grouped.quantile(0.25), grouped.quantile(0.75)
    categories = keys.cat.categories
    codes = keys.cat.codes.to_numpy()
    low = (q1 - 1.5 * (q3 - q1)).reindex(categories).to_numpy()[codes]
    high = (q3 + 1.5 * (q3 - q1)).reindex(categories).to_numpy()[codes]
    numbers = values.to_numpy(dtype="float64")
    inside = (numbers >= low) & (numbers <= high)
    whiskers = values[inside].groupby(keys[inside], observed=True)
    stats = pd.DataFrame({
        "n": grouped.size(), "minimo": grouped.min(),
        "baffo inferiore": whiskers.min(), "q1": q1, "mediana": grouped.median(), "q3": q3,
        "baffo superiore": whiskers.max(), "massimo": grouped.max(),
        "anomali": pd.Series(~inside, index=values.index).groupby(keys, observed=True).sum(),
    })
    return _labelled(stats), ~inside


def _prepare_boxplot(rows: pd.DataFrame, spec: dict, chart: ChartType) -> ChartData:
    notes: list[str] = []
    y = _numeric_column(rows, spec.get("y"), "Scegli la colonna numerica dei valori (asse Y).")
    rows = _drop_missing(rows, [y], notes)
    x = spec.get("x") or ""
    if x:
        _column(rows, x, "")
        x_keys = category_keys(rows[x], spec.get("date_unit", DEFAULT_DATE_UNIT))
    else:
        x_keys = pd.Series(pd.Categorical([y] * len(rows)), name=X_KEY)
    ranking = _labelled(rows[y].groupby(x_keys, observed=True).median())
    rows, x_keys = _apply_top(rows, x_keys, ranking, spec, notes, x, f"mediana di {y}")
    if len(x_keys.cat.categories) > chart.max_categories:
        raise ValueError(f"Troppe categorie per il box plot: usa «Mostra solo i primi» (massimo "
                         f"{_count(chart.max_categories)}).")

    stats, outside = _box_stats(rows[y], x_keys)
    stats = stats.loc[_order(stats.index.tolist(), ranking, _resolved_sort(spec.get("sort"), rows[x] if x else None))]
    positions = np.flatnonzero(outside)
    if len(positions) > MAX_POINTS:
        notes.append(f"Disegnati {_count(MAX_POINTS)} valori anomali su {_count(len(positions))}.")
        positions = np.sort(np.random.default_rng(0).choice(positions, MAX_POINTS, replace=False))
    points = pd.DataFrame({"x": x_keys.to_numpy()[positions].astype(str),
                           "y": rows[y].to_numpy(dtype="float64")[positions], "row": positions})
    return ChartData(chart, _with_categories(stats, x or "colonna"), rows, x_name=x, value_name=y,
                     x_keys=x_keys, points=points, notes=notes)


def _prepare_scatter(rows: pd.DataFrame, spec: dict, chart: ChartType) -> ChartData:
    notes: list[str] = []
    x = _column(rows, spec.get("x"), "Scegli la colonna dell'asse X (numeri o date).")
    if not (_is_number(rows[x]) or is_date(rows[x])):
        raise ValueError("Nella dispersione l'asse X deve essere una colonna numerica o di date.")
    y = _numeric_column(rows, spec.get("y"), "Scegli la colonna numerica dell'asse Y.")
    columns = [x, y]
    size = ""
    if chart.variant == "bubble":
        size = _numeric_column(rows, spec.get("size"), "Scegli la colonna numerica della dimensione delle bolle.")
        columns.append(size)
    rows = _drop_missing(rows, columns, notes)
    series_keys = _series_keys(rows, spec, chart, [], notes)

    positions = np.arange(len(rows))
    if len(rows) > MAX_POINTS:
        notes.append(f"Disegnati {_count(MAX_POINTS)} punti scelti a caso su {_count(len(rows))}.")
        positions = np.sort(np.random.default_rng(0).choice(len(rows), MAX_POINTS, replace=False))
    sample = rows.iloc[positions]
    points = pd.DataFrame({"x": sample[x].to_numpy(), "y": sample[y].to_numpy(dtype="float64"), "row": positions})
    if size:
        points["size"] = sample[size].to_numpy(dtype="float64")
    series = spec.get("series") if series_keys is not None else ""
    if series:
        points["series"] = series_keys.to_numpy()[positions].astype(str)
        columns.append(series)
    table = sample[list(dict.fromkeys(columns))].reset_index(drop=True)
    return ChartData(chart, table, rows, x_name=x, series_name=series, value_name=y, size_name=size,
                     series_keys=series_keys, points=points, notes=notes)


PREPARERS = {
    "aggregate": _prepare_aggregate,
    "histogram": _prepare_histogram,
    "boxplot": _prepare_boxplot,
    "scatter": _prepare_scatter,
}


# ---------------------------------------------------------------------------
# Panels: one chart per value of a column
# ---------------------------------------------------------------------------

def _panel_notes(panels: dict[str, ChartData]) -> list[str]:
    """Notes of the panels: once if every panel has it, otherwise with the panels that have it."""
    owners: dict[str, list[str]] = {}
    for label, panel in panels.items():
        for note in panel.notes:
            owners.setdefault(note, []).append(label)
    return [note if len(labels) == len(panels) else f"{', '.join(labels)}: {note}" for note, labels in owners.items()]


def _panel_table(panels: dict[str, ChartData], name: str) -> pd.DataFrame:
    """Tables of the panels one under the other, with the panel in the first column."""
    tables = []
    for label, panel in panels.items():
        table = panel.table.copy()
        table.insert(0, name if name not in table.columns else f"{name} (pannello)", label)
        tables.append(table)
    return pd.concat(tables, ignore_index=True, sort=False)


def _prepare_panels(rows: pd.DataFrame, spec: dict, chart: ChartType) -> ChartData:
    """One chart per value of the «panels» column, each prepared on its own rows."""
    name = _column(rows, spec.get("panels"), "")
    if name == spec.get("x"):
        raise ValueError("La colonna dei pannelli deve essere diversa da quella dell'asse X.")
    keys = category_keys(rows[name], spec.get("panel_date_unit") or DEFAULT_PANEL_DATE_UNIT, PANEL_KEY)
    labels = list(keys.cat.categories)
    if len(labels) > MAX_PANELS:
        raise ValueError(f"Troppi pannelli: {_count(len(labels))} valori di «{name}» (massimo {MAX_PANELS}). "
                         "Filtra i dati o raggruppa le date.")
    codes = keys.cat.codes.to_numpy()
    panels: dict[str, ChartData] = {}
    errors: list[tuple[str, ValueError]] = []  # panels that cannot be drawn: a note (all of them: the error)
    for code, label in enumerate(labels):
        try:
            panels[label] = PREPARERS[chart.kind](rows[codes == code].reset_index(drop=True), spec, chart)
        except ValueError as error:
            errors.append((label, error))
    if not panels:
        raise errors[0][1]
    first = next(iter(panels.values()))
    prepared = pd.concat([panel.prepared_rows[[ROW_KEY]] for panel in panels.values()], ignore_index=True)
    return ChartData(chart, _panel_table(panels, name), prepared, x_name=first.x_name,
                     series_name=first.series_name, value_name=first.value_name, size_name=first.size_name,
                     notes=[*(f"{label}: {error}" for label, error in errors), *_panel_notes(panels)], panel_name=name, panels=panels)


def _attach_source(data: ChartData, df: pd.DataFrame) -> None:
    """The chart (and each panel) keeps only the positions of its rows in the dataset."""
    data.source, data.positions = df, data.prepared_rows[ROW_KEY].to_numpy()
    data.prepared_rows = None  # frees the filtered copy
    for panel in data.panels.values():
        _attach_source(panel, df)


def prepare(df: pd.DataFrame, spec: dict) -> ChartData:
    """
    Data of a chart (the original does not change).

    spec (JSON-serializable):
      chart: key of CHART_TYPES; filters: filter tree (as in the other tabs)
      x: categories (or numeric column for histogram and scatter); series: column that splits into series
      measures: [{"col": ..., "fn": ...}] as in Group By (empty = number of rows)
      y, size: numeric columns of box plot, scatter and bubbles
      date_unit: grouping of the dates on the X axis (key of DATE_UNITS); bins: histogram bins
      top_n, top_from (TOP | BOTTOM), others: only the first N categories, the others dropped or in OTHERS
      sort: one of SORT_OPTIONS
      panels: column that splits the chart into one chart per value (Top N and sorting inside each panel);
      panel_date_unit: grouping of a date panels column (key of DATE_UNITS)
    """
    chart = CHART_TYPES.get(spec.get("chart") or "bar")
    if chart is None:
        raise ValueError(f"Tipo di grafico sconosciuto: {spec.get('chart')}")
    mask = eng.filter_mask(df, spec.get("filters")).to_numpy(dtype=bool)
    if not mask.any():
        raise ValueError("Nessuna riga da disegnare: controlla i filtri.")
    rows = df.assign(**{ROW_KEY: np.arange(len(df))})  # new column; the others stay shared with df
    rows = (rows if mask.all() else rows[mask]).reset_index(drop=True)
    data = _prepare_panels(rows, spec, chart) if spec.get("panels") else PREPARERS[chart.kind](rows, spec, chart)
    _attach_source(data, df)
    return data


# ---------------------------------------------------------------------------
# From the chart to the source rows
# ---------------------------------------------------------------------------

def drill_positions(data: ChartData, item: dict) -> np.ndarray:
    """Positions in the dataset of the source rows of the clicked element
    (the "p", "x", "s", "row" keys of the ECharts data item)."""
    if "p" in item and data.panels:
        return drill_positions(data.panels[item["p"]], {key: value for key, value in item.items() if key != "p"})
    if "row" in item:
        return data.positions[[int(item["row"])]]
    mask = np.ones(len(data.positions), dtype=bool)
    if "x" in item and data.x_keys is not None:
        mask &= (data.x_keys == item["x"]).to_numpy()
    if "s" in item and data.series_keys is not None:
        mask &= (data.series_keys == item["s"]).to_numpy()
    return data.positions[mask]


def drill(data: ChartData, item: dict) -> pd.DataFrame:
    """Source rows of the clicked element, read from the dataset (with its index)."""
    return data.source.iloc[drill_positions(data, item)]


def selection_text(data: ChartData, item: dict) -> str:
    if "p" in item and data.panels:
        rest = {key: value for key, value in item.items() if key != "p"}
        return f"{data.panel_name} = {item['p']} · {selection_text(data.panels[item['p']], rest)}"
    if "row" in item:
        return "punto selezionato"
    parts = []
    if "x" in item and data.x_name:
        parts.append(f"{data.x_name} = {item['x']}")
    if "s" in item and data.series_name:
        parts.append(f"{data.series_name} = {item['s']}")
    return " · ".join(parts) or "elemento selezionato"
