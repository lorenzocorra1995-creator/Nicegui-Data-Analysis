import numpy as np
import pytest

from data_explorer.core import pivot as pv
TOTAL = "Totale"


def spec(rows, columns, *values, **extra):
    return {"rows": rows, "columns": columns, "values": list(values), **extra}


def measure(col="importo", fn="somma", **extra):
    return {"col": col, "fn": fn, **extra}


def row(frame, **labels):
    mask = np.ones(len(frame), dtype=bool)
    for field, label in labels.items():
        mask &= (frame[field] == label).to_numpy()
    return frame[mask].iloc[0]


# ----------------------------------------------------------------- aggregations

def test_totals_match_pandas(df):
    table = pv.run_pivot(df, spec(["regione"], ["prodotto"], measure(), measure("qta", "media"))).frame
    expected = df.pivot_table(index="regione", columns="prodotto", values="importo", aggfunc="sum",
                              margins=True, margins_name=TOTAL)
    assert row(table, regione="Nord")["somma importo | A"] == pytest.approx(expected.loc["Nord", "A"])
    assert row(table, regione=TOTAL)["somma importo | Totale"] == pytest.approx(df.importo.sum())
    assert row(table, regione=TOTAL)["media qta | Totale"] == pytest.approx(df.qta.mean())  # not decomposable


def test_sum_of_text_rejected_before_computing(df):
    with pytest.raises(ValueError, match="testo"):
        pv.build_cube(df, spec(["prodotto"], [], measure("regione", "somma")))
    table = pv.run_pivot(df, spec(["prodotto"], [], measure("regione", "conteggio"))).frame
    assert row(table, prodotto=TOTAL)["conteggio regione"] == len(df)


def test_quantiles_and_mode_in_totals(df):
    values = [measure("importo", "q1"), measure("qta", "moda"), measure("note", "nulli")]
    table = pv.run_pivot(df, spec(["regione"], [], *values)).frame
    nord = df[df.regione == "Nord"]
    assert row(table, regione="Nord")["q1 importo"] == pytest.approx(nord.importo.quantile(0.25))
    assert row(table, regione=TOTAL)["q1 importo"] == pytest.approx(df.importo.quantile(0.25))
    assert row(table, regione=TOTAL)["moda qta"] == df.qta.mode().iloc[0]
    assert row(table, regione=TOTAL)["nulli note"] == df.note.isna().sum()


def test_too_many_columns_rejected(df, monkeypatch):
    monkeypatch.setattr(pv, "MAX_COLUMNS", 4)
    with pytest.raises(ValueError, match="colonne"):
        pv.run_pivot(df, spec(["regione"], ["prodotto"], measure()))  # 4 products + Totale = 5 columns


def test_subtotals_and_layout(df):
    values = measure()
    table = pv.run_pivot(df, spec(["regione", "prodotto"], [], values), {"subtotal_rows": True}).frame
    assert row(table, regione="Nord", prodotto=TOTAL)["somma importo"] == pytest.approx(
        df.loc[df.regione == "Nord", "importo"].sum())
    assert table.iloc[-1]["regione"] == TOTAL and table.iloc[-1]["prodotto"] == ""
    hidden = pv.run_pivot(df, spec(["regione", "prodotto"], [], values),
                          {"subtotal_rows": False, "grand_rows": False}).frame
    assert TOTAL not in hidden["prodotto"].values and TOTAL not in hidden["regione"].values


def test_column_names_and_naming(df):
    values = [measure(name="Fatturato"), measure("qta", "media")]
    table = pv.run_pivot(df, spec(["regione"], ["prodotto"], *values), {"naming": "{colonna} {valore}"}).frame
    assert "Fatturato | A" in table.columns and "qta A" in table.columns
    table = pv.run_pivot(df, spec(["regione"], [], *values)).frame
    assert table.columns.tolist() == ["regione", "Fatturato", "media qta"]


def test_date_grouping(df):
    table = pv.run_pivot(df, spec(["data"], [], measure("qta", "conteggio"), dates={"data": "Anno"})).frame
    assert table["data"].tolist()[:3] == ["2024", "2025", "2026"]


def test_requires_values_axes_and_rows(df):
    with pytest.raises(ValueError):
        pv.run_pivot(df, spec(["regione"], []))
    with pytest.raises(ValueError):
        pv.run_pivot(df, spec([], [], measure()))
    with pytest.raises(ValueError):
        pv.run_pivot(df, spec(["regione"], ["regione"], measure()))
    nobody = {"logica": "AND", "condizioni": [{"col": "regione", "op": "=", "val": "Ovest"}]}
    with pytest.raises(ValueError):
        pv.run_pivot(df, spec(["regione"], [], measure(), filters=nobody))


# ------------------------------------------------------- show values as

