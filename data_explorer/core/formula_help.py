"""
Help for writing formulas (no UI dependency).

  - column names ready to insert in a formula (in quotes when needed)
  - readable type of each column
  - guide to the DuckDB functions: for each one an explanation, arguments,
    warnings and examples, with the results calculated by DuckDB itself
"""
from __future__ import annotations

import datetime as dt
import re
from decimal import Decimal
from functools import cache
from typing import NamedTuple

import duckdb
import numpy as np
import pandas as pd

from data_explorer.core import columns
# ---------------------------------------------------------------------------
# Column names and types
# ---------------------------------------------------------------------------

PLAIN_NAME = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")


@cache
def reserved_words() -> frozenset[str]:
    """DuckDB reserved words: as column names they must be in quotes."""
    with duckdb.connect() as connection:
        rows = connection.execute(
            "SELECT keyword_name FROM duckdb_keywords() WHERE keyword_category = 'reserved'").fetchall()
    return frozenset(word for (word,) in rows)


def quote_column(name: str) -> str:
    """Name usable in a formula: in quotes if it contains spaces, dots, accents or is a reserved word."""
    if PLAIN_NAME.fullmatch(name) and name.lower() not in reserved_words():
        return name
    escaped = name.replace('"', '""')
    return f'"{escaped}"'


def column_kind(dtype) -> str:
    """Readable type: booleano, numero, data or testo."""
    if pd.api.types.is_bool_dtype(dtype):
        return "booleano"
    if pd.api.types.is_numeric_dtype(dtype):
        return "numero"
    if pd.api.types.is_datetime64_any_dtype(dtype):
        return "data"
    return "testo"


# ---------------------------------------------------------------------------
# Guide: structure
# ---------------------------------------------------------------------------
# In the syntax, lower-case words outside quotes are placeholders: each one is explained in `arguments`.

class Function(NamedTuple):
    syntax: str
    summary: str                           # one line, shown in the list
    details: str                           # full explanation
    arguments: tuple[tuple[str, str], ...]  # (placeholder, meaning)
    examples: tuple[str, ...]              # formulas calculated on the sample table SAMPLE
    notes: str = ""                        # warnings and typical errors


SAMPLE = pd.DataFrame({
    "regione": ["Nord", "Nord", "Sud", "Nord", "Sud"],
    "data": pd.to_datetime(["2024-01-15", "2024-02-10", "2024-01-20", "2024-03-05", "2024-02-25"]),
    "importo": [100.0, 300.0, 200.0, 400.0, 250.0],
})

OVER_EXPLAINED = (
    " OVER indica su quali righe calcolare: OVER () tutte le righe, OVER (PARTITION BY gruppo) "
    "le righe con lo stesso valore di gruppo, ORDER BY dentro OVER rende il calcolo progressivo. "
    "Ogni riga mantiene il proprio valore e riceve accanto il risultato."
)

# ---------------------------------------------------------------------------
# Guide: content
# ---------------------------------------------------------------------------

