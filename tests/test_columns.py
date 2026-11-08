import numpy as np
import pandas as pd
import pytest

from data_explorer.core import columns as c
def group(*conditions, logic="AND"):
    return {"logica": logic, "condizioni": list(conditions)}


def cond(col, op, val=""):
    return {"col": col, "op": op, "val": val}


# -------------------------------------------------------------- format change

@pytest.mark.parametrize("decimal, values, expected", [
    (",", ["1.234,56", " 12 ", "€ 3,5", "n.d.", None], [1234.56, 12, 3.5, np.nan, np.nan]),
    (".", ["1,234.56", "3.5"], [1234.56, 3.5]),
])
def test_text_to_number(decimal, values, expected):
    out = c.convert(pd.Series(values, dtype=object), "Numero", decimal)
    assert out.dtype == "float64"
    np.testing.assert_array_equal(out.to_numpy(), expected)


def test_number_keeps_real_numbers_in_mixed_columns():
    mixed = pd.Series([1234.5, "1.000,5", "x"], dtype=object)  # typical of a mixed Excel column
    assert c.convert(mixed, "Numero").tolist()[:2] == [1234.5, 1000.5]


def test_integer_and_text():
    assert c.convert(pd.Series(["3,7", "x"]), "Numero intero").tolist() == [4, pd.NA]
    text = c.convert(pd.Series([1.5, np.nan]), "Testo")
    assert text.iloc[0] == "1.5" and pd.isna(text.iloc[1])


def test_dates_iso_and_italian():
    dates = c.convert(pd.Series(["2024-01-02", "31/12/2024", "x", None]), "Data")
    assert dates.iloc[0] == pd.Timestamp("2024-01-02") and dates.iloc[1] == pd.Timestamp("2024-12-31")
    assert dates.iloc[2:].isna().all()


@pytest.mark.parametrize("to, expected", [
    ("Anno", 2024), ("Trimestre", 4), ("Mese", 11), ("Giorno", 30),
    ("Anno-mese", "2024-11"), ("Data senza ora", pd.Timestamp("2024-11-30")),
])
def test_date_parts(to, expected):
    assert c.convert(pd.Series(pd.to_datetime(["2024-11-30 18:45"])), to).iloc[0] == expected


def test_lost_values_ignore_blanks():
    source = pd.Series(["1", "n.d.", "", None])
    assert c.lost_values(source, c.convert(source, "Numero")).tolist() == ["n.d."]


def test_default_name():
    assert c.default_name("data", "Anno-mese") == "data_anno_mese"


# --------------------------------------------------------------------- formula

def test_formula_row_by_row(df):
    assert c.evaluate_formula(df, "importo * qta").equals(df.importo * df.qta)
    assert c.evaluate_formula(df, "IF(importo > 1000, 'alto', 'basso');").isin(["alto", "basso"]).all()
    assert c.evaluate_formula(df, "importo / SUM(importo) OVER ()").sum() == pytest.approx(1)


def test_formula_keeps_index_of_filtered_data(df):
    filtered = df[df.regione == "Nord"]
    assert c.evaluate_formula(filtered, "qta + 1").equals(filtered.qta + 1)


@pytest.mark.parametrize("formula", ["", "SUM(importo)", "importo, qta"])
def test_formula_must_give_one_value_per_row(df, formula):
    with pytest.raises(ValueError):
        c.evaluate_formula(df, formula)


# ------------------------------------------------------------------ if… then

def test_case_first_true_rule_wins(df):
    rules = [{"filters": group(cond("importo", ">", "1200")), "value": "alto"},
             {"filters": group(cond("importo", ">", "800")), "value": "medio"}]
    out = c.case_when(df, rules, "basso")
    expected = np.select([df.importo > 1200, df.importo > 800], ["alto", "medio"], "basso")
    assert out.tolist() == expected.tolist()


def test_case_values_and_otherwise(df):
    rules = [{"filters": group(cond("regione", "=", "Nord")), "value": "1"}]
    numbers = c.case_when(df, rules, "0")
    assert pd.api.types.is_numeric_dtype(numbers) and numbers.sum() == (df.regione == "Nord").sum()
    assert c.case_when(df, rules).isna().sum() == (df.regione != "Nord").sum()  # new column: null


def test_case_keeps_current_values_when_overwriting(df):
    rules = [{"filters": group(cond("regione", "=", "Nord")), "value": "N"}]
    out = c.case_when(df, rules, current=df.regione)
    assert set(out) == {"N", "Sud", "Centro"}


def test_case_on_text_column_stays_text(df):
    rules = [{"filters": group(cond("qta", ">", "10")), "value": "molti"}]
    out = c.case_when(df, rules, current=df.qta)
    assert out.map(type).eq(str).all()  # kept numbers converted to text: no mixed types


def test_case_requires_complete_rules(df):
    with pytest.raises(ValueError):
        c.case_when(df, [])
    with pytest.raises(ValueError, match="regola 1"):
        c.case_when(df, [{"filters": group(cond("importo", ">", "")), "value": "x"}])


# ------------------------------------------------------------- applying

def test_new_column_after_source_and_original_unchanged(df):
    spec = {"mode": "convert", "column": "data", "to": "Anno", "target": "anno"}
    out = c.calculate(df, spec)
    assert list(out.columns).index("anno") == list(df.columns).index("data") + 1
    assert "anno" not in df.columns


def test_overwrite_keeps_position(df):
    spec = {"mode": "formula", "formula": "importo * 2", "target": "importo", "overwrite": True}
    out = c.calculate(df, spec)
    assert list(out.columns) == list(df.columns) and out.importo.equals(df.importo * 2)


@pytest.mark.parametrize("target, overwrite", [("", False), ("importo", False), ("nuova", True)])
def test_target_validation(df, target, overwrite):
    with pytest.raises(ValueError):
        c.calculate(df, {"mode": "formula", "formula": "1", "target": target, "overwrite": overwrite})


def test_formula_can_use_other_tables(df):
    spec = {"mode": "formula", "formula": "qta - (SELECT MIN(qta) FROM altro)", "target": "delta"}
    out = c.calculate(df, spec, {"altro": df})
    assert out.delta.min() == 0


@pytest.mark.parametrize("formula, expected", [
    ("SUM(importo) OVER (PARTITION BY regione)", lambda d: d.groupby("regione").importo.transform("sum")),
    ("importo - LAG(importo) OVER (ORDER BY data)", lambda d: d.importo.diff()),
    ("ROW_NUMBER() OVER (ORDER BY data DESC)", lambda d: pd.Series(range(len(d), 0, -1), index=d.index)),
])
def test_window_formulas_keep_row_order(df, formula, expected):
    pd.testing.assert_series_equal(c.evaluate_formula(df, formula), expected(df),
                                   check_names=False, check_dtype=False)
