import pandas as pd
import pytest

from data_explorer.core import columns as c
from data_explorer.core import formula_help as fh
ALL_FUNCTIONS = [function for functions in fh.GUIDE.values() for function in functions]


@pytest.mark.parametrize("name, expected", [
    ("importo", "importo"),
    ("Descr.Azienda", '"Descr.Azienda"'),
    ("Codice fiscale", '"Codice fiscale"'),
    ("Specialità", '"Specialità"'),
    ("Ind./ Det.", '"Ind./ Det."'),
    ("order", '"order"'),
    ('dice "ciao"', '"dice ""ciao"""'),
])
def test_quote_column(name, expected):
    assert fh.quote_column(name) == expected


def test_quoted_names_work_in_formulas():
    data = pd.DataFrame({"Descr.Azienda": ["abc"], "Codice fiscale": ["X1"], "order": [2], 'dice "ciao"': [3]})
    for name in data.columns:
        assert c.evaluate_formula(data, fh.quote_column(name)).tolist() == data[name].tolist()


def test_column_kind(df):
    kinds = {str(column): fh.column_kind(dtype) for column, dtype in df.dtypes.items()}
    assert kinds == {"regione": "testo", "prodotto": "testo", "importo": "numero",
                     "qta": "numero", "data": "data", "note": "testo"}
    assert fh.column_kind(pd.Series([True]).dtype) == "booleano"


@pytest.mark.parametrize("function", ALL_FUNCTIONS, ids=lambda function: function.syntax)
def test_guide_is_complete_and_examples_run(function):
    documented = [name for name, _ in function.arguments]
    assert documented == [function.syntax[start:end] for start, end in fh.placeholders(function.syntax)]
    assert function.summary and function.details and function.examples
    for example in function.examples:
        fh.example_result(example)  # every example must work in DuckDB


def test_example_result_value_or_table():
    assert fh.example_result("LENGTH('Milano')") == "6"
    assert fh.example_result("'Rossi' || NULL") == "NULL"
    table = fh.example_result("SUM(importo) OVER (PARTITION BY regione)")
    assert table["risultato"].tolist() == [800, 800, 450, 800, 450]


@pytest.mark.parametrize("value, expected", [
    ("Roma", "'Roma'"), (None, "NULL"), (float("nan"), "NULL"), (True, "vero"),
    (3.0, "3"), (0.3333333, "0.3333"), (pd.Timestamp("2024-03-15"), "2024-03-15"),
    (pd.Timestamp("2024-03-15 18:30"), "2024-03-15 18:30"),
])
def test_format_value(value, expected):
    assert fh.format_value(value) == expected


def test_search_uses_syntax_and_summary_only():
    left = next(function for function in ALL_FUNCTIONS if function.syntax.startswith("LEFT("))
    assert not hasattr(left, "excel")
    assert fh.search_text(left) == f"{left.syntax} {left.summary}".lower()
    assert "destra" not in fh.search_text(left)  # the Excel name of RIGHT has nothing to do with it


def test_guide_syntax_is_unique():
    syntaxes = [function.syntax for function in ALL_FUNCTIONS]
    assert len(syntaxes) == len(set(syntaxes))


@pytest.mark.parametrize("syntax, placeholder", [
    ("ROUND(numero, decimali)", "numero"),
    ("DATE_TRUNC('month', data)", "data"),
    ("STRFTIME(data, '%d/%m/%Y')", "data"),
    ("CASE WHEN condizione THEN valore ELSE altro END", "condizione"),
    ("ROW_NUMBER() OVER (PARTITION BY gruppo ORDER BY colonna)", "gruppo"),
    ("CURRENT_DATE", ""),
])
def test_first_placeholder(syntax, placeholder):
    start, end = fh.first_placeholder(syntax)
    assert syntax[start:end] == placeholder


def function_name(function) -> str:
    return fh.FUNCTION_NAME.search(fh.QUOTED_TEXT.sub("", function.syntax)).group().lower()


@pytest.mark.parametrize("topic", fh.GUIDE)
def test_guide_is_alphabetical(topic):
    names = [function_name(function) for function in fh.GUIDE[topic]]
    operators = [name for name in names if not name[0].isalpha()]
    words = names[len(operators):]
    assert names[:len(operators)] == operators  # operators (||, //, %) come first
    assert words == sorted(words)


def test_guide_order_examples():
    assert [function_name(function) for function in fh.GUIDE["Logica"]] == [
        "and", "between", "case", "coalesce", "if", "ilike", "in", "is", "nullif"]
    assert fh.GUIDE["Testo"][0].syntax == "testo || altro"
