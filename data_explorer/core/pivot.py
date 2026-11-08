"""
Pivot as a dynamic cube, modelled on Excel pivot tables (no UI dependency).

Building (`build_cube`), slow, once per choice of fields and filters:
  - the row and column fields become sorted integer codes, as in the Excel cache
    (one dictionary of labels per field, dates grouped by year, quarter, month…);
  - all the levels are calculated together: detail, subtotals and totals of rows and columns;
    decomposable functions (sum, count, min, max, nulls) are derived from the detail
    without reading the data again, the others (mean, median, distinct…) from the data of each level.

Presentation (`render`), immediate: «Mostra valori come», visible total levels,
sorting, empty cells and names. It does not aggregate anything again: that is why it can change live.

Cube spec:     {"rows": [field], "columns": [field], "values": [measure], "filters": tree,
                "dates": {date field: unit of charts.DATE_UNITS}}
Measure:       {"col", "fn", "name", "show_as", "relative_to", "base_field", "base_item"}
Layout:        {"grand_rows", "grand_columns", "subtotal_rows", "subtotal_columns",
                "fill_zero", "naming", "sort_by" (None = labels, or index of the measure), "descending"}
"""
from __future__ import annotations

import copy
import json
from dataclasses import dataclass
from functools import cached_property

import numpy as np
import pandas as pd

from data_explorer.plotting import charts
from data_explorer.core import engine as eng
TOTAL = -1  # code of «all the items» of a field: the total rows and columns
TOTAL_LABEL = "Totale"
MAX_COLUMNS = 500  # value columns above which the grid would freeze the browser
ROW_LEVEL, COLUMN_LEVEL = "_row_level", "_column_level"

# functions obtained from the already aggregated detail, without reading the data again (rollup)
ROLLUP = {"somma": "sum", "conteggio": "sum", "min": "min", "max": "max", "nulli": "sum"}


# ---------------------------------------------------------------------------
# «Mostra valori come»
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class Calculation:
    kind: str
    uses_reference: bool  # needs «rispetto a»
    uses_base_field: bool  # needs the field along which to calculate (running totals, rank)
    short: str  # text added to the column name


@dataclass(frozen=True)
class Reference:
    kind: str
    uses_base_field: bool
    uses_base_item: bool
    short: str  # {field} and {item} replaced by the base field and the item


NO_CALCULATION = "Nessun calcolo"
CALCULATIONS = {
    NO_CALCULATION: Calculation("value", False, False, ""),
    "% di": Calculation("percent", True, False, "% di"),
    "Differenza da": Calculation("difference", True, False, "diff. da"),
    "Differenza % da": Calculation("percent_difference", True, False, "diff. % da"),
    "Totale progressivo in": Calculation("running", False, True, "progressivo in"),
    "% progressiva in": Calculation("running_percent", False, True, "% progressiva in"),
    "Rango dal più grande in": Calculation("rank_descending", False, True, "rango ↓ in"),
    "Rango dal più piccolo in": Calculation("rank_ascending", False, True, "rango ↑ in"),
    "Indice": Calculation("index", False, False, "indice"),
}

GRAND_TOTAL = "Totale complessivo"
REFERENCES = {
    GRAND_TOTAL: Reference("grand", False, False, "totale"),
    "Totale di riga": Reference("row_total", False, False, "tot. riga"),
    "Totale di colonna": Reference("column_total", False, False, "tot. colonna"),
    "Riga padre": Reference("parent_row", False, False, "riga padre"),
    "Colonna padre": Reference("parent_column", False, False, "colonna padre"),
    "Totale padre (campo base)": Reference("parent_field", True, False, "padre {field}"),
    "Elemento (campo base)": Reference("item", True, True, "{field} {item}"),
    "Precedente (campo base)": Reference("previous", True, False, "{field} precedente"),
    "Successivo (campo base)": Reference("next", True, False, "{field} successivo"),
}


def calculation_of(measure: dict) -> Calculation:
    return CALCULATIONS.get(measure.get("show_as") or NO_CALCULATION, CALCULATIONS[NO_CALCULATION])


def reference_of(measure: dict) -> Reference:
    return REFERENCES.get(measure.get("relative_to") or GRAND_TOTAL, REFERENCES[GRAND_TOTAL])