TEXT = [
    Function(
        "LENGTH(testo)", "Numero di caratteri",
        "Conta i caratteri del testo, spazi compresi. Se il testo è nullo il risultato è nullo.",
        (("testo", "testo tra apici o colonna di testo"),),
        ("LENGTH('Milano')", "LENGTH(' a b ')", "LENGTH(CAST(12345 AS VARCHAR))"),
        notes="Accetta solo testo: su una colonna numerica (es. una matricola) converti prima, "
              "LENGTH(CAST(colonna AS VARCHAR)). LEN è un sinonimo.",
    ),
    Function(
        "UPPER(testo)", "Tutto maiuscolo",
        "Converte tutte le lettere in maiuscolo; numeri e simboli restano invariati.",
        (("testo", "testo o colonna di testo"),),
        ("UPPER('Rossi srl')",),
    ),
    Function(
        "LOWER(testo)", "Tutto minuscolo",
        "Converte tutte le lettere in minuscolo. Utile per confronti che ignorano maiuscole e minuscole.",
        (("testo", "testo o colonna di testo"),),
        ("LOWER('ROSSI Srl')",),
    ),
    Function(
        "TRIM(testo)", "Toglie gli spazi all'inizio e alla fine",
        "Elimina gli spazi iniziali e finali, tipici dei dati esportati da gestionali.",
        (("testo", "testo o colonna di testo"),),
        ("TRIM('   Rossi  ')",),
        notes="A differenza di Excel non riduce gli spazi doppi interni: per quelli usa "
              "REGEXP_REPLACE(testo, ' +', ' ', 'g'). LTRIM e RTRIM tolgono solo a sinistra o a destra.",
    ),
    Function(
        "LEFT(testo, caratteri)", "Primi caratteri a sinistra",
        "Restituisce i primi caratteri del testo. Se il testo è più corto lo restituisce intero.",
        (("testo", "testo o colonna di testo"), ("caratteri", "quanti caratteri prendere")),
        ("LEFT('RSSMRA80A01H501U', 6)",),
    ),
    Function(
        "RIGHT(testo, caratteri)", "Ultimi caratteri a destra",
        "Restituisce gli ultimi caratteri del testo.",
        (("testo", "testo o colonna di testo"), ("caratteri", "quanti caratteri prendere")),
        ("RIGHT('IT60X0542811101000000123456', 6)",),
    ),
    Function(
        "SUBSTRING(testo, inizio, caratteri)", "Parte centrale del testo",
        "Estrae una parte del testo partendo da una posizione. Il primo carattere è in posizione 1.",
        (("testo", "testo o colonna di testo"), ("inizio", "posizione del primo carattere da prendere"),
         ("caratteri", "quanti caratteri prendere (se ometti, fino alla fine)")),
        ("SUBSTRING('RSSMRA80A01H501U', 7, 2)", "SUBSTRING('Milano', 3)"),
    ),
    Function(
        "REPLACE(testo, cerca, sostituisci)", "Sostituisce un testo con un altro",
        "Sostituisce tutte le occorrenze del testo cercato. Distingue maiuscole e minuscole.",
        (("testo", "testo o colonna in cui cercare"), ("cerca", "testo da trovare, tra apici"),
         ("sostituisci", "testo da mettere al suo posto, tra apici ('' per cancellare)")),
        ("REPLACE('Via Roma, 1', 'Via', 'V.')", "REPLACE('12.345,67', '.', '')"),
    ),
    Function(
        "CONCAT(valore, altro, ...)", "Unisce più valori in un testo",
        "Unisce in un unico testo tutti i valori indicati, nell'ordine. I numeri vengono convertiti in testo.",
        (("valore", "primo valore (testo, numero, colonna)"), ("altro", "valori successivi, quanti ne servono")),
        ("CONCAT('Rossi', ' ', 'Mario')", "CONCAT('Lotto ', 42)", "CONCAT('a', NULL, 'b')"),
        notes="I valori nulli vengono saltati: è la scelta giusta con colonne che possono essere vuote.",
    ),
    Function(
        "testo || altro", "Unisce due testi",
        "Operatore di concatenazione, come & in Excel. Si può ripetere: a || ' ' || b.",
        (("testo", "primo testo"), ("altro", "testo da accodare")),
        ("'Rossi' || ' ' || 'Mario'", "'Rossi' || NULL"),
        notes="Se uno dei due è nullo il risultato è nullo: con colonne che possono essere vuote usa CONCAT.",
    ),
    Function(
        "CONTAINS(testo, cerca)", "Vero se il testo contiene un altro testo",
        "Restituisce vero se il testo cercato compare nel testo, altrimenti falso.",
        (("testo", "testo o colonna in cui cercare"), ("cerca", "testo da trovare, tra apici")),
        ("CONTAINS('Milano Nord', 'Nord')", "CONTAINS('Milano Nord', 'nord')"),
        notes="Distingue maiuscole e minuscole: per ignorarle usa CONTAINS(LOWER(testo), 'nord') "
              "oppure testo ILIKE '%nord%'.",
    ),
    Function(
        "STRPOS(testo, cerca)", "Posizione di un testo dentro un altro",
        "Restituisce la posizione (da 1) della prima occorrenza del testo cercato; 0 se non c'è.",
        (("testo", "testo o colonna in cui cercare"), ("cerca", "testo da trovare, tra apici")),
        ("STRPOS('Rossi Mario', ' ')", "STRPOS('Rossi', 'z')"),
        notes="L'ordine degli argomenti è opposto a Excel: prima il testo in cui cercare, poi quello cercato. "
              "Distingue maiuscole e minuscole e restituisce 0 invece di un errore.",
    ),
    Function(
        "SPLIT_PART(testo, separatore, parte)", "Una parte di un testo separato",
        "Divide il testo dove trova il separatore e restituisce la parte indicata (la prima è 1).",
        (("testo", "testo o colonna da dividere"), ("separatore", "carattere che separa le parti, tra apici"),
         ("parte", "numero della parte da restituire")),
        ("SPLIT_PART('Rossi;Mario;Roma', ';', 3)", "SPLIT_PART('2024-03-15', '-', 1)", "SPLIT_PART('a;b', ';', 5)"),
        notes="Se la parte non esiste il risultato è un testo vuoto, non nullo.",
    ),
    Function(
        "LPAD(testo, caratteri, riempimento)", "Completa a sinistra fino a una lunghezza",
        "Aggiunge il carattere di riempimento a sinistra finché il testo raggiunge la lunghezza indicata. "
        "Tipico per codici con zeri iniziali. RPAD fa lo stesso a destra.",
        (("testo", "testo o colonna di testo"), ("caratteri", "lunghezza finale"),
         ("riempimento", "carattere da aggiungere, tra apici")),
        ("LPAD('42', 5, '0')", "LPAD(CAST(7 AS VARCHAR), 3, '0')", "LPAD('123456', 3, '0')"),
        notes="Se il testo è già più lungo viene tagliato. Su un numero converti prima: CAST(numero AS VARCHAR).",
    ),
    Function(
        "REGEXP_EXTRACT(testo, regex)", "Estrae la parte che corrisponde a un modello",
        "Restituisce la prima parte del testo che corrisponde all'espressione regolare, testo vuoto se non ce n'è. "
        "Esempi di modelli: [0-9]+ una sequenza di cifre, [A-Z]{2} due lettere maiuscole.",
        (("testo", "testo o colonna in cui cercare"), ("regex", "espressione regolare, tra apici")),
        ("REGEXP_EXTRACT('Ordine n. 4521 del 2024', '[0-9]+')", "REGEXP_EXTRACT('Targa AB123CD', '[A-Z]{2}[0-9]{3}[A-Z]{2}')"),
    ),
    Function(
        "REGEXP_REPLACE(testo, regex, sostituisci, 'g')", "Sostituisce le parti che corrispondono a un modello",
        "Sostituisce le parti del testo che corrispondono all'espressione regolare. "
        "Il quarto argomento 'g' sostituisce tutte le occorrenze (senza, solo la prima).",
        (("testo", "testo o colonna da modificare"), ("regex", "espressione regolare, tra apici"),
         ("sostituisci", "testo da mettere al suo posto, tra apici")),
        ("REGEXP_REPLACE('Mario   Rossi', ' +', ' ', 'g')", "REGEXP_REPLACE('Tel. 041-123 456', '[^0-9]', '', 'g')"),
    ),
    Function(
        "CAST(valore AS VARCHAR)", "Converte un numero o una data in testo",
        "Trasforma qualsiasi valore in testo, ad esempio per usarlo con le funzioni di testo.",
        (("valore", "numero, data o colonna da convertire"),),
        ("CAST(12.5 AS VARCHAR)", "CAST(DATE '2024-03-15' AS VARCHAR)"),
        notes="Le date diventano testo nel formato 2024-03-15: per un formato diverso usa STRFTIME.",
    ),
]

