"""
Generazione di codice con Claude (nessuna dipendenza da UI).

Privacy: all'API vengono inviati SOLO la richiesta dell'utente e i metadati
(nome tabella, nome colonna, tipo). Nessun valore dei dati lascia il computer.
"""
from __future__ import annotations

import ast
import re

import anthropic
import duckdb
import pandas as pd

MODELS = {
    "claude-fable-5-1": "Fable 5.1 — massima capacità",
    "claude-opus-5-5": "Opus 5.5 — molto capace",
    "claude-sonnet-5-5": "Sonnet 5.5 — equilibrato",
    "claude-haiku-5-5": "Haiku 5.5 — veloce ed economico",
}
DEFAULT_MODEL = "claude-sonnet-5-5"

EFFORTS = {
    "auto": "Predefinito del modello",
    "low": "Basso",
    "medium": "Medio",
    "high": "Alto",
    "xhigh": "Molto alto",
    "max": "Massimo",
}
DEFAULT_EFFORT = "auto"

MAX_TOKENS = 16000  # il ragionamento conta nei token: lascia spazio anche con sforzo alto

RULES = {
    "SQL": (
        "Write a single DuckDB SQL query that returns a result table. "
        "Use only the tables and columns listed in the metadata. "
        "Quote column names that contain spaces or special characters."
    ),
    "Python": (
        "Write pandas code. Available variables: df (the active dataset), datasets (dict name -> DataFrame), "
        "pd, np, and sql('SELECT ...') which runs DuckDB SQL on the tables. "
        "The last expression is displayed to the user (DataFrame or Series become a table), "
        "so end with the object to show instead of using print() on large data. "
        "Do not read or write files and do not use the network."
    ),
}

SYSTEM_PROMPT = (
    "You are a data analysis assistant embedded in a local data exploration tool. "
    "You only know the structure of the data (table names, column names, column types), never the values: "
    "do not assume literal values or formats beyond what the user states. "
    "Reply with exactly one fenced code block and absolutely nothing outside it: "
    "no greetings, no explanations, no text before or after the block. "
    "Inside the block write only executable code and comments "
    "(-- for SQL, # for Python). Comments must be short and in Italian."
)


def describe_tables(tables: dict[str, pd.DataFrame]) -> str:
    """Metadati in testo: una riga per tabella con 'colonna (tipo)'."""
    lines = []
    for name, table in tables.items():
        columns = ", ".join(f"{col} ({dtype})" for col, dtype in table.dtypes.items())
        lines.append(f"- {name}: {columns}")
    return "\n".join(lines)


def build_prompt(request: str, language: str, tables: dict[str, pd.DataFrame]) -> str:
    return (
        f"Language: {language}\n"
        f"Rules: {RULES[language]}\n\n"
        f"Metadata (name: column (type)):\n{describe_tables(tables)}\n\n"
        f"User request (in Italian):\n{request.strip()}"
    )


def clean_code(text: str) -> str:
    """Estrae solo il contenuto del primo blocco ```...```; senza blocco usa tutto il testo."""
    match = re.search(r"```(?:\w+)?\n(.*?)```", text, re.DOTALL)
    return (match.group(1) if match else text).strip()


def validate_code(code: str, language: str) -> None:
    """Controlla (senza eseguire) che il testo sia codice sintatticamente valido."""
    try:
        if not code:
            raise ValueError("risposta vuota")
        if language == "SQL":
            with duckdb.connect() as connection:
                connection.extract_statements(code)
        else:
            ast.parse(code)
    except (ValueError, SyntaxError, duckdb.Error) as error:
        raise ValueError("La risposta dell'AI non è codice valido: riprova o riformula la richiesta.") from error


def generate_code(api_key: str, request: str, language: str, tables: dict[str, pd.DataFrame],
                  model: str = DEFAULT_MODEL, effort: str = DEFAULT_EFFORT) -> str:
    options = {} if effort == DEFAULT_EFFORT else {"output_config": {"effort": effort}}
    client = anthropic.Anthropic(api_key=api_key)
    response = client.messages.create(
        model=model,
        max_tokens=MAX_TOKENS,
        system=SYSTEM_PROMPT,
        messages=[{"role": "user", "content": build_prompt(request, language, tables)}],
        **options,
    )
    text = "".join(block.text for block in response.content if block.type == "text")
    code = clean_code(text)
    validate_code(code, language)
    return code
