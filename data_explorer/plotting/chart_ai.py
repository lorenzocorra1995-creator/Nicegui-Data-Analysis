"""
Chart from a request written in Italian (no UI dependency).

The AI does not write code: it answers with the choices of the Charts tab (type, columns, values, Top N, labels...).
The choices are checked (allowed types, columns and values) and then fill in the controls, so they stay editable.

Privacy: the API receives ONLY the request and the metadata (column name, type). No data value leaves the computer.
"""
from __future__ import annotations

import json

import pandas as pd

from data_explorer.ai import ai_assistant as ai
from data_explorer.plotting import chart_options as co
from data_explorer.plotting import charts
from data_explorer.core import engine as eng
# ---------------------------------------------------------------------------
# Request
# ---------------------------------------------------------------------------

SYSTEM_PROMPT = (
    "You configure a chart in a local data exploration tool. "
    "You only know the structure of the data (column names and types), never the values: "
    "do not assume literal values or formats beyond what the user states. "
    "Reply with exactly one JSON object and absolutely nothing else: no markdown, no explanations. "
    "Use only the chart keys, column names and values listed in the prompt, copied exactly. "
    "Include only the fields that the chosen chart uses; omit the others."
)

CHOICES = {  # closed-choice fields: allowed values
    "date_unit": list(charts.DATE_UNITS),
    "panel_date_unit": list(charts.DATE_UNITS),
    "top_from": [charts.TOP, charts.BOTTOM],
    "sort": charts.SORT_OPTIONS,
    "labels": list(co.LABEL_MODES),
    "decimals": co.DECIMALS,
}

FIELDS = (
    "chart: one chart key from the list",
    "x: column on the X axis (categories); for histogram a numeric column; for scatter and bubble a numeric or date column",
    "series: column that splits the data into series (colors)",
    "y: numeric column (box plot, scatter, bubble)",
    "size: numeric column for the bubble size",
    "panels: column that splits the chart into one small chart per value (e.g. one per year); "
    "top_n and sort then apply inside each panel",
    f"panel_date_unit: how to group a date panels column, one of: {', '.join(CHOICES['panel_date_unit'])}",
    "measures: list of {\"col\": column, \"fn\": function} computed for each category; empty list = number of rows; "
    f"fn is one of: {', '.join(eng.AGG_FUNCTIONS)}",
    f"date_unit: how to group a date X column, one of: {', '.join(CHOICES['date_unit'])}",
    f"bins: number of classes of the histogram (2-{charts.MAX_BINS})",
    "top_n: keep only the first N categories (0 = all)",
    f"top_from: {' or '.join(repr(value) for value in CHOICES['top_from'])} (which values to keep for top_n)",
    "others: true = group the remaining categories in a single '(altri)' category instead of dropping them",
    f"sort: order of the X axis, one of: {', '.join(CHOICES['sort'])}",
    f"labels: data labels, one of: {', '.join(CHOICES['labels'])}",
    f"decimals: one of: {', '.join(CHOICES['decimals'])}",
    "compact, smooth, average: true/false (compact numbers, smoothed lines, average line)",
    "title: short title in Italian",
)

RULES = (
    "Pick the chart type that best answers the request. "
    "combo needs at least two measures (the first as bars, the others as lines); "
    "pareto, pie, donut, rose, treemap, funnel and heatmap use a single measure; heatmap requires series; "
    "boxplot: x = categories, y = numeric column; scatter and bubble: x = numeric or date column, y = numeric column; "
    "for trends over time use a line chart with a date column as x and date_unit for the granularity. "
    "For «top N per year» (or per any other group) use panels = that column and top_n on x; panels must differ from x. "
    "Use fn 'somma' for totals, 'media' for averages, 'conteggio' to count non-null values."
)


def describe_charts() -> str:
    return "\n".join(f"- {key}: {chart.label} (uses: {', '.join(sorted(charts.used_fields(chart)))})"
                     for key, chart in charts.CHART_TYPES.items())


def build_prompt(request: str, df: pd.DataFrame) -> str:
    return (
        f"Chart types (key: label (fields used)):\n{describe_charts()}\n\n"
        "Reply with a JSON object with these fields:\n" + "\n".join(f"- {field}" for field in FIELDS) + "\n\n"
        f"Rules: {RULES}\n\n"
        f"Metadata (name: column (type)):\n{ai.describe_tables({'df': df})}\n\n"
        f"User request (in Italian):\n{request.strip()}"
    )


