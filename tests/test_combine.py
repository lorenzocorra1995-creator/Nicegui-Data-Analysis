import pandas as pd
import pytest

from data_explorer.core import combine as c
from data_explorer.core import engine as eng
def values(df: pd.DataFrame, *columns: str) -> list[tuple]:
    """Rows as tuples, with nulls (NaN, NA, None) turned into None."""
    return [tuple(None if pd.isna(value) else value for value in row)
            for row in df[list(columns)].itertuples(index=False)]


def keys(left="customer", op="=", right="customer") -> dict:
    return {"keys": [c.new_key(left, op, right)]}


@pytest.fixture
def sales():
    return pd.DataFrame({"id": [1, 2, 3, 4], "customer": [10, 20, 10, 99], "amount": [5.0, 6.0, 7.0, 8.0]})


@pytest.fixture
def customers():
    return pd.DataFrame({"customer": [10, 20, 30], "name": ["Ann", "Bob", "Cy"]})


# ------------------------------------------------------------------ join: types

@pytest.mark.parametrize("how, expected", [
    ("inner", [(1, "Ann"), (2, "Bob"), (3, "Ann")]),
    ("left", [(1, "Ann"), (2, "Bob"), (3, "Ann"), (4, None)]),
    ("right", [(1, "Ann"), (2, "Bob"), (3, "Ann"), (None, "Cy")]),
    ("full", [(1, "Ann"), (2, "Bob"), (3, "Ann"), (4, None), (None, "Cy")]),
])
def test_two_sided_joins_keep_left_order_then_unmatched_right(sales, customers, how, expected):
    out = c.join(sales, customers, {"how": how, **keys()})
    assert values(out, "id", "name") == expected


def test_cross_join_ignores_keys(sales, customers):
    out = c.join(sales, customers, {"how": "cross"})
    assert len(out) == 12
    assert values(out.head(3), "id", "name") == [(1, "Ann"), (1, "Bob"), (1, "Cy")]


@pytest.mark.parametrize("how, expected_columns, expected_rows", [
    ("left_semi", ["id", "customer", "amount"], [(1,), (2,), (3,)]),
    ("left_anti", ["id", "customer", "amount"], [(4,)]),
    ("right_semi", ["customer", "name"], [("Ann",), ("Bob",)]),
    ("right_anti", ["customer", "name"], [("Cy",)]),
])
def test_semi_and_anti_joins_return_one_side_only(sales, customers, how, expected_columns, expected_rows):
    out = c.join(sales, customers, {"how": how, **keys()})
    assert list(out.columns) == expected_columns
    assert values(out, "id" if "id" in out else "name") == expected_rows


def test_join_types_cover_every_kind():
    assert set(c.JOIN_TYPES) == {"inner", "left", "right", "full", "cross",
                                 "left_semi", "left_anti", "right_semi", "right_anti"}


# ------------------------------------------------------------------ join: keys

def test_several_keys_must_all_match():
    left = pd.DataFrame({"a": [1, 1, 2], "b": ["x", "y", "x"], "v": [1, 2, 3]})
    right = pd.DataFrame({"a": [1, 2], "b": ["y", "x"], "w": [10, 20]})
    spec = {"how": "inner", "keys": [c.new_key("a", "=", "a"), c.new_key("b", "=", "b")]}
    assert values(c.join(left, right, spec), "v", "w") == [(2, 10), (3, 20)]


def test_keys_can_have_different_names(sales):
    cities = pd.DataFrame({"code": [10, 20], "city": ["Roma", "Pisa"]})
    out = c.join(sales, cities, {"how": "inner", **keys("customer", "=", "code")})
    assert values(out, "id", "city") == [(1, "Roma"), (2, "Pisa"), (3, "Roma")]


