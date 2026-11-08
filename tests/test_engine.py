import numpy as np
import pandas as pd
import pytest

from data_explorer.core import engine as e
def group(*conditions, logic="AND"):
    return {"logica": logic, "condizioni": list(conditions)}


def cond(col, op, val=""):
    return {"col": col, "op": op, "val": val}


# --------------------------------------------------------------------- filters

def test_nested_filters(df):
    tree = group(
        cond("regione", "=", "Nord"),
        group(cond("importo", ">", "1200"), cond("prodotto", "in lista", "A, B"), logic="OR"),
    )
    expected = df[(df.regione == "Nord") & ((df.importo > 1200) | df.prodotto.isin(["A", "B"]))]
    assert e.apply_filters(df, tree).equals(expected)


@pytest.mark.parametrize("condition, expected_mask", [
    (cond("note", "è nullo"), lambda d: d.note.isna()),
    (cond("importo", "tra", "900,1100"), lambda d: d.importo.between(900, 1100)),
    (cond("data", ">=", "01/01/2026"), lambda d: d.data >= "2026-01-01"),
    (cond("regione", "contiene", "nor"), lambda d: d.regione == "Nord"),
])
def test_filter_operators(df, condition, expected_mask):
    assert len(e.apply_filters(df, group(condition))) == expected_mask(df).sum()


def test_incomplete_conditions_are_ignored(df):
    tree = group(cond("importo", ">", ""), group(logic="OR"))
    assert len(e.apply_filters(df, tree)) == len(df)


def test_has_conditions_ignores_incomplete(df):
    assert not e.has_conditions(df, group(cond("importo", ">", ""), group(cond("", "="))))
    assert e.has_conditions(df, group(group(cond("note", "è nullo"))))


def test_unique_values_for_every_column_type(df):
    assert e.unique_values(df, "regione") == ["Centro", "Nord", "Sud"]
    assert e.unique_values(df, "note") == ["ok", "urgente"]  # nulls are excluded
    assert e.unique_values(df, "inesistente") is None
    numbers = pd.DataFrame({"n": [3.0, 1.5, None, 1.5, 1168.72], "i": [10, 2, 2, 3, 1],
                            "b": [True, False, True, True, False],
                            "d": pd.to_datetime(["2024-01-05", "2024-01-01", None, "2024-01-05 10:00", "2023-12-31"],
                                                format="mixed")})
    assert e.unique_values(numbers, "n") == ["1,5", "3", "1168,72"]  # in numeric order, decimal comma
    assert e.unique_values(numbers, "i") == ["1", "2", "3", "10"]
    assert e.unique_values(numbers, "b") == ["False", "True"]
    assert e.unique_values(numbers, "d") == ["2023-12-31", "2024-01-01", "2024-01-05", "2024-01-05 10:00:00"]


def test_proposed_values_filter_their_own_rows():
    """Each proposed value, chosen with =, in lista or non in lista, finds exactly its own rows."""
    data = pd.DataFrame({"n": [3.0, 1.5, None, 1.5, 1168.72], "ni": pd.array([1, None, 3, 3, 5], dtype="Int64"),
                         "b": [True, False, True, True, False],
                         "d": pd.to_datetime(["2024-01-05", "2024-01-01", None, "2024-01-05 10:00", "2023-12-31"],
                                             format="mixed")})
    for column in data.columns:
        values = e.unique_values(data, column)
        for label in values:
            expected = (data[column].map(e.value_label) == label) & data[column].notna()
            assert e.filter_mask(data, group(cond(column, "=", label))).tolist() == expected.tolist(), (column, label)
        chosen = values[:2]
        inside = data[column].map(e.value_label).isin(chosen) & data[column].notna()
        assert e.filter_mask(data, group(cond(column, "in lista", chosen))).tolist() == inside.tolist(), column
        assert e.filter_mask(data, group(cond(column, "non in lista", chosen))).tolist() == (~inside).tolist(), column


