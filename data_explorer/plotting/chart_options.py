"""
ECharts options of the charts (no UI dependency).

build_options(data, view) turns a ChartData (see charts.py) into the ui.echart options of NiceGUI.
`view` holds only display choices (labels, decimals, title, ...): they are applied without
recalculating the data. build_panels(data, view, same_scale) gives the options of each panel of a chart split
into panels, with the same value scale and the same color for each series or category in every panel. The JavaScript functions (Italian-style numbers, labels, tooltips) live in the keys
with the ":" prefix, which NiceGUI turns into functions in the browser.
"""
from __future__ import annotations

import json
import math
from dataclasses import dataclass, field

import numpy as np
import pandas as pd

from data_explorer.plotting.charts import BOX_STATS, OTHERS, SCALED_DRAWS, ChartData, ChartType, is_date
from data_explorer.core.number_format import ITALIAN_LOCALE, localized

AUTO_LABELS = "Automatiche"
LABEL_MODES = {
    AUTO_LABELS: None,  # depends on the chart type
    "Nessuna": (),
    "Valore": ("value",),
    "Percentuale": ("share",),
    "Valore e percentuale": ("value", "share"),
    "Categoria": ("name",),
    "Categoria e valore": ("name", "value"),
    "Categoria e percentuale": ("name", "share"),
}
AUTO_DECIMALS = "Automatici"
DECIMALS = [AUTO_DECIMALS, "0", "1", "2", "3"]
DEFAULT_LABEL_SIZE = 12
LABEL_SIZE_RANGE = (6, 40)  # size of the data labels, in pixels
PLAIN_TEXT = {"fontWeight": "normal", "textBorderWidth": 0, "textShadowBlur": 0}  # labels without outline or shadow


def label_size(value) -> int:
    """Size of the labels within LABEL_SIZE_RANGE; the default one if the value is missing."""
    if value is None:
        return DEFAULT_LABEL_SIZE
    low, high = LABEL_SIZE_RANGE
    return min(max(int(value), low), high)

ZOOM_FROM = 30  # above this many categories the scroll bar appears


def _num(value) -> float | None:
    """Number for JSON: None for nulls and infinities."""
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


# ---------------------------------------------------------------------------
# JavaScript functions for ECharts (Italian-style numbers, labels, tooltips)
# ---------------------------------------------------------------------------

ESCAPE = "const esc = (t) => String(t).replace(/[&<>]/g, (c) => ({'&': '&amp;', '<': '&lt;', '>': '&gt;'})[c]); "


def js_number(decimals: str = AUTO_DECIMALS, compact: bool = False, suffix: str = "") -> str:
    """Italian-style number (1.234,5); compact: 1,2 K · 3,4 Mln · 5 Mrd."""
    digits = ("maximumFractionDigits: 2" if str(decimals) == AUTO_DECIMALS
              else f"minimumFractionDigits: {int(decimals)}, maximumFractionDigits: {int(decimals)}")
    short = ("for (const [size, unit] of [[1e9, ' Mrd'], [1e6, ' Mln'], [1e3, ' K']]) "
             "if (Math.abs(v) >= size) return (v / size).toLocaleString('it-IT', {maximumFractionDigits: 1}) + unit + end; "
             if compact else "")
    return ("(v) => { const end = " + json.dumps(suffix) + "; "
            "if (v === null || v === undefined || v === '' || isNaN(v)) return ''; "
            + short + "return Number(v).toLocaleString('it-IT', {" + digits + "}) + end; }")


def js_axis(suffix: str = "") -> str:
    return js_number(AUTO_DECIMALS, True, suffix)


def js_label(parts: tuple[str, ...], number: str, separator: str = ": ", html: bool = False) -> str:
    """Text of a label: category, value and percentage (p.data.share), in the chosen parts."""
    return ("(p) => { " + (ESCAPE if html else "const esc = (t) => t; ") + "const f = " + number + "; "
            "const v = Array.isArray(p.value) ? p.value[p.value.length - 1] : p.value; "
            "const share = p.data && p.data.share != null "
            "? (p.data.share * 100).toLocaleString('it-IT', {maximumFractionDigits: 1}) + '%' : ''; "
            "const show = " + json.dumps(list(parts)) + "; "
            "const value = show.includes('value') ? f(v) : ''; "
            "const pct = show.includes('share') ? share : ''; "
            "const text = value && pct ? value + ' (' + pct + ')' : value || pct; "
            "const name = show.includes('name') ? esc(p.name) : ''; "
            "return name && text ? name + " + json.dumps(separator) + " + text : name || text; }")