def test_null_keys_do_not_match_unless_requested():
    left = pd.DataFrame({"k": [1.0, None], "v": ["a", "b"]})
    right = pd.DataFrame({"k": [None, 1.0], "w": ["x", "y"]})
    spec = {"how": "inner", **keys("k", "=", "k")}
    assert values(c.join(left, right, spec), "v", "w") == [("a", "y")]
    assert values(c.join(left, right, {**spec, "null_keys": True}), "v", "w") == [("a", "y"), ("b", "x")]


@pytest.mark.parametrize("op, expected", [
    ("<", [(10, 20), (10, 30), (20, 30)]),
    (">=", [(10, 10), (20, 10), (20, 20), (30, 10), (30, 20), (30, 30)]),
    ("≠", [(10, 20), (10, 30), (20, 10), (20, 30), (30, 10), (30, 20)]),
])
def test_keys_can_compare_with_other_operators(op, expected):
    left = pd.DataFrame({"a": [10, 20, 30]})
    right = pd.DataFrame({"b": [10, 20, 30]})
    out = c.join(left, right, {"how": "inner", **keys("a", op, "b")})
    assert values(out, "a", "b") == expected


def test_text_match_compares_numbers_and_strings():
    left = pd.DataFrame({"k": [7, 8], "v": ["a", "b"]})
    right = pd.DataFrame({"k": ["7", "9"], "w": ["x", "y"]})
    spec = {"how": "inner", **keys("k", "=", "k")}
    with pytest.raises(ValueError, match="tipi diversi"):
        c.join(left, right, spec)
    assert values(c.join(left, right, {**spec, "match": "text"}), "v", "w") == [("a", "x")]


def test_loose_text_match_ignores_case_and_edge_spaces():
    left = pd.DataFrame({"k": ["Rossi ", "bianchi"], "v": [1, 2]})
    right = pd.DataFrame({"k": ["rossi", "VERDI"], "w": [10, 20]})
    spec = {"how": "inner", **keys("k", "=", "k")}
    assert c.join(left, right, spec).empty
    assert values(c.join(left, right, {**spec, "match": "text_loose"}), "v", "w") == [(1, 10)]


def test_columns_with_spaces_and_reserved_words_work():
    left = pd.DataFrame({"order": [1, 2], "my col": ["a", "b"]})
    right = pd.DataFrame({"order": [2], "select": ["x"]})
    out = c.join(left, right, {"how": "inner", **keys("order", "=", "order")})
    assert values(out, "my col", "select") == [("b", "x")]


def test_suggest_keys_prefers_identifiers_over_columns_with_few_values():
    orders = pd.DataFrame({"region": ["N", "S", "N", "S"] * 5, "client": list(range(20)), "amount": range(20)})
    clients = pd.DataFrame({"client": list(range(20)), "region": ["N", "S"] * 10, "name": list("abcdefghijklmnopqrst")})
    assert c.suggest_keys(orders, clients) == [c.new_key("client", "=", "client")]  # not «region», which comes first


def test_suggest_keys_proposes_nothing_when_no_column_is_unique():
    left = pd.DataFrame({"region": ["N", "S"] * 10, "kind": list("ab") * 10})
    right = pd.DataFrame({"region": ["N", "S"] * 5, "kind": list("ab") * 5})
    assert c.suggest_keys(left, right) == []


def test_suggest_keys_uses_first_common_column_of_same_type(sales, customers):
    assert c.suggest_keys(sales, customers) == [c.new_key("customer", "=", "customer")]
    other = pd.DataFrame({"customer": ["a"], "x": [1]})
    assert c.suggest_keys(sales, other) == []


# ------------------------------------------------------------------ join: multiplied rows, order, types

def test_join_stops_when_the_keys_multiply_the_rows(monkeypatch):
    monkeypatch.setattr(c, "MIN_RESULT_CELLS", 1_000)
    left = pd.DataFrame({"region": ["N", "S", "C"] * 300, "v": range(900)})
    right = pd.DataFrame({"region": ["N", "S", "C"] * 300, "w": range(900)})  # 270.000 result rows
    with pytest.raises(ValueError, match="supera il limite") as error:
        c.join(left, right, {"how": "inner", **keys("region", "=", "region")})
    assert "controlla le chiavi" in str(error.value)
    with pytest.raises(ValueError, match="supera il limite"):
        c.join(left, right, {"how": "cross"})


