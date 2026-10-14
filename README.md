# Data Explorer

Strumento **locale** per esplorare e analizzare dati tabellari dal browser, senza scrivere codice (ma potendolo fare quando serve).
È costruito con **Python**, **[NiceGUI](https://nicegui.io)** (interfaccia web), **pandas** e **DuckDB** (elaborazione).

I dati restano sul tuo computer: l'app gira in locale e non usa servizi esterni.
L'unica eccezione, facoltativa, è l'**Assistente AI**: invia a Claude soltanto la richiesta e la struttura delle tabelle (nomi e tipi delle colonne), **mai i valori**.

---

## Indice

1. [Funzionalità in breve](#funzionalità-in-breve)
2. [Requisiti](#requisiti)
3. [Installazione](#installazione)
4. [Avvio](#avvio)
5. [Guida all'uso](#guida-alluso)
6. [Riferimento: operatori dei filtri](#riferimento-operatori-dei-filtri)
7. [Riferimento: funzioni di aggregazione](#riferimento-funzioni-di-aggregazione)
8. [Esportazione](#esportazione)
9. [Assistente AI e privacy](#assistente-ai-e-privacy)
10. [Struttura del progetto](#struttura-del-progetto)
11. [Test](#test)
12. [Limiti e note](#limiti-e-note)
13. [Risoluzione dei problemi](#risoluzione-dei-problemi)

---

## Funzionalità in breve

| Scheda | Cosa permette di fare |
|---|---|
| **Dati** | Visualizzare il dataset, filtrarlo con condizioni **AND/OR annidate** e consultare la struttura delle colonne (tipo, valori nulli e distinti). |
| **Group By** | Raggruppare per una o più colonne e calcolare più aggregazioni insieme, oppure contare la frequenza dei valori (**Value counts**) con percentuali. |
| **Pivot** | Creare tabelle pivot con righe, colonne, più misure, totali e celle vuote a zero. |
| **Console** | Scrivere query **SQL** (DuckDB) o codice **Python** (pandas) sui dataset caricati, con un assistente AI facoltativo che genera il codice da una richiesta in italiano. |

Sotto ogni risultato è sempre possibile: **scaricare in Excel**, **scaricare in CSV** (formato italiano), **aprire direttamente in Excel** e **salvare il risultato come nuovo dataset** da rifiltrare o interrogare.

Si possono caricare **più dataset** insieme e passare dall'uno all'altro con il selettore in alto.

---

## Requisiti

- **Python 3.11 o superiore** (testato su 3.11, 3.12 e 3.13).
- Un browser moderno (Chrome, Edge, Firefox, Safari).
- Facoltativi:
  - **Microsoft Excel** (o un'altra applicazione associata ai file `.xlsx`) per il pulsante *Apri in Excel*;
  - una **API key Anthropic** per l'Assistente AI.

Dipendenze Python (in `requirements.txt`):

| Pacchetto | Uso |
|---|---|
| `nicegui` | Interfaccia web (server locale e componenti grafici). |
| `pandas`, `numpy` | Elaborazione dei dati: filtri, group by, pivot. |
| `duckdb` | Esecuzione delle query SQL sui DataFrame. |
| `pyarrow` | Lettura e scrittura dei file Parquet e Feather. |
| `openpyxl` | Lettura e scrittura dei file Excel. |
| `anthropic` | Assistente AI (Claude). |

---

## Installazione

```bash
git clone https://github.com/lorenzocorra1995-creator/Nicegui-Data-Analysis.git
cd Nicegui-Data-Analysis

# (consigliato) ambiente virtuale
python -m venv .venv
# Windows:      .venv\Scripts\activate
# macOS/Linux:  source .venv/bin/activate

pip install -r requirements.txt
```

Per eseguire anche i test usa `pip install -r requirements-dev.txt`, che aggiunge `pytest` e `pytest-asyncio`.

---

## Avvio

### Da riga di comando

```bash
python app.py                                  # avvia vuoto: carichi i file dall'interfaccia (+)
python app.py vendite.parquet clienti.csv      # carica subito uno o più file
python app.py --port 8123 vendite.parquet      # usa una porta diversa
python app.py --no-browser                     # non apre il browser in automatico
python app.py --help                           # elenco delle opzioni
```

| Opzione | Predefinito | Descrizione |
|---|---|---|
| `files` | nessuno | Percorsi dei file da caricare all'avvio. Il nome del file (senza estensione) diventa il nome del dataset. |
| `--port` | `8090` | Porta del server web locale. |
| `--no-browser` | disattivato | Avvia il server senza aprire il browser. |

All'avvio l'app apre il browser su `http://localhost:8090` (o sulla porta scelta). Per chiuderla premi `Ctrl+C` nel terminale.

### Da Jupyter

```python
from launcher import esplora

esplora(df)                          # un dataset, chiamato "df"
esplora(vendite=df1, clienti=df2)    # più dataset: i nomi diventano i nomi delle tabelle SQL
esplora(vendite=df1, porta=8123)     # porta diversa
```

`esplora()` salva i DataFrame in una cartella temporanea (in Parquet, oppure in pickle se il Parquet non è possibile, ad esempio con colonne a tipi misti) e avvia Data Explorer in un **processo separato**: il notebook resta libero e puoi lanciare `esplora()` più volte, usando porte diverse. La funzione restituisce il processo (`subprocess.Popen`), che puoi chiudere con `.terminate()`.

> Il notebook deve girare nella cartella del progetto, oppure la cartella va aggiunta a `sys.path`, così che `launcher` sia importabile.

### Formati supportati

| Formato | Estensioni | Note |
|---|---|---|
| CSV / testo | `.csv`, `.txt`, `.tsv` | Il separatore (`,` `;` tab …) viene riconosciuto automaticamente. |
| Excel | `.xlsx`, `.xlsm`, `.xls` | Viene letto il primo foglio. |
| Parquet | `.parquet`, `.pq` | Consigliato per file grandi. |
| Feather | `.feather` | |
| Pickle | `.pkl`, `.pickle` | Apri solo file di cui ti fidi (vedi [Limiti e note](#limiti-e-note)). |

---

## Guida all'uso

### Barra superiore

- **Dataset attivo**: selettore del dataset su cui lavorano tutte le schede.
- **＋ Carica dataset**: apre la finestra di caricamento, dove puoi:
  - indicare un **percorso locale** (consigliato per i file grandi, perché il file non passa dal browser);
  - **trascinare** uno o più file nell'area di upload.
- **🗑 Rimuovi**: toglie il dataset attivo dalla sessione (il file su disco non viene toccato).

Se carichi un dataset con un nome già presente, viene rinominato automaticamente (`vendite_2`, `vendite_3`, …).

### Scheda Dati

1. Costruisci i filtri nel riquadro **Filtri sul dataset**:
   - **Condizione** aggiunge una riga *colonna – operatore – valore*;
   - **Gruppo** aggiunge un sottogruppo con logica propria. I gruppi si possono annidare a piacere e ogni livello ha un colore diverso;
   - il selettore **AND/OR** di ogni gruppo decide se devono valere *tutte* le condizioni o *almeno una*;
   - **Azzera filtri** svuota l'albero.
2. Premi **Applica filtri**: la tabella mostra le righe che soddisfano i filtri.

Esempio: `regione = Nord` **AND** (`importo > 1200` **OR** `prodotto in lista A, B`).

Le condizioni incomplete (senza colonna o senza valore) e i gruppi vuoti vengono **ignorati**, senza generare errori.

Il riquadro **Struttura colonne** riporta per ogni colonna il tipo, il numero di valori non nulli, nulli e distinti.

### Scheda Group By

- **Raggruppa per**: una o più colonne chiave. Se non ne scegli nessuna, l'aggregazione viene fatta sull'intero dataset (una sola riga).
- Modalità **Aggregazioni**: aggiungi una o più coppie *colonna + funzione* con **Aggiungi valore**. Se non aggiungi valori ottieni il conteggio delle righe per gruppo (`n_righe`). Le colonne risultanti si chiamano `funzione_colonna` (es. `somma_importo`).
- Modalità **Value counts**: scegli le **colonne da contare**. Ottieni il `conteggio` di ogni valore (o combinazione di valori) per ogni gruppo e, se attivi l'opzione, la `percentuale` sul totale del gruppo (o sul totale del dataset se non ci sono chiavi).
- **Filtri**: vengono applicati *prima* del raggruppamento.
- **Ordina per chiavi**: ordina il risultato per le colonne di raggruppamento. In *Value counts* ordina per chiave e poi per conteggio decrescente.

I valori nulli nelle chiavi formano un gruppo a sé e non vengono scartati.

### Scheda Pivot

- **Righe** e **Colonne**: le dimensioni della tabella. Serve almeno una delle due.
- **Valori**: una o più misure *colonna + funzione*, con le stesse funzioni del Group By.
- **Filtri**: applicati *prima* della pivot.
- **Totali**: aggiunge la riga e la colonna `Totale`. I totali sono calcolati con la stessa funzione della misura, quindi sono corretti anche per medie, minimi e massimi.
- **Celle vuote = 0**: sostituisce con 0 le combinazioni senza dati.

Il risultato è una tabella "piatta": le intestazioni sono del tipo `somma di importo | Nord`.

### Scheda Console

Seleziona **SQL** o **Python**, scrivi il codice e premi **Esegui** oppure `Ctrl+Invio` (`Cmd+Invio` su Mac). Il codice di ciascun linguaggio viene ricordato quando passi dall'uno all'altro.

**SQL (DuckDB)**

- Ogni dataset è una tabella con il suo nome; `df` indica sempre il dataset attivo. I nomi delle tabelle disponibili sono indicati sopra l'editor.
- I nomi vengono resi validi per SQL: i caratteri non alfanumerici diventano `_` e, se il nome inizia con una cifra, viene aggiunto il prefisso `t_` (es. `vendite-2024` → `vendite_2024`, `2024` → `t_2024`).
- Si possono fare join tra dataset diversi e usare tutto il dialetto DuckDB.

```sql
SELECT v.regione, c.segmento, SUM(v.importo) AS totale
FROM vendite v JOIN clienti c USING (id_cliente)
GROUP BY ALL
ORDER BY totale DESC
```

**Python**

Variabili disponibili:

| Nome | Contenuto |
|---|---|
| `df` | Il dataset attivo (DataFrame pandas). |
| `datasets` | Dizionario `nome → DataFrame` con tutti i dataset caricati. |
| `pd`, `np` | pandas e numpy. |
| `sql("SELECT …")` | Esegue una query DuckDB e restituisce un DataFrame. |

- Come in Jupyter, **l'ultima espressione viene mostrata**: se è un DataFrame o una Series appare nella tabella dei risultati, con l'indice visibile come prima colonna; gli altri valori vengono mostrati come testo.
- Il testo stampato con `print()` e gli errori (traceback) compaiono nel riquadro sotto l'editor.
- Le variabili create restano disponibili nelle esecuzioni successive.
- Se riassegni `df` (es. `df = df[df.importo > 0]`), il **dataset attivo viene aggiornato**. Per vedere la modifica anche nelle altre schede, riseleziona il dataset nel selettore in alto.

```python
top = df.groupby("regione")["importo"].sum().sort_values(ascending=False)
top
```

---

## Riferimento: operatori dei filtri

| Operatore | Valore da inserire | Comportamento |
|---|---|---|
| `=` `≠` | un valore | Uguale / diverso. Confronto numerico sulle colonne numeriche, per data sulle colonne data, altrimenti testuale esatto. Con il valore vuoto, `=` trova le celle uguali alla stringa vuota. |
| `>` `<` `>=` `<=` | un valore | Confronto numerico, per data o alfabetico, secondo il tipo della colonna. |
| `contiene` / `non contiene` | testo | Ricerca del testo nella cella, **senza distinzione** tra maiuscole e minuscole. |
| `inizia con` / `finisce con` | testo | Come sopra, sull'inizio o sulla fine del testo. |
| `in lista` / `non in lista` | `a, b, c` | Valori separati da virgola. Sulle colonne numeriche i valori vengono convertiti in numeri. |
| `tra` | `min, max` | Intervallo con estremi inclusi, su numeri o date (es. `100, 500` oppure `2024-01-01, 2024-03-31`). |
| `regex` | espressione regolare | Espressione regolare Python, che **distingue** maiuscole e minuscole (usa `(?i)` per ignorarle). Un'espressione non valida non restituisce righe. |
| `è nullo` / `non è nullo` | nessuno | Celle vuote (NaN/None) o piene. |

Formati dei valori:
- **numeri**: la virgola decimale è accettata (`1234,5` equivale a `1234.5`);
- **date** nei confronti: è accettato il formato italiano con il giorno per primo (`31/12/2024`) e il formato ISO (`2024-12-31`).

---

## Riferimento: funzioni di aggregazione

Disponibili sia nel Group By sia nella Pivot.

| Funzione | Significato |
|---|---|
| `somma`, `media`, `mediana` | Somma, media aritmetica, mediana. |
| `conteggio` | Numero di valori **non nulli**. |
| `distinti` | Numero di valori distinti (nulli esclusi). |
| `min`, `max` | Minimo e massimo. |
| `primo`, `ultimo` | Primo / ultimo valore non nullo del gruppo, nell'ordine delle righe. |
| `dev.std`, `varianza` | Deviazione standard e varianza campionarie. |
| `range` | `max − min`. |
| `q1`, `q3` | Primo e terzo quartile (25° e 75° percentile). |
| `moda` | Valore più frequente (a parità di frequenza, il più piccolo in ordine crescente o alfabetico). |
| `nulli` | Numero di valori nulli. |
| `% nulli` | Percentuale di valori nulli sul totale del gruppo. |

---

## Esportazione

Ogni pannello dei risultati ha quattro pulsanti, che lavorano sempre sul **risultato completo**, anche quando la tabella ne mostra solo una parte:

| Pulsante | Risultato |
|---|---|
| **Excel** | Scarica un `.xlsx` con la riga di intestazione bloccata, i filtri automatici attivi e la larghezza delle colonne adattata al contenuto. |
| **CSV** | Scarica un CSV in **formato italiano**: separatore `;`, decimali con la `,`, codifica UTF-8 con BOM, così Excel lo apre correttamente con un doppio clic. |
| **Apri in Excel** | Salva un `.xlsx` nella cartella temporanea del sistema e lo apre con l'applicazione predefinita. |
| **Salva come dataset** | Aggiunge il risultato all'elenco dei dataset, così puoi filtrarlo, raggrupparlo o interrogarlo in SQL/Python (`datasets['nome']`). |

Le date con fuso orario vengono convertite in date senza fuso, perché Excel non le supporta.

---

## Assistente AI e privacy

Nella scheda **Console**, il riquadro **Assistente AI** genera codice SQL o Python a partire da una richiesta scritta in italiano. Il codice viene **inserito nell'editor ma non eseguito**: puoi leggerlo, modificarlo e poi eseguirlo tu.

1. Inserisci la tua **API key Anthropic** (la puoi creare su [console.anthropic.com](https://console.anthropic.com)). La chiave resta in memoria solo finché l'app è aperta e non viene salvata su disco.
2. Scegli il **modello**:

   | Modello | Caratteristiche |
   |---|---|
   | Fable 5.1 | Massima capacità, costo più alto. |
   | Opus 5.5 | Molto capace. |
   | Sonnet 5.5 | Equilibrato (predefinito). |
   | Haiku 5.5 | Veloce ed economico. |

3. Scegli lo **sforzo** (*Predefinito*, *Basso* … *Massimo*): più è alto, più il modello ragiona e più la risposta è lenta e costosa.
4. Descrivi cosa vuoi ottenere (es. *"totale importo per regione, solo per il 2024, in ordine decrescente"*) e premi **Genera codice**.

Il linguaggio generato è quello selezionato nella console (SQL o Python).

**Cosa viene inviato a Claude:** la tua richiesta e, per ogni tabella, il nome e l'elenco `colonna (tipo)`. **Nessun valore dei dati** lascia il computer: questo comportamento è verificato dai test (`test_ai.py`).

Prima di inserirlo nell'editor, il codice ricevuto viene controllato sintatticamente, senza eseguirlo. Se la risposta non è codice valido, se Claude rifiuta la richiesta o se la risposta è troppo lunga, compare un messaggio che spiega il problema.

---

## Struttura del progetto

```
Nicegui-Data-Analysis/
├── app.py              Interfaccia NiceGUI: pagine, schede, filtri, risultati, avvio da riga di comando
├── engine.py           Motore di analisi, senza dipendenze dall'interfaccia
├── ai_assistant.py     Generazione di codice con Claude (solo metadati)
├── style.py            Aspetto grafico (colori, font, angoli arrotondati)
├── launcher.py         Avvio da Jupyter: esplora(df)
├── demo_data.py        Dataset di esempio riproducibile, usato dai test
├── conftest.py         Fixture di pytest
├── test_engine.py      Test del motore
├── test_ai.py          Test dell'assistente AI (con client simulato, senza chiamate reali)
├── test_ui.py          Test dell'interfaccia (simulatore utente di NiceGUI)
├── pytest.ini          Configurazione dei test
├── requirements.txt    Dipendenze
└── requirements-dev.txt  Dipendenze per i test
```

### Architettura

```
┌──────────────────────────┐      ┌──────────────────────────────┐
│  app.py  (interfaccia)   │ ───► │  engine.py  (logica pura)    │
│  schede, filtri, griglia │      │  load_file, apply_filters,   │
│  export, console         │      │  run_groupby, run_pivot,     │
│                          │      │  run_sql, run_python, export │
│                          │ ───► ├──────────────────────────────┤
│                          │      │  ai_assistant.py             │
│                          │      │  build_prompt, generate_code │
└──────────────────────────┘      └──────────────────────────────┘
```

- **`engine.py`** contiene tutta la logica e non importa NiceGUI: puoi usarlo anche da script o notebook.
  I filtri sono rappresentati come un albero JSON serializzabile:

  ```python
  {"logica": "AND", "condizioni": [
      {"col": "regione", "op": "=", "val": "Nord"},
      {"logica": "OR", "condizioni": [
          {"col": "importo", "op": ">", "val": "1200"},
          {"col": "prodotto", "op": "in lista", "val": "A, B"},
      ]},
  ]}
  ```

  ```python
  import engine
  df = engine.load_file("vendite.csv")
  filtrato = engine.apply_filters(df, albero)
  tot = engine.run_groupby(df, ["regione"], [{"col": "importo", "fn": "somma"}], filtri=albero)
  ```

- **`app.py`** gestisce lo stato della sessione (dataset caricati, dataset attivo, codice della console) e costruisce l'interfaccia. Le operazioni pesanti (group by, pivot, query, export) vengono eseguite fuori dal thread dell'interfaccia, così la pagina resta reattiva.
- **`ai_assistant.py`** costruisce il prompt con i soli metadati, chiama l'API e controlla la sintassi del codice ricevuto.

---

## Test

```bash
pip install -r requirements-dev.txt
python -m pytest -q
```

I test coprono:
- il motore: filtri annidati e singoli operatori, group by, value counts, pivot con totali, SQL, console Python, export Excel/CSV;
- l'assistente AI: che nel prompt finiscano solo i metadati e mai i valori, l'estrazione del codice dalla risposta, i parametri di modello e sforzo, i messaggi d'errore. Non vengono fatte chiamate reali all'API;
- l'interfaccia: apertura della pagina, filtri, group by, pivot, console SQL e Python, export e salvataggio dei dataset, assistente AI.

I test vengono eseguiti automaticamente su GitHub Actions a ogni push, con Python 3.11, 3.12 e 3.13 (`.github/workflows/tests.yml`).

---

## Limiti e note

- **Righe mostrate**: la tabella mostra al massimo **10.000 righe**, in pagine da 100 (configurabili). Gli export contengono sempre tutte le righe.
- **Memoria**: i dataset sono tenuti in memoria (pandas). La dimensione gestibile dipende dalla RAM disponibile; per file grandi preferisci il formato Parquet e il caricamento da percorso.
- **Uso individuale**: l'app è pensata per un solo utente sulla propria macchina. Lo stato è condiviso tra le schede del browser aperte sulla stessa istanza.
- **Console Python**: il codice viene eseguito con i permessi del tuo utente, senza isolamento. È uno strumento personale e non va esposto in rete.
- **File pickle**: aprire un `.pkl` può eseguire codice arbitrario. Carica solo file pickle creati da te o da fonti fidate.
- **Apri in Excel**: apre il file sul computer **dove gira l'app**. I file creati restano nella cartella temporanea del sistema.
- **Dataset salvati**: i dataset creati con *Salva come dataset* esistono solo per la sessione corrente. Per conservarli, esportali.

---

## Risoluzione dei problemi

| Problema | Soluzione |
|---|---|
| `Address already in use` all'avvio | La porta è occupata: usa `--port 8123` (o un'altra porta). |
| Il browser non si apre | Apri a mano `http://localhost:8090` (o la porta scelta). |
| CSV letto in una sola colonna | Il separatore non è stato riconosciuto: verifica che il file abbia un separatore coerente in tutte le righe. |
| `Formato non supportato` | L'estensione non è tra quelle elencate in [Formati supportati](#formati-supportati): converti il file in CSV, Excel o Parquet. |
| *Apri in Excel* non fa nulla | Nessuna applicazione è associata ai file `.xlsx`: usa il pulsante **Excel** per scaricare il file. |
| L'Assistente AI dà errore di autenticazione | Controlla la API key. |
| L'Assistente AI risponde "non è codice valido" | Riformula la richiesta in modo più preciso, oppure scegli un modello o uno sforzo più alti. |