def js_box_tooltip(number: str) -> str:
    return ("(p) => { " + ESCAPE + "const f = " + number + "; const v = p.data.value; "
            "if (p.seriesType !== 'boxplot') return esc(p.name) + ': ' + f(v[1]); "
            "const rows = [['Baffo superiore', v[4]], ['Q3', v[3]], ['Mediana', v[2]], ['Q1', v[1]], "
            "['Baffo inferiore', v[0]]]; "
            "return '<b>' + esc(p.name) + '</b><br>' + rows.map(([t, x]) => t + ': ' + f(x)).join('<br>'); }")


def js_point_tooltip(names: list[str], number: str, dates: bool, date_only: bool) -> str:
    x_text = "f"
    if dates:
        x_text = "(v) => new Date(v).toLocale" + ("DateString" if date_only else "String") + "('it-IT')"
    return ("(p) => { " + ESCAPE + "const f = " + number + "; const fx = " + x_text + "; "
            "const v = p.data.value; const names = " + json.dumps(names) + "; "
            "const lines = names.map((n, i) => esc(n) + ': ' + (i === 0 ? fx(v[0]) : f(v[i]))); "
            "const head = p.seriesName === names[1] ? '' : '<b>' + esc(p.seriesName) + '</b><br>'; "
            "return head + lines.join('<br>'); }")


def js_cell_tooltip(number: str) -> str:
    return ("(p) => { " + ESCAPE + "const f = " + number + "; "
            "return esc(p.data.x) + ' · ' + esc(p.data.s) + ': ' + f(p.value[2]); }")


def js_bubble_size(low: float, high: float) -> str:
    span = (high - low) or 1
    return f"(v) => v[2] === null ? 5 : 5 + 25 * Math.sqrt(Math.max(0, (v[2] - {low}) / {span}))"


@dataclass
class View:
    """Display choices: they are applied without recalculating the data."""
    labels: str = AUTO_LABELS
    decimals: str = AUTO_DECIMALS
    compact: bool = False
    smooth: bool = False
    average: bool = False
    title: str = ""
    locale: str = ITALIAN_LOCALE  # style of the numbers (separators): `number_format.STYLES`
    label_size: int = DEFAULT_LABEL_SIZE  # size of the data labels, in pixels
    ink: str = "#1d1d1f"  # text color: labels outside the chart elements
    surface: str = "#ffffff"  # background color: borders between slices and rectangles
    heat: list[str] = field(default_factory=lambda: list(HEAT_COLORS))  # heatmap scale
    colors: list[str] = field(default_factory=list)  # series colors of the theme (same color in every panel)
    value_range: tuple[float, float] | None = None  # value scale shared by the panels (None = automatic)

    def number(self, suffix: str = "") -> str:
        """Tooltip format: all the digits (never compact)."""
        return js_number(self.decimals, False, suffix)

    def label(self, chart: ChartType, position: str, suffix: str = "", separator: str = ": ") -> dict:
        return self.label_from(LABEL_MODES[chart.auto_labels], position, suffix, separator)

    def label_from(self, auto: tuple[str, ...], position: str, suffix: str = "", separator: str = ": ") -> dict:
        """Data labels; with «Automatiche» it shows the `auto` parts."""
        parts = LABEL_MODES.get(self.labels)
        parts = auto if parts is None else parts
        if not parts:
            return {"show": False}
        label = {"show": True, "position": position, "fontSize": self.label_size, **PLAIN_TEXT,
                 ":formatter": js_label(parts, js_number(self.decimals, self.compact, suffix), separator)}
        if not position.startswith("inside"):  # inside bars and cells ECharts picks the color for contrast
            label["color"] = self.ink
        return label


# ---------------------------------------------------------------------------
# ECharts options: shared parts
# ---------------------------------------------------------------------------

FONT = "-apple-system, BlinkMacSystemFont, 'SF Pro Text', Inter, 'Segoe UI', Roboto, sans-serif"
MUTED = "#6e6e73"
OTHERS_COLOR = "#c7c7cc"
HEAT_COLORS = ["#eef5ff", "#9cc3ff", "#0071e3", "#003a75"]
DASHED = {"lineStyle": {"type": "dashed"}}


