import json

import pytest

from data_explorer.plotting import chart_options as co
from data_explorer.plotting import charts as c
MEASURES = [{"col": "importo", "fn": "somma"}, {"col": "qta", "fn": "media"}]


def spec_for(chart: str) -> dict:
    """A valid choice of columns for each chart type (sample dataset)."""
    spec = {"chart": chart, "x": "regione", "series": "prodotto", "measures": MEASURES, "y": "importo", "size": "qta"}
    if chart == "histogram":
        spec["x"] = "importo"
    if chart in ("scatter", "bubble"):
        spec["x"] = "data"
    return spec


def formatters(options) -> list[str]:
    """All the values of the ":" keys (JavaScript functions)."""
    found = []
    if isinstance(options, dict):
        for key, value in options.items():
            found += [value] if key.startswith(":") else formatters(value)
    elif isinstance(options, list):
        for value in options:
            found += formatters(value)
    return found


@pytest.mark.parametrize("chart", list(c.CHART_TYPES))
@pytest.mark.parametrize("labels", list(co.LABEL_MODES))
def test_every_chart_builds_valid_options(df, chart, labels):
    data = c.prepare(df, spec_for(chart))
    options = co.build_options(data, {"labels": labels, "average": True, "title": "Vendite", "compact": True})
    json.dumps(options, allow_nan=False)  # valid JSON: no NaN or numpy type
    assert options["series"] and options["title"]["text"] == "Vendite"
    assert all(code.startswith("(") and "=>" in code for code in formatters(options))


def test_items_carry_the_keys_for_drill_through(df):
    data = c.prepare(df, spec_for("bar"))
    item = co.build_options(data)["series"][0]["data"][0]
    assert {"value", "share", "x", "s"} <= set(item)
    assert len(c.drill(data, item)) > 0


@pytest.mark.parametrize("labels, shown", [("Nessuna", False), ("Valore", True), ("Categoria e percentuale", True)])
def test_labels_follow_the_choice(df, labels, shown):
    data = c.prepare(df, {"chart": "bar", "x": "regione"})
    assert co.build_options(data, {"labels": labels})["series"][0]["label"]["show"] is shown


def test_label_size_follows_the_choice(df):
    data = c.prepare(df, {"chart": "bar", "x": "regione"})
    label = co.build_options(data, {"labels": "Valore"})["series"][0]["label"]
    assert label["fontSize"] == co.DEFAULT_LABEL_SIZE
    assert label["textBorderWidth"] == 0 and label["textShadowBlur"] == 0 and label["fontWeight"] == "normal"
    assert label["color"] == co.View().ink  # outside the bars: plain text in the theme color
    assert co.build_options(data, {"labels": "Valore", "label_size": 16})["series"][0]["label"]["fontSize"] == 16
    assert co.label_size(None) == co.DEFAULT_LABEL_SIZE and co.label_size(99) == co.LABEL_SIZE_RANGE[1]
    assert co.label_size(14.0) == 14 and co.label_size(1) == co.LABEL_SIZE_RANGE[0]


def test_automatic_labels_depend_on_the_chart(df):
    bar = co.build_options(c.prepare(df, {"chart": "bar", "x": "regione"}))
    pie = co.build_options(c.prepare(df, {"chart": "pie", "x": "regione"}))
    assert bar["series"][0]["label"] == {"show": False}
    assert '["name", "share"]' in pie["series"][0]["label"][":formatter"]


def test_number_format_options():
    assert "maximumFractionDigits: 2" in co.js_number()
    assert "minimumFractionDigits: 1, maximumFractionDigits: 1" in co.js_number("1")
    assert "Mln" in co.js_number(compact=True) and "Mln" not in co.js_number()


def test_percent_charts_have_a_100_axis(df):
    options = co.build_options(c.prepare(df, spec_for("stacked100")))
    assert options["yAxis"][0]["max"] == 100


def test_horizontal_bars_put_categories_on_the_vertical_axis(df):
    options = co.build_options(c.prepare(df, spec_for("hbar")))
    assert options["yAxis"]["type"] == "category" and options["yAxis"]["inverse"]


def test_zoom_bar_only_with_many_categories(df):
    few = co.build_options(c.prepare(df, {"chart": "bar", "x": "regione"}))
    many = co.build_options(c.prepare(df, {"chart": "line", "x": "data", "date_unit": "Giorno"}))
    assert "dataZoom" not in few and many["dataZoom"][1]["endValue"] == co.ZOOM_FROM - 1


def test_others_is_grey_with_a_single_series(df):
    data = c.prepare(df, {"chart": "bar", "x": "prodotto", "top_n": 2, "others": True})
    last = co.build_options(data)["series"][0]["data"][-1]
    assert last["x"] == c.OTHERS and last["itemStyle"]["color"] == co.OTHERS_COLOR


def test_average_line_not_on_stacked_charts(df):
    view = {"average": True}
    bar = co.build_options(c.prepare(df, {"chart": "bar", "x": "regione"}), view)
    stacked = co.build_options(c.prepare(df, spec_for("stacked")), view)
    assert "markLine" in bar["series"][0]
    assert all("markLine" not in series for series in stacked["series"])


def test_combo_puts_lines_on_the_right_axis(df):
    options = co.build_options(c.prepare(df, spec_for("combo")))
    assert [s["type"] for s in options["series"]] == ["bar", "line"] and options["series"][1]["yAxisIndex"] == 1