NUMBERS = [
    Function(
        "ROUND(numero, decimali)", "Arrotonda",
        "Arrotonda al numero di decimali indicato; da 5 in su arrotonda per eccesso.",
        (("numero", "numero o colonna numerica"),
         ("decimali", "cifre decimali da tenere (0 = intero, negativo = decine, centinaia, ...)")),
        ("ROUND(3.14159, 2)", "ROUND(2.5)", "ROUND(1234.5, -2)"),
    ),
    Function(
        "FLOOR(numero)", "Arrotonda per difetto all'intero",
        "Restituisce l'intero immediatamente inferiore o uguale.",
        (("numero", "numero o colonna numerica"),),
        ("FLOOR(3.7)", "FLOOR(-3.7)"),
        notes="Con i negativi va verso il basso (-3.7 diventa -4): per togliere i decimali senza arrotondare usa TRUNC.",
    ),
    Function(
        "CEIL(numero)", "Arrotonda per eccesso all'intero",
        "Restituisce l'intero immediatamente superiore o uguale.",
        (("numero", "numero o colonna numerica"),),
        ("CEIL(3.2)", "CEIL(-3.2)"),
    ),
    Function(
        "TRUNC(numero)", "Toglie i decimali",
        "Elimina la parte decimale senza arrotondare.",
        (("numero", "numero o colonna numerica"),),
        ("TRUNC(3.7)", "TRUNC(-3.7)"),
    ),
    Function(
        "ABS(numero)", "Valore assoluto",
        "Restituisce il numero senza segno. Utile per calcolare scostamenti.",
        (("numero", "numero o colonna numerica"),),
        ("ABS(-250)", "ABS(980 - 1000)"),
    ),
    Function(
        "numero // divisore", "Divisione intera",
        "Quoziente della divisione tra numeri interi, senza decimali.",
        (("numero", "dividendo intero"), ("divisore", "divisore intero")),
        ("17 // 5",),
        notes="Vale per numeri interi: con numeri decimali usa FLOOR(numero / divisore).",
    ),
    Function(
        "numero % divisore", "Resto della divisione",
        "Resto della divisione intera. Esempio tipico: numero % 2 = 0 per i numeri pari.",
        (("numero", "dividendo"), ("divisore", "divisore")),
        ("17 % 5", "-7 % 3"),
        notes="Con i negativi il segno segue il dividendo: -7 % 3 dà -1, mentre in Excel RESTO(-7; 3) dà 2.",
    ),
    Function(
        "POWER(numero, esponente)", "Potenza",
        "Eleva il numero all'esponente indicato.",
        (("numero", "base"), ("esponente", "esponente (0.5 = radice quadrata)")),
        ("POWER(2, 10)", "POWER(1.05, 3)"),
    ),
    Function(
        "SQRT(numero)", "Radice quadrata",
        "Radice quadrata del numero.",
        (("numero", "numero o colonna numerica, non negativo"),),
        ("SQRT(16)",),
    ),
    Function(
        "GREATEST(valore, altro, ...)", "Il maggiore tra più valori della stessa riga",
        "Confronta più valori della stessa riga e restituisce il maggiore; i nulli vengono ignorati.",
        (("valore", "primo valore o colonna"), ("altro", "altri valori o colonne")),
        ("GREATEST(3, 10, 7)", "GREATEST(1, NULL, 3)"),
        notes="Lavora sulla singola riga. Per il massimo di una colonna su più righe usa MAX(colonna) OVER (...).",
    ),
    Function(
        "LEAST(valore, altro, ...)", "Il minore tra più valori della stessa riga",
        "Confronta più valori della stessa riga e restituisce il minore; i nulli vengono ignorati.",
        (("valore", "primo valore o colonna"), ("altro", "altri valori o colonne")),
        ("LEAST(3, 10, 7)",),
    ),
    Function(
        "CAST(testo AS DOUBLE)", "Converte un testo in numero",
        "Trasforma un testo in numero decimale; AS INTEGER per un numero intero. "
        "Il separatore decimale deve essere il punto.",
        (("testo", "testo o colonna da convertire"),),
        ("CAST('12.5' AS DOUBLE)", "CAST(REPLACE('12,5', ',', '.') AS DOUBLE)"),
        notes="Se anche un solo valore non è convertibile (es. '12,5' o 'n.d.') l'intera formula va in errore: "
              "usa TRY_CAST. Per numeri all'italiana (1.234,56) usa la modalità «Cambio formato».",
    ),
    Function(
        "TRY_CAST(testo AS DOUBLE)", "Converte in numero, nullo se non valido",
        "Come CAST, ma i valori non convertibili diventano nulli invece di bloccare la formula.",
        (("testo", "testo o colonna da convertire"),),
        ("TRY_CAST('12.5' AS DOUBLE)", "TRY_CAST('n.d.' AS DOUBLE)"),
    ),
]