def _base(view: View, legend: bool, zoom: bool = False) -> dict:
    title = view.title.strip()
    features = {"saveAsImage": {"title": "Salva immagine", "name": title or "grafico", "pixelRatio": 2}}
    if zoom:
        features["dataZoom"] = {"title": {"zoom": "Zoom su un'area", "back": "Annulla zoom"}}
    features["restore"] = {"title": "Ripristina"}
    options = {
        "textStyle": {"fontFamily": FONT},
        "animationDuration": 400,
        "tooltip": {"confine": True},
        "toolbox": {"right": 8, "top": 0, "feature": features},
    }
    if title:
        options["title"] = {"text": title, "left": 8, "top": 0, "textStyle": {"fontSize": 15, "fontWeight": 600}}
    if legend:
        options["legend"] = {"type": "scroll", "top": 28 if title else 0, "left": "center", "width": "65%",
                             "itemGap": 16}
    return options


def _top(view: View, legend: bool) -> int:
    """Space at the top for title and legend."""
    return 40 + 28 * bool(view.title.strip()) + 28 * legend


def _category_axis(categories: list[str], horizontal: bool) -> dict:
    longest = max(map(len, categories), default=0)
    label = {"hideOverlap": True, "width": 140 if horizontal else 110, "overflow": "truncate"}
    if not horizontal and len(categories) * max(longest, 4) > 110:
        label["rotate"] = 30
    axis = {"type": "category", "data": categories, "axisLabel": label, "axisTick": {"alignWithLabel": True}}
    if horizontal:
        axis["inverse"] = True  # the first category at the top
    return axis


AXIS_NAMES = {  # position of the value axis title, always inside the chart area
    "left": {"nameTextStyle": {"color": MUTED, "align": "left"}},
    "right": {"nameTextStyle": {"color": MUTED, "align": "right"}},
    "bottom": {"nameTextStyle": {"color": MUTED}, "nameLocation": "middle", "nameGap": 28},
}


def _value_axis(name: str = "", suffix: str = "", maximum: float | None = None, side: str = "left") -> dict:
    axis = {"type": "value", "axisLabel": {":formatter": js_axis(suffix)}, "splitLine": DASHED}
    if name:
        axis.update(name=name, **AXIS_NAMES[side])
    if maximum is not None:
        axis["max"] = maximum
    return axis


def _inside_zoom(axis: str) -> dict:
    """Drag to scroll; the wheel zooms only with Ctrl (the page keeps scrolling)."""
    return {"type": "inside", axis: 0, "zoomOnMouseWheel": "ctrl", "moveOnMouseWheel": False}


def _category_zoom(horizontal: bool) -> list[dict]:
    axis = "yAxisIndex" if horizontal else "xAxisIndex"
    window = {"startValue": 0, "endValue": ZOOM_FROM - 1}
    slider = {"right": 4, "width": 14} if horizontal else {"bottom": 8, "height": 18}
    return [{**_inside_zoom(axis), **window}, {"type": "slider", axis: 0, **window, **slider}]


def _nice_step(raw: float) -> float:
    """The smallest «round» step ≥ raw (1, 2, 3, 5 × a power of ten)."""
    magnitude = 10 ** math.floor(math.log10(raw))
    return next(multiple * magnitude for multiple in (1, 2, 3, 5, 10) if multiple * magnitude >= raw)


def _nice_ceiling(value: float, parts: int = 5) -> float:
    """The smallest «round number» ≥ value that splits into `parts` ticks (e.g. 1.108.508 → 1.500.000)."""
    return _nice_step(value / parts) * parts


def _scale(value_range: tuple[float, float], from_zero: bool, parts: int = 5) -> dict:
    """Value axis on a fixed range with round ticks (panels with the same scale)."""
    low, high = value_range
    if from_zero:
        low, high = min(low, 0.0), max(high, 0.0)
    step = _nice_step((high - low) / parts) if high > low else _nice_step(abs(high) or 1.0)
    return {"min": math.floor(low / step) * step, "max": math.ceil(high / step) * step, "interval": step}


