"""
Join and union of datasets (no UI dependency).

  - chained join: the starting dataset is joined to a second dataset, the result to a third one, and so on.
    Each step has a type (inner, left, right, full, cross, semi, anti), keys with comparison operators,
    key matching and columns. DuckDB runs each step on Arrow tables.
  - union: appends the rows of two or more datasets (UNION ALL, UNION, INTERSECT, EXCEPT), aligning the columns
    by name or by position.

Each dataset can be filtered first (filter tree from engine.py). The columns of each step are decided with
a «plan»: a list of JSON-serializable dicts (source, name, kept, duplicate...) that the interface shows
and edits before running. The plan of a step depends on the columns of the previous steps (plan_chain).
"""
from __future__ import annotations

from collections import Counter
from itertools import chain
from typing import NamedTuple

import numpy as np
import pandas as pd
import pyarrow as pa
import pyarrow.compute as pc

from data_explorer.core import columns as cols
from data_explorer.core import engine as eng
from data_explorer.core import formula_help as fh
ORIGIN = "origine"           # optional column with the origin of each row of the join
ORIGIN_SOURCE = "riga"       # source of the origin column in the preview headers
ROW = "__row__"              # row number in the starting dataset, in the Arrow tables
ROW_ORDER = {"left": "__left_row__", "right": "__right_row__"}  # same number in the result of the step (order)
MERGE_FLAG = "__merge__"     # helper column of the pandas merge (union INTERSECT / EXCEPT)
DEFAULT_JOIN_NAMES = ("sinistra", "destra")
TWO_SIDE_KEYS = ("left_filters", "right_filters", "columns")

# A join with keys repeated on both sides multiplies the rows (even billions): above this number of cells
# (rows × columns) the step stops. The limit grows with the size of the input data.
MIN_RESULT_CELLS = 100_000_000
RESULT_GROWTH = 3
KEY_SAMPLE_ROWS = 20_000     # rows examined to tell whether a column is suitable as a key
MIN_KEY_UNIQUENESS = 0.9     # share of distinct values needed for a column to be proposed as a key


# ---------------------------------------------------------------------------
# Shared parts: filters, names, checks, tables without rows
# ---------------------------------------------------------------------------

def filtered(df: pd.DataFrame, tree: dict | None) -> pd.DataFrame:
    """Filtered `df`; without conditions it returns the same object (the Arrow cache of run_sql reuses it)."""
    return eng.apply_filters(df, tree) if tree and eng.has_conditions(df, tree) else df


def _unique(name: str, used: set[str]) -> str:
    """`name`, with a number at the end if already in `used`; the chosen name is added to `used`."""
    candidate, number = name, 2
    while candidate in used:
        candidate = f"{name}_{number}"
        number += 1
    used.add(candidate)
    return candidate


def default_names(count: int) -> list[str]:
    return [f"dataset_{number}" for number in range(1, count + 1)]


def unique_labels(names: list[str]) -> list[str]:
    """Dataset names for headers and suffixes; the same dataset used twice becomes «name», «name_2»."""
    used: set[str] = set()
    return [_unique(name, used) for name in names]


def _check_names(names: list[str]) -> None:
    """The column names of the result must be there, and must not be empty or repeated."""
    if not names:
        raise ValueError("Scegli almeno una colonna da includere nel risultato.")
    if any(not name.strip() for name in names):
        raise ValueError("Ogni colonna del risultato deve avere un nome.")
    repeated = sorted(name for name, count in Counter(names).items() if count > 1)
    if repeated:
        raise ValueError(f"Nomi di colonna ripetuti nel risultato: {', '.join(repeated)}. "
                         "Rinominali (o escludili) nel piano delle colonne.")


def _result_names(plan: list[dict], origin: bool) -> list[str]:
    """Column names of the result: the ones of the plan plus, if requested, the origin column (union)."""
    return [entry["name"] for entry in plan] + ([ORIGIN] if origin else [])