def test_limit_grows_with_the_size_of_the_datasets(monkeypatch):
    monkeypatch.setattr(c, "MIN_RESULT_CELLS", 1_000)
    left = pd.DataFrame({"k": range(5_000), "v": range(5_000)})
    right = pd.DataFrame({"k": range(5_000), "w": range(5_000)})
    assert len(c.join(left, right, {"how": "inner", **keys("k", "=", "k")})) == 5_000  # 1:1, above the minimum but within the limits


def test_one_to_many_rows_follow_left_order_then_right_order():
    left = pd.DataFrame({"k": [2, 1, 3], "v": ["a", "b", "c"]})
    right = pd.DataFrame({"k": [1, 2, 1, 2, 1], "w": [10, 20, 11, 21, 12]})
    spec = {"how": "full", **keys("k", "=", "k")}
    assert values(c.join(left, right, spec), "v", "w") == [("a", 20), ("a", 21), ("b", 10), ("b", 11), ("b", 12),
                                                          ("c", None)]
    assert values(c.join(left, right, {**spec, "how": "right"}), "v", "w") == [
        ("a", 20), ("a", 21), ("b", 10), ("b", 11), ("b", 12)]


def test_outer_join_keeps_exact_large_integers():
    ids = pd.DataFrame({"id": [2**60 + 1, 5]})
    other = pd.DataFrame({"id": [5, 9], "w": [1, 2]})
    out = c.join(ids, other, {"how": "full", **keys("id", "=", "id")})
    assert out["id"].dtype == "Int64" and out["id"].tolist()[0] == 2**60 + 1


def test_columns_without_nulls_keep_their_plain_types(sales, customers):
    out = c.join(sales, customers, {"how": "inner", **keys()})
    assert out["id"].dtype == "int64" and out["amount"].dtype == "float64"


def test_mixed_type_columns_are_treated_as_text():
    left = pd.DataFrame({"k": [1, 2], "code": [1, "x"]})  # mixed column, typical of Excel
    right = pd.DataFrame({"k": [2, 1], "w": ["b", "a"]})
    out = c.join(left, right, {"how": "inner", **keys("k", "=", "k")})
    assert values(out, "code", "w") == [("1", "a"), ("x", "b")]


def test_join_does_not_change_its_inputs(sales, customers):
    before = sales.copy(), customers.copy()
    c.join(sales, customers, {"how": "full", "merge_keys": True, "indicator": True, **keys(),
                              "left_filters": {"logica": "AND", "condizioni": [eng.new_condition("id", ">", "1")]}})
    pd.testing.assert_frame_equal(sales, before[0])
    pd.testing.assert_frame_equal(customers, before[1])


# ------------------------------------------------------------------ join: filters

def test_prefilters_apply_before_the_join(sales, customers):
    spec = {"how": "left", **keys(),
            "left_filters": {"logica": "AND", "condizioni": [eng.new_condition("amount", ">", "5.5")]},
            "right_filters": {"logica": "AND", "condizioni": [eng.new_condition("name", "=", "Ann")]}}
    out = c.join(sales, customers, spec)
    assert values(out, "id", "name") == [(2, None), (3, "Ann"), (4, None)]


def test_empty_filters_keep_the_same_dataset(sales):
    assert c.filtered(sales, None) is sales
    assert c.filtered(sales, eng.new_group()) is sales
    assert c.filtered(sales, {"logica": "AND", "condizioni": [eng.new_condition("", "=", "")]}) is sales


# ------------------------------------------------------------------ join: column plan

def by_name(plan: list[dict]) -> dict[str, dict]:
    return {f"{entry['side']}.{entry['column']}": entry for entry in plan}