def _shares(values: pd.DataFrame, by_row: bool) -> np.ndarray:
    """Share of each value in the total of the series (column) or of the category (row)."""
    array = values.to_numpy(dtype="float64")
    totals = np.nansum(array, axis=1 if by_row else 0, keepdims=True)
    with np.errstate(divide="ignore", invalid="ignore"):
        return np.where(totals != 0, array / totals, np.nan)


def _items(categories: list[str], values, shares, series_label: str | None, grey_others: bool) -> list[dict]:
    """Data of a series: value, share and keys to trace back to the rows."""
    items = []
    for position, category in enumerate(categories):
        item = {"value": _num(values[position]), "x": category}
        if shares is not None:
            item["share"] = _num(shares[position])
        if series_label is not None:
            item["s"] = series_label
        if grey_others and category == OTHERS:
            item["itemStyle"] = {"color": OTHERS_COLOR}
        items.append(item)
    return items


def _average_line(number: str, label: bool, horizontal: bool) -> dict:
    """Dashed average line; labelled only with one series (with several series the labels would overlap)."""
    text = {"show": label, ":formatter": "(p) => 'Media: ' + (" + number + ")(p.value)",
            "position": "start" if horizontal else "insideEndTop"}
    return {"symbol": "none", "silent": True, "lineStyle": {"type": "dashed"},
            "data": [{"type": "average", "name": "Media"}], "label": text}


# ---------------------------------------------------------------------------
# ECharts options: one builder per family
# ---------------------------------------------------------------------------

def _cartesian(data: ChartData, view: View) -> dict:
    chart = data.chart
    categories = data.table.iloc[:, 0].astype(str).tolist()
    values = data.table.iloc[:, 1:]
    dual = chart.variant in ("combo", "pareto")
    legend = values.shape[1] > 1
    zoom = len(categories) > ZOOM_FROM
    shares = _shares(values, by_row=chart.stack and legend)
    array = values.to_numpy(dtype="float64")

    options = _base(view, legend, zoom=True)
    options["tooltip"].update(trigger="axis", axisPointer={"type": "line" if chart.series_type == "line" else "shadow"})
    bottom = 40 if zoom and not chart.horizontal else 36 if chart.horizontal and data.value_name else 12
    options["grid"] = {"left": 12, "right": 36 if zoom and chart.horizontal else 24, "top": _top(view, legend),
                       "bottom": bottom, "containLabel": True}
    options["series"] = []
    for index, name in enumerate(values.columns):
        right = dual and index > 0
        line = right or (chart.series_type == "line" and not dual)
        suffix = "%" if chart.percent or (right and chart.variant == "pareto") else ""
        position = "top" if line else "inside" if chart.stack else "right" if chart.horizontal else "top"
        series = {
            "name": str(name), "type": "line" if line else "bar",
            "data": _items(categories, array[:, index], None if right else shares[:, index],
                           data.series_of.get(name), grey_others=not legend),
            "label": view.label(chart, position, suffix),
            "labelLayout": {"hideOverlap": True},
            "tooltip": {":valueFormatter": view.number(suffix)},
            "emphasis": {"focus": "series"},
        }
        if right:
            series["yAxisIndex"] = 1
        if chart.stack:
            series["stack"] = "totale"
        if line:
            series.update(smooth=view.smooth and not chart.step, showSymbol=len(categories) <= 60, symbolSize=6,
                          connectNulls=True)
        if chart.area:
            series["areaStyle"] = {"opacity": 0.6 if chart.stack else 0.25}
        if chart.step:
            series["step"] = "middle"
        if chart.kind == "histogram":
            series["barCategoryGap"] = "3%"
        if view.average and not chart.stack and not right:
            series["markLine"] = _average_line(view.number(suffix), not legend, chart.horizontal)
        if name == OTHERS:
            series["itemStyle"] = {"color": OTHERS_COLOR}
        options["series"].append(series)

    category_axis = _category_axis(categories, chart.horizontal)
    if chart.series_type == "line" and not dual:
        category_axis["boundaryGap"] = False
    value_axes = [_value_axis(str(values.columns[0]) if dual else data.value_name, "%" if chart.percent else "",
                              100 if chart.percent else None, "bottom" if chart.horizontal else "left")]
    if view.value_range and not chart.percent:
        value_axes[0].update(_scale(view.value_range, from_zero=True))
    if dual:
        pareto = chart.variant == "pareto"
        right_name = str(values.columns[1]) if values.shape[1] == 2 else ""
        value_axes.append({**_value_axis(right_name, "%" if pareto else "", 100 if pareto else None, "right"),
                           "splitLine": {"show": False}})
    options["xAxis"], options["yAxis"] = ((value_axes, category_axis) if chart.horizontal
                                          else (category_axis, value_axes))
    if zoom:
        options["dataZoom"] = _category_zoom(chart.horizontal)
    return options


