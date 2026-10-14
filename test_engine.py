import numpy as np
import pandas as pd
import pytest

import engine as e


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


def test_value_counts_percent(df):
    out = e.run_value_counts(df, ["regione"], ["prodotto"])
    assert out.groupby("regione")["percentuale"].sum().round(0).eq(100).all()
    assert e.run_value_counts(df, [], ["regione"])["conteggio"].sum() == len(df)


def test_value_counts_requires_columns(df):
    with pytest.raises(ValueError):
        e.run_value_counts(df, ["regione"], ["regione"])


# ----------------------------------------------------------------------- pivot

def test_pivot_with_totals(df):
    values = [{"col": "importo", "fn": "somma"}, {"col": "qta", "fn": "media"}]
    out = e.run_pivot(df, ["regione"], ["prodotto"], values, totali=True)
    assert "Totale" in out["regione"].values


def test_pivot_requires_values_and_axes(df):
    with pytest.raises(ValueError):
        e.run_pivot(df, ["regione"], [], [])
    with pytest.raises(ValueError):
        e.run_pivot(df, [], [], [{"col": "importo", "fn": "somma"}])


# ------------------------------------------------------------------------- sql

def test_sql_query(df):
    out = e.run_sql("SELECT regione, SUM(importo) tot FROM df GROUP BY 1", {"df": df})
    assert len(out) == 3


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