def needs_base_field(measure: dict) -> bool:
    calculation = calculation_of(measure)
    return calculation.uses_base_field or (calculation.uses_reference and reference_of(measure).uses_base_field)


def needs_base_item(measure: dict) -> bool:
    return calculation_of(measure).uses_reference and reference_of(measure).uses_base_item


def show_as_text(measure: dict) -> str:
    """Short description of the calculation, for the column name («% di tot. riga»); empty without a calculation."""
    calculation = calculation_of(measure)
    field, item = measure.get("base_field") or "", measure.get("base_item") or ""
    if calculation.uses_reference:
        target = reference_of(measure).short.format(field=field, item=item)
        return f"{calculation.short} {target}"
    if calculation.uses_base_field:
        return f"{calculation.short} {field}"
    return calculation.short


# ---------------------------------------------------------------------------
# Spec checks
# ---------------------------------------------------------------------------

def valid_values(df: pd.DataFrame, values: list[dict] | None) -> list[dict]:
    return [value for value in values or [] if value.get("col") in df.columns]


def aggregates_of(values: list[dict]) -> list[tuple[str, str]]:
    """(column, function) pairs to calculate, only once even if shown in several ways."""
    return list(dict.fromkeys((value["col"], value["fn"]) for value in values))


def _check_fields(df: pd.DataFrame, rows: list[str], columns: list[str], values: list[dict]) -> None:
    if not values:
        raise ValueError("Seleziona almeno un valore da aggregare.")
    if not rows and not columns:
        raise ValueError("Seleziona almeno una riga o una colonna.")
    missing = [field for field in [*rows, *columns] if field not in df.columns]
    if missing:
        raise ValueError(f"Colonne inesistenti: {', '.join(missing)}.")
    both = set(rows) & set(columns)
    if both or len(set(rows)) < len(rows) or len(set(columns)) < len(columns):
        raise ValueError("Ogni campo può comparire una volta sola tra righe e colonne.")


def _check_measure(measure: dict, fields: list[str]) -> None:
    label = measure.get("name") or f"{measure['fn']} {measure['col']}"
    if needs_base_field(measure) and measure.get("base_field") not in fields:
        raise ValueError(f"«{label}»: scegli il campo base tra i campi di righe e colonne.")
    if needs_base_item(measure) and not measure.get("base_item"):
        raise ValueError(f"«{label}»: scegli l'elemento del campo base.")


def cube_signature(spec: dict) -> str:
    """What decides the aggregations (not the calculations nor the look)."""
    rows, columns = list(spec.get("rows") or []), list(spec.get("columns") or [])
    dates = {field: unit for field, unit in (spec.get("dates") or {}).items() if field in [*rows, *columns]}
    return json.dumps([rows, columns, spec.get("filters"), dates], sort_keys=True, default=str)


# ---------------------------------------------------------------------------
# Cube
# ---------------------------------------------------------------------------

@dataclass
class Cube:
    rows: list[str]
    columns: list[str]
    aggregates: list[tuple[str, str]]  # (column, function), one per value column in `cells`
    labels: dict[str, list[str]]  # field → labels in the order of the codes
    cells: pd.DataFrame  # field codes (TOTAL = all), levels and one value per aggregate
    codes: pd.DataFrame  # field codes for each source row
    source: pd.DataFrame  # whole dataset
    positions: np.ndarray  # positions in the dataset of the rows that pass the filters
    signature: str
    filters: dict | None

    @property
    def row_keys(self) -> list[str]:
        return [f"r{i}" for i in range(len(self.rows))]

    @property
    def column_keys(self) -> list[str]:
        return [f"c{i}" for i in range(len(self.columns))]

    @property
    def keys(self) -> list[str]:
        return [*self.row_keys, *self.column_keys]

    @property
    def field_keys(self) -> dict[str, str]:
        return dict(zip([*self.rows, *self.columns], self.keys))

    def aggregate_key(self, measure: dict) -> str:
        return f"a{self.aggregates.index((measure['col'], measure['fn']))}"

    def covers(self, spec: dict) -> bool:
        """True if the cube already works for the spec: same fields, filters and dates, aggregations already calculated."""
        wanted = set(aggregates_of(valid_values(self.source, spec.get("values"))))
        return self.signature == cube_signature(spec) and wanted <= set(self.aggregates)

    @cached_property
    def index(self) -> pd.MultiIndex:
        return pd.MultiIndex.from_frame(self.cells[self.keys])

    def lookup(self, values: pd.Series, keys: pd.DataFrame, valid: np.ndarray | None = None) -> pd.Series:
        """Value of the cell with the keys `keys` (one row per cell); NaN if it does not exist or is not valid."""
        found = pd.Series(values.to_numpy(), index=self.index).reindex(pd.MultiIndex.from_frame(keys)).to_numpy()
        if valid is not None:
            found = np.where(valid, found, np.nan)
        return pd.Series(found, index=self.cells.index, dtype="float64")