def _positive_items(data: ChartData, relative_to_max: bool) -> list[dict]:
    """Categories with a positive value (pie, funnel), with the share of the total or of the maximum."""
    names = data.table.iloc[:, 0].astype(str).tolist()
    values = data.table.iloc[:, 1].to_numpy(dtype="float64")
    positive = values > 0
    base = values[positive].max() if relative_to_max and positive.any() else values[positive].sum()
    items = []
    for name, value, keep in zip(names, values, positive):
        if keep:
            item = {"name": name, "value": float(value), "share": float(value / base), "x": name}
            if name == OTHERS:
                item["itemStyle"] = {"color": OTHERS_COLOR}
            items.append(item)
    return items


def _pie(data: ChartData, view: View) -> dict:
    chart = data.chart
    options = _base(view, legend=True)
    options["tooltip"]["trigger"] = "item"
    radius = {"donut": ["38%", "64%"], "rose": ["12%", "66%"]}.get(chart.variant, "64%")
    series = {
        "type": "pie", "name": str(data.table.columns[1]), "data": _positive_items(data, relative_to_max=False),
        "radius": radius, "center": ["50%", "56%"], "avoidLabelOverlap": True, "minShowLabelAngle": 4,
        "itemStyle": {"borderColor": view.surface, "borderWidth": 1, "borderRadius": 6 if chart.variant else 0},
        "label": view.label(chart, "outside"),
        "tooltip": {":formatter": js_label(("name", "value", "share"), view.number(), html=True)},
    }
    if chart.variant == "rose":
        series["roseType"] = "radius"
    options["series"] = [series]
    return options


def _funnel(data: ChartData, view: View) -> dict:
    chart = data.chart
    options = _base(view, legend=True)
    options["tooltip"]["trigger"] = "item"
    options["series"] = [{
        "type": "funnel", "name": str(data.table.columns[1]), "data": _positive_items(data, relative_to_max=True),
        "sort": "none", "left": "15%", "right": "15%", "top": _top(view, True), "bottom": 16, "gap": 2,
        "minSize": "8%", "label": view.label(chart, "inside"), "labelLine": {"show": False},
        "itemStyle": {"borderColor": view.surface, "borderWidth": 1},
        "tooltip": {":formatter": js_label(("name", "value", "share"), view.number(), html=True)},
    }]
    return options


def _treemap(data: ChartData, view: View) -> dict:
    chart = data.chart
    names = data.table.iloc[:, 0].astype(str).tolist()
    columns = data.table.columns[1:]
    array = data.table.iloc[:, 1:].to_numpy(dtype="float64")
    total = np.where(array > 0, array, 0).sum() or 1
    nested = bool(data.series_of)
    nodes = []
    for row, name in enumerate(names):
        children = [{"name": str(column), "value": float(value), "share": float(value / total), "x": name,
                     "s": data.series_of[column]}
                    for column, value in zip(columns, array[row]) if nested and value > 0]
        size = sum(child["value"] for child in children) if nested else array[row, 0]
        if not size > 0:
            continue
        node = {"name": name, "share": float(size / total), "x": name}
        if nested:
            node["children"] = children
        else:
            node["value"] = float(size)
        if name == OTHERS:
            node["itemStyle"] = {"color": OTHERS_COLOR}
        nodes.append(node)
    options = _base(view, legend=False)
    options["series"] = [{
        "type": "treemap", "name": data.value_name, "data": nodes,
        "top": _top(view, False), "left": 8, "right": 8, "bottom": 32 if nested else 8,
        "roam": False, "nodeClick": "zoomToNode" if nested else False,
        "breadcrumb": {"show": nested, "bottom": 4},
        "label": view.label(chart, "insideTopLeft", separator="\n"),
        "upperLabel": {"show": nested, "height": 22},
        "itemStyle": {"borderColor": view.surface, "borderWidth": 1, "gapWidth": 1},
        "levels": [  # root, categories, series
            {"itemStyle": {"borderWidth": 0, "gapWidth": 2}, "upperLabel": {"show": False}},
            {"itemStyle": {"borderColor": view.surface, "borderWidth": 2, "gapWidth": 2}},
            {"colorSaturation": [0.35, 0.6], "itemStyle": {"gapWidth": 1, "borderColorSaturation": 0.6}},
        ],
        "tooltip": {":formatter": js_label(("name", "value", "share"), view.number(), html=True)},
    }]
    return options