def test_typed_numbers_and_dates_in_lists():
    data = pd.DataFrame({"n": [1234.56, 1.5, 7.0], "d": pd.to_datetime(["2024-01-05", "2024-02-01", "2023-12-31"])})
    assert e.filter_mask(data, group(cond("n", "in lista", ["1.234,56", "7"]))).tolist() == [True, False, True]
    assert e.filter_mask(data, group(cond("n", "in lista", ["1,234.56"]))).tolist() == [True, False, False]
    assert e.filter_mask(data, group(cond("d", "in lista", ["05/01/2024", "2023-12-31"]))).tolist() == [True, False, True]
    assert e.filter_mask(data, group(cond("d", "=", "01/02/2024"))).tolist() == [False, True, False]


def test_unique_values_are_not_capped():
    many = pd.DataFrame({"c": [f"v{i}" for i in range(5000)]})
    assert len(e.unique_values(many, "c")) == 5000


def test_search_values_limits_and_filters():
    values = [f"cliente {i}" for i in range(500)] + ["Rossi", "rossetti"]
    assert len(e.search_values(values)) == e.MAX_OPTIONS
    assert e.search_values(values, "ROSS") == ["Rossi", "rossetti"]  # ignoring case
    assert e.search_values(values, "  rossi ") == ["Rossi"]
    assert e.search_values(values, "inesistente") == []
    assert e.search_values(values, "cliente", limit=3) == ["cliente 0", "cliente 1", "cliente 2"]


@pytest.mark.parametrize("op, value, expected", [
    ("in lista", "a, b", ["a", "b"]),
    ("in lista", ["a"], ["a"]),
    ("=", ["a", "b"], "a"),
    ("=", [], ""),
    ("contiene", ["a", "b"], "a, b"),
    ("=", "x", "x"),
])
def test_adapt_value(op, value, expected):
    assert e.adapt_value(op, value) == expected


def test_list_values_in_filters(df):
    inside = e.apply_filters(df, group(cond("prodotto", "in lista", ["A", "B"])))
    outside = e.apply_filters(df, group(cond("prodotto", "non in lista", ["A", "B"])))
    assert set(inside.prodotto) == {"A", "B"} and set(outside.prodotto) == {"C", "D"}
    assert len(e.apply_filters(df, group(cond("prodotto", "in lista", [])))) == len(df)  # incomplete: ignored


def test_value_with_comma_in_list_is_not_split():
    data = pd.DataFrame({"c": ["a,b", "a", "b"]})
    assert len(e.apply_filters(data, group(cond("c", "in lista", ["a,b"])))) == 1


def test_describe_filters_with_list():
    assert e.describe_filters(group(cond("prodotto", "in lista", ["A", "B"]))) == 'prodotto in lista "A, B"'


# --------------------------------------------------------------------- groupby

def test_groupby_multiple_aggregations(df):
    aggs = [{"col": "importo", "fn": "somma"}, {"col": "importo", "fn": "media"}, {"col": "qta", "fn": "max"}]
    out = e.run_groupby(df, ["regione", "prodotto"], aggs)
    assert out.shape == (12, 5)


def test_groupby_without_keys_or_aggregations(df):
    assert e.run_groupby(df, [], [{"col": "importo", "fn": "somma"}]).shape == (1, 1)
    assert e.run_groupby(df, ["regione"], []).columns.tolist() == ["regione", "n_righe"]


def test_new_aggregations(df):
    fns = ("q1", "q3", "moda", "range", "% nulli", "varianza", "nulli")
    out = e.run_groupby(df, ["regione"], [{"col": "importo", "fn": fn} for fn in fns])
    assert out.shape == (3, len(fns) + 1)