def test_plan_tags_duplicates_of_the_right_side(sales, customers):
    plan = by_name(c.join_plan(sales, customers, {"how": "left", **keys()}, ("sales", "customers")))
    assert plan["left.customer"]["name"] == "customer" and plan["left.customer"]["duplicate"]
    assert plan["right.customer"]["name"] == "customer_customers" and plan["right.customer"]["duplicate"]
    assert plan["right.name"]["name"] == "name" and not plan["right.name"]["duplicate"]
    assert plan["right.name"]["source"] == "customers"


@pytest.mark.parametrize("naming, expected", [
    ("duplicates", ["id", "customer", "customer_b", "name"]),
    ("right", ["id", "customer", "customer_b", "name_b"]),
    ("all", ["id_a", "customer_a", "customer_b", "name_b"]),
])
def test_plan_naming_policies(sales, customers, naming, expected):
    plan = c.join_plan(sales[["id", "customer"]], customers, {"how": "inner", "naming": naming, **keys()}, ("a", "b"))
    assert [entry["name"] for entry in plan] == expected


def test_plan_names_stay_unique_when_the_suffixed_name_already_exists():
    left = pd.DataFrame({"x": [1], "x_b": [2]})
    right = pd.DataFrame({"x": [3], "x_b": [4]})
    plan = c.join_plan(left, right, {"how": "inner", "keys": [], "naming": "all"}, ("a", "b"))
    names = [entry["name"] for entry in plan]
    assert len(set(names)) == 4


def test_self_join_labels_are_distinct(sales):
    plan = c.join_plan(sales, sales, {"how": "inner", "naming": "all", **keys("id", "=", "id")}, ("sales", "sales"))
    assert {entry["source"] for entry in plan} == {"sales", "sales_2"}
    assert len({entry["name"] for entry in plan}) == len(plan)


def test_self_join_runs_with_distinct_column_names():
    staff = pd.DataFrame({"id": [1, 2, 3], "boss": [None, 1, 1], "name": ["a", "b", "c"]})
    spec = {"how": "left", "naming": "all", **keys("boss", "=", "id")}
    out = c.join(staff, staff, spec, ("staff", "staff"))
    assert list(out.columns) == ["id_staff", "boss_staff", "name_staff", "id_staff_2", "boss_staff_2", "name_staff_2"]
    assert values(out, "name_staff", "name_staff_2") == [("a", None), ("b", "a"), ("c", "a")]


def test_keys_can_be_categories_and_dates():
    left = pd.DataFrame({"k": pd.Categorical(["a", "b"]), "d": pd.to_datetime(["2024-01-01", "2024-01-02"]), "v": [1, 2]})
    right = pd.DataFrame({"k": ["a", "b"], "d": pd.to_datetime(["2024-01-01", "2024-02-02"]), "w": [10, 20]})
    spec = {"how": "inner", "keys": [c.new_key("k", "=", "k"), c.new_key("d", "=", "d")]}
    assert values(c.join(left, right, spec), "v", "w") == [(1, 10)]


def test_semi_join_plan_has_only_the_kept_side(sales, customers):
    plan = c.join_plan(sales, customers, {"how": "left_semi", **keys()})
    assert {entry["side"] for entry in plan} == {"left"}
    assert not any(entry["duplicate"] for entry in plan)


def test_overrides_rename_and_exclude_columns(sales, customers):
    overrides = {("right", "name"): {"name": "client"}, ("left", "amount"): {"keep": False}}
    plan = c.join_plan(sales, customers, {"how": "left", **keys()}, overrides=overrides)
    out = c.join(sales, customers, {"how": "left", **keys(), "columns": plan})
    assert list(out.columns) == ["id", "customer", "customer_destra", "client"]


def test_join_uses_the_names_of_the_plan(sales, customers):
    plan = c.join_plan(sales, customers, {"how": "inner", **keys()})
    plan[0]["name"] = "sale_id"
    out = c.join(sales, customers, {"how": "inner", **keys(), "columns": plan})
    assert out.columns[0] == "sale_id"


