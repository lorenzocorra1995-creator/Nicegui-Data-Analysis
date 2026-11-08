import numpy as np
import pandas as pd
import pytest

from data_explorer.plotting import charts as c
from data_explorer.core import engine as eng
def spec(**values):
    return {"chart": "bar", "x": "regione", "measures": [{"col": "importo", "fn": "somma"}], **values}


def categories(data):
    return data.table.iloc[:, 0].tolist()


# ------------------------------------------------------------------ categories

def test_category_keys_natural_order_with_empty_last():
    keys = c.category_keys(pd.Series([3, None, 1, 20.5]))
    assert list(keys.cat.categories) == ["1", "3", "20,5", c.EMPTY]
    assert keys.tolist() == ["3", c.EMPTY, "1", "20,5"]


@pytest.mark.parametrize("unit, expected", [
    ("Giorno", ["2024-01-31", "2024-02-01", "2024-02-07"]),
    ("Settimana", ["2024-01-29", "2024-02-05"]),  # start of the week (Monday)
    ("Mese", ["2024-01", "2024-02"]),
    ("Trimestre", ["2024-T1"]),
    ("Anno", ["2024"]),
])
def test_dates_grouped_by_unit(unit, expected):
    dates = pd.Series(pd.to_datetime(["2024-02-07", "2024-01-31", "2024-02-01"]))
    assert list(c.category_keys(dates, unit).cat.categories) == expected


# --------------------------------------------------------------- aggregation

def test_sum_by_category_sorted_by_value(df):
    data = c.prepare(df, spec())
    expected = df.groupby("regione").importo.sum().sort_values(ascending=False)
    assert categories(data) == expected.index.tolist()
    np.testing.assert_allclose(data.table["somma di importo"], expected.to_numpy())
    assert data.value_name == "somma di importo"


def test_no_measures_counts_rows(df):
    data = c.prepare(df, spec(measures=[]))
    assert data.table[c.ROW_COUNT].sum() == len(df)


def test_filters_are_applied_first(df):
    filters = {"logica": "AND", "condizioni": [{"col": "regione", "op": "=", "val": "Nord"}]}
    data = c.prepare(df, spec(x="prodotto", filters=filters))
    assert len(data.rows) == (df.regione == "Nord").sum()
    assert data.table["somma di importo"].sum() == pytest.approx(df[df.regione == "Nord"].importo.sum())


def test_input_is_not_modified(df):
    before = df.copy()
    c.prepare(df, spec(series="prodotto", top_n=2, others=True))
    pd.testing.assert_frame_equal(df, before)


# ---------------------------------------------------------------------- Top N

def test_top_n_keeps_only_those_categories_and_rows(df):
    data = c.prepare(df, spec(x="prodotto", top_n=2))
    top = df.groupby("prodotto").importo.sum().nlargest(2).index.tolist()
    assert categories(data) == top
    assert set(data.rows.prodotto) == set(top) and len(data.rows) == df.prodotto.isin(top).sum()
    assert data.notes


def test_top_n_lowest_values(df):
    data = c.prepare(df, spec(x="prodotto", top_n=1, top_from=c.BOTTOM))
    assert categories(data) == [df.groupby("prodotto").importo.sum().idxmin()]


def test_top_n_with_others_keeps_every_row(df):
    data = c.prepare(df, spec(x="prodotto", top_n=2, others=True))
    totals = df.groupby("prodotto").importo.sum()
    assert len(data.table) == 3 and categories(data)[-1] == c.OTHERS
    assert data.table["somma di importo"].iloc[-1] == pytest.approx(totals.nsmallest(2).sum())
    assert len(data.rows) == len(df)


@pytest.mark.parametrize("sort", c.SORT_OPTIONS)
def test_others_stays_last_in_every_order(df, sort):
    assert categories(c.prepare(df, spec(x="prodotto", top_n=2, others=True, sort=sort)))[-1] == c.OTHERS


# --------------------------------------------------------------- sorting

@pytest.mark.parametrize("sort, expected", [
    (c.LABEL_ASC, ["Centro", "Nord", "Sud"]),
    (c.LABEL_DESC, ["Sud", "Nord", "Centro"]),
])
def test_sort_by_label(df, sort, expected):
    assert categories(c.prepare(df, spec(sort=sort))) == expected


def test_sort_by_value_ascending(df):
    values = c.prepare(df, spec(sort=c.VALUE_ASC)).table["somma di importo"]
    assert values.is_monotonic_increasing


def test_automatic_order_keeps_dates_chronological(df):
    data = c.prepare(df, spec(chart="line", x="data", date_unit="Mese"))
    assert categories(data) == sorted(categories(data)) and categories(data)[0] == "2024-01"


def test_automatic_order_keeps_numbers_in_order(df):
    labels = categories(c.prepare(df, spec(x="qta")))
    assert labels == [str(n) for n in sorted(df.qta.unique())]


# -------------------------------------------------------------------- series