# ---------------------------------------------------------------------------
# Answer
# ---------------------------------------------------------------------------

DEFAULTS = {
    "chart": "bar", "x": None, "series": None, "y": None, "size": None, "measures": [],
    "panels": None, "panel_date_unit": charts.DEFAULT_PANEL_DATE_UNIT,
    "date_unit": charts.DEFAULT_DATE_UNIT, "bins": charts.DEFAULT_BINS,
    "top_n": 0, "top_from": charts.TOP, "others": False, "sort": charts.AUTO,
    "labels": co.AUTO_LABELS, "decimals": co.AUTO_DECIMALS,
    "compact": False, "smooth": False, "average": False, "title": "",
}
COLUMN_FIELDS = ("x", "series", "y", "size", "panels")
FLAG_FIELDS = ("others", "compact", "smooth", "average")


def parse_answer(text: str) -> dict:
    """The JSON object of the answer (even when it is wrapped in a code block)."""
    text = ai.clean_code(text)
    try:
        answer = json.loads(text[text.find("{"):text.rfind("}") + 1])
    except json.JSONDecodeError as error:
        raise ValueError("La risposta dell'AI non è valida: riprova o riformula la richiesta.") from error
    if not isinstance(answer, dict):
        raise ValueError("La risposta dell'AI non è valida: riprova o riformula la richiesta.")
    return answer


def resolve_column(name, columns: list[str]) -> str:
    """Exact name of the column (ignoring upper and lower case); error if it does not exist."""
    matches = [column for column in columns if column == name] or [
        column for column in columns if column.lower() == str(name).lower()]
    if len(matches) != 1:
        raise ValueError(f"L'AI ha usato una colonna che non esiste: «{name}».")
    return matches[0]


def to_int(value, default: int, low: int, high: int) -> int:
    try:
        return min(max(int(float(value)), low), high)
    except (TypeError, ValueError):
        return default


def to_measures(items, columns: list[str]) -> list[dict]:
    measures = []
    for item in items if isinstance(items, list) else []:
        if not isinstance(item, dict) or not item.get("col"):
            continue
        function = item.get("fn") or "somma"
        if function not in eng.AGG_FUNCTIONS:
            raise ValueError(f"L'AI ha usato una funzione di aggregazione che non esiste: «{function}».")
        measures.append({"col": resolve_column(item["col"], columns), "fn": function})
    return measures


def normalize(answer: dict, columns: list[str]) -> dict:
    """Complete and valid choices for the controls of the Charts tab; fields the chart type does not use keep their defaults."""
    key = answer.get("chart")
    if key not in charts.CHART_TYPES:
        raise ValueError(f"L'AI ha scelto un tipo di grafico che non esiste: «{key}».")
    used = charts.used_fields(charts.CHART_TYPES[key])
    settings = {**DEFAULTS, "chart": key, "title": str(answer.get("title") or "").strip()}
    for field in COLUMN_FIELDS:
        if field in used and answer.get(field):
            settings[field] = resolve_column(answer[field], columns)
    if "measures" in used:
        settings["measures"] = to_measures(answer.get("measures"), columns)
    for field, allowed in CHOICES.items():
        if (field in used or field == "decimals") and str(answer.get(field)) in allowed:
            settings[field] = str(answer[field])
    if "bins" in used:
        settings["bins"] = to_int(answer.get("bins"), charts.DEFAULT_BINS, 2, charts.MAX_BINS)
    if "top_n" in used:
        settings["top_n"] = to_int(answer.get("top_n"), 0, 0, charts.MAX_CATEGORIES)
    for field in FLAG_FIELDS:
        settings[field] = (field == "compact" or field in used) and answer.get(field) is True
    return settings


def generate_settings(api_key: str, request: str, df: pd.DataFrame,
                      model: str = ai.DEFAULT_MODEL, effort: str = ai.DEFAULT_EFFORT) -> dict:
    """Choices for the requested chart (see `normalize`)."""
    text = ai.ask(api_key, SYSTEM_PROMPT, build_prompt(request, df), model, effort)
    return normalize(parse_answer(text), [str(column) for column in df.columns])
