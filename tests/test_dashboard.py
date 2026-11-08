import pandas as pd
import pytest

from data_explorer.ui import dashboard as dash_module
from data_explorer.ui.dashboard import Dashboard


def outputs(dash, page=0):
    return list(dash.compute(page).values())


def test_kpis_follow_the_choice_slicers(df):
    dash = Dashboard(df, "Vendite").slicer("regione").kpi("Righe").kpi("Totale", "importo", "somma", decimals=2)
    rows, total = outputs(dash)
    assert rows.value == len(df) and total.value == pytest.approx(df.importo.sum())
    dash.slicers[0].value = ["Nord", "Sud"]
    rows, total = outputs(dash)
    chosen = df[df.regione.isin(["Nord", "Sud"])]
    assert rows.value == len(chosen) and total.value == pytest.approx(chosen.importo.sum())


def test_single_and_initial_values():
    df = pd.DataFrame({"prodotto": ["A", "B", "A"], "qta": [1, 2, 3]})
    dash = Dashboard(df).slicer("prodotto", kind="single", value="A").kpi("Pezzi", "qta")
    assert outputs(dash)[0].value == 4
    assert Dashboard(df).slicer("prodotto", value=["B"]).slicers[0].value == ["B"]


def test_range_slicers_on_numbers_and_dates(df):
    dash = Dashboard(df).slicer("importo").slicer("data").kpi("Righe")
    numbers, dates = dash.slicers
    assert numbers.kind == dates.kind == dash_module.RANGE and dates.dates
    assert dates.bounds == (f"{df.data.min():%Y-%m-%d}", f"{df.data.max():%Y-%m-%d}")
    numbers.value = (900, None)
    dates.value = ("2024-02-01", "2024-03-31")
    expected = df[(df.importo >= 900) & df.data.between("2024-02-01", "2024-03-31")]
    assert outputs(dash)[0].value == len(expected)


def test_click_on_a_chart_filters_the_other_visuals(df):
    dash = Dashboard(df).chart("bar", x="regione", value="importo").kpi("Righe")
    visual = dash.pages[0].visuals[0]
    chart, _ = outputs(dash)
    assert dash.select(visual, chart, {"x": "Nord", "value": 1.0, "name": "Nord"})
    chart, rows = outputs(dash)
    assert rows.value == (df.regione == "Nord").sum()
    assert len(chart.value.table) == df.regione.nunique()  # the clicked chart keeps all its elements
    assert "Nord" in dash.cross.text
    assert dash.select(visual, chart, {"x": "Nord"}) and dash.cross is None  # second click: removed
    assert not dash.select(visual, chart, {"value": 3})  # e.g. average line: no source rows


def test_cross_filter_with_a_slicer_and_dates(df):
    dash = Dashboard(df).slicer("prodotto", value=["A"]).chart("line", x="data", date_unit="Mese").kpi("Righe")
    visual = dash.pages[0].visuals[0]
    chart, _ = outputs(dash)
    month = chart.value.table.iloc[0, 0]
    dash.select(visual, chart, {"x": month})
    expected = df[(df.prodotto == "A") & (df.data.dt.strftime("%Y-%m") == month)]
    assert outputs(dash)[1].value == len(expected)


def test_tables_pivots_and_python_visuals(df):
    dash = (Dashboard(df)
            .table(groups="regione", value="importo", sort_by="somma importo", limit=2)
            .table(columns=["regione", "qta"])
            .pivot(rows="regione", columns="prodotto", value="importo")
            .python(lambda d: d.groupby("prodotto").qta.sum())
            .python(lambda d: {"series": [{"type": "bar", "data": [len(d)]}]})
            .python(lambda d: len(d))
            .text("## Note"))
    grouped, rows, pivot_table, series, chart, number, text = outputs(dash)
    assert len(grouped.value) == 2 and grouped.value.iloc[0, 1] >= grouped.value.iloc[1, 1]
    assert list(rows.value.columns) == ["regione", "qta"]
    assert len(pivot_table.value.frame) == df.regione.nunique() + 1  # with the total row
    assert series.value[0] == "table" and chart.value[0] == "echarts"
    assert number.value == ("text", str(len(df))) and text.value == "## Note"


def test_an_error_stays_in_its_visual(df):
    dash = Dashboard(df).python(lambda d: 1 / 0).kpi("Righe")
    broken, rows = outputs(dash)
    assert "division" in broken.error and rows.value == len(df)
    dash.slicers.append(dash_module.Slicer("regione", dash_module.MULTI, "regione", value=["nessuna"]))
    assert outputs(dash)[1].error.startswith("Nessuna riga")


def test_pages_and_widths(df):
    dash = Dashboard(df).kpi("Righe").page("Dettaglio").chart("Torta", x="prodotto", width=12)
    assert [page.title for page in dash.pages] == [dash_module.DEFAULT_PAGE, "Dettaglio"]
    assert dash.pages[0].visuals[0].width == dash_module.DEFAULT_WIDTHS["kpi"]
    second = dash.compute(1)
    assert list(second) == [1] and second[1].value.chart.label == "Torta"


@pytest.mark.parametrize("build, message", [
    (lambda d: d.slicer("regioni"), "Colonne disponibili"),
    (lambda d: d.chart("ciambellone", x="regione"), "Tipi disponibili"),
    (lambda d: d.chart("bar", x="regione", colore="rosso"), "opzioni sconosciute"),
    (lambda d: d.kpi("Totale", "importo", "totale"), "Funzione sconosciuta"),
    (lambda d: d.kpi("Totale", "regione", "somma"), "regione"),
    (lambda d: d.kpi("Righe", width=13), "width"),
    (lambda d: d.slicer("regione", kind="range"), "numeri e date"),
])
def test_written_mistakes_give_clear_messages(df, build, message):
    with pytest.raises(ValueError, match=message):
        build(Dashboard(df))


def test_measures_in_every_written_form(df):
    forms = ["importo", ("importo", "media"), {"col": "qta", "fn": "max"}]
    assert dash_module.measures_of(df, measures=forms) == [
        {"col": "importo", "fn": "somma"}, {"col": "importo", "fn": "media"}, {"col": "qta", "fn": "max", "name": ""}]
    assert dash_module.measures_of(df) == []


def test_example_runs_on_the_dataset(df):
    env = {"Dashboard": Dashboard, "df": df}
    exec(dash_module.example_code(df), env)
    dash = env["dash"]
    assert all(not output.error for page in range(len(dash.pages)) for output in dash.compute(page).values())


def test_chart_panels_use_the_written_look(df):
    dash = Dashboard(df).chart("bar", x="regione", title="Titolo", labels="Valore")
    visual = dash.pages[0].visuals[0]
    (label, options), = dash_module.chart_panels(visual, outputs(dash)[0].value, {})
    assert label == "" and options["series"][0]["label"]["show"]
    assert not options.get("title", {}).get("text")