def _heatmap(data: ChartData, view: View) -> dict:
    chart = data.chart
    x_labels = data.table.iloc[:, 0].astype(str).tolist()
    columns = data.table.columns[1:]
    array = data.table.iloc[:, 1:].to_numpy(dtype="float64")
    finite = array[np.isfinite(array)]
    total = finite.sum() or 1
    items = [{"value": [i, j, float(array[i, j])], "share": float(array[i, j] / total),
              "x": x_labels[i], "s": data.series_of.get(column, str(column))}
             for i in range(len(x_labels)) for j, column in enumerate(columns) if np.isfinite(array[i, j])]
    low = float(finite.min()) if finite.size else 0.0
    high = float(finite.max()) if finite.size else 1.0
    if view.value_range:
        low, high = view.value_range
    zoom = len(x_labels) > ZOOM_FROM

    options = _base(view, legend=False)
    options["tooltip"]["trigger"] = "item"
    options["grid"] = {"left": 12, "right": 96, "top": _top(view, False), "bottom": 40 if zoom else 12,
                       "containLabel": True}
    options["xAxis"] = {**_category_axis(x_labels, False), "splitArea": {"show": True}}
    options["yAxis"] = {"type": "category", "data": [str(column) for column in columns], "inverse": True,
                        "splitArea": {"show": True}, "axisLabel": {"width": 140, "overflow": "truncate"}}
    options["visualMap"] = {"min": low, "max": high if high > low else low + 1, "calculable": True,
                            "orient": "vertical", "right": 4, "top": "middle", "inRange": {"color": view.heat},
                            ":formatter": js_axis()}
    options["series"] = [{
        "type": "heatmap", "name": data.value_name, "data": items, "label": view.label(chart, "inside"),
        "emphasis": {"itemStyle": {"borderColor": "#1d1d1f", "borderWidth": 1}},
        "tooltip": {":formatter": js_cell_tooltip(view.number())},
    }]
    if zoom:
        options["dataZoom"] = _category_zoom(False)
    return options


def _radar(data: ChartData, view: View) -> dict:
    names = data.table.iloc[:, 0].astype(str).tolist()
    columns = data.table.columns[1:]
    array = np.nan_to_num(data.table.iloc[:, 1:].to_numpy(dtype="float64"))
    smallest, largest = view.value_range or (float(array.min()), float(array.max()))
    low = -_nice_ceiling(-smallest) if smallest < 0 else 0.0
    high = _nice_ceiling(largest) if largest > 0 else 1.0
    items = []
    for j, column in enumerate(columns):
        item = {"name": str(column), "value": [float(value) for value in array[:, j]]}
        if column in data.series_of:
            item["s"] = data.series_of[column]
        items.append(item)
    options = _base(view, legend=len(items) > 1)
    options["tooltip"].update({"trigger": "item", ":valueFormatter": view.number()})
    options["radar"] = {"indicator": [{"name": name, "min": low, "max": high} for name in names],
                        "radius": "62%", "center": ["50%", "57%"], "axisName": {"color": MUTED}}
    options["series"] = [{"type": "radar", "name": data.value_name, "data": items, "symbolSize": 5,
                          "areaStyle": {"opacity": 0.12}, "emphasis": {"focus": "self"}}]
    return options


