"""
ECharts options for the charts of the Statistics tab (no UI dependency).

build_options(plot, view) turns a stats.Plot into ui.echart options; `view` (chart_options.View) applies
the same look choices as the Charts tab: title, decimals, compact numbers, labels, smooth lines and average
(the last three only where they make sense, see VIEW_FIELDS). Content of plot.data by type:
- "bars":    categories, bars {series: values}, lines {series: values} (lines on the same categories)
- "boxplot": categories, boxes [[lower whisker, q1, median, q3, upper whisker], ...], outliers [[position, value], ...]
             (data labels show the median of each box; boxes of different columns, without y_name, get one
             panel and value axis each)
- "scatter": points {series: [[x, y], ...]}, lines {series: [[x, y], ...]}
- "lines":   x (labels), series {series: values}
- "heatmap": x, y (labels), values (rows = y, columns = x), optional min/max, diverging (two-color scale)
"""
from __future__ import annotations

from data_explorer.plotting.chart_options import DASHED, View, _average_line, _base, _category_axis, _inside_zoom, _num, _value_axis, js_axis
from data_explorer.core.number_format import localized
from data_explorer.core.stats import Plot

DIVERGING = ["#d93025", "#f1f1f1", "#0071e3"]  # from −1 (red) to +1 (blue)
ZOOM_FROM = 40  # above this many categories the scroll bar appears
HEATMAP_LABELS_UP_TO = 400  # cells above which automatic labels are hidden
MAX_BOX_PANELS = 8  # boxes of different columns, each on its own axis; beyond this they share one axis

# Optional look choices used by each type (title, decimals, compact numbers and height always apply)
VIEW_FIELDS = {"bars": {"labels", "average"}, "boxplot": {"labels"}, "scatter": {"labels"},
               "lines": {"labels", "smooth", "average"}, "heatmap": {"labels"}}


def view_fields(plots: list[Plot]) -> set[str]:
    return set().union(*(VIEW_FIELDS[plot.kind] for plot in plots))


def _numbers(values) -> list[float | None]:
    return [_num(value) for value in values]


def _average(series: dict, view: View, legend: bool) -> dict:
    """Average line on the series, if requested (labelled only when there is no legend, as in Charts)."""
    if view.average:
        series["markLine"] = _average_line(view.number(), not legend, False)
    return series


def _grid(view: View, legend: bool, bottom: int = 12) -> dict:
    return {"left": 12, "right": 24, "top": 40 + 28 * bool(view.title.strip()) + 28 * legend, "bottom": bottom,
            "containLabel": True}


def _slider(count: int) -> list[dict]:
    if count <= ZOOM_FROM:
        return []
    return [_inside_zoom("xAxisIndex"), {"type": "slider", "xAxisIndex": 0, "bottom": 8, "height": 18}]


def _bars(plot: Plot, view: View) -> dict:
    data = plot.data
    legend = len(data["bars"]) + len(data["lines"]) > 1
    zoom = _slider(len(data["categories"]))
    options = _base(view, legend=legend)
    options["tooltip"].update({"trigger": "axis", ":valueFormatter": view.number()})
    options["grid"] = _grid(view, legend, 40 if zoom else 12)
    options["xAxis"] = {**_category_axis(data["categories"], False), "name": plot.x_name, "nameLocation": "middle",
                        "nameGap": 28}
    options["yAxis"] = _value_axis(plot.y_name)
    options["series"] = ([_average({"type": "bar", "name": name, "data": _numbers(values),
                                    "label": view.label_from((), "top")}, view, legend)
                          for name, values in data["bars"].items()]
                         + [{"type": "line", "name": name, "data": _numbers(values), "symbol": "none", "smooth": True,
                             "lineStyle": {"width": 2}} for name, values in data["lines"].items()])
    if zoom:
        options["dataZoom"] = zoom
    return options


def _box_panels(plot: Plot, view: View) -> dict:
    """One box per panel, each with its own value axis: columns with different scales (e.g. amounts in thousands
    and discounts under 1) stay readable instead of becoming flat lines on a shared axis."""
    data = plot.data
    count = len(data["categories"])
    options = _base(view, legend=False)
    options["tooltip"].update({"trigger": "item", ":valueFormatter": view.number()})
    top = _grid(view, False)["top"]
    width = 100 / count
    options["grid"] = [{"left": f"{width * i + 1}%", "width": f"{width - 2}%", "top": top, "bottom": 12,
                        "containLabel": True} for i in range(count)]
    options["xAxis"] = [{**_category_axis([name], False), "gridIndex": i} for i, name in enumerate(data["categories"])]
    options["yAxis"] = [{**_value_axis(""), "scale": True, "gridIndex": i} for i in range(count)]
    label = view.label_from((), "right")
    options["series"] = []
    for i, (name, box) in enumerate(zip(data["categories"], data["boxes"])):
        axes = {"xAxisIndex": i, "yAxisIndex": i}
        options["series"] += [
            {"type": "boxplot", "name": name, "data": [_numbers(box)], "itemStyle": {"borderWidth": 1.5}, **axes},
            {"type": "scatter", "name": "valori anomali", "symbolSize": 6, "itemStyle": {"opacity": 0.55}, **axes,
             "data": [[0, _num(value)] for position, value in data["outliers"] if position == i]},
        ]
        if label["show"]:  # invisible point on the median, which carries the label
            options["series"].append({"type": "scatter", "name": "mediana", "symbolSize": 0, "silent": True,
                                      "tooltip": {"show": False}, "label": label, "data": [[0, _num(box[2])]], **axes})
    return options