def test_merged_keys_give_one_column_filled_from_both_sides(sales, customers):
    spec = {"how": "full", "merge_keys": True, **keys()}
    plan = by_name(c.join_plan(sales, customers, spec))
    assert plan["left.customer"]["merged_with"] == "customer" and plan["left.customer"]["keep"]
    assert c.merged_away(plan["right.customer"]) and not plan["right.customer"]["keep"]
    out = c.join(sales, customers, spec)
    assert list(out.columns) == ["id", "customer", "amount", "name"]
    assert values(out, "customer")[-1] == (30,)


def test_merged_keys_need_equal_keys_of_the_same_type(sales):
    cities = pd.DataFrame({"code": ["10"], "city": ["Roma"]})
    spec = {"how": "left", "merge_keys": True, "match": "text", **keys("customer", "=", "code")}
    assert not any(entry["merged_with"] for entry in c.join_plan(sales, cities, spec))
    left_only = {"how": "left", "merge_keys": True, **keys("customer", "<", "customer")}
    assert not any(entry["merged_with"] for entry in c.join_plan(sales, sales, left_only))


def test_indicator_column_tells_where_each_row_comes_from(sales, customers):
    out = c.join(sales, customers, {"how": "full", "indicator": True, **keys()})
    assert out[c.ORIGIN].tolist() == ["entrambi"] * 3 + ["solo sinistra", "solo destra"]
    semi = c.join(sales, customers, {"how": "left_semi", "indicator": True, **keys()})
    assert c.ORIGIN not in semi


def test_column_sources_for_the_preview_headers(sales, customers):
    plan = c.join_plan(sales, customers, {"how": "left", **keys(), "indicator": True}, ("sales", "customers"),
                       overrides={("left", "amount"): {"keep": False}})
    sources = c.column_sources(plan)
    assert sources["id"] == ("sales", False)
    assert sources["customer_customers"] == ("customers", True)
    assert sources[c.ORIGIN] == (c.ORIGIN_SOURCE, False)
    assert "amount" not in sources


def test_origin_column_cannot_clash_with_a_result_column(sales, customers):
    plan = c.join_plan(sales, customers, {"how": "inner", **keys(), "indicator": True})
    origin = next(entry for entry in plan if entry["side"] == "origin")
    origin["name"] = "id"  # the origin column takes the name of a column already kept
    with pytest.raises(ValueError, match="ripetuti"):
        c.join(sales, customers, {"how": "inner", **keys(), "columns": plan})


# ------------------------------------------------------------------ join: errors

@pytest.mark.parametrize("spec, message", [
    ({"how": "nope"}, "sconosciuto"),
    ({"how": "inner"}, "almeno una chiave"),
    ({"how": "inner", "keys": [c.new_key("customer", "=", "")]}, "incompleta"),
    ({"how": "inner", "keys": [c.new_key("customer", "??", "customer")]}, "operatore"),
    ({"how": "inner", **keys(), "match": "nope"}, "Confronto"),
])
def test_join_explains_what_is_wrong(sales, customers, spec, message):
    with pytest.raises(ValueError, match=message):
        c.join(sales, customers, spec)


def test_join_rejects_repeated_empty_or_missing_names(sales, customers):
    plan = c.join_plan(sales, customers, {"how": "inner", **keys()})
    spec = {"how": "inner", **keys()}
    repeated = [dict(entry) for entry in plan]
    repeated[-1]["name"] = repeated[0]["name"]
    with pytest.raises(ValueError, match="ripetuti"):
        c.join(sales, customers, {**spec, "columns": repeated})
    blank = [dict(entry) for entry in plan]
    blank[0]["name"] = " "
    with pytest.raises(ValueError, match="nome"):
        c.join(sales, customers, {**spec, "columns": blank})
    none = [{**entry, "keep": False} for entry in plan]
    with pytest.raises(ValueError, match="almeno una colonna"):
        c.join(sales, customers, {**spec, "columns": none})