def _same_kind(first: pd.Series, second: pd.Series) -> bool:
    return fh.column_kind(first.dtype) == fh.column_kind(second.dtype)


def _dtype_of(frame: pd.DataFrame, column: str):
    values = frame[column]
    return values.dtype if isinstance(values, pd.Series) else values.iloc[:, 0].dtype


def _empty_frame(columns: list[tuple[str, object]]) -> pd.DataFrame:
    """Table without rows with these names and types: enough to build plans and to validate the keys."""
    frame = pd.DataFrame({position: pd.Series(dtype=dtype) for position, (_, dtype) in enumerate(columns)})
    frame.columns = [name for name, _ in columns]
    return frame


def _join_type(spec: dict) -> JoinType:
    if spec.get("how") not in JOIN_TYPES:
        raise ValueError(f"Tipo di join sconosciuto: {spec.get('how')}")
    if spec.get("match", "exact") not in KEY_MATCHES:
        raise ValueError(f"Confronto delle chiavi sconosciuto: {spec.get('match')}")
    return JOIN_TYPES[spec["how"]]


# ---------------------------------------------------------------------------
# Join: types, keys, comparisons
# ---------------------------------------------------------------------------

class JoinType(NamedTuple):
    label: str
    description: str
    sql: str                       # SQL keyword in front of JOIN
    sides: tuple[str, ...]         # sides whose columns appear in the result (the first one drives the row order)
    keyed: bool = True             # False: no keys (cross)


JOIN_TYPES = {
    "inner": JoinType("Inner", "Solo le righe che hanno una corrispondenza in entrambi i dataset.",
                      "INNER", ("left", "right")),
    "left": JoinType("Left", "Tutte le righe di sinistra, con i dati di destra dove c'è una corrispondenza.",
                     "LEFT", ("left", "right")),
    "right": JoinType("Right", "Tutte le righe di destra, con i dati di sinistra dove c'è una corrispondenza.",
                      "RIGHT", ("left", "right")),
    "full": JoinType("Full outer", "Tutte le righe di entrambi i dataset, abbinate dove c'è una corrispondenza.",
                     "FULL", ("left", "right")),
    "cross": JoinType("Cross", "Ogni riga di sinistra con ogni riga di destra (nessuna chiave).",
                      "CROSS", ("left", "right"), keyed=False),
    "left_semi": JoinType("Left semi", "Righe di sinistra che hanno una corrispondenza; solo colonne di sinistra.",
                          "SEMI", ("left",)),
    "left_anti": JoinType("Left anti", "Righe di sinistra senza alcuna corrispondenza; solo colonne di sinistra.",
                          "ANTI", ("left",)),
    "right_semi": JoinType("Right semi", "Righe di destra che hanno una corrispondenza; solo colonne di destra.",
                           "SEMI", ("right",)),
    "right_anti": JoinType("Right anti", "Righe di destra senza alcuna corrispondenza; solo colonne di destra.",
                           "ANTI", ("right",)),
}

KEY_OPERATORS = {"=": "=", "≠": "<>", "<": "<", "<=": "<=", ">": ">", ">=": ">="}


class KeyMatch(NamedTuple):
    label: str
    sql: str                       # how the value of a key is read ({} = the column)


KEY_MATCHES = {
    "exact": KeyMatch("Valore esatto", "{}"),
    "text": KeyMatch("Come testo (es. 7 = «7»)", "CAST({} AS VARCHAR)"),
    "text_loose": KeyMatch("Come testo, senza maiuscole né spazi ai bordi", "lower(trim(CAST({} AS VARCHAR)))"),
}

NAMING = {
    "duplicates": "Solo i duplicati di destra",
    "right": "Tutte le colonne di destra",
    "all": "Tutte le colonne",
}

ALIAS = {"left": "l", "right": "r"}
OTHER = {"left": "right", "right": "left"}