DATES = [
    Function(
        "YEAR(data)", "Anno",
        "Restituisce l'anno della data come numero.",
        (("data", "data o colonna di tipo data"),),
        ("YEAR(DATE '2024-03-15')", "YEAR(data)"),
        notes="La colonna deve essere di tipo data: se è testo convertila prima (STRPTIME o «Cambio formato»).",
    ),
    Function(
        "QUARTER(data)", "Trimestre (1-4)",
        "Restituisce il trimestre dell'anno: 1 per gennaio-marzo, 4 per ottobre-dicembre.",
        (("data", "data o colonna di tipo data"),),
        ("QUARTER(DATE '2024-08-15')",),
    ),
    Function(
        "MONTH(data)", "Mese (1-12)",
        "Restituisce il mese della data come numero.",
        (("data", "data o colonna di tipo data"),),
        ("MONTH(DATE '2024-03-15')",),
    ),
    Function(
        "DAY(data)", "Giorno del mese",
        "Restituisce il giorno del mese (1-31).",
        (("data", "data o colonna di tipo data"),),
        ("DAY(DATE '2024-03-15')",),
    ),
    Function(
        "ISODOW(data)", "Giorno della settimana (1 = lunedì)",
        "Restituisce il giorno della settimana: 1 lunedì, 7 domenica.",
        (("data", "data o colonna di tipo data"),),
        ("ISODOW(DATE '2024-03-15')",),
        notes="DAYNAME(data) restituisce il nome del giorno, in inglese.",
    ),
    Function(
        "DATE_TRUNC(periodo, data)", "Inizio del periodo (mese, trimestre, anno)",
        "Riporta la data al primo giorno del periodo indicato. Utile per raggruppare per mese "
        "mantenendo una data vera, che si ordina correttamente.",
        (("periodo", "'year', 'quarter', 'month', 'week' o 'day', tra apici"), ("data", "data o colonna di tipo data")),
        ("DATE_TRUNC('month', DATE '2024-03-15')", "DATE_TRUNC('quarter', DATE '2024-03-15')"),
    ),
    Function(
        "LAST_DAY(data)", "Ultimo giorno del mese",
        "Restituisce l'ultimo giorno del mese della data, anni bisestili compresi.",
        (("data", "data o colonna di tipo data"),),
        ("LAST_DAY(DATE '2024-02-10')",),
    ),
    Function(
        "STRFTIME(data, formato)", "Data in testo con un formato a scelta",
        "Converte la data in testo secondo il formato. Codici: %d giorno, %m mese, %Y anno, "
        "%H ore, %M minuti, %B nome del mese (in inglese).",
        (("data", "data o colonna di tipo data"), ("formato", "formato tra apici, es. '%d/%m/%Y'")),
        ("STRFTIME(DATE '2024-03-15', '%d/%m/%Y')", "STRFTIME(DATE '2024-03-15', '%Y-%m')"),
    ),
    Function(
        "STRPTIME(testo, formato)", "Testo in data secondo un formato",
        "Interpreta il testo come data secondo il formato indicato (stessi codici di STRFTIME).",
        (("testo", "testo o colonna da convertire"), ("formato", "formato del testo tra apici, es. '%d/%m/%Y'")),
        ("STRPTIME('15/03/2024', '%d/%m/%Y')",),
        notes="Se anche un solo valore non rispetta il formato la formula va in errore: usa TRY_STRPTIME.",
    ),
    Function(
        "TRY_STRPTIME(testo, formato)", "Testo in data, nullo se non valido",
        "Come STRPTIME, ma i testi che non rispettano il formato diventano nulli.",
        (("testo", "testo o colonna da convertire"), ("formato", "formato del testo tra apici")),
        ("TRY_STRPTIME('15/03/2024', '%d/%m/%Y')", "TRY_STRPTIME('n.d.', '%d/%m/%Y')"),
    ),
    Function(
        "MAKE_DATE(anno, mese, giorno)", "Costruisce una data da anno, mese e giorno",
        "Crea una data a partire da tre numeri, ad esempio da colonne separate.",
        (("anno", "anno (numero)"), ("mese", "mese 1-12"), ("giorno", "giorno 1-31")),
        ("MAKE_DATE(2024, 3, 15)",),
    ),
    Function(
        "DATE_DIFF(periodo, inizio, fine)", "Differenza tra due date",
        "Conta quanti confini di periodo ci sono tra le due date: giorni, mesi o anni ('day', 'month', 'year').",
        (("periodo", "'day', 'week', 'month' o 'year', tra apici"), ("inizio", "data iniziale"),
         ("fine", "data finale (CURRENT_DATE per oggi)")),
        ("DATE_DIFF('day', DATE '2024-01-01', DATE '2024-03-01')",
         "DATE_DIFF('month', DATE '2024-01-31', DATE '2024-02-01')",
         "DATE_DIFF('year', DATE '2020-06-15', DATE '2024-03-01')"),
        notes="Conta i cambi di mese o anno, non i periodi compiuti: tra 31/01 e 01/02 dà 1 mese. "
              "Per gli anni compiuti (età, anzianità) usa DATE_SUB('year', inizio, fine).",
    ),
    Function(
        "DATE_SUB(periodo, inizio, fine)", "Periodi compiuti tra due date",
        "Conta i periodi interi trascorsi tra le due date, come per calcolare età o anzianità.",
        (("periodo", "'day', 'month' o 'year', tra apici"), ("inizio", "data iniziale"), ("fine", "data finale")),
        ("DATE_SUB('year', DATE '2020-06-15', DATE '2024-03-01')",),
    ),
    Function(
        "data + INTERVAL (numero) DAY", "Aggiunge giorni, mesi o anni",
        "Somma un intervallo alla data. Al posto di DAY puoi usare MONTH o YEAR; con il segno - sottrai. "
        "Aggiungendo mesi, se il giorno non esiste si va all'ultimo del mese.",
        (("data", "data o colonna di tipo data"), ("numero", "quanti giorni (o mesi, anni): numero o colonna")),
        ("DATE '2024-03-15' + INTERVAL (10) DAY", "DATE '2024-01-31' + INTERVAL (1) MONTH"),
        notes="Il risultato contiene anche l'ora (00:00): per avere solo la data usa CAST(... AS DATE).",
    ),
    Function(
        "CURRENT_DATE", "Data di oggi",
        "La data odierna. Si scrive senza parentesi. Uso tipico: DATE_DIFF('day', data, CURRENT_DATE) "
        "per i giorni trascorsi.",
        (),
        ("CURRENT_DATE",),
    ),
]