def test_percent_references(df):
    values = [measure(show_as="% di", relative_to="Totale di riga"),
              measure(show_as="% di", relative_to="Totale di colonna"),
              measure(show_as="% di", relative_to=pv.GRAND_TOTAL)]
    table = pv.run_pivot(df, spec(["regione"], ["prodotto"], *values)).frame
    by_row = [column for column in table.columns if "tot. riga" in column]
    assert row(table, regione="Nord")[by_row].iloc[-1] == pytest.approx(100)
    assert row(table, regione="Nord")[by_row[:-1]].sum() == pytest.approx(100)
    by_column = [column for column in table.columns if "tot. colonna" in column]
    assert table[by_column[0]].iloc[:-1].sum() == pytest.approx(100)
    grand = [column for column in table.columns if "(% di totale)" in column]
    assert row(table, regione=TOTAL)[grand[-1]] == pytest.approx(100)


def test_parent_and_differences(df):
    values = [measure(show_as="% di", relative_to="Riga padre"),
              measure(show_as="Differenza da", relative_to="Elemento (campo base)",
                      base_field="prodotto", base_item="A", name="diff A"),
              measure(show_as="Differenza % da", relative_to="Precedente (campo base)",
                      base_field="prodotto", name="vs prec")]
    table = pv.run_pivot(df, spec(["regione", "prodotto"], [], *values), {"subtotal_rows": True}).frame
    nord = df[df.regione == "Nord"].groupby("prodotto").importo.sum()
    assert row(table, regione="Nord", prodotto="B")["diff A"] == pytest.approx(nord["B"] - nord["A"])
    assert row(table, regione="Nord", prodotto="A")["diff A"] == pytest.approx(0)
    assert row(table, regione="Nord", prodotto="C")["vs prec"] == pytest.approx((nord["C"] - nord["B"]) / nord["B"] * 100)
    assert np.isnan(row(table, regione="Nord", prodotto="A")["vs prec"])
    parent = [column for column in table.columns if "riga padre" in column][0]
    assert table.loc[table.prodotto != TOTAL].query("regione == 'Nord'")[parent].sum() == pytest.approx(100)


def test_running_rank_and_index(df):
    values = [measure(show_as="Totale progressivo in", base_field="prodotto", name="prog"),
              measure(show_as="% progressiva in", base_field="prodotto", name="prog %"),
              measure(show_as="Rango dal più grande in", base_field="prodotto", name="rango"),
              measure(show_as="Indice", name="indice")]
    table = pv.run_pivot(df, spec(["regione", "prodotto"], [], *values), {"subtotal_rows": True}).frame
    nord = df[df.regione == "Nord"].groupby("prodotto").importo.sum()
    assert row(table, regione="Nord", prodotto="C")["prog"] == pytest.approx(nord[["A", "B", "C"]].sum())
    assert row(table, regione="Nord", prodotto="D")["prog %"] == pytest.approx(100)
    assert row(table, regione="Nord", prodotto=nord.idxmax())["rango"] == 1
    assert table["indice"].dropna().eq(1).all()  # without column fields the index is 1


def test_base_field_required(df):
    with pytest.raises(ValueError):
        pv.run_pivot(df, spec(["regione"], [], measure(show_as="Totale progressivo in")))
    with pytest.raises(ValueError):
        pv.run_pivot(df, spec(["regione"], [], measure(show_as="% di", relative_to="Elemento (campo base)",
                                                        base_field="regione")))


# ------------------------------------------------------ sorting, cube, drill

def test_sort_by_value(df):
    table = pv.run_pivot(df, spec(["regione"], [], measure()), {"sort_by": 0, "descending": True}).frame
    sums = df.groupby("regione").importo.sum().sort_values(ascending=False)
    assert table["regione"].tolist() == [*sums.index, TOTAL]


def test_cube_reused_for_display_changes(df):
    cube = pv.build_cube(df, spec(["regione"], ["prodotto"], measure()))
    assert cube.covers(spec(["regione"], ["prodotto"], measure(show_as="Indice"), measure()))
    assert not cube.covers(spec(["regione"], ["prodotto"], measure("qta")))
    assert not cube.covers(spec(["regione"], [], measure()))
    table = pv.render(cube, [measure(show_as="Indice")], {})
    assert len(table.frame) == 4


def test_drill(df):
    table = pv.run_pivot(df, spec(["regione"], ["prodotto"], measure()))
    position = table.frame.index[table.frame.regione == "Nord"][0]
    rows = pv.drill(table, position, "somma importo | B")
    assert len(rows) == len(df[(df.regione == "Nord") & (df.prodotto == "B")])
    assert rows.columns.tolist() == df.columns.tolist()
    assert len(pv.drill(table, position, "regione")) == (df.regione == "Nord").sum()
    assert pv.selection_text(table, position, "somma importo | B") == "regione = Nord · prodotto = B"