def test_vectorized_aggregations_match_per_group_functions(df):
    """The vectorized functions give the same values as the old functions called group by group,
    also with null keys, groups without values and tied modes."""
    data = df.assign(regione=df.regione.where(df.index % 7 != 0), importo=df.importo.where(df.index % 5 != 0))
    data.loc[data.prodotto == "D", "importo"] = None  # groups without values
    data["intero"] = data.qta % 3  # many tied modes
    keys = ["regione", "prodotto"]
    for col in ("importo", "intero", "note"):
        fns = [fn for fn in ("q1", "q3", "range", "moda", "nulli", "% nulli")
               if fn in e.allowed_functions(data[col])]
        out = e.run_groupby(data, keys, [{"col": col, "fn": fn, "name": fn} for fn in fns])
        expected = data.groupby(keys, dropna=False).agg(**{fn: (col, e.AGG_FUNCTIONS[fn]) for fn in fns}).reset_index()
        pd.testing.assert_frame_equal(out, expected, check_dtype=False)


def test_numeric_functions_rejected_on_text(df):
    assert "somma" not in e.allowed_functions(df.regione) and "conteggio" in e.allowed_functions(df.regione)
    assert "somma" not in e.allowed_functions(df.data) and "media" in e.allowed_functions(df.data)
    assert e.allowed_functions(df.importo) == list(e.AGG_FUNCTIONS)
    with pytest.raises(ValueError, match="testo"):
        e.run_groupby(df, ["prodotto"], [{"col": "regione", "fn": "somma"}])


def test_value_counts_percent(df):
    out = e.run_value_counts(df, ["regione"], ["prodotto"])
    assert out.groupby("regione")["percentuale"].sum().round(0).eq(100).all()
    assert e.run_value_counts(df, [], ["regione"])["conteggio"].sum() == len(df)


def test_value_counts_requires_columns(df):
    with pytest.raises(ValueError):
        e.run_value_counts(df, ["regione"], ["regione"])


def test_groupby_column_names(df):
    aggs = [{"col": "importo", "fn": "somma"}, {"col": "importo", "fn": "media", "name": "Media"},
            {"col": "qta", "fn": "max"}]
    out = e.run_groupby(df, ["regione"], aggs)
    assert out.columns.tolist() == ["regione", "somma importo", "Media", "max qta"]
    out = e.run_groupby(df, ["regione"], [{"col": "importo", "fn": fn} for fn in ("somma", "media")], naming="{colonna}")
    assert out.columns.tolist() == ["regione", "importo", "importo (2)"]
    assert e.run_groupby(df, ["regione"], [{"col": "regione", "fn": "conteggio"}], naming="{colonna}").columns.tolist() == [
        "regione", "regione (2)"]


def test_measure_name_placeholders():
    agg = {"col": "importo", "fn": "somma"}
    assert e.measure_name(agg, "{funzione}_{colonna}") == "somma_importo"
    assert e.measure_name(agg, "{colonna} {valore}", "Nord") == "importo Nord"
    assert e.measure_name(agg, "{colonna}", "Nord") == "importo | Nord"
    assert e.measure_name(agg, "{colonna} - {valore}") == "importo"
    assert e.measure_name({**agg, "name": "Fatturato"}, "{colonna}", "Nord") == "Fatturato | Nord"
    assert e.measure_name(agg, "") == "somma importo"


# ------------------------------------------------------------------------- sql

def test_sql_query(df):
    out = e.run_sql("SELECT regione, SUM(importo) tot FROM df GROUP BY 1", {"df": df})
    assert len(out) == 3


def test_sql_converts_each_dataframe_once(df):
    e.ARROW_CACHE.clear()
    e.run_sql("SELECT 1 FROM df", {"df": df, "dati": df})
    assert len(e.ARROW_CACHE._items) == 1
    e.run_sql("SELECT 1 FROM x", {"x": df.copy()})
    assert len(e.ARROW_CACHE._items) == 1  # the old dataset was freed


def test_sql_sees_inplace_changes_after_python_console(df):
    env = {"df": df, "pd": pd, "np": np}
    assert e.run_sql("SELECT SUM(qta) s FROM df", {"df": df})["s"][0] == df.qta.sum()
    e.run_python("df['qta'] = 0", env)
    assert e.run_sql("SELECT SUM(qta) s FROM df", {"df": df})["s"][0] == 0