def new_key(left: str = "", op: str = "=", right: str = "") -> dict:
    """Join condition: the left column, compared with `op`, to the right column."""
    return {"left": left, "op": op, "right": right}


def _uniqueness(series: pd.Series) -> float:
    """Share of distinct values in the first rows: 1 = all different (an identifier)."""
    sample = series.head(KEY_SAMPLE_ROWS)
    return sample.nunique() / len(sample) if len(sample) else 0.0


def suggest_keys(left: pd.DataFrame, right: pd.DataFrame) -> list[dict]:
    """
    Proposed key: among the columns with the same name and type, the one with the most distinct values in one of the two
    datasets (a column with few values, like a region, would multiply the rows). None if no column is unique enough.
    """
    common = [name for name in map(str, left.columns) if name in right.columns and _same_kind(left[name], right[name])]
    scores = {name: max(_uniqueness(left[name]), _uniqueness(right[name])) for name in common}
    best = max(scores, key=scores.get, default=None)
    return [new_key(best, "=", best)] if best is not None and scores[best] >= MIN_KEY_UNIQUENESS else []


def _checked_keys(left: pd.DataFrame, right: pd.DataFrame, spec: dict, join_type: JoinType) -> list[dict]:
    if not join_type.keyed:
        return []
    keys = spec.get("keys") or []
    if not keys:
        raise ValueError("Aggiungi almeno una chiave di join.")
    exact = spec.get("match", "exact") == "exact"
    for number, key in enumerate(keys, start=1):
        if key.get("left") not in left.columns or key.get("right") not in right.columns:
            raise ValueError(f"La chiave {number} è incompleta: scegli una colonna per ogni dataset.")
        if key.get("op", "=") not in KEY_OPERATORS:
            raise ValueError(f"La chiave {number} ha un operatore sconosciuto: {key.get('op')}")
        if exact and not _same_kind(left[key["left"]], right[key["right"]]):
            kinds = (fh.column_kind(left[key["left"]].dtype), fh.column_kind(right[key["right"]].dtype))
            raise ValueError(f"Le colonne «{key['left']}» ({kinds[0]}) e «{key['right']}» ({kinds[1]}) hanno tipi "
                             "diversi: scegli il confronto «Come testo» oppure converti una colonna "
                             "dalla scheda Colonne calcolate.")
    return keys


def _condition(keys: list[dict], match: str, null_keys: bool) -> str:
    """ON condition: all the keys must be true. With `null_keys` two nulls match each other (only for «=»)."""
    template = KEY_MATCHES[match].sql
    parts = []
    for key in keys:
        left = template.format(f"l.{fh.quote_column(key['left'])}")
        right = template.format(f"r.{fh.quote_column(key['right'])}")
        if key["op"] == "=" and null_keys:
            parts.append(f"{left} IS NOT DISTINCT FROM {right}")
        else:
            parts.append(f"{left} {KEY_OPERATORS[key['op']]} {right}")
    return " AND ".join(parts)


# ---------------------------------------------------------------------------
# Join: column plan of a step
# ---------------------------------------------------------------------------
# entry: {"side": "left" | "right" | "origin", "source": name of the source dataset, "column": source column,
#        "name": name in the result, "keep": bool, "duplicate": the name also exists in the other dataset,
#        "merged_with": key column of the other side it is merged with, or None}
# "origin" is the column with the origin of the rows (only if requested); in later steps it is a left column.

def merged_away(entry: dict) -> bool:
    """Right key absorbed by the left one (option «unisci le chiavi»): it does not appear in the result."""
    return entry["side"] == "right" and entry.get("merged_with") is not None