def _boxplot(plot: Plot, view: View) -> dict:
    data = plot.data
    if not plot.y_name and 1 < len(data["categories"]) <= MAX_BOX_PANELS:  # boxes of different columns
        return _box_panels(plot, view)
    zoom = _slider(len(data["categories"]))
    options = _base(view, legend=False, zoom=True)
    options["tooltip"].update({"trigger": "item", ":valueFormatter": view.number()})
    options["grid"] = _grid(view, False, 40 if zoom else 12)
    options["xAxis"] = _category_axis(data["categories"], False)
    options["yAxis"] = {**_value_axis(plot.y_name), "scale": True}
    options["series"] = [
        {"type": "boxplot", "name": plot.y_name or "valori", "data": [_numbers(box) for box in data["boxes"]],
         "itemStyle": {"borderWidth": 1.5}},
        {"type": "scatter", "name": "valori anomali", "data": [[position, _num(value)] for position, value in data["outliers"]],
         "symbolSize": 6, "itemStyle": {"opacity": 0.55}},
    ]
    label = view.label_from((), "right")
    if label["show"]:  # invisible points on the medians, which carry the labels
        options["series"].append({"type": "scatter", "name": "mediana", "symbolSize": 0, "silent": True,
                                  "tooltip": {"show": False}, "label": label,
                                  "data": [[position, _num(box[2])] for position, box in enumerate(data["boxes"])]})
    if zoom:
        options["dataZoom"] = zoom
    return options


def _scatter(plot: Plot, view: View) -> dict:
    data = plot.data
    legend = len(data["points"]) + len(data["lines"]) > 1
    options = _base(view, legend=legend, zoom=True)
    options["tooltip"].update({"trigger": "item", ":valueFormatter": view.number()})
    options["grid"] = _grid(view, legend, 36)
    options["xAxis"] = {"type": "value", "name": plot.x_name, "nameLocation": "middle", "nameGap": 28, "scale": True,
                        "splitLine": DASHED, "axisLabel": {":formatter": js_axis()}}
    options["yAxis"] = {**_value_axis(plot.y_name), "scale": True}
    options["series"] = (
        [{"type": "scatter", "name": name, "data": [_numbers(point) for point in points], "symbolSize": 6,
          "itemStyle": {"opacity": 0.6}, "label": view.label_from((), "top")} for name, points in data["points"].items()]
        + [{"type": "line", "name": name, "data": [_numbers(point) for point in points], "symbol": "none",
            "lineStyle": {"width": 2, **({"type": "dashed"} if len(points) == 2 else {})}}
           for name, points in data["lines"].items()])
    options["dataZoom"] = [_inside_zoom("xAxisIndex"), _inside_zoom("yAxisIndex")]
    return options


def _lines(plot: Plot, view: View) -> dict:
    data = plot.data
    legend = len(data["series"]) > 1
    options = _base(view, legend=legend, zoom=True)
    options["tooltip"].update({"trigger": "axis", ":valueFormatter": view.number()})
    options["grid"] = _grid(view, legend, 40)
    options["xAxis"] = {"type": "category", "data": data["x"], "boundaryGap": False, "axisLabel": {"hideOverlap": True}}
    options["yAxis"] = {**_value_axis(plot.y_name), "scale": True}
    options["series"] = [_average({"type": "line", "name": name, "data": _numbers(values), "showSymbol": False,
                                   "connectNulls": False, "smooth": view.smooth, "label": view.label_from((), "top")},
                                  view, legend) for name, values in data["series"].items()]
    options["dataZoom"] = [_inside_zoom("xAxisIndex"), {"type": "slider", "xAxisIndex": 0, "bottom": 8, "height": 18}]
    return options


def _heatmap(plot: Plot, view: View) -> dict:
    data = plot.data
    items = [[i, j, _num(value)] for j, row in enumerate(data["values"]) for i, value in enumerate(row)]
    finite = [item[2] for item in items if item[2] is not None]
    low = data.get("min", min(finite, default=0.0))
    high = data.get("max", max(finite, default=1.0))
    options = _base(view, legend=False)
    options["tooltip"].update({"trigger": "item", ":valueFormatter": view.number()})
    options["grid"] = {**_grid(view, False), "right": 96}
    options["xAxis"] = {**_category_axis([str(label) for label in data["x"]], False), "splitArea": {"show": True},
                        "name": plot.x_name, "nameLocation": "middle", "nameGap": 28}
    options["yAxis"] = {"type": "category", "data": [str(label) for label in data["y"]], "inverse": True,
                        "splitArea": {"show": True}, "axisLabel": {"width": 140, "overflow": "truncate"},
                        "name": plot.y_name}
    options["visualMap"] = {"min": low, "max": high if high > low else low + 1, "calculable": True, "orient": "vertical",
                            "right": 4, "top": "middle", ":formatter": js_axis(),
                            "inRange": {"color": DIVERGING if data.get("diverging") else view.heat}}
    auto = ("value",) if len(items) <= HEATMAP_LABELS_UP_TO else ()
    options["series"] = [{"type": "heatmap", "data": items, "label": view.label_from(auto, "inside"),
                          "emphasis": {"itemStyle": {"borderColor": "#1d1d1f", "borderWidth": 1}}}]
    return options


BUILDERS = {"bars": _bars, "boxplot": _boxplot, "scatter": _scatter, "lines": _lines, "heatmap": _heatmap}


def build_options(plot: Plot, view: View) -> dict:
    return localized(BUILDERS[plot.kind](plot, view), view.locale)