# ------------------------------------------------------------------ union: column plan

@pytest.fixture
def first():
    return pd.DataFrame({"x": [1, 2, 2], "y": ["p", "q", "q"]})


@pytest.fixture
def second():
    return pd.DataFrame({"y": ["q", "r"], "z": [1.5, 2.5], "x": [2, 3]})


@pytest.mark.parametrize("align, expected", [
    ("by_name", [("x", ["x", "x"]), ("y", ["y", "y"]), ("z", [None, "z"])]),
    ("common", [("x", ["x", "x"]), ("y", ["y", "y"])]),
    ("first", [("x", ["x", "x"]), ("y", ["y", "y"])]),
    ("position", [("x", ["x", "y"]), ("y", ["y", "z"]), ("x", [None, "x"])]),
])
def test_union_plan_alignments(first, second, align, expected):
    plan = c.union_plan([first, second], align)
    assert [(entry["name"], entry["sources"]) for entry in plan] == expected
    assert all(entry["keep"] for entry in plan)


def test_union_plan_rejects_unknown_alignment(first):
    with pytest.raises(ValueError, match="sconosciuto"):
        c.union_plan([first], "nope")


# ------------------------------------------------------------------ union: execution

def test_union_all_fills_missing_columns_and_keeps_order(first, second):
    out = c.union([first, second], {"how": "union_all"})
    assert list(out.columns) == ["x", "y", "z"]
    assert out["x"].tolist() == [1, 2, 2, 2, 3]
    assert values(out, "z") == [(None,)] * 3 + [(1.5,), (2.5,)]
    assert out["x"].dtype.kind == "i"


def test_union_distinct_removes_duplicates_keeping_the_first(first, second):
    out = c.union([first, second], {"how": "union", "align": "common"})
    assert values(out, "x", "y") == [(1, "p"), (2, "q"), (3, "r")]


def test_intersect_and_except_compare_whole_rows(first, second):
    spec = {"align": "common"}
    assert values(c.union([first, second], {**spec, "how": "intersect"}), "x", "y") == [(2, "q")]
    assert values(c.union([first, second], {**spec, "how": "except"}), "x", "y") == [(1, "p")]


def test_set_operations_match_nulls_like_sql():
    a = pd.DataFrame({"k": [1.0, None, 2.0]})
    b = pd.DataFrame({"k": [None, 3.0]})
    assert values(c.union([a, b], {"how": "intersect"}), "k") == [(None,)]
    assert values(c.union([a, b], {"how": "except"}), "k") == [(1.0,), (2.0,)]


def test_set_operations_need_every_column_in_every_dataset(first, second):
    with pytest.raises(ValueError, match="manca in un dataset"):
        c.union([first, second], {"how": "intersect", "align": "by_name"})


def test_union_of_three_datasets():
    parts = [pd.DataFrame({"n": [number]}) for number in (1, 2, 3)]
    assert c.union(parts, {"how": "union_all"})["n"].tolist() == [1, 2, 3]
    shared = [pd.DataFrame({"n": numbers}) for numbers in ([1, 2, 3], [2, 3, 4], [3, 9])]
    assert c.union(shared, {"how": "intersect"})["n"].tolist() == [3]
    assert c.union(shared, {"how": "except"})["n"].tolist() == [1]


def test_origin_column_names_the_dataset_of_each_row(first, second):
    out = c.union([first, second], {"how": "union_all", "origin": True}, ["first", "second"])
    assert out[c.ORIGIN].tolist() == ["first"] * 3 + ["second"] * 2
    distinct = c.union([first, second], {"how": "union", "align": "common", "origin": True}, ["first", "second"])
    assert distinct[c.ORIGIN].tolist() == ["first", "first", "second"]
    same = c.union([first, first], {"how": "union_all", "origin": True}, ["first", "first"])
    assert set(same[c.ORIGIN]) == {"first", "first_2"}


