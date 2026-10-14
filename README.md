# Data Explorer

Strumento locale di analisi dati: Python + NiceGUI + pandas + DuckDB. Nessun servizio esterno.

## Installazione e avvio
    pip install -r requirements.txt
    python app.py                          # carichi i file dall'interfaccia (+)
    python app.py vendite.parquet clienti.csv
    python app.py --port 8123 vendite.parquet

Da Jupyter:
    from launcher import esplora
    esplora(vendite=df1, clienti=df2)      # i nomi diventano le tabelle SQL

## Cosa fa
- **Dati**: filtri AND/OR annidati (gruppi dentro gruppi), anteprima, struttura colonne.
- **Group By**: più chiavi, più aggregazioni (somma, media, mediana, varianza, range, quartili, moda, nulli, ...), filtri applicati prima.
  Modalità **Value counts**: conteggio e percentuale dei valori per gruppo.
- **Pivot**: righe, colonne, più valori, totali, filtri applicati prima.
- **Console**: SQL (DuckDB) o Python. Ctrl+Invio esegue.
  - SQL: ogni dataset è una tabella col suo nome; `df` = dataset attivo.
  - Python: `df`, `datasets`, `pd`, `np`, `sql("SELECT ...")`. L'ultima espressione viene mostrata come in Jupyter (l'indice di DataFrame e Series è visibile come prima colonna).
  - **Assistente AI**: scrivi la richiesta in italiano e Claude genera il codice (SQL o Python) nell'editor, senza eseguirlo. Inserisci la tua API key Anthropic nella casella e scegli modello (Fable, Opus, Sonnet, Haiku) e sforzo (basso … massimo). All'API vengono inviati solo la richiesta e i metadati (nomi tabelle/colonne e tipi), mai i valori dei dati.
- **Sotto ogni risultato**: scarica Excel, scarica CSV (formato italiano `;` e `,`), **Apri in Excel**, **Salva come dataset**
  (il risultato di un group by/pivot/query diventa un dataset da rifiltrare o interrogare).

## Operatori filtro
= ≠ > < >= <= · contiene · non contiene · inizia con · finisce con · in lista · non in lista · tra · regex · è nullo · non è nullo

## Note
- In tabella si vedono al massimo 10.000 righe; l'export contiene sempre tutto.
- "Apri in Excel" salva un .xlsx temporaneo e lo apre con l'app predefinita (richiede Excel sul PC dove gira lo strumento).
- Struttura: `engine.py` (logica, senza UI), `app.py` (interfaccia), `style.py` (aspetto grafico), `ai_assistant.py` (generazione codice con Claude), `launcher.py` (avvio da Jupyter).
- Test: `python -m pytest -q`