def _merged_keys(left: pd.DataFrame, right: pd.DataFrame, spec: dict, join_type: JoinType) -> dict[str, str]:
    """«=» keys between columns of the same type to merge into a single column: {left column: right column}."""
    if not (spec.get("merge_keys") and join_type.keyed and len(join_type.sides) == 2):
        return {}
    merged: dict[str, str] = {}
    for key in spec.get("keys") or []:
        if (key.get("op") == "=" and key.get("left") in left.columns and key.get("right") in right.columns
                and _same_kind(left[key["left"]], right[key["right"]])):
            merged.setdefault(key["left"], key["right"])
    return merged


def _tag(label: str) -> str:
    return "".join(char if char.isalnum() else "_" for char in label).strip("_")


def _is_tagged(entry: dict, naming: str) -> bool:
    """True if the column name gets the name of the source dataset at the end."""
    if naming == "all":
        return True
    if entry["side"] == "left":
        return False
    return naming == "right" or entry["duplicate"]


def _name_columns(plan: list[dict], naming: str) -> None:
    """Default names: names without a suffix take precedence; names with a suffix always stay unique."""
    tagged = [_is_tagged(entry, naming) for entry in plan]
    used = {entry["name"] for entry, is_tagged in zip(plan, tagged) if not is_tagged}
    for entry, is_tagged in zip(plan, tagged):
        if is_tagged:
            entry["name"] = _unique(f"{entry['column']}_{_tag(entry['source'])}", used)


def _origin_entry(plan: list[dict]) -> dict:
    used = {entry["name"] for entry in plan}
    return {"side": "origin", "source": ORIGIN_SOURCE, "column": ORIGIN, "name": _unique(ORIGIN, used),
            "keep": True, "duplicate": False, "merged_with": None}


def join_plan(left: pd.DataFrame, right: pd.DataFrame, spec: dict, names: tuple[str, str] = DEFAULT_JOIN_NAMES,
              overrides: dict | None = None, left_sources: dict[str, str] | None = None) -> list[dict]:
    """
    Column plan of a step: one entry for each column that can appear in the result.
    Uses from `spec`: how, keys, merge_keys, naming (see NAMING), indicator.
    left_sources: {left column: source dataset}, for the steps after the first one.
    overrides: {(side, column): {"name": ..., "keep": ...}} user choices, applied on top of the defaults.
    """
    join_type = JOIN_TYPES[spec["how"]]
    labels = dict(zip(("left", "right"), unique_labels(list(names))))
    frames = {"left": left, "right": right}
    sources = left_sources or {}
    merged = _merged_keys(left, right, spec, join_type)
    partners = {"left": merged, "right": {right_name: left_name for left_name, right_name in merged.items()}}
    plan = []
    for side in join_type.sides:
        other = {str(name) for name in frames[OTHER[side]].columns}
        for column in map(str, frames[side].columns):
            source = sources.get(column, labels["left"]) if side == "left" else labels["right"]
            plan.append({"side": side, "source": source, "column": column, "name": column, "keep": True,
                         "duplicate": len(join_type.sides) == 2 and column in other,
                         "merged_with": partners[side].get(column)})
    _name_columns(plan, spec.get("naming", "duplicates"))
    if spec.get("indicator") and len(join_type.sides) == 2:
        plan.append(_origin_entry(plan))
    for entry in plan:
        choice = (overrides or {}).get((entry["side"], entry["column"]), {})
        entry["name"] = choice.get("name", entry["name"])
        entry["keep"] = choice.get("keep", True) and not merged_away(entry)
    return plan


def column_sources(plan: list[dict]) -> dict[str, tuple[str, bool]]:
    """{name in the result: (source dataset, is a duplicate)} for the kept columns: headers of the preview."""
    return {entry["name"]: (entry["source"], entry["duplicate"]) for entry in plan if entry["keep"]}


def _next_schema(plan: list[dict], left: pd.DataFrame, right: pd.DataFrame) -> tuple[pd.DataFrame, dict[str, str]]:
    """Empty table with the columns kept by the step (which become the left side of the next one) and their sources."""
    kept = [entry for entry in plan if entry["keep"]]
    columns = []
    for entry in kept:
        if entry["side"] == "left":
            dtype = _dtype_of(left, entry["column"])
        elif entry["side"] == "right":
            dtype = _dtype_of(right, entry["column"])
        else:
            dtype = object
        columns.append((entry["name"], dtype))
    return _empty_frame(columns), {entry["name"]: entry["source"] for entry in kept}