def test_sql_mixed_type_column_falls_back(df):
    mixed = pd.DataFrame({"a": [1, "x", 2.5]})
    assert len(e.run_sql("SELECT * FROM m", {"m": mixed})) == 3


def test_sql_ddl_statement_runs(df):
    assert isinstance(e.run_sql("CREATE TABLE x AS SELECT 1", {"df": df}), pd.DataFrame)


# --------------------------------------------------------------------- console

def test_python_console(df):
    env = {"df": df, "pd": pd, "np": np}
    stdout, value, error = e.run_python("print('ciao')\nx = df.importo.sum()\ndf.groupby('regione').size()", env)
    assert error is None and "ciao" in stdout and value is not None and env["x"]


def test_python_console_error(df):
    _, _, error = e.run_python("1/0", {"df": df})
    assert "ZeroDivisionError" in error


def test_console_recognises_and_prepares_charts():
    options = {"height": 300, "xAxis": {"data": pd.Index(["a", "b"])},
               "series": [{"type": "bar", "data": np.array([1.5, np.nan])}], "when": pd.Timestamp("2024-01-02")}
    assert e.chart_kind(options) == "echarts"
    assert e.chart_kind({"a": 1}) is None and e.chart_kind(pd.DataFrame()) is None
    clean, height = e.echarts_options(options)
    assert height == 300 and "height" not in clean and "height" in options
    assert clean["series"][0]["data"] == [1.5, None] and clean["xAxis"]["data"] == ["a", "b"]
    assert clean["when"].startswith("2024-01-02")
    assert e.echarts_options({"series": []})[1] == e.DEFAULT_CONSOLE_CHART_HEIGHT


def test_console_matplotlib_figure_becomes_an_image():
    pyplot = pytest.importorskip("matplotlib.pyplot")
    figure, axes = pyplot.subplots()
    axes.plot([1, 2, 3])
    assert e.chart_kind(figure) == "matplotlib" and e.chart_kind(axes) == "matplotlib"
    assert e.figure_image(axes).startswith("data:image/svg+xml;base64,")


def test_display_frame_shows_index(df):
    described = e.to_display_frame(df.describe())
    assert described.columns[0] == "index" and "mean" in described["index"].values
    assert e.to_display_frame(df.regione.value_counts()).shape == (3, 2)


def test_display_frame_keeps_plain_range_index(df):
    assert e.to_display_frame(df).columns.tolist() == df.columns.tolist()
    assert e.to_display_frame(42) is None


def test_display_frame_flattens_multiindex_columns(df):
    out = e.to_display_frame(df.groupby("regione").importo.agg(["sum", "mean"]))
    assert out.columns.tolist() == ["regione", "sum", "mean"]
    nested = e.to_display_frame(df.groupby("regione").agg({"importo": ["sum", "mean"]}))
    assert "importo | sum" in nested.columns


# ---------------------------------------------------------------------- export

def test_exports(df):
    table = e.run_groupby(df, ["regione"], [{"col": "importo", "fn": "somma"}])
    assert e.to_xlsx_bytes(table)[:2] == b"PK"
    assert e.to_csv_bytes(table).startswith(b"\xef\xbb\xbf")


def test_excel_export_with_timezone():
    tz_df = pd.DataFrame({"t": pd.date_range("2024", periods=3, tz="Europe/Rome")})
    e.to_xlsx_bytes(tz_df)


def test_excel_export_with_missing_values(df):
    assert df.note.isna().any()
    assert e.to_xlsx_bytes(df)[:2] == b"PK"
    assert e.to_xlsx_bytes(e.to_display_frame(df.describe(include="all")))[:2] == b"PK"


# --------------------------------------------------------------------- loading