def _grouped(frame: pd.DataFrame, group: list[str]):
    """Groups by the keys; without keys a single group (the grand total)."""
    return frame.groupby(group if group else np.zeros(len(frame), dtype=int), sort=False)


def _flat(level: pd.DataFrame, group: list[str]) -> pd.DataFrame:
    return level.reset_index() if group else level.reset_index(drop=True)


def _raw_aggregates(aggregates: list[tuple[str, str]], sources: dict[str, str],
                    only_raw: bool = False) -> dict[str, tuple[str, str]]:
    """Aggregations calculated from the data (with `only_raw` only those that cannot be derived from the detail)."""
    return {f"a{i}": (sources[column], function)
            for i, (column, function) in enumerate(aggregates) if not (only_raw and function in ROLLUP)}


def _level(work: pd.DataFrame, finest: pd.DataFrame, group: list[str],
           aggregates: list[tuple[str, str]], sources: dict[str, str]) -> pd.DataFrame:
    """One total level: decomposable functions from the detail, the others from the data."""
    rolled = {f"a{i}": pd.NamedAgg(column=f"a{i}", aggfunc=ROLLUP[function])
              for i, (_, function) in enumerate(aggregates) if function in ROLLUP}
    raw = _raw_aggregates(aggregates, sources, only_raw=True)
    parts = []
    if rolled:
        parts.append(_grouped(finest, group).agg(**rolled))
    if raw:
        parts.append(eng.aggregate(work, _grouped(work, group), raw))
    level = pd.concat(parts, axis=1)[[f"a{i}" for i in range(len(aggregates))]]
    return _flat(level, group)


def build_cube(df: pd.DataFrame, spec: dict) -> Cube:
    """Encodes the fields and calculates all the total levels (detail, subtotals, totals)."""
    rows, columns = list(spec.get("rows") or []), list(spec.get("columns") or [])
    values = valid_values(df, spec.get("values"))
    _check_fields(df, rows, columns, values)
    eng.check_measures(df, values)
    aggregates = aggregates_of(values)
    dates = spec.get("dates") or {}

    mask = eng.filter_mask(df, spec.get("filters")).to_numpy()
    positions = np.flatnonzero(mask)
    if not len(positions):
        raise ValueError("Nessuna riga passa i filtri.")
    used = list(dict.fromkeys([*rows, *columns, *(column for column, _ in aggregates)]))
    data = df[used] if mask.all() else df[used].iloc[positions]

    fields = [*rows, *columns]
    keys = [*(f"r{i}" for i in range(len(rows))), *(f"c{i}" for i in range(len(columns)))]
    codes, labels = {}, {}
    for field, key in zip(fields, keys):
        categories = charts.category_keys(data[field], dates.get(field))
        codes[key] = categories.cat.codes.to_numpy().astype("int32")
        labels[field] = [str(label) for label in categories.cat.categories]
    codes = pd.DataFrame(codes)

    sources = {column: f"s{j}" for j, column in enumerate(dict.fromkeys(column for column, _ in aggregates))}
    work = codes.assign(**{name: data[column].to_numpy() for column, name in sources.items()})
    finest = _flat(eng.aggregate(work, _grouped(work, keys), _raw_aggregates(aggregates, sources)), keys)

    levels = []
    for row_level in range(len(rows) + 1):
        for column_level in range(len(columns) + 1):
            group = [*keys[:row_level], *keys[len(rows):len(rows) + column_level]]
            level = finest if group == keys else _level(work, finest, group, aggregates, sources)
            levels.append(level.assign(**{key: TOTAL for key in keys if key not in group},
                                       **{ROW_LEVEL: row_level, COLUMN_LEVEL: column_level}))
    cells = pd.concat(levels, ignore_index=True)
    cells[keys] = cells[keys].astype("int32")
    return Cube(rows, columns, aggregates, labels, cells, codes, df, positions,
                cube_signature(spec), copy.deepcopy(spec.get("filters")))