def test_series_become_columns():
    df = pd.DataFrame({"g": ["a", "a", "b"], "s": ["x", "y", "x"], "v": [1.0, 2.0, 3.0]})
    data = c.prepare(df, spec(x="g", series="s", measures=[{"col": "v", "fn": "somma"}], sort=c.LABEL_ASC))
    assert list(data.table.columns) == ["g", "x", "y"]
    assert data.table["y"].tolist() == [2.0, 0.0]  # sum without rows = 0
    assert data.series_of == {"x": "x", "y": "y"}


def test_non_additive_measure_without_rows_stays_empty():
    df = pd.DataFrame({"g": ["a", "a", "b"], "s": ["x", "y", "x"], "v": [1.0, 2.0, 3.0]})
    data = c.prepare(df, spec(x="g", series="s", measures=[{"col": "v", "fn": "media"}], sort=c.LABEL_ASC))
    assert np.isnan(data.table["y"].iloc[1])


def test_many_measures_with_series_are_named_by_both(df):
    measures = [{"col": "importo", "fn": "somma"}, {"col": "qta", "fn": "media"}]
    data = c.prepare(df, spec(series="prodotto", measures=measures))
    assert "somma di importo | A" in data.table.columns and "media di qta | D" in data.table.columns
    assert data.series_of["media di qta | D"] == "D"


def test_too_many_series_are_grouped_in_others():
    df = pd.DataFrame({"g": ["a"] * 40, "s": [f"s{i:02}" for i in range(40)], "v": range(40)})
    data = c.prepare(df, spec(x="g", series="s", measures=[{"col": "v", "fn": "somma"}]))
    assert data.table.shape[1] == 1 + c.MAX_SERIES and data.table.columns[-1] == c.OTHERS
    assert data.table.iloc[0, 1:].sum() == sum(range(40))


# --------------------------------------------------------- chart variants

def test_stacked_100_rows_sum_to_100(df):
    data = c.prepare(df, spec(chart="stacked100", series="prodotto"))
    np.testing.assert_allclose(data.table.iloc[:, 1:].sum(axis=1), 100)


def test_pareto_is_descending_and_reaches_100(df):
    data = c.prepare(df, spec(chart="pareto", x="prodotto", sort=c.LABEL_ASC))
    assert data.table["somma di importo"].is_monotonic_decreasing
    assert data.table[c.CUMULATIVE].iloc[-1] == pytest.approx(100)


def test_single_value_charts_use_only_the_first(df):
    data = c.prepare(df, spec(chart="pie", measures=[{"col": "importo", "fn": "somma"}, {"col": "qta", "fn": "media"}]))
    assert list(data.table.columns) == ["regione", "somma di importo"] and data.notes


def test_histogram_classes_cover_every_row(df):
    data = c.prepare(df, {"chart": "histogram", "x": "importo", "bins": 12})
    assert len(data.table) == 12 and data.table[c.ROW_COUNT].sum() == len(df)
    assert len(set(categories(data))) == 12


def test_histogram_keeps_empty_classes():
    df = pd.DataFrame({"v": [0.0, 0.1, 9.9, 10.0]})
    data = c.prepare(df, {"chart": "histogram", "x": "v", "bins": 5})
    assert data.table[c.ROW_COUNT].tolist() == [2, 0, 0, 0, 2]


def test_boxplot_statistics_and_outliers():
    values = list(range(1, 21)) + [100]
    df = pd.DataFrame({"g": ["a"] * 21, "v": values})
    data = c.prepare(df, {"chart": "boxplot", "x": "g", "y": "v"})
    row = data.table.iloc[0]
    series = pd.Series(values)
    assert row["mediana"] == series.median() and row["q1"] == series.quantile(0.25)
    assert row["baffo superiore"] == 20 and row["massimo"] == 100 and row["anomali"] == 1
    assert data.points["y"].tolist() == [100] and data.points["row"].tolist() == [20]


def test_boxplot_without_categories_draws_one_box(df):
    data = c.prepare(df, {"chart": "boxplot", "y": "importo"})
    assert len(data.table) == 1 and data.table.iloc[0]["n"] == len(df)


def test_scatter_drops_missing_values_and_samples(df, monkeypatch):
    df = df.assign(importo=df.importo.mask(df.index < 10))
    monkeypatch.setattr(c, "MAX_POINTS", 100)
    data = c.prepare(df, {"chart": "bubble", "x": "qta", "y": "importo", "size": "qta", "series": "regione"})
    assert len(data.rows) == len(df) - 10 and len(data.points) == 100
    assert {"x", "y", "size", "series", "row"} <= set(data.points.columns)
    assert len(data.notes) == 2  # dropped rows and sample


# ------------------------------------------------------------- drill-through

def test_drill_returns_rows_of_the_clicked_element(df):
    data = c.prepare(df, spec(series="prodotto"))
    rows = c.drill(data, {"x": "Nord", "s": "A"})
    assert len(rows) == ((df.regione == "Nord") & (df.prodotto == "A")).sum()
    assert c.selection_text(data, {"x": "Nord", "s": "A"}) == "regione = Nord · prodotto = A"