LOGIC = [
    Function(
        "IF(condizione, se_vero, se_falso)", "Un valore se la condizione è vera, un altro se no",
        "Valuta la condizione su ogni riga e restituisce il primo valore se è vera, il secondo se è falsa o nulla.",
        (("condizione", "confronto, es. importo > 1000"), ("se_vero", "valore se vera"), ("se_falso", "valore se falsa")),
        ("IF(1500 > 1000, 'alto', 'basso')", "IF(importo > 250, 'alto', 'basso')"),
        notes="I due valori devono essere dello stesso tipo: IF(..., 'alto', 0) va in errore. Per più casi usa CASE WHEN.",
    ),
    Function(
        "CASE WHEN condizione THEN valore ELSE altro END", "Più condizioni in ordine",
        "Valuta le condizioni in ordine e restituisce il valore della prima vera. "
        "Puoi ripetere WHEN ... THEN ... quante volte vuoi. Senza ELSE, se nessuna è vera il risultato è nullo.",
        (("condizione", "confronto da verificare"), ("valore", "risultato se la condizione è vera"),
         ("altro", "risultato se nessuna condizione è vera")),
        ("CASE WHEN importo >= 300 THEN 'alto' WHEN importo >= 200 THEN 'medio' ELSE 'basso' END",),
    ),
    Function(
        "COALESCE(valore, sostituto, ...)", "Primo valore non nullo",
        "Restituisce il primo valore non nullo tra quelli indicati. Tipico per sostituire i vuoti.",
        (("valore", "valore o colonna che può essere nulla"), ("sostituto", "valore da usare se è nulla")),
        ("COALESCE(NULL, 'nessuna')", "COALESCE(NULL, NULL, 3)"),
    ),
    Function(
        "NULLIF(valore, confronto)", "Nullo se il valore è uguale al confronto",
        "Restituisce nullo quando il valore è uguale al secondo argomento, altrimenti il valore. "
        "Uso tipico: evitare divisioni per zero, importo / NULLIF(qta, 0).",
        (("valore", "valore o colonna"), ("confronto", "valore da trasformare in nullo")),
        ("100 / NULLIF(0, 0)", "NULLIF('n.d.', 'n.d.')"),
    ),
    Function(
        "valore IS NULL", "Vero se il valore è nullo",
        "Restituisce vero se il valore è vuoto (nullo). IS NOT NULL fa il contrario.",
        (("valore", "valore o colonna da controllare"),),
        ("NULL IS NULL", "'' IS NULL"),
        notes="Un testo vuoto '' non è nullo: per trattarli allo stesso modo usa COALESCE(valore, '') = ''. "
              "Non usare valore = NULL: dà sempre nullo.",
    ),
    Function(
        "valore IN ('a', 'b')", "Vero se il valore è in un elenco",
        "Restituisce vero se il valore è uguale a uno di quelli dell'elenco. NOT IN per escludere.",
        (("valore", "valore o colonna da controllare"),),
        ("'Nord' IN ('Nord', 'Sud')", "regione NOT IN ('Nord')"),
    ),
    Function(
        "valore BETWEEN minimo AND massimo", "Vero se compreso tra due estremi",
        "Restituisce vero se il valore è maggiore o uguale al minimo e minore o uguale al massimo.",
        (("valore", "valore o colonna da controllare"), ("minimo", "estremo inferiore (incluso)"),
         ("massimo", "estremo superiore (incluso)")),
        ("5 BETWEEN 1 AND 10", "data BETWEEN DATE '2024-01-01' AND DATE '2024-01-31'"),
    ),
    Function(
        "testo ILIKE modello", "Confronto con un modello (* di Excel)",
        "Vero se il testo corrisponde al modello: % indica qualsiasi sequenza di caratteri, _ un solo carattere. "
        "ILIKE ignora maiuscole e minuscole, LIKE le distingue.",
        (("testo", "testo o colonna da controllare"), ("modello", "modello tra apici, es. '%nord%'")),
        ("'Milano Nord' ILIKE '%nord%'", "'Rossi' LIKE 'R_ssi'"),
    ),
    Function(
        "condizione AND altra", "Combina condizioni (E, O, NON)",
        "AND è vera se lo sono entrambe, OR se almeno una, NOT inverte. Usa le parentesi per chiarire l'ordine: "
        "a AND (b OR c).",
        (("condizione", "primo confronto"), ("altra", "secondo confronto")),
        ("5 > 1 AND 5 < 10", "regione = 'Nord' OR importo > 300", "NOT (5 > 1)"),
    ),
]