def field_items(df: pd.DataFrame, field: str, date_unit: str | None = None) -> list[str]:
    """Labels of a field, as the cube would use them (to choose the base item)."""
    if field not in df.columns:
        return []
    return [str(label) for label in charts.category_keys(df[field], date_unit).cat.categories]


# ---------------------------------------------------------------------------
# Shown values
# ---------------------------------------------------------------------------

def _drop_last(keys: pd.DataFrame, axis: list[str]) -> None:
    """Removes the last field of each cell on the axis (the parent cell); totals stay themselves."""
    level = (keys[axis] != TOTAL).sum(axis=1).to_numpy()
    for position, key in enumerate(axis):
        keys.loc[level == position + 1, key] = TOTAL


def _reference_keys(cube: Cube, kind: str, field: str = "", item: str = "") -> tuple[pd.DataFrame, np.ndarray]:
    """Keys of the reference cell of each cell (type of Reference) and the cells for which it makes sense."""
    keys = cube.cells[cube.keys].copy()
    valid = np.ones(len(keys), dtype=bool)
    if kind == "grand":
        keys[cube.keys] = TOTAL
    elif kind == "row_total":
        keys[cube.column_keys] = TOTAL
    elif kind == "column_total":
        keys[cube.row_keys] = TOTAL
    elif kind == "parent_row":
        _drop_last(keys, cube.row_keys)
    elif kind == "parent_column":
        _drop_last(keys, cube.column_keys)
    else:
        key = cube.field_keys[field]
        present = keys[key].to_numpy() != TOTAL
        valid &= present
        if kind == "parent_field":
            axis = cube.row_keys if key in cube.row_keys else cube.column_keys
            keys[axis[axis.index(key) + 1:]] = TOTAL
        elif kind == "item":
            item = str(item)
            if item not in cube.labels[field]:
                raise ValueError(f"«{item}» non è un elemento di {field}.")
            keys.loc[present, key] = cube.labels[field].index(item)
        else:
            shifted = keys[key].to_numpy() + (-1 if kind == "previous" else 1)
            valid &= (shifted >= 0) & (shifted < len(cube.labels[field]))
            keys[key] = np.where(valid, shifted, TOTAL)
    return keys, valid


def _along_field(cube: Cube, number: pd.Series, field: str, kind: str) -> pd.Series:
    """Running totals and ranks along a field, separately for each combination of the other fields."""
    key = cube.field_keys[field]
    others = [name for name in cube.keys if name != key]
    present = cube.cells[key].to_numpy() != TOTAL
    frame = cube.cells.loc[present, cube.keys].assign(_value=number[present]).sort_values(key, kind="stable")
    values = _grouped(frame, others)["_value"]
    if kind == "running":
        along, outside = values.cumsum(), number
    elif kind == "running_percent":
        along = values.cumsum() / values.transform("sum") * 100
        outside = pd.Series(np.where(number.notna(), 100.0, np.nan), index=cube.cells.index)
    else:
        along = values.rank(method="min", ascending=kind == "rank_ascending")
        outside = pd.Series(np.nan, index=cube.cells.index)
    return outside.where(~present, along.reindex(cube.cells.index))


def show_values(cube: Cube, measure: dict) -> pd.Series:
    """Value of each cube cell as requested by «Mostra valori come»."""
    value = cube.cells[cube.aggregate_key(measure)]
    calculation = calculation_of(measure)
    if calculation.kind == "value":
        return value
    number = pd.to_numeric(value, errors="coerce").astype("float64")
    field, item = measure.get("base_field") or "", measure.get("base_item") or ""
    if calculation.kind == "index":
        grand, row_total, column_total = (cube.lookup(number, *_reference_keys(cube, kind))
                                          for kind in ("grand", "row_total", "column_total"))
        result = number * grand / (row_total * column_total)
    elif calculation.uses_reference:
        reference = cube.lookup(number, *_reference_keys(cube, reference_of(measure).kind, field, item))
        if calculation.kind == "percent":
            result = number / reference * 100
        elif calculation.kind == "difference":
            result = number - reference
        else:
            result = (number - reference) / reference * 100
    else:
        result = _along_field(cube, number, field, calculation.kind)
    return result.replace([np.inf, -np.inf], np.nan)