def plan_chain(frames: list[pd.DataFrame], steps: list[dict], names: list[str] | None = None,
               overrides: dict | None = None) -> list[list[dict]]:
    """
    Column plans of all the steps of a join chain. Each step has `right` (index of the dataset in `frames`);
    the starting dataset is `frames[0]`. Uses only names and types: no data is processed.
    overrides: {(step index, side, column): {"name": ..., "keep": ...}}.
    """
    labels = unique_labels(list(names) if names else default_names(len(frames)))
    left = frames[0].head(0)
    sources = {str(column): labels[0] for column in left.columns}
    plans = []
    for number, step in enumerate(steps):
        _join_type(step)
        right = frames[step["right"]].head(0)
        step_overrides = {(side, column): choice for (index, side, column), choice in (overrides or {}).items()
                          if index == number}
        plan = join_plan(left, right, step, (labels[0], labels[step["right"]]), step_overrides, sources)
        plans.append(plan)
        left, sources = _next_schema(plan, left, right)
    return plans


# ---------------------------------------------------------------------------
# Join: running a step (DuckDB on Arrow tables)
# ---------------------------------------------------------------------------

def _select_items(plan: list[dict]) -> list[str]:
    items = []
    for entry in plan:
        if entry["side"] == "origin":
            expression = (f"CASE WHEN l.{ROW} IS NULL THEN 'solo destra' "
                          f"WHEN r.{ROW} IS NULL THEN 'solo sinistra' ELSE 'entrambi' END")
        else:
            expression = f"{ALIAS[entry['side']]}.{fh.quote_column(entry['column'])}"
            if entry["side"] == "left" and entry.get("merged_with"):
                expression = f"COALESCE({expression}, r.{fh.quote_column(entry['merged_with'])})"
        items.append(f"{expression} AS {fh.quote_column(entry['name'])}")
    return items


def join_query(spec: dict, keys: list[dict], plan: list[dict]) -> str:
    """
    DuckDB query of a step on the tables `left_source` and `right_source`. The result includes the
    ROW_ORDER helper columns, from which `_in_row_order` gets the order of the rows.
    """
    join_type = JOIN_TYPES[spec["how"]]
    tables = {"left": "left_source AS l", "right": "right_source AS r"}
    first = join_type.sides[0]  # right semi/anti joins start from the right table
    source = f"{tables[first]} {join_type.sql} JOIN {tables[OTHER[first]]}"
    if join_type.keyed:
        source += f" ON {_condition(keys, spec.get('match', 'exact'), bool(spec.get('null_keys')))}"
    items = _select_items(plan) + [f"{ALIAS[side]}.{ROW} AS {ROW_ORDER[side]}" for side in join_type.sides]
    return f"SELECT {', '.join(items)}\nFROM {source}"


def _arrow_source(df: pd.DataFrame, tree: dict | None) -> pa.Table:
    """Arrow table of the dataset with the filters applied and the original row number of each row."""
    table = eng.arrow_table(df)
    rows = np.arange(len(df), dtype=np.int64)
    if tree and eng.has_conditions(df, tree):
        rows = np.flatnonzero(eng.filter_mask(df, tree).to_numpy(dtype=bool)).astype(np.int64)
        table = table.take(pa.array(rows))
    return with_row(table, rows)


def with_row(table: pa.Table, rows: np.ndarray | None = None) -> pa.Table:
    """Adds the ROW column: the original row number (by default the position in the table)."""
    numbers = np.arange(table.num_rows, dtype=np.int64) if rows is None else rows
    return table.append_column(ROW, pa.array(numbers))


