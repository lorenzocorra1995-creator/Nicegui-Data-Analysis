import anthropic
import pytest

import ai_assistant as ai

SECRET = "VALORE_RISERVATO_123"


def test_prompt_contains_only_metadata(df):
    df.loc[0, "note"] = SECRET
    prompt = ai.build_prompt("somma importo per regione", "SQL", {"df": df})
    assert SECRET not in prompt
    assert "regione (" in prompt and "importo (float64)" in prompt
    assert "somma importo per regione" in prompt


def test_prompt_uses_language_rules(df):
    assert "DuckDB" in ai.build_prompt("x", "SQL", {"df": df})
    assert "pandas" in ai.build_prompt("x", "Python", {"df": df})


@pytest.mark.parametrize("raw, expected", [
    ("SELECT 1", "SELECT 1"),
    ("```sql\nSELECT 1\n```", "SELECT 1"),
    ("Ecco:\n```python\ndf.head()\n```\nfine", "df.head()"),
])
def test_clean_code(raw, expected):
    assert ai.clean_code(raw) == expected


def test_generate_code_sends_only_metadata(monkeypatch, df):
    sent = {}

    class FakeMessages:
        def create(self, **kwargs):
            sent.update(kwargs)
            block = type("Block", (), {"type": "text", "text": "```sql\nSELECT 1\n```"})()
            return type("Response", (), {"content": [block]})()

    class FakeClient:
        def __init__(self, api_key):
            sent["api_key"] = api_key
            self.messages = FakeMessages()

    monkeypatch.setattr(anthropic, "Anthropic", FakeClient)
    df.loc[0, "note"] = SECRET
    assert ai.generate_code("sk-test", "totali", "SQL", {"df": df}) == "SELECT 1"
    assert sent["api_key"] == "sk-test"
    assert SECRET not in str(sent["messages"])


def fake_client(monkeypatch, sent):
    class FakeMessages:
        def create(self, **kwargs):
            sent.update(kwargs)
            block = type("Block", (), {"type": "text", "text": "SELECT 1"})()
            return type("Response", (), {"content": [block]})()

    class FakeClient:
        def __init__(self, api_key):
            self.messages = FakeMessages()

    monkeypatch.setattr(anthropic, "Anthropic", FakeClient)


def test_model_and_effort_are_sent(monkeypatch, df):
    sent = {}
    fake_client(monkeypatch, sent)
    ai.generate_code("k", "x", "SQL", {"df": df}, model="claude-opus-5-5", effort="xhigh")
    assert sent["model"] == "claude-opus-5-5"
    assert sent["output_config"] == {"effort": "xhigh"}


def test_default_effort_is_omitted(monkeypatch, df):
    sent = {}
    fake_client(monkeypatch, sent)
    ai.generate_code("k", "x", "SQL", {"df": df})
    assert sent["model"] == ai.DEFAULT_MODEL
    assert "output_config" not in sent