# ---------------------------------------------------------------------------
# Presentation: flat table
# ---------------------------------------------------------------------------

@dataclass
class PivotTable:
    frame: pd.DataFrame
    row_codes: pd.DataFrame  # row field codes of each table row
    column_codes: dict[str, dict[str, int]]  # name of the value column → column field codes
    cube: Cube


def _visible_levels(count: int, subtotals: bool, grand: bool) -> set[int]:
    levels = {count}
    if subtotals:
        levels |= set(range(1, count))
    if grand:
        levels.add(0)
    return levels


def _order(keys: pd.DataFrame, axis: list[str], ranks: dict[str, np.ndarray] | None = None,
           descending: bool = False) -> np.ndarray:
    """Order of the rows (or columns): per field, the sort value and then the code;
    totals go after their items."""
    sort_keys = []
    for key in axis:
        code = keys[key].to_numpy()
        total = code == TOTAL
        rank = ranks[key] if ranks else code.astype("float64")
        rank = -rank if descending else rank
        sort_keys += [np.where(total | np.isnan(rank), np.inf, rank), np.where(total, np.inf, code)]
    return np.lexsort(sort_keys[::-1]) if sort_keys else np.arange(len(keys))


def _value_ranks(cube: Cube, keys: pd.DataFrame, measure: dict) -> dict[str, np.ndarray]:
    """For each row field, the total of the measure of its item (within the parent) over all the columns."""
    number = pd.to_numeric(cube.cells[cube.aggregate_key(measure)], errors="coerce")
    table = pd.Series(number.to_numpy(), index=cube.index)
    ranks = {}
    for position, key in enumerate(cube.row_keys):
        prefix = keys.assign(**{name: TOTAL for name in [*cube.row_keys[position + 1:], *cube.column_keys]})
        ranks[key] = table.reindex(pd.MultiIndex.from_frame(prefix[cube.keys])).to_numpy(dtype="float64")
    return ranks


def _labels(codes: pd.DataFrame, fields: list[str], keys: list[str], labels: dict[str, list[str]]) -> dict:
    """Field labels: the first total field says «Totale», the following ones stay empty."""
    result, previous_total = {}, np.zeros(len(codes), dtype=bool)
    for field, key in zip(fields, keys):
        code = codes[key].to_numpy()
        total = code == TOTAL
        names = np.asarray(labels[field], dtype=object)[np.where(total, 0, code)] if labels[field] else \
            np.full(len(code), "", dtype=object)
        result[field] = np.where(total, np.where(previous_total, "", TOTAL_LABEL), names)
        previous_total = total
    return result


def _column_labels(columns: pd.DataFrame, cube: Cube) -> list[str]:
    """Label of each pivot column: items of the column fields joined by « / »."""
    texts = _labels(columns, cube.columns, cube.column_keys, cube.labels).values()
    return [" / ".join(str(part) for part in parts if part) for parts in zip(*texts)]


def _measure_label(measure: dict, naming: str) -> dict:
    """Measure with the naming pattern extended by the calculation, if the name is not written by hand."""
    shown = show_as_text(measure)
    if (measure.get("name") or "").strip() or not shown:
        return measure
    pattern = (naming or "").strip() or eng.DEFAULT_NAMING
    return {**measure, "name": f"{pattern} ({shown})"}