def _is_ordered(arrays: list[np.ndarray]) -> bool:
    """True if the rows are already sorted by `arrays` (the first column takes precedence)."""
    if len(arrays[0]) < 2:
        return True
    tied = np.ones(len(arrays[0]) - 1, dtype=bool)
    for values in arrays:
        if (tied & (values[:-1] > values[1:])).any():
            return False
        tied &= values[:-1] == values[1:]
    return True


def _in_row_order(table: pa.Table, sides: tuple[str, ...]) -> pa.Table:
    """
    Rows in the order of the starting datasets (left first, then right; rows without a match at the end),
    without the helper columns. DuckDB usually returns them like this already: they are sorted only if needed.
    """
    order = [ROW_ORDER[side] for side in sides]
    arrays = [table[name].fill_null(np.iinfo(np.int64).max).to_numpy() for name in order]
    if not _is_ordered(arrays):
        table = table.take(pc.sort_indices(table, sort_keys=[(name, "ascending") for name in order]))
    return table.select([name for name in table.column_names if name not in order])


def _without(table: pa.Table, name: str) -> pa.Table:
    return table.select([column for column in table.column_names if column != name])


def _cells(table: pa.Table) -> int:
    return table.num_rows * table.num_columns


def _check_plan_columns(plan: list[dict], left_names: list[str], right_names: list[str]) -> None:
    """The plan must refer to columns that really exist (an old plan after an upstream change does not)."""
    for entry in plan:
        if (entry["side"] == "left" and entry["column"] not in left_names) or (
                entry["side"] == "right" and entry["column"] not in right_names):
            raise ValueError("Il piano delle colonne non corrisponde ai dati: ricostruiscilo.")


def _thousands(number: int) -> str:
    return f"{number:,}".replace(",", ".")


def join_chain(frames: list[pd.DataFrame], steps: list[dict], names: list[str] | None = None,
               filters: list[dict | None] | None = None, plans: list[list[dict]] | None = None) -> pd.DataFrame:
    """
    Join chain: the dataset `frames[0]` is joined to `frames[steps[0]["right"]]`, then the result to another one, and so on.
    Returns a new DataFrame with the rows in the starting order (each step: left, then right).

    steps: [{"right": index in frames, "how": key of JOIN_TYPES, "keys": [new_key(...)], "match": key of KEY_MATCHES,
             "null_keys": bool, "merge_keys": bool, "naming": key of NAMING, "indicator": bool}, ...]
    filters: one filter tree per dataset (None = no filter), applied before each join.
    plans: column plan of each step (from plan_chain); if missing it is calculated with the default values.
    If a step produces too many rows (see MIN_RESULT_CELLS) it stops with a ValueError.
    """
    if not steps:
        raise ValueError("Aggiungi almeno un join.")
    labels = unique_labels(list(names) if names else default_names(len(frames)))
    filters = list(filters) if filters else [None] * len(frames)
    plans = plans if plans is not None else plan_chain(frames, steps, labels)
    if len(plans) != len(steps):
        raise ValueError("Il piano delle colonne non corrisponde ai join: ricostruiscilo.")
    eng.ARROW_CACHE.keep_only(frames)

    current = _arrow_source(frames[0], filters[0])
    for number, step in enumerate(steps):
        join_type = _join_type(step)
        right = _arrow_source(frames[step["right"]], filters[step["right"]])
        kept = [entry for entry in plans[number] if entry["keep"]]
        _check_names([entry["name"] for entry in kept])
        _check_plan_columns(kept, current.column_names, right.column_names)
        keys = _checked_keys(current.schema.empty_table().to_pandas(), right.schema.empty_table().to_pandas(),
                             step, join_type)
        limit = max(MIN_RESULT_CELLS, RESULT_GROWTH * (_cells(current) + _cells(right)))
        try:
            result = eng.run_sql_arrow(join_query(step, keys, kept), {"left_source": current, "right_source": right},
                                       limit)
        except eng.ResultTooLarge as error:
            raise ValueError(f"Il join produce più di {_thousands(error.rows)} righe e supera il limite di "
                             f"{_thousands(limit)} celle (righe × colonne). Di solito le chiavi hanno molti valori "
                             "ripetuti in entrambi i dataset: controlla le chiavi, aggiungine altre, filtra i dati "
                             "o escludi colonne.") from None
        current = with_row(_in_row_order(result, join_type.sides))
    return eng.arrow_to_pandas(_without(current, ROW))