def _boxplot(data: ChartData, view: View) -> dict:
    categories = data.table.iloc[:, 0].astype(str).tolist()
    position = {label: index for index, label in enumerate(categories)}
    stats = data.table[list(BOX_STATS)].to_numpy(dtype="float64")
    boxes = [{"value": [_num(value) for value in row], "x": label} for label, row in zip(categories, stats)]
    outliers = [{"name": label, "value": [position[label], float(value)], "row": int(row)}
                for label, value, row in data.points[["x", "y", "row"]].itertuples(index=False)]
    zoom = len(categories) > ZOOM_FROM

    options = _base(view, legend=False, zoom=True)
    options["tooltip"].update({"trigger": "item", ":formatter": js_box_tooltip(view.number())})
    options["grid"] = {"left": 12, "right": 24, "top": _top(view, False), "bottom": 40 if zoom else 12,
                       "containLabel": True}
    options["xAxis"] = _category_axis(categories, False)
    options["yAxis"] = {**_value_axis(data.value_name), "scale": True}
    if view.value_range:
        options["yAxis"].update(_scale(view.value_range, from_zero=False))
    options["series"] = [
        {"type": "boxplot", "name": data.value_name, "data": boxes, "itemStyle": {"borderWidth": 1.5}},
        {"type": "scatter", "name": "valori anomali", "data": outliers, "symbolSize": 6,
         "itemStyle": {"opacity": 0.55}},
    ]
    if zoom:
        options["dataZoom"] = _category_zoom(False)
    return options


def _scatter(data: ChartData, view: View) -> dict:
    chart = data.chart
    points = data.points
    bubble = chart.variant == "bubble"
    dates = is_date(points["x"])
    date_only = dates and bool((points["x"] == points["x"].dt.normalize()).all())
    xs = (points["x"].dt.strftime("%Y-%m-%dT%H:%M:%S") if dates else points["x"].astype("float64")).tolist()
    columns = ["y", "size"] if bubble else ["y"]
    values = [[x, *row] for x, row in zip(xs, points[columns].to_numpy(dtype="float64").tolist())]
    groups = list(data.series_keys.cat.categories) if "series" in points else [data.value_name]
    labels = points["series"].to_numpy() if "series" in points else np.full(len(points), data.value_name)

    options = _base(view, legend=len(groups) > 1, zoom=True)
    names = [data.x_name, data.value_name] + ([data.size_name] if bubble else [])
    options["tooltip"].update({"trigger": "item", ":formatter": js_point_tooltip(names, view.number(), dates, date_only)})
    size = js_bubble_size(float(points["size"].min()), float(points["size"].max())) if bubble else None
    options["series"] = []
    for group in groups:
        items = [{"value": value, "row": int(row)}
                 for value, row, label in zip(values, points["row"], labels) if label == group]
        series = {"type": "scatter", "name": str(group), "data": items, "itemStyle": {"opacity": 0.5 if bubble else 0.75},
                  "emphasis": {"focus": "series"}}
        series.update({":symbolSize": size} if bubble else {"symbolSize": 7})
        if group == OTHERS:
            series["itemStyle"]["color"] = OTHERS_COLOR
        options["series"].append(series)
    x_axis = {"type": "time" if dates else "value", "name": data.x_name, "nameLocation": "middle",
              "nameGap": 28, "scale": True, "splitLine": DASHED}
    if not dates:
        x_axis["axisLabel"] = {":formatter": js_axis()}
    options["xAxis"] = x_axis
    options["yAxis"] = {**_value_axis(data.value_name), "scale": True}
    if view.value_range:
        options["yAxis"].update(_scale(view.value_range, from_zero=False))
    options["grid"] = {"left": 12, "right": 24, "top": _top(view, len(groups) > 1), "bottom": 36, "containLabel": True}
    options["dataZoom"] = [_inside_zoom("xAxisIndex"), _inside_zoom("yAxisIndex")]
    return options


BUILDERS = {
    "cartesian": _cartesian,
    "pie": _pie,
    "funnel": _funnel,
    "treemap": _treemap,
    "heatmap": _heatmap,
    "radar": _radar,
    "boxplot": _boxplot,
    "scatter": _scatter,
}


# ---------------------------------------------------------------------------
# Hand-written charts (console)
# ---------------------------------------------------------------------------