WINDOWS = [
    Function(
        "SUM(numero) OVER ()", "Totale di tutte le righe",
        "Somma la colonna su tutte le righe e mostra il totale su ognuna; diviso per il totale dà la quota." + OVER_EXPLAINED,
        (("numero", "colonna numerica da sommare"),),
        ("SUM(importo) OVER ()", "importo / SUM(importo) OVER ()"),
    ),
    Function(
        "SUM(numero) OVER (PARTITION BY gruppo)", "Totale del gruppo della riga",
        "Somma la colonna sulle righe dello stesso gruppo." + OVER_EXPLAINED,
        (("numero", "colonna numerica da sommare"), ("gruppo", "colonna (o più colonne separate da virgola) che definisce il gruppo")),
        ("SUM(importo) OVER (PARTITION BY regione)", "importo / SUM(importo) OVER (PARTITION BY regione)"),
    ),
    Function(
        "AVG(numero) OVER (PARTITION BY gruppo)", "Media del gruppo della riga",
        "Media della colonna sulle righe dello stesso gruppo; utile per gli scostamenti dalla media." + OVER_EXPLAINED,
        (("numero", "colonna numerica"), ("gruppo", "colonna che definisce il gruppo")),
        ("AVG(importo) OVER (PARTITION BY regione)", "importo - AVG(importo) OVER (PARTITION BY regione)"),
    ),
    Function(
        "MAX(valore) OVER (PARTITION BY gruppo)", "Massimo del gruppo (MIN per il minimo)",
        "Valore massimo della colonna nel gruppo; MIN per il minimo. Funziona anche con date e testi." + OVER_EXPLAINED,
        (("valore", "colonna da confrontare"), ("gruppo", "colonna che definisce il gruppo")),
        ("MAX(data) OVER (PARTITION BY regione)", "importo = MAX(importo) OVER (PARTITION BY regione)"),
    ),
    Function(
        "COUNT(*) OVER (PARTITION BY gruppo)", "Numero di righe del gruppo",
        "Conta le righe del gruppo. COUNT(colonna) conta solo i valori non nulli." + OVER_EXPLAINED,
        (("gruppo", "colonna che definisce il gruppo"),),
        ("COUNT(*) OVER (PARTITION BY regione)",),
    ),
    Function(
        "ROW_NUMBER() OVER (PARTITION BY gruppo ORDER BY ordine)", "Numero progressivo nel gruppo",
        "Numera le righe da 1 dentro ogni gruppo, nell'ordine indicato (DESC per decrescente). "
        "Senza PARTITION BY numera tutte le righe." + OVER_EXPLAINED,
        (("gruppo", "colonna che definisce il gruppo"), ("ordine", "colonna che stabilisce l'ordine")),
        ("ROW_NUMBER() OVER (PARTITION BY regione ORDER BY data)",),
    ),
    Function(
        "RANK() OVER (ORDER BY numero DESC)", "Posizione in classifica",
        "Posizione della riga in classifica. A parità di valore stessa posizione e la successiva salta "
        "(1, 1, 3); DENSE_RANK non salta (1, 1, 2)." + OVER_EXPLAINED,
        (("numero", "colonna su cui fare la classifica"),),
        ("RANK() OVER (ORDER BY importo DESC)", "RANK() OVER (PARTITION BY regione ORDER BY importo DESC)"),
    ),
    Function(
        "SUM(numero) OVER (ORDER BY ordine)", "Totale progressivo (cumulato)",
        "Somma la colonna dalla prima riga fino a quella corrente, nell'ordine indicato. "
        "Con PARTITION BY il progressivo riparte per ogni gruppo." + OVER_EXPLAINED,
        (("numero", "colonna da sommare"), ("ordine", "colonna che stabilisce l'ordine, es. una data")),
        ("SUM(importo) OVER (ORDER BY data)", "SUM(importo) OVER (PARTITION BY regione ORDER BY data)"),
    ),
    Function(
        "LAG(valore) OVER (ORDER BY ordine)", "Valore della riga precedente",
        "Restituisce il valore della riga precedente nell'ordine indicato; LEAD quello della successiva. "
        "La prima riga non ha precedente e dà nullo: LAG(valore, 1, 0) usa 0 al suo posto." + OVER_EXPLAINED,
        (("valore", "colonna da leggere"), ("ordine", "colonna che stabilisce l'ordine")),
        ("LAG(importo) OVER (ORDER BY data)", "importo - LAG(importo) OVER (PARTITION BY regione ORDER BY data)"),
    ),
]

