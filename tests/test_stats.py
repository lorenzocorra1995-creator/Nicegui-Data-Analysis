import json

import numpy as np
import pandas as pd
import pytest
from scipy import stats as sps

from data_explorer.plotting import chart_options
from data_explorer.core import engine as eng
from data_explorer.core import stats
from data_explorer.plotting import stats_charts
@pytest.fixture
def data(df):
    out = df.copy()
    out["alto"] = out["importo"] > 1000
    return out


SPECS = {
    "describe": {"group": "regione"},
    "distribution": {"column": "importo"},
    "frequencies": {"category": "prodotto"},
    "outliers": {"columns": ["importo", "qta"]},
    "correlation": {"method": "spearman"},
    "compare_groups": {"column": "importo", "group": "regione"},
    "one_sample": {"column": "importo", "mu": 1000},
    "paired": {"column": "importo", "column2": "qta"},
    "independence": {"category": "regione", "category2": "prodotto"},
    "linear": {"y": "importo", "x": ["qta", "regione"]},
    "logistic": {"y": "alto", "x": ["qta", "prodotto"]},
    "time_series": {"date": "data", "column": "importo", "time_unit": "Settimana", "time_agg": "media"},
}


def test_catalog_covers_every_analysis_and_field():
    assert set(SPECS) == set(stats.ANALYSES)
    for analysis in stats.ANALYSES.values():
        assert set(analysis.fields) <= set(stats.FIELD_LABELS)
        assert set(analysis.labels) <= set(analysis.fields)


@pytest.mark.parametrize("key", list(SPECS))
def test_every_analysis_gives_tables_and_drawable_plots(data, key):
    result = stats.run(data, {"analysis": key, **SPECS[key]})
    assert result.tables and all(isinstance(table, pd.DataFrame) and len(table) for name, table in result.tables.items()
                                 if name != "Righe anomale")
    for plot in result.plots:
        json.dumps(stats_charts.build_options(plot, chart_options.View(title=plot.title)))


def test_plots_follow_the_chart_view_choices():
    bars = stats.Plot("bars", "Istogramma", {"categories": ["a", "b"], "bars": {"n": [1, 2]}, "lines": {"curva": [1, 2]}})
    lines = stats.Plot("lines", "Serie", {"x": ["g", "f"], "series": {"importo": [3, 4]}})
    heat = stats.Plot("heatmap", "Correlazioni", {"x": ["a"], "y": ["b"], "values": [[0.5]]})
    view = chart_options.View(title="Mio titolo", labels="Valore", decimals="1", smooth=True, average=True)

    options = stats_charts.build_options(bars, view)
    assert options["title"]["text"] == "Mio titolo"
    bar, curve = options["series"]
    assert bar["label"]["show"] and "minimumFractionDigits: 1" in bar["label"][":formatter"]
    assert bar["markLine"]["data"] == [{"type": "average", "name": "Media"}] and "markLine" not in curve
    line = stats_charts.build_options(lines, view)["series"][0]
    assert line["smooth"] and line["label"]["show"] and "markLine" in line

    plain = chart_options.View()
    assert not stats_charts.build_options(bars, plain)["series"][0]["label"]["show"]  # automatic: no labels
    assert "markLine" not in stats_charts.build_options(lines, plain)["series"][0]
    assert stats_charts.build_options(heat, plain)["series"][0]["label"]["show"]  # small map: values in the cells
    assert not stats_charts.build_options(heat, chart_options.View(labels="Nessuna"))["series"][0]["label"]["show"]

    box = stats.Plot("boxplot", "Box plot", {"categories": ["a", "b"], "boxes": [[1, 2, 3, 4, 5], [2, 3, 4, 5, 6]],
                                            "outliers": []}, y_name="v")  # groups of one column: shared axis
    assert len(stats_charts.build_options(box, plain)["series"]) == 2  # without labels: box and outliers
    medians = stats_charts.build_options(box, chart_options.View(labels="Valore", label_size=16))["series"][-1]
    assert medians["data"] == [[0, 3.0], [1, 4.0]] and medians["label"]["fontSize"] == 16

    columns = stats.Plot("boxplot", "Box plot", {"categories": ["importo", "sconto"],
                                                 "boxes": [[10, 200, 300, 400, 25000], [0, 0.1, 0.1, 0.2, 0.3]],
                                                 "outliers": [[0, 90000.0]]})  # different columns: one panel each
    panels = stats_charts.build_options(columns, plain)
    assert len(panels["grid"]) == len(panels["yAxis"]) == 2 and all(axis["scale"] for axis in panels["yAxis"])
    assert [series["yAxisIndex"] for series in panels["series"]] == [0, 0, 1, 1]
    assert panels["series"][1]["data"] == [[0, 90000.0]] and panels["series"][3]["data"] == []

    assert stats_charts.view_fields([bars, heat]) == {"labels", "average"}
    assert stats_charts.view_fields([]) == set()