def written_chart_defaults(options: dict, ink: str) -> dict:
    """ECharts options written by the user, with the style of the app charts: labels in plain text and Italian-style
    numbers in labels, tooltips and value axes. The keys written by the user stay as they are."""
    series = options.get("series")
    for item in series if isinstance(series, list) else [series]:
        label = item.get("label") if isinstance(item, dict) else None
        if not isinstance(label, dict) or not label.get("show"):
            continue
        for key, value in PLAIN_TEXT.items():
            label.setdefault(key, value)
        position = label.get("position")
        if isinstance(position, str) and not position.startswith("inside"):  # inside the elements ECharts decides
            label.setdefault("color", ink)
        if "formatter" not in label and ":formatter" not in label:
            label[":formatter"] = js_label(("value",), js_number())
    tooltip = options.get("tooltip")
    if isinstance(tooltip, dict) and not {"formatter", ":formatter", "valueFormatter", ":valueFormatter"} & set(tooltip):
        tooltip[":valueFormatter"] = js_number()
    for name in ("xAxis", "yAxis"):
        axes = options.get(name)
        for axis in axes if isinstance(axes, list) else [axes]:
            if isinstance(axis, dict) and axis.get("type", "value" if name == "yAxis" else "category") == "value":
                axis_label = axis.setdefault("axisLabel", {})
                if isinstance(axis_label, dict) and not {"formatter", ":formatter"} & set(axis_label):
                    axis_label[":formatter"] = js_axis()  # 1,2 Mln as in the app charts
    return options


def build_options(data: ChartData, view: dict | None = None) -> dict:
    """ECharts options of the chart; `view`: View fields (labels, decimals, title, ...)."""
    view = View(**(view or {}))
    return localized(BUILDERS[data.chart.draw](data, view), view.locale)


# ---------------------------------------------------------------------------
# Panels: one chart per value of a column
# ---------------------------------------------------------------------------

BY_CATEGORY = {"pie", "funnel", "treemap", "radar"}  # drawings whose colored elements are the data items


def value_bounds(data: ChartData) -> tuple[float, float] | None:
    """Smallest and largest value on the value scale of the chart (None = no value scale)."""
    chart = data.chart
    if chart.draw not in SCALED_DRAWS or chart.percent:
        return None
    if chart.draw == "scatter":
        values = data.points["y"].to_numpy(dtype="float64")
    elif chart.draw == "boxplot":
        values = np.concatenate([data.table[list(BOX_STATS)].to_numpy(dtype="float64").ravel(),
                                 data.points["y"].to_numpy(dtype="float64")])
    else:
        values = data.table.iloc[:, 1:].to_numpy(dtype="float64")
        if chart.variant in ("combo", "pareto"):  # only the left axis: the first value
            values = values[:, :1]
        if chart.stack:  # stacked bars: totals of the positive and of the negative parts
            values = np.column_stack([np.nansum(np.where(values > 0, values, 0), axis=1),
                                      np.nansum(np.where(values < 0, values, 0), axis=1)])
    finite = values[np.isfinite(values)]
    return (float(finite.min()), float(finite.max())) if finite.size else None


def _colored(options: dict, draw: str) -> list[dict]:
    """Elements that take a palette color: the series or, for pie, funnel, treemap and radar, the data items."""
    if draw in BY_CATEGORY:
        return options["series"][0]["data"]
    return options["series"] if draw in ("cartesian", "scatter") else []


def _same_colors(panels: list[dict], draw: str, colors: list[str]) -> None:
    """Each series (or category) gets the same color in every panel, in order of first appearance."""
    color_of: dict[str, str] = {}
    for options in panels:
        for element in _colored(options, draw):
            color = color_of.setdefault(element["name"], colors[len(color_of) % len(colors)])
            element.setdefault("itemStyle", {}).setdefault("color", color)  # «(altri)» stays grey


def build_panels(data: ChartData, view: dict | None = None, same_scale: bool = True) -> list[tuple[str, dict]]:
    """(label, ECharts options) of each panel; a chart without panels is one panel with an empty label.
    Each panel is titled with its value; `same_scale` gives every panel the same value scale."""
    if not data.panels:
        return [("", build_options(data, view))]
    view = view or {}
    bounds = [found for panel in data.panels.values() if (found := value_bounds(panel))] if same_scale else []
    shared = (min(low for low, _ in bounds), max(high for _, high in bounds)) if bounds else None
    built = [(label, build_options(panel, {**view, "title": f"{data.panel_name}: {label}", "value_range": shared}))
             for label, panel in data.panels.items()]
    if view.get("colors"):
        _same_colors([options for _, options in built], data.chart.draw, view["colors"])
    return built