QUOTED_TEXT = re.compile(r"'[^']*'")
FUNCTION_NAME = re.compile(r"[A-Z_]{2,}|\|\||//|%")  # upper-case keyword or operator


def alphabetical(functions: list[Function]) -> list[Function]:
    """Functions in alphabetical order by name (operators come first), then by syntax."""
    def sort_key(function: Function) -> tuple[bool, str, str]:
        match = FUNCTION_NAME.search(QUOTED_TEXT.sub("", function.syntax))
        name = (match.group() if match else function.syntax).lower()
        return name[0].isalpha(), name, function.syntax.lower()
    return sorted(functions, key=sort_key)


GUIDE: dict[str, list[Function]] = {
    "Testo": alphabetical(TEXT),
    "Numeri": alphabetical(NUMBERS),
    "Date": alphabetical(DATES),
    "Logica": alphabetical(LOGIC),
    "Totali e gruppi": alphabetical(WINDOWS),
}

# ---------------------------------------------------------------------------
# Guide: usage
# ---------------------------------------------------------------------------

PLACEHOLDER = re.compile(r"'[^']*'|\b([a-z][a-z_]*)\b")  # texts in quotes are not placeholders


def placeholders(syntax: str) -> list[tuple[int, int]]:
    """Positions (start, end) of the placeholders in the syntax."""
    return [match.span(1) for match in PLACEHOLDER.finditer(syntax) if match.group(1)]