def test_scatter_points_keep_the_row_position(df):
    data = c.prepare(df, spec_for("scatter"))
    options = co.build_options(data)
    assert options["xAxis"]["type"] == "time"
    item = options["series"][0]["data"][0]
    assert c.drill(data, item).shape[0] == 1


@pytest.mark.parametrize("value, expected", [(1_108_508, 1_500_000), (42, 50), (0.7, 1), (300, 500)])  # 5 «round» ticks: 300 → step 100
def test_nice_ceiling(value, expected):
    assert co._nice_ceiling(value) == pytest.approx(expected)


def test_palette_colors_reach_borders_and_heatmap(df):
    view = {"surface": "#123456", "heat": ["#000000", "#111111", "#222222", "#333333"]}
    pie = co.build_options(c.prepare(df, spec_for("pie")), view)
    assert pie["series"][0]["itemStyle"]["borderColor"] == "#123456"
    treemap = co.build_options(c.prepare(df, spec_for("treemap")), view)
    assert treemap["series"][0]["itemStyle"]["borderColor"] == "#123456"
    heatmap = co.build_options(c.prepare(df, spec_for("heatmap")), view)
    assert heatmap["visualMap"]["inRange"]["color"] == view["heat"]


def test_default_colors_unchanged(df):
    pie = co.build_options(c.prepare(df, spec_for("pie")), {})
    assert pie["series"][0]["itemStyle"]["borderColor"] == "#ffffff"
    heatmap = co.build_options(c.prepare(df, spec_for("heatmap")), {})
    assert heatmap["visualMap"]["inRange"]["color"] == co.HEAT_COLORS


@pytest.mark.parametrize("chart", list(c.CHART_TYPES))
def test_american_style_changes_every_number_formatter(df, chart):
    data = c.prepare(df, spec_for(chart))
    italian = formatters(co.build_options(data, {"labels": "Valore e percentuale", "compact": True}))
    american = formatters(co.build_options(data, {"labels": "Valore e percentuale", "compact": True, "locale": "en-US"}))
    assert len(italian) == len(american) and any("'it-IT', {" in code for code in italian)
    assert not any("'it-IT', {" in code for code in american) and any("'en-US', {" in code for code in american)


def test_written_charts_get_plain_labels_and_italian_numbers():
    options = {"tooltip": {"trigger": "axis"}, "series": [
        {"type": "bar", "data": [1], "label": {"show": True, "position": "top"}},
        {"type": "bar", "data": [2], "label": {"show": True, "position": "inside", "fontWeight": "bold",
                                               "formatter": "{c} €"}},
        {"type": "line", "data": [3]},
    ]}
    out = co.written_chart_defaults(options, "#123456")
    top, inside, line = out["series"]
    assert top["label"]["textBorderWidth"] == 0 and top["label"]["color"] == "#123456"
    assert "it-IT" in top["label"][":formatter"]
    assert inside["label"]["fontWeight"] == "bold" and "color" not in inside["label"]  # the user's choices stay
    assert inside["label"]["formatter"] == "{c} €" and ":formatter" not in inside["label"]
    assert "label" not in line and "it-IT" in out["tooltip"][":valueFormatter"]
    assert co.written_chart_defaults({"series": {"type": "pie"}}, "#000000") == {"series": {"type": "pie"}}
    axes = co.written_chart_defaults({"xAxis": {"type": "category"}, "yAxis": [{"type": "value"},
                                      {"type": "value", "axisLabel": {"formatter": "{value} %"}}], "series": []}, "#000")
    assert "axisLabel" not in axes["xAxis"] and "it-IT" in axes["yAxis"][0]["axisLabel"][":formatter"]
    assert axes["yAxis"][1]["axisLabel"] == {"formatter": "{value} %"}


# -------------------------------------------------------------------- panels

def panel_spec(chart: str = "bar") -> dict:
    return {"chart": chart, "x": "prodotto", "series": "regione", "measures": MEASURES[:1], "panels": "data"}


def test_panels_share_scale_and_colors(df):
    data = c.prepare(df, panel_spec())
    view = {"colors": ["#111111", "#222222", "#333333"]}
    panels = co.build_panels(data, view)
    assert [label for label, _ in panels] == ["2024", "2025", "2026"]
    assert panels[0][1]["title"]["text"] == "data: 2024"
    axes = [options["yAxis"][0] for _, options in panels]
    assert all(axis["max"] == axes[0]["max"] and axis["min"] == 0 for axis in axes)
    largest = max(co.value_bounds(panel)[1] for panel in data.panels.values())
    assert axes[0]["max"] >= largest
    colors = [{series["name"]: series["itemStyle"]["color"] for series in options["series"]} for _, options in panels]
    assert all(color == colors[0] for color in colors)  # every region with the same color in every panel


def test_panels_without_same_scale_and_single_chart(df):
    panels = co.build_panels(c.prepare(df, panel_spec()), {}, same_scale=False)
    assert all("max" not in options["yAxis"][0] for _, options in panels)
    [(label, options)] = co.build_panels(c.prepare(df, {"chart": "bar", "x": "regione"}))
    assert label == "" and options == co.build_options(c.prepare(df, {"chart": "bar", "x": "regione"}))


@pytest.mark.parametrize("chart", list(c.CHART_TYPES))
def test_every_chart_type_draws_in_panels(df, chart):
    spec = {**spec_for(chart), "panels": "note"}
    for _, options in co.build_panels(c.prepare(df, spec), {"colors": ["#123456"]}):
        json.dumps({key: value for key, value in options.items()})