def test_union_by_position_uses_the_names_of_the_first_dataset():
    a = pd.DataFrame({"city": ["Roma"], "pop": [3]})
    b = pd.DataFrame({"town": ["Pisa"], "people": [1]})
    out = c.union([a, b], {"how": "union_all", "align": "position"})
    assert list(out.columns) == ["city", "pop"] and out["city"].tolist() == ["Roma", "Pisa"]


def test_union_plan_can_map_rename_reorder_and_drop_columns(first, second):
    plan = c.union_plan([first, second], "by_name")
    plan[0]["sources"][1] = "z"                       # column x receives z of the second dataset
    plan[2]["keep"] = False                           # no z column
    plan[0]["name"] = "value"
    plan.reverse()                                    # y before value
    out = c.union([first, second], {"how": "union_all", "columns": plan})
    assert list(out.columns) == ["y", "value"]
    assert out["value"].tolist() == [1, 2, 2, 1.5, 2.5]


def test_union_plan_rejects_bad_names_and_stale_mappings(first, second):
    plan = c.union_plan([first, second], "common")
    plan[1]["name"] = plan[0]["name"]
    with pytest.raises(ValueError, match="ripetuti"):
        c.union([first, second], {"how": "union_all", "columns": plan})
    plan = c.union_plan([first, second, second], "common")
    with pytest.raises(ValueError, match="ricostruiscilo"):
        c.union([first, second], {"how": "union_all", "columns": plan})
    with pytest.raises(ValueError, match="non esiste"):
        c.union([first, second], {"how": "union_all", "columns": [{"name": "q", "keep": True, "sources": ["nope", "x"]}]})
    with pytest.raises(ValueError, match="almeno due"):
        c.union([first], {"how": "union_all"})
    with pytest.raises(ValueError, match="sconosciuto"):
        c.union([first, second], {"how": "nope"})


def test_different_types_in_the_same_column_become_text(first):
    other = pd.DataFrame({"x": ["a", "b"], "y": ["s", "t"]})
    plan = c.union_plan([first, other], "common")
    assert c.type_conflicts([first, other], plan) == {"x": {"numero", "testo"}}
    out = c.union([first, other], {"how": "union_all"})
    assert out["x"].tolist() == ["1", "2", "2", "a", "b"]


def test_columns_without_values_do_not_cause_type_conflicts(first):
    empty = pd.DataFrame({"x": [None, None], "y": ["s", "t"]})
    plan = c.union_plan([first, empty], "common")
    assert c.type_conflicts([first, empty], plan) == {}


def test_union_prefilters_each_dataset(first, second):
    only_x_2 = {"logica": "AND", "condizioni": [eng.new_condition("x", "=", "2")]}
    spec = {"how": "union_all", "align": "common", "filters": [only_x_2, None]}
    assert c.union([first, second], spec)["x"].tolist() == [2, 2, 2, 3]


def test_union_ignores_duplicate_row_labels(first):
    shuffled = pd.concat([first, first])  # index with repeated labels
    out = c.union([shuffled, first], {"how": "union_all"})
    assert len(out) == 9 and out["x"].isna().sum() == 0


def test_union_does_not_change_the_inputs(first, second):
    before = first.copy(), second.copy()
    c.union([first, second], {"how": "union", "origin": True})
    pd.testing.assert_frame_equal(first, before[0])
    pd.testing.assert_frame_equal(second, before[1])


def test_union_types_are_covered():
    assert list(c.UNION_TYPES) == ["union_all", "union", "intersect", "except"]
    assert set(c.APPENDING) <= set(c.UNION_TYPES)


# ------------------------------------------------------------------ chained join