def first_placeholder(syntax: str) -> tuple[int, int]:
    """Start and end of the first placeholder; if there are none, the end of the text."""
    spans = placeholders(syntax)
    return spans[0] if spans else (len(syntax), len(syntax))


def search_text(function: Function) -> str:
    """Text in which to search for a function: syntax and description."""
    return f"{function.syntax} {function.summary}".lower()


def format_value(value, quote_text: bool = True) -> str:
    """Readable value: texts in quotes, NULL for nulls, dates without time if it is midnight."""
    if isinstance(value, str):
        return f"'{value}'" if quote_text else value
    if value is None or pd.isna(value):
        return "NULL"
    if isinstance(value, (bool, np.bool_)):
        return "vero" if value else "falso"
    if isinstance(value, (dt.date, pd.Timestamp)):
        stamp = pd.Timestamp(value)
        return f"{stamp:%Y-%m-%d}" if stamp == stamp.normalize() else f"{stamp:%Y-%m-%d %H:%M}"
    if isinstance(value, (float, Decimal)):
        number = float(value)
        return str(int(number)) if number.is_integer() else f"{number:.4f}".rstrip("0")
    return str(value)


@cache
def example_result(example: str) -> str | pd.DataFrame:
    """Result of an example calculated on SAMPLE: one value if it is the same for all the rows,
    otherwise the sample table with the «risultato» column."""
    with duckdb.connect() as connection:
        connection.register("df", SAMPLE)
        values = connection.execute(columns.formula_query(example)).df().iloc[:, 0]
    if values.astype(str).nunique(dropna=False) == 1:
        return format_value(values.iloc[0])
    return SAMPLE.assign(risultato=values.to_numpy())