def join(left: pd.DataFrame, right: pd.DataFrame, spec: dict,
         names: tuple[str, str] = DEFAULT_JOIN_NAMES) -> pd.DataFrame:
    """
    Join of two datasets (a single step of the chain). spec: as in join_chain, plus left_filters, right_filters
    and columns (column plan, optional).
    """
    step = {**{key: value for key, value in spec.items() if key not in TWO_SIDE_KEYS}, "right": 1}
    plans = [spec["columns"]] if spec.get("columns") else None
    return join_chain([left, right], [step], list(names), [spec.get("left_filters"), spec.get("right_filters")], plans)


# ---------------------------------------------------------------------------
# Union: types and alignment
# ---------------------------------------------------------------------------

UNION_TYPES = {
    "union_all": "Accoda tutte le righe (UNION ALL)",
    "union": "Accoda senza righe duplicate (UNION)",
    "intersect": "Solo le righe presenti in tutti i dataset (INTERSECT)",
    "except": "Righe del primo dataset assenti dagli altri (EXCEPT)",
}
APPENDING = ("union_all", "union")           # types that append; the others compare whole rows
ALIGNMENTS = {
    "by_name": "Per nome: tutte le colonne",
    "common": "Per nome: solo le colonne comuni",
    "first": "Per nome: solo le colonne del primo dataset",
    "position": "Per posizione (i nomi sono quelli del primo dataset)",
}


def union_plan(frames: list[pd.DataFrame], align: str = "by_name") -> list[dict]:
    """
    Column plan of the union: one entry per result column, {"name", "keep", "sources"}, where `sources`
    has, for each dataset, the column that flows into it (None = missing in that dataset: rows with null values).
    """
    if align not in ALIGNMENTS:
        raise ValueError(f"Allineamento sconosciuto: {align}")
    if not frames:
        raise ValueError("Servono dei dataset.")
    names = [[str(name) for name in frame.columns] for frame in frames]
    if align == "position":
        rows = [[each[index] if index < len(each) else None for each in names]
                for index in range(max(map(len, names)))]
    else:
        wanted = {"by_name": list(dict.fromkeys(chain.from_iterable(names))),
                  "common": [name for name in names[0] if all(name in each for each in names)],
                  "first": names[0]}[align]
        rows = [[name if name in each else None for each in names] for name in wanted]
    return [{"name": next(source for source in row if source is not None), "keep": True, "sources": row}
            for row in rows]


def entry_kinds(frames: list[pd.DataFrame], entry: dict) -> set[str]:
    """Types (number, text, date, boolean) of the data that flows into a column of the plan.
    Columns without any value have no type and do not count (it only checks whether the types differ)."""
    mapped = [frame[source] for frame, source in zip(frames, entry["sources"]) if source is not None]
    kinds = {fh.column_kind(series.dtype) for series in mapped}
    if len(kinds) > 1:
        kinds = {fh.column_kind(series.dtype) for series in mapped if series.notna().any()}
    return kinds


def type_conflicts(frames: list[pd.DataFrame], plan: list[dict]) -> dict[str, set[str]]:
    """Columns of the plan whose datasets have different types: {name: types}."""
    kinds = ((entry["name"], entry_kinds(frames, entry)) for entry in plan)
    return {name: found for name, found in kinds if len(found) > 1}