def render(cube: Cube, values: list[dict], layout: dict | None = None) -> PivotTable:
    """Flat pivot table from the cube: sorted rows, chosen totals, one set of columns per measure."""
    layout = layout or {}
    values = valid_values(cube.source, values)
    if not values:
        raise ValueError("Seleziona almeno un valore da aggregare.")
    missing = [value for value in values if (value["col"], value["fn"]) not in cube.aggregates]
    if missing:
        raise ValueError("Le misure sono cambiate: ricrea la pivot.")
    fields = [*cube.rows, *cube.columns]
    for value in values:
        _check_measure(value, fields)

    row_levels = _visible_levels(len(cube.rows), layout.get("subtotal_rows", False), layout.get("grand_rows", True))
    column_levels = _visible_levels(len(cube.columns), layout.get("subtotal_columns", False),
                                    layout.get("grand_columns", True))
    visible = (cube.cells[ROW_LEVEL].isin(row_levels) & cube.cells[COLUMN_LEVEL].isin(column_levels)).to_numpy()
    measure_keys = [f"m{i}" for i in range(len(values))]
    long = cube.cells.loc[visible, [*cube.keys, ROW_LEVEL]].assign(
        **{key: show_values(cube, value)[visible] for key, value in zip(measure_keys, values)})

    row_index = cube.row_keys or [ROW_LEVEL]  # without row fields: a single row (level 0)
    rows = long[row_index].drop_duplicates().reset_index(drop=True)
    sort_by = layout.get("sort_by")
    ranks = _value_ranks(cube, rows, values[sort_by]) if sort_by is not None and cube.row_keys else None
    rows = rows.iloc[_order(rows, cube.row_keys, ranks, layout.get("descending", False))].reset_index(drop=True)

    wide = long.set_index([*row_index, *cube.column_keys])[measure_keys]
    column_tuples: list[tuple] = [()]
    column_names: list[str | None] = [None]
    if cube.column_keys:
        columns = long[cube.column_keys].drop_duplicates().reset_index(drop=True)
        if len(columns) * len(values) > MAX_COLUMNS:
            count = f"{len(columns) * len(values):,}".replace(",", ".")
            raise ValueError(f"La pivot avrebbe {count} colonne di valori (limite {MAX_COLUMNS}): "
                             f"filtra i dati o sposta «{cube.columns[-1]}» nelle righe.")
        columns = columns.iloc[_order(columns, cube.column_keys)]
        column_tuples = list(columns.itertuples(index=False, name=None))
        column_names = _column_labels(columns, cube)
        wide = wide.unstack(cube.column_keys)
        wide = wide.reindex(columns=[(key, *codes) for key in measure_keys for codes in column_tuples])
    wide = wide.reindex(pd.MultiIndex.from_frame(rows) if len(row_index) > 1 else pd.Index(rows[row_index[0]]))

    naming = layout.get("naming") or eng.DEFAULT_NAMING
    names, column_codes = [], []
    for value in values:
        labelled = _measure_label(value, naming)
        for codes, column_name in zip(column_tuples, column_names):
            names.append(eng.measure_name(labelled, naming, column_name))
            column_codes.append(dict(zip(cube.column_keys, codes)))
    names = eng.unique_names(names, cube.rows)

    body = pd.DataFrame({name: wide.iloc[:, i].to_numpy() for i, name in enumerate(names)}).infer_objects()
    if layout.get("fill_zero"):
        body = body.fillna(0)
    labels = pd.DataFrame(_labels(rows, cube.rows, cube.row_keys, cube.labels), index=body.index)
    frame = pd.concat([labels, body], axis=1)
    return PivotTable(frame, rows[cube.row_keys], dict(zip(names, column_codes)), cube)


def run_pivot(df: pd.DataFrame, spec: dict, layout: dict | None = None) -> PivotTable:
    """Cube and table in one step."""
    return render(build_cube(df, spec), spec.get("values") or [], layout)


# ---------------------------------------------------------------------------
# Drill-down: source rows of a cell
# ---------------------------------------------------------------------------

def _selected_codes(table: PivotTable, row: int, column: str | None) -> dict[str, int]:
    selected = table.row_codes.iloc[row].to_dict() if len(table.row_codes.columns) else {}
    selected.update(table.column_codes.get(column or "", {}))
    return {key: int(code) for key, code in selected.items() if code != TOTAL}


def drill(table: PivotTable, row: int, column: str | None = None) -> pd.DataFrame:
    """Dataset rows that make up the cell (table row, value column); on a label
    or on a column total, the whole row."""
    cube = table.cube
    mask = np.ones(len(cube.positions), dtype=bool)
    for key, code in _selected_codes(table, row, column).items():
        mask &= cube.codes[key].to_numpy() == code
    return cube.source.iloc[cube.positions[mask]]


def selection_text(table: PivotTable, row: int, column: str | None = None) -> str:
    fields = dict(zip(table.cube.keys, [*table.cube.rows, *table.cube.columns]))
    parts = [f"{fields[key]} = {table.cube.labels[fields[key]][code]}"
             for key, code in _selected_codes(table, row, column).items()]
    return " · ".join(parts) or "tutte le righe"
