import pandas as pd
import pytest

from data_explorer.core import import_plan as ip
@pytest.fixture
def raw():
    return pd.DataFrame({
        "codice": ["00123", "00456", None],
        "importo": ["1.234,56", "7,5", ""],
        "pezzi": ["3", "12", "4"],
        "giorno": ["31/01/2024", "01/02/2024", None],
        "anno": ["2024", "2023", "2022"],
        "nota": ["a", "b", "c"],
        "numero": [1.5, 2.0, None],
        "intero": [1, 2, 3],
        "data": pd.to_datetime(["2024-01-01", "2024-01-02", None]),
        "flag": [True, False, True],
    })


def test_detected_formats(raw):
    detected = {column: ip.detected_format(raw[column]) for column in raw}
    assert detected == {"codice": "Testo", "importo": "Testo", "pezzi": "Testo", "giorno": "Testo", "anno": "Testo",
                        "nota": "Testo", "numero": "Numero", "intero": "Numero intero", "data": "Data",
                        "flag": "Sì/No"}


def test_column_plan_lists_every_column_as_detected(raw):
    plan = ip.column_plan(raw)
    assert [item["name"] for item in plan] == list(raw.columns)
    assert all(item["keep"] and item["format"] == ip.KEEP for item in plan)
    assert plan[1] == {"position": 1, "name": "importo", "keep": True, "detected": "Testo", "format": ip.KEEP}


def test_apply_plan_keeps_renames_and_converts(raw):
    plan = ip.column_plan(raw)
    plan[0]["keep"] = False
    plan[5]["name"] = "commento"
    plan[1]["format"], plan[2]["format"], plan[3]["format"] = "Numero", "Numero intero", "Data"
    plan[7]["format"] = "Testo"
    out = ip.apply_plan(raw, plan)
    assert list(out.columns) == ["importo", "pezzi", "giorno", "anno", "commento", "numero", "intero", "data", "flag"]
    assert out["importo"].tolist()[:2] == [1234.56, 7.5] and pd.isna(out["importo"].iloc[2])
    assert str(out["pezzi"].dtype) == "Int64" and str(out["giorno"].iloc[0].date()) == "2024-01-31"
    assert out["intero"].tolist() == ["1", "2", "3"]
    assert raw["importo"].iloc[0] == "1.234,56"  # the original does not change


def test_apply_plan_needs_one_column_and_unique_names(raw):
    plan = ip.column_plan(raw)
    with pytest.raises(ValueError, match="almeno una"):
        ip.apply_plan(raw, [{**item, "keep": False} for item in plan])
    plan[1]["name"] = "Codice"
    with pytest.raises(ValueError, match="diversi"):
        ip.apply_plan(raw, plan)


def test_lost_counts_only_for_converted_columns(raw):
    plan = ip.column_plan(raw)
    plan[5]["format"] = "Numero"  # note: a, b, c are not numbers
    out = ip.apply_plan(raw, plan)
    assert ip.lost_counts(raw, plan, out) == {"nota": 3}


def test_numbers_chosen_as_text_are_reloaded():
    read = pd.DataFrame({"codice": [123, 456], "qta": [1, 2]})
    written = pd.DataFrame({"codice": ["00123", "00456"], "qta": ["1", "2"]})
    plan = ip.column_plan(read)
    assert ip.text_reload_needed(plan) == []
    plan[0]["format"] = "Testo"
    assert [item["name"] for item in ip.text_reload_needed(plan)] == ["codice"]
    restored = ip.with_text_columns(read, {0: written["codice"]})
    assert restored["codice"].tolist() == ["00123", "00456"] and read["codice"].tolist() == [123, 456]


def test_text_columns_from_csv_and_excel(tmp_path):
    from data_explorer.core import engine as eng
    csv = tmp_path / "a.csv"
    csv.write_text("codice;qta\n00123;1\n00456;2\n00789;3\n")
    assert eng.load_text_columns("a.csv", csv, ["codice"])["codice"].tolist() == ["00123", "00456", "00789"]
    assert len(eng.load_text_columns("a.csv", csv.read_bytes(), rows=2)) == 2
    book = tmp_path / "b.xlsx"
    pd.DataFrame({"codice": ["007", "008"], "n": [1, 2]}).to_excel(book, index=False)
    assert eng.load_text_columns("b.xlsx", book, ["n"])["n"].tolist() == ["1", "2"]
    assert eng.can_read_as_text("x.CSV") and not eng.can_read_as_text("x.parquet")


def test_suggest_formats_only_for_text_that_converts(raw):
    plan = ip.column_plan(raw)
    assert ip.suggest_formats(raw, plan) == 4
    formats = {item["name"]: item["format"] for item in plan}
    assert formats["importo"] == "Numero" and formats["pezzi"] == formats["anno"] == "Numero intero"
    assert formats["giorno"] == "Data senza ora"
    assert formats["codice"] == formats["nota"] == ip.KEEP  # «00123» stays text, «a» does not convert
    assert formats["numero"] == formats["data"] == ip.KEEP  # columns already read as numbers or dates
    assert ip.suggest_formats(raw, plan) == 0  # formats already chosen are not touched