def test_drill_on_others_and_on_a_point(df):
    data = c.prepare(df, spec(x="prodotto", top_n=2, others=True))
    top = df.groupby("prodotto").importo.sum().nlargest(2).index
    assert len(c.drill(data, {"x": c.OTHERS})) == (~df.prodotto.isin(top)).sum()
    points = c.prepare(df, {"chart": "scatter", "x": "qta", "y": "importo"})
    row = c.drill(points, {"row": 7})
    assert len(row) == 1
    pd.testing.assert_series_equal(row.iloc[0], df.iloc[7])


# ------------------------------------------------------------------- errors

@pytest.mark.parametrize("bad_spec, message", [
    (spec(x=None), "asse X"),
    (spec(measures=[{"col": "regione", "fn": "somma"}]), "numeri"),
    (spec(chart="heatmap"), "Serie"),
    (spec(chart="combo"), "almeno due valori"),
    ({"chart": "histogram", "x": "regione"}, "numerica"),
    ({"chart": "scatter", "x": "qta"}, "asse Y"),
    (spec(chart="pie", x="data", date_unit="Nessuno"), "Troppe categorie"),
    (spec(filters={"logica": "AND", "condizioni": [{"col": "regione", "op": "=", "val": "Est"}]}), "filtri"),
])
def test_clear_errors(df, bad_spec, message):
    with pytest.raises(ValueError, match=message):
        c.prepare(df, bad_spec)


def test_used_fields_per_chart_type():
    fields = {key: c.used_fields(chart) for key, chart in c.CHART_TYPES.items()}
    assert {"x", "series", "measures", "top_n", "sort", "labels", "average"} <= fields["bar"]
    assert "bins" in fields["histogram"] and "measures" not in fields["histogram"]
    assert "sort" not in fields["pareto"] and "series" not in fields["pareto"]
    assert {"y", "x"} <= fields["scatter"] and "top_n" not in fields["scatter"] and "size" not in fields["scatter"]
    assert "size" in fields["bubble"] and "y" in fields["boxplot"]
    assert "series" not in fields["pie"] and "smooth" in fields["line"] and "smooth" not in fields["step"]
    assert "average" not in fields["stacked"]


def test_chart_data_keeps_positions_not_a_copy(df):
    tree = {"logica": "AND", "condizioni": [eng.new_condition("regione", "=", "Nord")]}
    data = c.prepare(df, {"chart": "bar", "x": "prodotto", "filters": tree})
    assert data.prepared_rows is None and data.source is df
    assert data.positions.tolist() == np.flatnonzero(df.regione == "Nord").tolist()
    pd.testing.assert_frame_equal(data.rows, df[df.regione == "Nord"])
    assert c.ROW_KEY not in data.table.columns and c.ROW_KEY not in df.columns


# -------------------------------------------------------------------- panels

def test_panels_keep_the_top_n_of_each_panel(df):
    data = c.prepare(df, spec(x="prodotto", top_n=2, panels="data"))  # date panels grouped by year
    years = df.data.dt.year.astype(str)
    assert list(data.panels) == ["2024", "2025", "2026"] and data.panel_name == "data"
    for year, panel in data.panels.items():
        totals = df[years == year].groupby("prodotto").importo.sum().sort_values(ascending=False)
        assert categories(panel) == totals.index[:2].tolist()  # the best two of that year
        assert panel.rows.data.dt.year.astype(str).eq(year).all()
    assert data.table.columns[0] == "data" and len(data.table) == 6
    assert sorted(data.positions) == sorted(np.concatenate([panel.positions for panel in data.panels.values()]))
    assert data.notes == ["Mostrati i 2 valori più alti di «prodotto» su 4 (per somma di importo)."]


def test_panel_drill_and_selection_text(df):
    data = c.prepare(df, spec(x="prodotto", panels="regione"))
    rows = c.drill(data, {"p": "Sud", "x": "A"})
    assert len(rows) == ((df.regione == "Sud") & (df.prodotto == "A")).sum()
    assert c.selection_text(data, {"p": "Sud", "x": "A"}) == "regione = Sud · prodotto = A"
    assert len(c.drill(data, {})) == len(df)


def test_panel_errors(df):
    with pytest.raises(ValueError, match="diversa da quella dell'asse X"):
        c.prepare(df, spec(panels="regione"))
    with pytest.raises(ValueError, match="Troppi pannelli"):
        c.prepare(df, spec(panels="data", panel_date_unit="Giorno"))


def test_panels_used_by_every_chart_type():
    assert all("panels" in c.used_fields(chart) for chart in c.CHART_TYPES.values())
    assert "same_scale" in c.used_fields(c.CHART_TYPES["bar"])
    assert "same_scale" not in c.used_fields(c.CHART_TYPES["pie"])
    assert "same_scale" not in c.used_fields(c.CHART_TYPES["stacked100"])