@pytest.fixture
def chain_frames():
    orders = pd.DataFrame({"ordine": [1, 2, 3], "cliente": [10, 20, 10]})
    clients = pd.DataFrame({"cliente": [10, 20], "citta": ["Roma", "Pisa"]})
    cities = pd.DataFrame({"citta": ["Roma"], "regione": ["Lazio"]})
    steps = [{"right": 1, "how": "inner", "keys": [c.new_key("cliente", "=", "cliente")]},
             {"right": 2, "how": "left", "keys": [c.new_key("citta", "=", "citta")]}]
    return [orders, clients, cities], steps


def test_chain_joins_three_datasets_in_order(chain_frames):
    frames, steps = chain_frames
    out = c.join_chain(frames, steps, ["ordini", "clienti", "citta"])
    assert list(out.columns) == ["ordine", "cliente", "cliente_clienti", "citta", "citta_citta", "regione"]
    assert values(out, "ordine", "regione") == [(1, "Lazio"), (2, None), (3, "Lazio")]


def test_chain_keeps_the_source_of_every_column(chain_frames):
    frames, steps = chain_frames
    plans = c.plan_chain(frames, steps, ["ordini", "clienti", "citta"])
    sources = c.column_sources(plans[-1])
    assert sources["ordine"] == ("ordini", False)
    assert sources["citta"] == ("clienti", True)  # duplicate in the second step: the suffix is the dataset name
    assert sources["citta_citta"] == ("citta", True)
    assert sources["regione"] == ("citta", False)


def test_chain_uses_the_plan_it_is_given(chain_frames):
    frames, steps = chain_frames
    plans = c.plan_chain(frames, steps, ["ordini", "clienti", "citta"])
    plans[1] = [{**entry, "keep": entry["name"] != "regione"} for entry in plans[1]]
    out = c.join_chain(frames, steps, ["ordini", "clienti", "citta"], plans=plans)
    assert "regione" not in out.columns


def test_renaming_upstream_changes_the_columns_of_the_next_step(chain_frames):
    frames, steps = chain_frames
    overrides = {(0, "right", "citta"): {"name": "città"}}
    plans = c.plan_chain(frames, steps, ["ordini", "clienti", "citta"], overrides)
    assert "città" in [entry["name"] for entry in plans[1] if entry["side"] == "left"]


def test_chain_filters_each_dataset_before_its_step(chain_frames):
    frames, steps = chain_frames
    only_rome = {"logica": "AND", "condizioni": [eng.new_condition("citta", "=", "Roma")]}
    out = c.join_chain(frames, steps, ["ordini", "clienti", "citta"], [None, only_rome, None])
    assert out["ordine"].tolist() == [1, 3]


def test_origin_columns_of_several_steps_get_distinct_names(chain_frames):
    frames, steps = chain_frames
    steps = [{**steps[0], "how": "full", "indicator": True}, {**steps[1], "indicator": True}]
    out = c.join_chain(frames, steps, ["ordini", "clienti", "citta"])
    assert c.ORIGIN in out.columns and f"{c.ORIGIN}_2" in out.columns
    assert set(out[c.ORIGIN]) <= {"entrambi", "solo sinistra", "solo destra"}


def test_chain_needs_at_least_one_step(chain_frames):
    frames, _ = chain_frames
    with pytest.raises(ValueError, match="almeno un join"):
        c.join_chain(frames, [])


def test_chain_rejects_a_plan_that_no_longer_matches_the_data(chain_frames):
    frames, steps = chain_frames
    plans = c.plan_chain(frames, steps, ["ordini", "clienti", "citta"])
    plans[1][0] = {**plans[1][0], "column": "colonna_inesistente", "side": "left"}
    with pytest.raises(ValueError, match="ricostruiscilo"):
        c.join_chain(frames, steps, ["ordini", "clienti", "citta"], plans=plans)


def test_plan_chain_checks_the_join_types(chain_frames):
    frames, _ = chain_frames
    with pytest.raises(ValueError, match="sconosciuto"):
        c.plan_chain(frames, [{"right": 1, "how": "nope"}])