@pytest.mark.parametrize("delimiter", [",", ";", "\t", "|"])
def test_load_csv_sniffs_delimiter(df, tmp_path, delimiter):
    path = tmp_path / "data.csv"
    df.to_csv(path, sep=delimiter, index=False)
    assert e.load_file(path).shape == df.shape


def test_load_csv_latin1_fallback(tmp_path):
    path = tmp_path / "latin.csv"
    path.write_bytes("città;valore\nRoma;1\n".encode("latin-1"))
    assert e.load_file(path)["città"].tolist() == ["Roma"]


def test_load_csv_windows_encoding(tmp_path):
    path = tmp_path / "windows.csv"
    path.write_bytes("Unità;Specialità;prezzo\nRomà;Cardiologia;€ 3\n".encode("cp1252"))
    data = e.load_file(path)
    assert data.columns.tolist() == ["Unità", "Specialità", "prezzo"]
    assert data.iloc[0].tolist() == ["Romà", "Cardiologia", "€ 3"]


def test_load_csv_utf8_with_bom(tmp_path):
    path = tmp_path / "bom.csv"
    path.write_bytes("Unità;valore\nx;1\n".encode("utf-8-sig"))
    assert e.load_file(path).columns.tolist() == ["Unità", "valore"]


def test_describe_columns(df):
    info = e.describe_columns(df)
    assert info["colonna"].tolist() == df.columns.tolist()
    assert info.loc[info["colonna"] == "note", "nulli"].iloc[0] == df.note.isna().sum()


def test_excel_sheet_selection(df, tmp_path):
    path = tmp_path / "book.xlsx"
    with pd.ExcelWriter(path) as writer:
        df.head(5).to_excel(writer, sheet_name="Primo", index=False)
        df.head(8).to_excel(writer, sheet_name="Secondo", index=False)
    assert e.is_excel(path) and not e.is_excel("x.csv")
    assert e.excel_sheets(path) == ["Primo", "Secondo"]
    assert e.excel_sheets(path.read_bytes()) == ["Primo", "Secondo"]
    assert len(e.load_file(path)) == 5
    assert len(e.load_file(path, "Secondo")) == 8
    assert len(e.load_bytes("book.xlsx", path.read_bytes(), "Secondo")) == 8


def test_excel_range_reads_only_the_cells(df, tmp_path):
    path = tmp_path / "range.xlsx"
    df.head(10).to_excel(path, sheet_name="Dati", index=False, startrow=2, startcol=1)  # headers in B3
    block = e.load_file(path, cells="B3:D6")  # first row of the range: headers; then 3 rows
    assert block.columns.tolist() == df.columns[:3].tolist() and len(block) == 3
    assert e.load_file(path, cells="d6:b3").shape == block.shape  # corners in any order
    assert e.load_bytes("range.xlsx", path.read_bytes(), None, "B3:B5").columns.tolist() == [df.columns[0]]
    assert e.load_text_columns("range.xlsx", path, [df.columns[2]], cells="B3:D6").shape == (3, 1)
    assert len(e.load_file(path, cells="B3:D3")) == 0  # only the headers
    for wrong in ("B:D", "pippo", "3:6"):
        with pytest.raises(ValueError, match="Intervallo non valido"):
            e.load_file(path, cells=wrong)


# --------------------------------------------------------------------- SQL on Arrow

def test_arrow_conversion_turns_mixed_type_columns_into_text():
    mixed = pd.DataFrame({"a": [1, "x", 2.5, None], "n": [1.0, 2.0, 3.0, 4.0]})
    table = e.arrow_table(mixed)
    assert table["a"].to_pylist() == ["1", "x", "2.5", None] and table["n"].to_pylist() == [1.0, 2.0, 3.0, 4.0]


def test_run_sql_arrow_returns_an_arrow_table(df):
    result = e.run_sql_arrow("SELECT regione, count(*) AS n FROM t GROUP BY 1 ORDER BY 1", {"t": e.arrow_table(df)})
    assert result.column_names == ["regione", "n"] and sum(result["n"].to_pylist()) == len(df)