def test_filters_apply_before_the_analysis(data):
    tree = eng.new_group()
    tree["condizioni"].append(eng.new_condition("regione", "=", "Nord"))
    result = stats.run(data, {"analysis": "describe", "columns": ["importo"], "filters": tree})
    assert result.tables["Colonne numeriche"]["n"].iloc[0] == (data["regione"] == "Nord").sum()


def test_numeric_summary_matches_pandas(data):
    summary = stats.numeric_summary(data["importo"])
    assert summary["media"] == pytest.approx(data["importo"].mean())
    assert summary["mediana"] == pytest.approx(data["importo"].median())
    assert summary["IC 95% inf"] < summary["media"] < summary["IC 95% sup"]


def test_two_groups_run_t_tests_like_scipy(data):
    result = stats.run(data, {"analysis": "compare_groups", "column": "importo", "group": "alto"})
    tests = result.tables["Test"].set_index("test")
    low, high = data.loc[~data["alto"], "importo"], data.loc[data["alto"], "importo"]
    expected = sps.ttest_ind(low, high, equal_var=False)
    assert tests.loc["t di Welch (varianze diverse)", "p-value"] == pytest.approx(expected.pvalue)
    assert "Tukey HSD" not in result.tables


def test_more_groups_add_tukey(data):
    result = stats.run(data, {"analysis": "compare_groups", "column": "importo", "group": "regione"})
    assert len(result.tables["Tukey HSD"]) == 3


def test_correlation_matrix_is_symmetric_with_unit_diagonal(data):
    matrix = stats.run(data, {"analysis": "correlation", "columns": ["importo", "qta"]}).tables["Matrice"]
    values = matrix.set_index("colonna").to_numpy()
    assert np.allclose(np.diag(values), 1) and np.allclose(values, values.T)


def test_correlation_rejects_text_columns(data):
    with pytest.raises(ValueError, match="non è numerica"):
        stats.run(data, {"analysis": "correlation", "columns": ["importo", "regione"]})


def test_outliers_flag_the_rows_beyond_the_limits():
    frame = pd.DataFrame({"x": [1.0, 2, 3, 2, 1, 2, 3, 100]})
    result = stats.run(frame, {"analysis": "outliers", "columns": ["x"], "outlier_method": "iqr", "threshold": 1.5})
    assert result.tables["Riepilogo"]["anomali"].iloc[0] == 1
    assert result.tables["Righe anomale"]["x"].tolist() == [100]


def test_linear_regression_recovers_the_coefficients():
    rng = np.random.default_rng(1)
    x = rng.normal(size=500)
    frame = pd.DataFrame({"x": x, "y": 3 + 2 * x + rng.normal(scale=0.1, size=500)})
    result = stats.run(frame, {"analysis": "linear", "y": "y", "x": ["x"]})
    coefficients = result.tables["Coefficienti"].set_index("variabile")["coefficiente"]
    assert coefficients[stats.INTERCEPT] == pytest.approx(3, abs=0.05)
    assert coefficients["x"] == pytest.approx(2, abs=0.05)
    assert len(result.plots) == 3  # line, observed/predicted, residuals


def test_logistic_needs_two_values(data):
    with pytest.raises(ValueError, match="esattamente due valori"):
        stats.run(data, {"analysis": "logistic", "y": "regione", "x": ["qta"]})


def test_independence_two_by_two_adds_fisher(data):
    result = stats.run(data, {"analysis": "independence", "category": "alto", "category2": "note"})
    assert "Fisher esatto" in result.tables["Test"]["test"].tolist()


def test_time_series_counts_rows_and_decomposes():
    frame = pd.DataFrame({"data": pd.date_range("2020-01-01", periods=48, freq="MS").repeat(3)})
    result = stats.run(frame, {"analysis": "time_series", "date": "data", "time_unit": "Mese", "time_agg": "conteggio"})
    assert result.tables["Serie"]["valore"].eq(3).all()
    assert "Indici stagionali" in result.tables


def test_time_series_without_values_asks_for_a_column(data):
    with pytest.raises(ValueError, match="colonna dei valori"):
        stats.run(data, {"analysis": "time_series", "date": "data", "time_agg": "somma"})


def test_missing_choice_gives_a_clear_error(data):
    with pytest.raises(ValueError, match="Scegli"):
        stats.run(data, {"analysis": "distribution"})


def test_analyses_read_only_the_columns_they_use():
    describe, outliers, compare = stats.ANALYSES["describe"], stats.ANALYSES["outliers"], stats.ANALYSES["compare_groups"]
    assert stats.used_columns(describe, {"columns": ["importo"], "group": "regione"}) == ["importo", "regione"]
    assert stats.used_columns(describe, {"columns": []}) is None  # empty = all
    assert stats.used_columns(outliers, {"columns": ["importo"]}) is None  # whole rows in the result
    assert sorted(stats.used_columns(compare, {"column": "importo", "group": "regione", "x": ["qta"]})) == ["importo", "regione"]