# ---------------------------------------------------------------------------
# Union: execution
# ---------------------------------------------------------------------------

def _check_union_plan(plan: list[dict], frames: list[pd.DataFrame], how: str, origin: bool) -> None:
    _check_names(_result_names(plan, origin))
    for entry in plan:
        if len(entry["sources"]) != len(frames):
            raise ValueError("Il piano delle colonne non corrisponde ai dataset scelti: ricostruiscilo.")
        for frame, source in zip(frames, entry["sources"]):
            if source is not None and source not in frame.columns:
                raise ValueError(f"La colonna «{source}» non esiste nel dataset.")
        if how not in APPENDING and None in entry["sources"]:
            raise ValueError(f"La colonna «{entry['name']}» manca in un dataset: {how.upper()} confronta righe intere, "
                             "quindi ogni colonna deve esistere in tutti (scegli l'allineamento «solo le colonne comuni»).")


def _aligned(frame: pd.DataFrame, index: int, plan: list[dict], as_text: set[str]) -> pd.DataFrame:
    """The columns of `frame` present in the plan, with the result names (missing ones are left out)."""
    series = {}
    for entry in plan:
        source = entry["sources"][index]
        if source is not None:
            values = frame[source].reset_index(drop=True)
            series[entry["name"]] = cols.convert(values, "Testo") if entry["name"] in as_text else values
    return pd.DataFrame(series, index=pd.RangeIndex(len(frame)))


def _set_operation(how: str, parts: list[pd.DataFrame]) -> pd.DataFrame:
    """INTERSECT / EXCEPT on all the columns, starting from the first dataset (unique rows, in the order of the first)."""
    result = parts[0].drop_duplicates()
    wanted = "both" if how == "intersect" else "left_only"
    for other in parts[1:]:
        merged = result.merge(other.drop_duplicates(), how="left", indicator=MERGE_FLAG)
        flag = merged.pop(MERGE_FLAG)
        result = merged[flag == wanted]
    return result.reset_index(drop=True)


def union(frames: list[pd.DataFrame], spec: dict, names: list[str] | None = None) -> pd.DataFrame:
    """
    Union of two or more datasets; returns a new DataFrame.

    spec (JSON-serializable):
      how: key of UNION_TYPES; align: key of ALIGNMENTS (used if `columns` is missing)
      filters: one filter tree per dataset (applied first); columns: column plan (default: union_plan)
      origin: adds the `origine` column with the dataset of each row (only UNION ALL and UNION; for UNION
              it is the first dataset in which the row appears)
    names: dataset names, used in the `origine` column
    """
    how = spec.get("how", "union_all")
    if how not in UNION_TYPES:
        raise ValueError(f"Tipo di union sconosciuto: {how}")
    if len(frames) < 2:
        raise ValueError("Servono almeno due dataset.")
    labels = unique_labels(names or [f"dataset_{number}" for number in range(1, len(frames) + 1)])
    trees = spec.get("filters") or [None] * len(frames)
    frames = [filtered(frame, tree) for frame, tree in zip(frames, trees)]
    plan = spec.get("columns") or union_plan(frames, spec.get("align", "by_name"))
    kept = [entry for entry in plan if entry["keep"]]
    origin = bool(spec.get("origin")) and how in APPENDING
    _check_union_plan(kept, frames, how, origin)

    as_text = set(type_conflicts(frames, kept))  # different types in the same column: all text, as in a CSV
    parts = [_aligned(frame, index, kept, as_text) for index, frame in enumerate(frames)]
    if how not in APPENDING:
        return _set_operation(how, parts)

    result = pd.concat(parts, ignore_index=True).reindex(columns=[entry["name"] for entry in kept])
    origins = np.repeat(labels, [len(part) for part in parts])
    if how == "union":
        first = ~result.duplicated().to_numpy()
        result, origins = result[first].reset_index(drop=True), origins[first]
    if origin:
        result[ORIGIN] = origins
    return result
