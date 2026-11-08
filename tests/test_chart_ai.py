import json

import anthropic
import pandas as pd
import pytest

from data_explorer.ai import ai_assistant as ai
from data_explorer.plotting import chart_ai
from data_explorer.plotting import chart_options as co
from data_explorer.plotting import charts
SECRET = "VALORE_RISERVATO_123"
COLUMNS = ["regione", "prodotto", "importo", "qta", "data", "note"]


def test_prompt_contains_only_metadata(df):
    df.loc[0, "note"] = SECRET
    prompt = chart_ai.build_prompt("importo per regione", df)
    assert SECRET not in prompt
    assert "regione (" in prompt and "importo (float64)" in prompt and "importo per regione" in prompt


def test_prompt_lists_every_chart_and_choice(df):
    prompt = chart_ai.build_prompt("x", df)
    assert all(f"- {key}: {chart.label}" in prompt for key, chart in charts.CHART_TYPES.items())
    for allowed in chart_ai.CHOICES.values():
        assert all(value in prompt for value in allowed)


@pytest.mark.parametrize("text", [
    '{"chart": "bar"}',
    '```json\n{"chart": "bar"}\n```',
    'Ecco il grafico: {"chart": "bar"} fatto',
])
def test_parse_answer(text):
    assert chart_ai.parse_answer(text) == {"chart": "bar"}


@pytest.mark.parametrize("text", ["", "niente json", "[1, 2]", '{"chart": '])
def test_parse_answer_rejects_invalid(text):
    with pytest.raises(ValueError, match="non è valida"):
        chart_ai.parse_answer(text)


def test_normalize_fills_controls_and_defaults():
    answer = {"chart": "stacked", "x": "regione", "series": "prodotto", "title": " Vendite ",
              "measures": [{"col": "importo", "fn": "somma"}], "top_n": "3", "top_from": "più alti",
              "others": True, "sort": "Valore decrescente", "labels": "Valore", "decimals": 1, "compact": True}
    settings = chart_ai.normalize(answer, COLUMNS)
    assert set(settings) == set(chart_ai.DEFAULTS)
    assert settings | {"measures": None} == chart_ai.DEFAULTS | {
        "chart": "stacked", "x": "regione", "series": "prodotto", "title": "Vendite", "measures": None,
        "top_n": 3, "others": True, "sort": "Valore decrescente", "labels": "Valore", "decimals": "1", "compact": True}
    assert settings["measures"] == [{"col": "importo", "fn": "somma"}]


def test_normalize_ignores_fields_the_chart_does_not_use():
    answer = {"chart": "pie", "x": "regione", "series": "prodotto", "y": "importo", "bins": 50, "smooth": True,
              "measures": [{"col": "importo", "fn": "somma"}]}
    settings = chart_ai.normalize(answer, COLUMNS)
    assert settings["series"] is None and settings["y"] is None  # the pie uses neither series nor Y
    assert settings["bins"] == charts.DEFAULT_BINS and settings["smooth"] is False


def test_normalize_clamps_numbers_and_resolves_case():
    settings = chart_ai.normalize({"chart": "histogram", "x": "IMPORTO", "bins": 9999}, COLUMNS)
    assert settings["x"] == "importo" and settings["bins"] == charts.MAX_BINS
    assert chart_ai.normalize({"chart": "histogram", "x": "importo", "bins": "boh"}, COLUMNS)["bins"] == charts.DEFAULT_BINS
    assert chart_ai.normalize({"chart": "bar", "top_n": -4}, COLUMNS)["top_n"] == 0


def test_normalize_ignores_values_outside_the_allowed_ones():
    settings = chart_ai.normalize({"chart": "bar", "sort": "a caso", "labels": "boh", "decimals": 7}, COLUMNS)
    assert (settings["sort"], settings["labels"], settings["decimals"]) == (charts.AUTO, co.AUTO_LABELS, co.AUTO_DECIMALS)


@pytest.mark.parametrize("answer, message", [
    ({"chart": "torta-3d"}, "tipo di grafico che non esiste"),
    ({}, "tipo di grafico che non esiste"),
    ({"chart": "bar", "x": "inventata"}, "colonna che non esiste"),
    ({"chart": "bar", "x": "regione", "measures": [{"col": "inventata", "fn": "somma"}]}, "colonna che non esiste"),
    ({"chart": "bar", "x": "regione", "measures": [{"col": "importo", "fn": "boh"}]}, "funzione di aggregazione"),
])
def test_normalize_rejects_invented_values(answer, message):
    with pytest.raises(ValueError, match=message):
        chart_ai.normalize(answer, COLUMNS)


def test_measure_without_column_is_ignored_and_function_defaults_to_sum():
    measures = chart_ai.to_measures([{"fn": "media"}, {"col": "importo"}, "x"], COLUMNS)
    assert measures == [{"col": "importo", "fn": "somma"}]


def fake_client(monkeypatch, reply: str, sent: dict):
    class FakeMessages:
        def create(self, **kwargs):
            sent.update(kwargs)
            return type("Response", (), {"content": [type("Block", (), {"type": "text", "text": reply})()]})()

    class FakeClient:
        def __init__(self, api_key):
            sent["api_key"] = api_key
            self.messages = FakeMessages()

    monkeypatch.setattr(anthropic, "Anthropic", FakeClient)


def test_generate_settings_sends_only_metadata_and_builds_a_real_chart(monkeypatch, df):
    sent = {}
    reply = json.dumps({"chart": "stacked", "x": "regione", "series": "prodotto", "title": "Importo",
                        "measures": [{"col": "importo", "fn": "somma"}]})
    fake_client(monkeypatch, f"```json\n{reply}\n```", sent)
    df.loc[0, "note"] = SECRET
    settings = chart_ai.generate_settings("sk-test", "importo per regione e prodotto", df, model="claude-opus-5-5", effort="high")
    assert sent["api_key"] == "sk-test" and sent["model"] == "claude-opus-5-5"
    assert sent["output_config"] == {"effort": "high"}
    assert SECRET not in str(sent["messages"]) and SECRET not in sent["system"]
    # the validated choices are accepted by charts.prepare and by the drawing
    spec = {key: settings[key] for key in ("chart", "x", "series", "y", "size", "measures", "date_unit", "bins",
                                           "top_n", "top_from", "others", "sort")}
    data = charts.prepare(df, spec)
    assert co.build_options(data, {"title": settings["title"]})["title"]["text"] == "Importo"


def test_generate_code_still_works_after_the_refactor(monkeypatch, df):
    sent = {}
    fake_client(monkeypatch, "```sql\nSELECT 1\n```", sent)
    assert ai.generate_code("k", "x", "SQL", {"df": df}) == "SELECT 1"
    assert sent["system"] == ai.SYSTEM_PROMPT


def test_normalize_keeps_the_panels():
    settings = chart_ai.normalize({"chart": "bar", "x": "prodotto", "panels": "DATA", "panel_date_unit": "Anno",
                                   "top_n": 5}, COLUMNS)
    assert (settings["panels"], settings["panel_date_unit"], settings["top_n"]) == ("data", "Anno", 5)
    assert "panels" in chart_ai.build_prompt("top 5 per anno", pd.DataFrame(columns=COLUMNS))
