import numpy as np
import pandas as pd
import pytest

from data_explorer.core import structure as st
def test_dataset_names():
    assert st.check_dataset_name("  vendite  ", ["altro"]) == "vendite"
    for name, others in (("", []), ("Vendite", ["vendite"]), ("a b", ["a_b"]), ("df", [])):
        with pytest.raises(ValueError):
            st.check_dataset_name(name, others)


def test_column_names():
    assert st.check_column_names([" a ", "b"]) == ["a", "b"]
    for names in (["a", ""], ["a", "A"], ["x", "x "]):
        with pytest.raises(ValueError):
            st.check_column_names(names)


def test_columns_are_reordered_renamed_and_removed():
    df = pd.DataFrame({"a": [1], "b": [2], 3: [3]})
    items = st.column_items(df)
    assert [item["name"] for item in items] == ["a", "b", "3"]
    items[0]["name"], items[1]["keep"] = "alfa", False
    out = st.apply_columns(df, [items[2], items[0], items[1]])
    assert list(out.columns) == [3, "alfa"] and out.iloc[0].tolist() == [3, 1]  # 3 stays an integer: not renamed
    assert st.renamed(df, items) == {"a": "alfa"}
    assert list(df.columns) == ["a", "b", 3]  # the original does not change
    with pytest.raises(ValueError):
        st.apply_columns(df, [{**item, "keep": False} for item in items])
    with pytest.raises(ValueError):
        st.apply_columns(df, [{**items[0], "name": "x"}, {**items[1], "name": "X", "keep": True}])


def test_duplicate_original_names_are_handled_by_position():
    df = pd.DataFrame([[1, 2]], columns=["x", "x"])
    items = st.column_items(df)
    items[1]["name"] = "y"
    assert list(st.apply_columns(df, items).columns) == ["x", "y"]


def test_sort_on_several_levels_is_stable_and_keeps_blanks_last():
    df = pd.DataFrame({"zona": ["b", "A", None, "a", "b"], "n": [2.0, 1.0, 3.0, np.nan, 1.0], "id": range(5)})
    out = st.sort_rows(df, [{"column": "zona", "descending": False}, {"column": "n", "descending": True}])
    assert out["id"].tolist() == [1, 3, 0, 4, 2]  # A and a tied (stable), b by n descending, blank last
    out = st.sort_rows(df, [{"column": "n", "descending": True}])
    assert out["id"].tolist() == [2, 0, 1, 4, 3]
    case = st.sort_rows(df, [st.new_level("zona")], case_sensitive=True)
    assert case["zona"].tolist()[:3] == ["A", "a", "b"]
    assert st.sort_rows(df, [st.new_level()])["id"].tolist() == list(range(5))  # empty level: no sorting
    with pytest.raises(ValueError):
        st.sort_rows(df, [st.new_level("manca")])


def test_mixed_columns_put_numbers_before_text():
    df = pd.DataFrame({"m": ["b", 10, None, 2, "A"]})
    assert st.sort_rows(df, [st.new_level("m")])["m"].tolist()[:4] == [2, 10, "A", "b"]
    assert st.sort_rows(df, [{"column": "m", "descending": True}])["m"].tolist()[:4] == ["b", "A", 10, 2]


def test_sort_dates_and_apply_together():
    df = pd.DataFrame({"d": pd.to_datetime(["2024-03-01", None, "2024-01-01"]), "v": [1, 2, 3]})
    items = st.column_items(df)
    items[1]["name"] = "valore"
    out = st.apply(df, items, [st.new_level("d")])
    assert out["valore"].tolist() == [3, 1, 2] and list(out.columns) == ["d", "valore"]