def test_run_sql_arrow_stops_when_the_result_is_too_large():
    table = e.arrow_table(pd.DataFrame({"x": range(1_000)}))
    with pytest.raises(e.ResultTooLarge) as error:
        e.run_sql_arrow("SELECT a.x, b.x AS y FROM t AS a CROSS JOIN t AS b", {"t": table}, max_cells=100_000)
    assert error.value.rows * 2 > 100_000 and isinstance(error.value, ValueError)
    assert e.run_sql_arrow("SELECT x FROM t", {"t": table}, max_cells=100_000).num_rows == 1_000


def test_arrow_to_pandas_keeps_integers_with_nulls_exact():
    import pyarrow as pa

    table = pa.table({"id": pa.array([2**60 + 1, None]), "plain": pa.array([1, 2]), "s": ["a", None]})
    out = e.arrow_to_pandas(table)
    assert out["id"].dtype == "Int64" and out["id"].iloc[0] == 2**60 + 1 and pd.isna(out["id"].iloc[1])
    assert out["plain"].dtype == "int64" and list(out.columns) == ["id", "plain", "s"]
    assert e.arrow_to_pandas(table.slice(0, 0)).shape == (0, 3) and e.arrow_to_pandas(pa.table({})).shape == (0, 0)



def test_filters_copy_only_what_is_needed(df):
    same = e.apply_filters(df, None)
    assert same is not df and np.shares_memory(same["importo"].to_numpy(), df["importo"].to_numpy())
    same["importo"] = 0  # copy-on-write: the dataset does not change
    assert (df["importo"] != 0).any()
    tree = {"logica": "AND", "condizioni": [e.new_condition("regione", "=", "Nord")]}
    narrow = e.apply_filters(df, tree, ["importo", "regione"])
    assert list(narrow.columns) == ["regione", "importo"] and len(narrow) == (df.regione == "Nord").sum()
    out = e.run_groupby(df, ["regione"], [{"col": "importo", "fn": "somma"}], tree)
    assert out["regione"].tolist() == ["Nord"]


# --- pasted lists (e.g. a column copied from Excel) ------------------------------

def test_pasted_values_from_excel_column():
    assert e.parse_pasted_values("A01\r\nB02\r\n\r\n A01 \r\n") == ["A01", "B02"]  # rows, spaces, blanks, duplicates
    assert e.parse_pasted_values("x\ty\tz") == ["x", "y", "z"]  # one row of cells
    assert e.parse_pasted_values("1,5\n2,7") == ["1,5", "2,7"]  # with rows the comma stays decimal
    assert e.parse_pasted_values("a; b;c") == ["a", "b", "c"]
    assert e.parse_pasted_values("a, b ,c") == ["a", "b", "c"]  # no other separator: commas
    assert e.parse_pasted_values("  \n ") == []


def test_merge_values_keeps_order_without_duplicates():
    assert e.merge_values("a, b", ["b", "c"]) == ["a", "b", "c"]
    assert e.merge_values(["x"], ["y", "x"]) == ["x", "y"]
    assert e.merge_values("", ["y"]) == ["y"]


def test_pasted_list_filters_text_and_numbers(df):
    codes = e.parse_pasted_values("Nord\r\nSud\r\n")
    tree = group(cond("regione", "in lista", codes))
    assert e.apply_filters(df, tree)["regione"].isin(["Nord", "Sud"]).all()
    numbers = df["qta"].drop_duplicates().head(3).astype(str).str.replace(".", ",", regex=False)
    tree = group(cond("qta", "non in lista", e.parse_pasted_values("\n".join(numbers))))
    assert not e.apply_filters(df, tree)["qta"].isin(df["qta"].drop_duplicates().head(3)).any()


def test_long_lists_are_described_briefly():
    values = [f"v{number}" for number in range(25)]
    text = e.describe_filters(group(cond("codice", "in lista", values)))
    assert text.startswith('codice in lista "v0, v1') and "v19… (altri 5, 25 valori in tutto)" in text
