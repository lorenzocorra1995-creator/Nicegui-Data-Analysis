# PROJECT_MAP — Data Explorer (schema per le sessioni future)

Scopo di questo file: capire in pochi secondi **dove intervenire** per una richiesta, senza rileggere tutto il codice.
Le righe indicate sono approssimative (snapshot attuale): per ritrovare un punto esatto usare `grep -n "nome_simbolo" file.py`.
Se i file cambiano in modo sostanziale, aggiornare questo file.
**Percorsi**: i moduli sono citati con il solo nome (`engine.py`, `app.py`…); dove si trovano nelle cartelle è in [§2](#2-mappa-dei-file). I comandi si lanciano dalla cartella `app/`.

---

## Indice

| # | Sezione | Contenuto |
|---|---------|-----------|
| 0 | [Regole di lavoro dell'utente](#0-regole-di-lavoro-dellutente) | Come devo scrivere e consegnare il codice |
| 1 | [Panoramica e stack](#1-panoramica-e-stack) | Cos'è il programma, librerie, avvio, test |
| 2 | [Mappa dei file](#2-mappa-dei-file) | Ruolo di ogni file e dipendenze tra moduli |
| 3 | [**Dove intervenire (richiesta → codice)**](#3-dove-intervenire-richiesta--codice) | Tabella di instradamento: la parte più utile |
| 4 | [engine.py](#4-enginepy--logica-dati-senza-ui) | Caricamento, filtri, groupby, pivot, SQL, Python, export |
| 4b | [pivot.py](#4b-pivotpy--pivot-come-cubo-senza-ui) | Cubo, «Mostra valori come», totali, ordinamento, drill-down |
| 5 | [columns.py](#5-columnspy--colonne-calcolate-senza-ui) | Cambio formato, formula, se… allora |
| 5b | [combine.py](#5b-combinepy--join-e-union-senza-ui) | Join (tutti i tipi), union, piani colonne |
| 6 | [formula_help.py](#6-formula_helppy--aiuto-alle-formule-senza-ui) | Guida funzioni DuckDB per l'editor formule |
| 7 | [charts.py](#7-chartspy--dati-dei-grafici-senza-ui) | Catalogo tipi di grafico e preparazione dati |
| 8 | [chart_options.py](#8-chart_optionspy--opzioni-echarts-senza-ui) | Da `ChartData` alle opzioni ECharts |
| 9b | [stats.py e stats_charts.py](#9b-statspy-e-stats_chartspy--statistica-senza-ui) | Analisi statistiche e loro grafici |
| 9f | [number_format.py](#9f-number_formatpy--formato-dei-numeri-senza-ui) | Formato dei numeri delle tabelle (nessuno / decimale, stile italiano / americano) e dei grafici |
| 9d | [workspace.py e notes.py](#9d-workspacepy-e-notespy--database-e-note-senza-ui) | Database dei dataset (progetti, cartelle) e note di origine |
| 9c | [excel_writer.py, excel_split.py, excel_report.py](#9c-excel_writerpy-excel_splitpy-excel_reportpy--file-excel-senza-ui) | Scrittura formattata, split, report |
| 9g | [dashboard.py](#9g-dashboardpy--dashboard-della-console-senza-ui) | Dashboard scritte nella Console: slicer, KPI, grafici, tabelle, pivot, pagine, filtro incrociato |
| 9 | [ai_assistant.py e chart_ai.py](#9-ai_assistantpy-e-chart_aipy--claude-senza-ui) | Generazione codice e configurazione grafici con Claude |
| 10 | [style.py](#10-stylepy--aspetto-grafico) | Palette, CSS, temi |
| 11 | [app.py](#11-apppy--interfaccia-nicegui) | Stato, componenti riusabili, schede, caricamento file, avvio |
| 12 | [launcher.py](#12-launcherpy--uso-da-jupyter) | `explore()` e invio all'app aperta |
| 13 | [Strutture dati chiave](#13-strutture-dati-chiave) | Albero filtri, `spec` colonne, `spec` grafico, `ChartData` |
| 14 | [Test](#14-test) | Quale test aggiornare per quale modulo; marker UI |
| 15 | [Discrepanze e punti di attenzione](#15-discrepanze-e-punti-di-attenzione) | Cose da sapere prima di toccare il codice |
| 16 | [Procedura di consegna](#16-procedura-di-consegna) | Passi da seguire quando modifico i `.py` |

---

## 0. Regole di lavoro dell'utente

1. **Mai usare le skills**, salvo richiesta esplicita.
2. Codice Python **in stile Python**, chiaro e semplice; **variabili in inglese**.
3. **Niente codice ridondante** (riusare helper esistenti prima di scriverne di nuovi).
4. Codice **organizzato per argomenti**: funzioni, logiche e architettura separate (il progetto già separa logica senza UI da interfaccia: mantenere questa divisione).
5. Se modifico file `.py`: **non mostro mai i paragrafi toccati**; consegno direttamente i file `.py` aggiornati da scaricare (vedi [§16](#16-procedura-di-consegna)).
6. Lingua della conversazione e dell'interfaccia: **italiano**. Identificatori, commenti e docstring nel progetto: **inglese**.

Nota sui nomi: tutto il codice è in inglese — identificatori (classi, funzioni, variabili, costanti, nomi dei test), commenti e docstring (es. `AppState`, `ResultPanel`, `FilterBuilder`, `filters`, `rows`, `columns`, `tables`). **Il nuovo codice va scritto in inglese.** Restano in italiano, per scelta, solo i testi visibili all'utente (etichette, pulsanti, notifiche, messaggi di errore, guida delle formule, fogli e intestazioni Excel) e le chiavi salvate o scambiate (es. le chiavi `"logica"` / `"condizioni"` dell'albero dei filtri, i nomi di colonna prodotti per l'utente, i marker UI dei test). `launcher.esplora` resta come alias di `launcher.explore` per i notebook esistenti (anche il parametro `porta` è rimasto invariato).

---

## 1. Panoramica e stack

Strumento **locale** di analisi dati, a singolo utente: NiceGUI (UI web) + pandas + DuckDB + PyArrow + openpyxl + xlsxwriter + scipy + statsmodels + anthropic.
Nessun servizio esterno tranne l'API Anthropic (opzionale, solo metadati: nomi e tipi di colonne, mai i valori).

- Dipendenze: `app/requirements.txt` (`nicegui>=3.14`, `pandas>=2.2`, `numpy`, `duckdb`, `openpyxl`, `pyarrow`, `anthropic`, `scipy`, `statsmodels`, `xlsxwriter`).
- Avvio: doppio clic su `DataExplorer.exe` (accanto a `app/`; usa il venv `app/NiceGuiApp`, nessun terminale, log in `app/logs/app.log`; se la porta 8090 è occupata apre solo il browser) oppure da `app/`: `python main.py [file ...] [--port N] [--db percorso.duckdb]` (porta predefinita 8090; database predefinito `app/data/workspace.duckdb`). Da Jupyter: `from launcher import explore` (con `app/` nel `sys.path`) oppure `from data_explorer.launcher import explore`. L'exe si rigenera con `app/tools/build_exe.ps1`.
- Test: da `app/`, `python -m pytest -q` (`pytest.ini`: `asyncio_mode = auto`, `main_file =`, `testpaths = tests`, `pythonpath = .`; `tests/conftest.py` espone la fixture `df` = `make_demo_df()` e il plugin `nicegui.testing.user_plugin`).
- Funzioni dell'app (9 schede): **Dati**, **Struttura** (rinomina, riordina ed elimina colonne; ordina righe su più livelli; annulla), **Colonne calcolate**, **Group By**, **Pivot**, **Join / Union**, **Grafici**, **Statistica**, **Excel** (split e report), **Console** (SQL/Python + assistente AI; in Python l'ultima riga può restituire una tabella, un grafico (opzioni ECharts, figura matplotlib o plotly) o una **dashboard** `Dashboard(df, titolo)`). Navigazione: sidebar a sinistra con le sezioni in gruppi (Prepara, Analizza, Esporta, Strumenti), comprimibile dal pulsante menu; contenuto a tutta larghezza. Header: Aspetto (temi pronti, chiaro/scuro, accento, tinta, angoli, colori dei grafici, colori avanzati; salvato tra un avvio e l'altro), menu «Dataset (N)» (dataset caricati con dimensione, rinomina, rimuovi, carica), carica.
- **Nessun dataset attivo globale**: in Dati, Struttura, Colonne calcolate, Group By, Pivot, Grafici, Statistica e Console ogni **finestra** sceglie il suo dataset (selettore «Dataset» in cima). In Group By, Pivot, Grafici e Console il selettore «Finestre» (1–6) impila più finestre, ognuna con dataset, filtri e scelte propri. Join / Union ed Excel scelgono i dataset nei loro `SourcePicker`.
- **Sotto-finestre** (Group By, Pivot, Grafici): ogni finestra ha i **filtri una sola volta** (sotto il selettore del dataset) e sotto di essi il selettore «Pivot / Grafici / Group By in questa finestra» (1–6) impila più analisi che leggono lo stesso dataset e gli stessi filtri; ognuna ha le sue scelte. Cambiare dataset ricostruisce filtri e sotto-finestre. I filtri si applicano al «Crea …» / «Esegui …» di ciascuna sotto-finestra.
- Sotto ogni risultato: menu unico «Esporta e salva» (Excel, CSV in formato italiano `;` `,`, Apri in Excel, Salva come dataset, Salva nel database, Aggiungi al report, Nota) + Schermo intero.
- Sopra ogni risultato: pulsante «Formato numeri» (interi e decimali): Nessun formato / Formato decimale (numero di decimali) + stile Italiano (`1.234,56`) o Americano (`1,234.56`). Vale per sezione della sidebar (`ResultPanel(section=...)`) e cambia solo la visualizzazione: Excel e CSV scaricati non cambiano. Nei grafici di Grafici e Statistica lo stile si sceglie in «Separatori numeri» (`ChartLook`).
- Tutte le finestre (dialog non a tutto schermo) si spostano trascinando la prima riga e si ridimensionano dall'angolo: CSS in `style.BASE_CSS`, script `style.DIALOG_SCRIPT` aggiunto da `style.apply`.

---

## 2. Mappa dei file

### Struttura delle cartelle

```
Def/
├─ DataExplorer.exe          launcher senza terminale (sorgente: app/tools/exe_launcher.py)
└─ app/
   ├─ main.py                entry point (python main.py); chiama data_explorer.app.main
   ├─ launcher.py            shim per i notebook (re-esporta data_explorer.launcher)
   ├─ pytest.ini, requirements.txt
   ├─ data_explorer/
   │  ├─ app.py, launcher.py
   │  ├─ core/       engine, columns, combine, pivot, stats, structure, import_plan,
   │  │              number_format, workspace, notes, formula_help
   │  ├─ plotting/   charts, chart_options, chart_ai, stats_charts
   │  ├─ excel_io/   excel_link, excel_report, excel_split, excel_writer
   │  ├─ ai/         ai_assistant
   │  └─ ui/         style, dashboard
   ├─ tests/         test_*.py, conftest.py, demo_data.py
   ├─ data/          workspace.duckdb (database predefinito: `workspace.DEFAULT_PATH`)
   ├─ docs/          PROJECT_MAP.md (questo file)
   ├─ tools/         exe_launcher.py, build_exe.ps1
   ├─ NiceGuiApp/    venv (usare `python -m pip`: gli script pip.exe hanno percorsi fissi)
   ├─ logs/, .nicegui/   log dell'exe e storage di NiceGUI (relativo alla cartella `app/` da cui si avvia)
```

Gli import sono assoluti dal pacchetto, con alias invariati: `from data_explorer.core import engine as eng`, `from data_explorer.plotting import charts`, `from data_explorer.ui import style`, ecc. Nei test: `from data_explorer import app as A`. `demo_data.py` sta in `tests/`.

### Moduli senza UI (testabili da soli)

| File | Righe | Ruolo |
|------|------:|-------|
| `core/engine.py` | 893 | Caricamento file, filtri AND/OR, groupby, value counts, console SQL/Python, export |
| `core/pivot.py` | 542 | Pivot come cubo (stile Excel): codifica dei campi, tutti i livelli di totale, «Mostra valori come», ordinamento, tabella in piano, righe di origine di una cella |
| `core/columns.py` | 217 | Colonne calcolate: conversione formato, formula DuckDB, regole se…allora |
| `core/combine.py` | 523 | Join (inner, left, right, full, cross, semi, anti; DuckDB su tabelle Arrow, con limite alle righe prodotte) e union (UNION ALL, UNION, INTERSECT, EXCEPT; pandas), con piani colonne |
| `core/formula_help.py` | 606 | Dati statici della guida alle funzioni DuckDB + nomi/tipi colonna per l'editor |
| `plotting/charts.py` | 700 | Catalogo dei 23 tipi di grafico, preparazione dati (`prepare`), drill-down |
| `plotting/chart_options.py` | 597 | `ChartData` → opzioni ECharts (+ funzioni JavaScript di formattazione) |
| `ai/ai_assistant.py` | 123 | Prompt, chiamata a Claude, pulizia/validazione del codice generato |
| `plotting/chart_ai.py` | 169 | Claude sceglie le impostazioni del grafico (JSON), validate e normalizzate |
| `core/stats.py` | 968 | Analisi statistiche: catalogo `ANALYSES` (descrittive, distribuzione, frequenze, anomali, correlazioni, test, regressioni, serie temporali), `run` |
| `ui/dashboard.py` | 395 | Dashboard della Console (stile Power BI): `Dashboard` (slicer, kpi, chart, table, pivot, python, text, page), filtro incrociato, `compute` per pagina, `example_code` |
| `plotting/stats_charts.py` | 192 | `stats.Plot` → opzioni ECharts (barre, box plot, dispersione, linee, mappa di calore) |
| `core/number_format.py` | 93 | Formato dei numeri: `NumberFormat`, `grid_formatter` (JavaScript delle colonne numeriche della griglia), `localized` (stile dei numeri nei grafici) |
| `excel_io/excel_writer.py` | 270 | Scrittura .xlsx con xlsxwriter: `ExcelStyle`, `Sheet`, `workbook_bytes` (Tabella, totali, condizionali, indice, fogli oltre il limite di righe), nomi validi, `zip_bytes`, `save_to_folder` |
| `excel_io/excel_split.py` | 183 | Split di un dataset in file/fogli: `split_plan`, `plan_table`, `build_files`, `split` |
| `excel_io/excel_report.py` | 67 | `Report` / `ReportItem`: tabelle raccolte (istantanee), ordine, nomi, `workbook` |
| `demo_data.py` (in `tests/`) | 15 | `make_demo_df()` per i test |
| `excel_io/excel_link.py` | 60 | Range selezionato nell'Excel aperto via COM (pywin32, solo Windows): `Selection`, `selection_of`, `current_selection`; la cartella deve essere salvata |
| `core/workspace.py` | 286 | Database DuckDB dei dataset: progetti e sottocartelle annidabili, dataset salvati così come sono (una tabella `data_<id>` ciascuno) con nota, albero per la UI |
| `core/notes.py` | 52 | Note di origine: operazione + dataset di origine (con la loro storia, rientrata) + filtri (`engine.describe_filters`) |
| `core/import_plan.py` | 72 | Piano di importazione: colonne da tenere, nomi, formati (rilevato, scelto), anteprima, valori persi, colonne da rileggere come testo |

### Moduli con UI / avvio

| File | Righe | Ruolo |
|------|------:|-------|
| `app.py` (in `data_explorer/`) | 4008 | Interfaccia NiceGUI: stato, schede, componenti, caricamento file, `main()` |
| `core/structure.py` | 135 | Nomi di dataset e colonne, ordine ed eliminazione delle colonne, ordinamento righe su più livelli (senza UI) |
| `ui/style.py` | 300 | Preferenze di aspetto personalizzabili, temi pronti, colori derivati, CSS a variabili, tema dei grafici |
| `launcher.py` (in `data_explorer/`; `main.py` e `launcher.py` in `app/`) | 92 | `explore()`: avvia l'app o invia DataFrame a quella aperta |

### Dipendenze interne (freccia = importa)

```
app.py ──► engine, pivot, dashboard, notes, workspace, import_plan, structure, columns, combine, formula_help, charts, chart_options, chart_ai, ai_assistant, stats, stats_charts, excel_writer, excel_split, excel_report, excel_link, number_format, style
stats ──► engine (scipy, statsmodels)
excel_writer ──► engine (xlsxwriter)
excel_split ──► engine, excel_writer
excel_report ──► excel_writer
stats_charts ──► stats, chart_options, number_format
dashboard ──► charts, chart_options, engine, pivot
structure ──► engine
chart_ai ──► ai_assistant, chart_options, charts, engine
chart_options ──► charts, number_format
charts ──► engine
pivot ──► engine, charts  (`AGG_FUNCTIONS`, `aggregate`, `check_measures`, `filter_mask`, `measure_name`, `unique_names`; `category_keys` per codici ed etichette)
columns ──► engine
combine ──► engine, columns, formula_help  (usa `engine.arrow_table`, `run_sql_arrow`, `arrow_to_pandas`)
formula_help ──► columns
workspace ──► engine (`to_arrow`, `arrow_to_pandas`)
notes ──► engine (`describe_filters`)
import_plan ──► columns (`convert`, `to_number`, `to_date`, `lost_values`), structure (`column_items`, `apply_columns`)
excel_link ──► engine  (COM tramite pywin32, solo Windows)
engine, ai_assistant, demo_data, number_format ──► (nessun modulo interno)
style ──► nicegui soltanto
launcher ──► (nessun modulo interno; parla con l'app via HTTP e la avvia con main.py)
main.py ──► app (main)
```

Regola: **la logica va nei moduli senza UI, `app.py` fa solo da collante** (controlli → `spec` → funzione del modulo → `ResultPanel.show`).

---

## 3. Dove intervenire (richiesta → codice)

| Se la richiesta riguarda… | Intervenire in | Note |
|---------------------------|----------------|------|
| Nuovo formato file / lettura CSV (delimitatore, encoding) | `engine.py` §Caricamento (`READERS`, `_read_csv`, `load_file`) | UI di caricamento: `app.py` §Header (`load_dataset_file`, `upload_dialog`) |
| Scelta del foglio Excel e dell'intervallo di celle (es. B3:F120) | `engine.excel_sheets`, `engine.cell_block`, `engine.read_excel_sheet` + `app.ask_sheet` (per Excel la finestra è sempre mostrata: foglio + intervallo facoltativo, marker `sheet-range`) | Dopo la lettura passa sempre da `ImportDialog` (formati rilevati) |
| Range selezionato nell'Excel aperto («Carica dataset» → «Range Excel») | `excel_link.current_selection` (COM, `selection_of`) · UI: `app.ExcelRangeDialog` (selezione letta ogni secondo, marker `excel-range`, `excel-range-ok`), `upload_dialog`, `load_dataset_file(..., choice)` | Il file si legge da disco con `engine.read_excel_sheet`: se la cartella ha modifiche non salvate l'importazione è rifiutata |
| Nuovo operatore di filtro / elenchi incollati | `engine.OPERATORS` (+ `OP_*`), `_condition_mask`, `describe_filters`, `parse_pasted_values` | UI valori: `FilterBuilder._value` in `app.py`; aggiornare `test_engine.py` |
| Comportamento dei filtri AND/OR | `engine.filter_mask`, `apply_filters(df, albero, columns=None)`, `has_conditions` | Albero: vedi [§13](#13-strutture-dati-chiave). Senza condizioni nessuna copia (copia leggera); `columns` = solo le colonne usate |
| Memoria | copy-on-write attivo anche con pandas 2 (`engine.py`, all'import) · `apply_filters` con `columns` in `run_groupby`, `run_value_counts`, `stats.run`; `pivot.build_cube` copia solo le colonne usate (`stats.used_columns`, `Analysis.all_columns`) · `ChartData` tiene `source` + `positions`, non le righe · `page_content()` svuota `S.themed` e `S.pickers` | Copie che restano: `AppState.undo`, `engine.ARROW_CACHE` (SQL), risultati nelle schede, report, variabili della Console |
| Aspetto del costruttore di filtri | `app.FilterBuilder` (+ `filter_box(df, expanded=False)`: chiuso di default, titolo «Filtri (N)» con le condizioni attive, `count_conditions`) | Usato in Dati, Statistica, Console, se…allora e (una volta per finestra, condiviso dalle sotto-finestre) in Group By, Pivot, Grafici |
| Nuova funzione di aggregazione | `engine.AGG_FUNCTIONS` (+ `NUMERIC_FUNCTIONS` / `NO_DATE_FUNCTIONS` se non vale per testi o date; + ramo vettoriale in `engine.aggregate` se pandas non ha un metodo di groupby veloce) | Compare in automatico in Group By, Pivot, Grafici (`AggregationList`) e nella validazione di `chart_ai`. `AggregationList` propone solo le funzioni di `engine.allowed_functions(column)` |
| Funzioni non adatte al tipo di colonna (es. somma di testi) | `engine.allowed_functions`, `check_measures` (chiamata da `run_groupby` e `pivot.build_cube` prima di calcolare) · UI: `AggregationList.functions`, `adapt`, `set_col` | La somma pandas di testi concatena le stringhe: su dataset grandi bloccava l'app |
| Group By / Value counts | `engine.run_groupby` (aggregazioni con `engine.aggregate`), `run_value_counts` · UI: `app.tab_groupby` | Nomi delle colonne decisi prima: schema `engine.NAMING_PATTERNS` / `DEFAULT_NAMING` (segnaposto {colonna}, {funzione}, {valore}) + nome per misura (`"name"` nelle aggregazioni, vince sullo schema): `engine.measure_name`, `unique_names` · UI: `app.NamingChoice`, `AggregationList(named=True)` |
| **Pivot** (campi, date, totali, subtotali, ordinamento, nomi) | `pivot.build_cube` (aggregazioni) e `pivot.render` (presentazione; limite `pivot.MAX_COLUMNS` colonne di valori) · UI: `app.PivotTab` (entrambe le fasi in thread con `PivotTab.compute`) | Nomi come nel Group By (`naming`, nome per misura; senza {valore} il valore va in fondo dopo «|»); con un calcolo e senza nome scritto lo schema riceve « (…)» da `show_as_text` |
| Pivot: nuovo «Mostra valori come» | `pivot.CALCULATIONS` (+ ramo in `show_values` o `_along_field`) · nuovo riferimento: `pivot.REFERENCES` + `_reference_keys` | Compare da solo nella UI (`PivotTab.show_as_controls`) |
| Pivot: righe di origine di una cella | `pivot.drill`, `selection_text` · `ResultPanel(on_cell_click=…)` (clic = scelta della cella; chiave nascosta `ROW_ID` nelle righe della griglia) · `PivotTab.select`, `drill` (pulsante «Righe di origine», marker `pivot-drill`) | |
| Struttura colonne (tipi, nulli, distinti) | `engine.describe_columns` · UI: `app.tab_data` (`load_structure`) | |
| Console SQL (cache Arrow, tabelle, nomi) | `engine.ArrowCache`, `run_sql`, `sql_table_name` · `app.AppState.sql_tables` | |
| Struttura del dataset (nomi, ordine/eliminazione colonne, ordinamento righe) | `structure.py` (`check_dataset_name`, `check_column_names`, `column_items`, `apply_columns`, `renamed`, `sort_rows`, `apply`) · UI: `app.StructureTab`, `app.rename_dialog(old_name)` | Modifiche sul dataset della finestra con `AppState.replace(name, df, description)` (tiene la versione precedente in `AppState.undo`) e `AppState.revert`, poi `refresh_dataset(name)`; rinomina con `AppState.rename` (mantiene l'ordine dei dataset) + `update_header({vecchio: nuovo})` |
| **Finestre di lavoro** (dataset per finestra, più finestre impilate) | Classi: `Pane` (cornice, titolo, ✕, `body`) → `DatasetPane` (selettore «Dataset», `build`, `show_size`, `sub_stack`) e `SubPane`; `Stack` (selettore del numero, `add`, `remove`, `set_count`, `update_titles`) → `PaneStack(section, builder, multiple)` («Finestre») e `SubStack(section, builder, dataset, filters)` (sotto-finestre). `MAX_WINDOWS`, `MULTI_WINDOW`, `OWN_SOURCES`, **`SUB_WINDOWS`** (sezione → (titolo, plurale)), `refresh_dataset(name, keep)`, `follow_datasets`, `update_header(renamed)`, `show_new_dataset` | Il builder di una sezione riceve il **nome** del dataset (`S.datasets[name]`), non il DataFrame; per le sezioni in `SUB_WINDOWS` riceve `(name, filters)` con il `FilterBuilder` condiviso (usare `filters.tree`, non creare un `filter_box`). Marker `pane-dataset`, `pane-count`, `pane-close`, `subpane-count`, `subpane-close` |
| Console Python (variabili disponibili, output) | `engine.run_python`, `to_display_frame` · `app.AppState.prepare_env`, `app.tab_console` | |
| Grafici dalla console | `engine.chart_kind`, `echarts_options` (tipi numpy/pandas → JSON, chiave facoltativa `height`), `figure_image` (matplotlib → SVG; backend `Agg`) · `chart_options.written_chart_defaults` (etichette in testo normale, numeri all'italiana in etichette, tooltip e assi; le chiavi scritte dall'utente restano) · `app.show_console_chart`, `console_chart_example` | plotly e matplotlib non sono dipendenze: si usano solo se l'utente li ha installati |
| **Dashboard dalla Console** (slicer, KPI, grafici, tabelle, pivot, pagine, filtro incrociato) | `dashboard.py` (`Dashboard`, `Slicer`, `Visual`, `Page`, `Output`, `CrossFilter`, `CALCULATIONS`, `chart_panels`, `example_code`) · UI: `app.DashboardView` (`DASHBOARD_DRAWERS`, `slicer_control`, `refresh` in thread con `version`, `select`, `full_screen`), ramo `isinstance(val, dashboard.Dashboard)` in `tab_console.execute`, `Dashboard` in `AppState.prepare_env`, pulsante «Esempio di dashboard» · CSS `.dash-grid`, `.dash-card`, `.dash-kpi` in `style.BASE_CSS` | Nuovo tipo di visual: metodo builder in `Dashboard` + funzione in `CALCULATIONS` + `DashboardView.draw_<tipo>` in `DASHBOARD_DRAWERS`. Marker `console-dashboard-example`, `dashboard-slicer`, `dashboard-pages`, `dashboard-visual`, `dashboard-kpi`, `dashboard-chart`, `dashboard-cross-clear` |
| Export Excel / CSV / apri in Excel | `engine.to_xlsx_bytes`, `to_csv_bytes`, `open_in_excel`, `_prepare_for_excel` · UI: `app.ResultPanel` | |
| Griglia risultati (colonne, formati, righe mostrate) | `app._col_defs`, `_json_rows`, `_unique_columns`, `ResultPanel` · `ROW_LIMITS`, `DEFAULT_PREVIEW_ROWS` | |
| Salva risultato come dataset | `app.ResultPanel.save_as_dataset` · `AppState.add` | |
| Nuova conversione di formato | `columns.CONVERSIONS`, `convert` (+ `DATE_PARTS`) | |
| Parsing numeri/date | `columns.to_number`, `to_date` | |
| Formula (valutazione, errori) | `columns.formula_query`, `evaluate_formula` | |
| Regole se… allora | `columns.case_when` · UI: `app.CaseRules` | |
| **Join / Union** (tipi, chiavi, esecuzione) | `combine.py`: `JOIN_TYPES`, `KEY_OPERATORS`, `KEY_MATCHES`, `join`, `UNION_TYPES`, `ALIGNMENTS`, `union` | Vedi [§5b](#5b-combinepy--join-e-union-senza-ui) |
| Nomi, duplicati, colonne tenute (piano colonne) | `combine.join_plan`, `_name_columns`, `NAMING` · `combine.union_plan` | UI: `app.JoinPanel.render_plan`, `UnionPanel.render_plan` |
| **Formato dei numeri** (nessuno / decimale, decimali, stile italiano / americano) | `number_format.py`: `NumberFormat`, `grid_formatter(fmt, integer, precise)`, `localized` · UI: `app.NumberFormatMenu` (pulsante «Formato numeri» in ogni `ResultPanel`, marker `number-format`, `number-format-mode`, `number-format-decimals`, `number-format-style`), `ResultPanel.change_format` / `redraw_columns`, `_col_defs(..., fmt)`, `AppState.number_formats` / `format_of` · grafici: `chart_options.View.locale`, `app.ChartLook.number_style` | Il formato è per **sezione** (`ResultPanel(section=<chiave di TABS>)`): le tabelle della stessa sezione (es. le due della Pivot, le tabelle della Statistica) cambiano insieme. Un nuovo `ResultPanel` richiede `section=` |
| Griglie dei risultati | `app.ResultPanel` (`GRID_LOCALE` testi AG Grid in italiano, `GRID_MIN_COLUMN_WIDTH`, intestazioni a capo, formato dei numeri da `number_format` (predefinito: 2 decimali, stile italiano; Statistica: 4 decimali massimi), `DATE_FORMAT` gg/mm/aaaa) · `show(..., total_rows=, reveal=)`: righe di totale in grassetto (classe `total-row`, flag `TOTAL_FLAG`), `reveal` porta il risultato in vista con `scroll_into_view` | La Pivot passa le righe di totale da `PivotTable.row_codes` |
| Intestazioni «fonte» e duplicati nell'anteprima | `app._col_defs`, `_group_by_source`, `ResultPanel.show(sources=...)` · `combine.column_sources` · CSS `.dup-column` in `style.BASE_CSS` | |
| Scheda Join / Union (controlli, dataset, filtri prima) | `app.tab_combine`, `JoinPanel`, `UnionPanel`, `PlanPanel`, `SourcePicker` | I selettori seguono i dataset caricati: `update_header` + `S.pickers` |
| Note salvate nel database | `app.save_to_database(df, name, folder_id, on_saved)`: la nota parte vuota (marker `db-note-text`), si salvano solo note scritte a mano; la nota di origine resta visibile da «Nota: origine e filtri» | |
| Come/dove si inserisce la colonna calcolata | `columns.with_column`, `calculate` | |
| UI scheda Colonne calcolate | `app.tab_columns` (+ `formula_editor`, `column_picker`, `formula_guide`) | `column_picker(df, editor, quote, target, marker)` è riusato dalla Console (SQL: `fh.quote_column`; Python: nome tra apici, `repr(colonna)`): restituisce `show(df)` che ridisegna i chip solo se le colonne cambiano. Marker `column-chip` (formule) e `console-column-chip` (Console) |
| Guida alle funzioni nelle formule | `formula_help.py` (liste `TEXT`, `NUMBERS`, `DATES`, `LOGIC`, `WINDOWS`, `GUIDE`) · UI: `app.function_card`, `function_details`, `example_view` | |
| **Pannelli** (un grafico per valore di una colonna, Top N e ordine dentro ogni pannello) | `charts._prepare_panels` (`MAX_PANELS`, `DEFAULT_PANEL_DATE_UNIT`, `_panel_notes`, `_panel_table`, `_attach_source`; `ChartData.panels` / `panel_name`; drill con chiave `"p"`) · `chart_options.build_panels(data, view, same_scale)` (`value_bounds`, `_scale`, `_same_colors`: stessa scala e stesso colore per serie/categoria in ogni pannello; `View.value_range`, `View.colors` da `style.chart_view`) · UI: `ChartTab.panel_column`, `panel_date_unit`, `panel_look` («Pannelli per riga», «Stessa scala»), `draw` (griglia di `ui.echart`), `select(event, panel)` | Campi `spec` `panels`, `panel_date_unit` anche in `chart_ai` (`COLUMN_FIELDS`, `CHOICES`, `DEFAULTS`) |
| **Nuovo tipo di grafico** | `charts.CHART_TYPES` (+ `ChartType`), poi disegno in `chart_options` (`BUILDERS` + costruttore `_xxx`, scelto da `ChartType.draw`) | Se usa campi nuovi: `charts.used_fields`; `chart_ai` lo scopre da solo da `CHART_TYPES` |
| Preparazione dati del grafico (Top N, altri, ordinamento, date) | `charts.py` §Top N / §Aggregazione / `_prepare_*` | |
| Aspetto del grafico (etichette, decimali, assi, tooltip, formati italiani) | `chart_options.py` (`View`, `js_*`, `_cartesian`, `_pie`, …) | Cambi solo visivi: non ricalcolano i dati |
| Controlli della scheda Grafici | `app.ChartTab` (tutta la costruzione — AI, Dati, Aspetto, «Crea grafico» — nell'expander `builder` «Costruzione del grafico», aperto di default, marker `chart-builder`, didascalia da `summary()` dopo «Crea grafico»; grafico e dati fuori; `data_controls`, `update_controls`, `spec`) · aspetto: `app.ChartLook` (condiviso con Statistica) | Nuovo campo di dati: `spec()`, `apply()`, `chart_ai.DEFAULTS/FIELDS/normalize`; nuovo campo di aspetto: `ChartLook` + `chart_options.View` (+ `stats_charts.VIEW_FIELDS` se vale anche in Statistica) |
| Clic sul grafico → righe di origine | `charts.drill`, `selection_text` · `app.ChartTab.select`, `show_table` | |
| Grafico creato dall'AI | `chart_ai.py` (`FIELDS`, `RULES`, `normalize`) · UI: `ChartTab.ai_build`, `apply` | |
| Generazione codice SQL/Python con AI | `ai_assistant.py` (`RULES`, `SYSTEM_PROMPT`, `generate_code`) · UI: `app.tab_console` (`generate`) | |
| Modelli / sforzo dell'AI, API key | `ai_assistant.MODELS`, `EFFORTS` · `app.ai_settings`, `AppState.api_key/ai_model/ai_effort` | |
| Colori, angoli, temi pronti, personalizzazione | `style.Appearance`, `PRESETS`, `resolve`, `BASE_CSS`, `css_variables` · `style.chart_theme`, `chart_view` | UI: `app.AppearanceDialog` (pulsante «Aspetto» nell'header); salvataggio in `app.storage.general[APPEARANCE_KEY]`, caricato all'avvio da `load_appearance` |
| Header, sidebar, tema, elenco dei dataset | `app.main_page`, `sidebar`, `DatasetMenu` (marker `datasets`, `rename-dataset`, `remove-dataset`), `update_header`, `remove_dataset(name)` | CSS: `.app-sidebar`, `.sidebar-group`, `.page-title`, `.app-panels` in `style.BASE_CSS` |
| Aggiungere/riordinare una sezione | `app.TABS` (gruppo, icona, builder) + nuova funzione `tab_xxx(df)` (o classe) | `sidebar()` crea il menu raggruppato, `page_content()` costruisce i pannelli |
| Stato globale (dataset, tema, codice console) | `app.AppState` / istanza `S` | |
| Avvio, porta, file da riga di comando | `app.main` | |
| **Statistica**: nuova analisi o nuovo test | `stats.py`: funzione `(rows, spec) -> StatResult` + voce in `ANALYSES` (campi da `FIELD_LABELS`) | Nuovo campo: `FIELD_LABELS` + controllo in `app.StatsTab.build_controls` |
| Statistica: grafici | `stats.Plot` (kind) → `stats_charts.BUILDERS` | |
| Statistica: interfaccia (controlli, tabelle in schede, grafici) | `app.StatsTab` | Tabelle con `ResultPanel(precise=True, fit_height=True)` |
| **Excel**: formattazione dei file generati (stili, totali, condizionali, indice, formati) | `excel_writer.py` (`ExcelStyle`, `_Formats`, `_write_sheet`, `workbook_bytes`) | UI: `app.OutputSettings` (anche destinazione: `deliver`) |
| Excel: split (modi, nomi con segnaposto, limiti) | `excel_split.py` (`MODES`, `fill_pattern`, `split_plan`, `MAX_FILES`, `MAX_SHEETS`) | UI: `app.SplitPanel` |
| Excel: report (raccolta, ordine, nomi dei fogli) | `excel_report.Report` · `S.report` | UI: `app.ReportPanel`, `ResultPanel.add_to_report`, `refresh_report` |
| Uso da Jupyter / invio a app aperta | `launcher.py` (`explore`, `send_to_running_app`) | Vedi [§15](#15-discrepanze-e-punti-di-attenzione) |
| Dati di esempio per test | `demo_data.make_demo_df` | |
| **Database** (progetti, cartelle, dataset salvati, file) | `workspace.Workspace` · UI: `app.DatabaseDialog` (pulsante «Database» in cima alla sidebar), `save_to_database`, `open_database` | `S.database` (aperto al primo uso da `S.database_path`; `--db` in `main`) |
| **Importazione** (colonne, nomi, formati, anteprima, conferma) | `import_plan.py`: `FORMATS`, `KEEP`, `detected_format`, `column_plan`, `apply_plan`, `lost_counts`, `text_reload_needed`, `with_text_columns` · `engine.can_read_as_text`, `load_text_columns` · UI: `app.ImportDialog` (chiamata da `load_dataset_file` dopo la lettura del file e dell'eventuale foglio) | Si parte sempre da «Come rilevato»; il pulsante facoltativo «Proponi formati» (`import_plan.suggest_formats`, marker `import-suggest`) propone Numero / Numero intero / Data / Data senza ora per le colonne di testo i cui valori si convertono tutti (codici con zeri iniziali esclusi). I file aperti da riga di comando o da Jupyter non passano dalla finestra |
| **Note** (testo, origine, filtri, storia) | `notes.make_note`, `file_origin`, `database_origin`, `with_change`/`without_change` · `AppState.origins`, `AppState.note_for` | Ogni `ResultPanel.show(..., note=...)` riceve la nota; pulsante nota in `ResultPanel`, riquadro in `app.note_dialog` |

---

## 4. `engine.py` — logica dati (senza UI)

Sezioni nell'ordine del file:

| Sezione | Righe | Simboli principali |
|---------|------:|--------------------|
| Caricamento | 37–128 | `CSV_DELIMITERS`, `CSV_ENCODINGS`, `_detect_encoding`, `_sniff_delimiter`, `_read_csv`, `READERS`, `is_excel`, `excel_sheets`, `cell_block(cells)`, `read_excel_sheet(path, sheet, cells, columns, rows, **options)`, `load_file(path, sheet, cells)`, `load_bytes(name, content, sheet, cells)` |
| Filtri AND/OR annidati | 129–315 | `OPERATORS`, `NO_VALUE_OPS`, `LIST_OPS`, `CHOICE_OPS`, `MAX_OPTIONS`, `MAX_DESCRIBED_VALUES`, `PASTE_SEPARATORS`, `new_group`, `parse_pasted_values` (elenco incollato da Excel: righe, celle, «;»; virgole solo se non c'è altro), `merge_values`, `_describe_list` (liste lunghe abbreviate nel riepilogo), `new_condition`, `value_label` (testo dei valori proposti: numeri con virgola decimale e senza migliaia, date AAAA-MM-GG), `unique_values` (valori distinti per ogni tipo di colonna: testo, numeri, booleani, date), `search_values`, `ISO_DATE`, `_to_num` (anche «1.234,56» e «1,234.56»), `_to_date` (AAAA-MM-GG o giorno prima), `adapt_value`, `_condition_mask`, `filter_mask`, `has_conditions`, `apply_filters`, `describe_filters` |
| Struttura colonne | 316–328 | `describe_columns` |
| GroupBy | 329–560 | `AGG_FUNCTIONS` (nome italiano → funzione pandas), `NUMERIC_FUNCTIONS`, `NO_DATE_FUNCTIONS`, `allowed_functions(series)`, `check_measures(df, measures)` (`ValueError` prima di calcolare), `_group_modes`, **`aggregate(frame, groups, measures)`** (aggregazioni vettoriali su un groupby: range, q1/q3, nulli, % nulli, moda senza lambda per gruppo; usata anche da `pivot` e da `charts._aggregate`), `measure_name`, `unique_names`, `run_groupby`, `run_value_counts` (la pivot è in `pivot.py`) |
| Console SQL (DuckDB) | 459–535 | `ArrowCache` (colonne a tipi misti → testo), `ARROW_CACHE`, `run_sql(query, tables)`, `sql_table_name` |
| SQL su Arrow | 538–615 | `ARROW_BATCH_ROWS`, `ResultTooLarge`, `UNCONVERTIBLE`, `arrow_table(df)` (in cache), `to_arrow(df)` (senza cache, per il database), **`run_sql_arrow(query, tables, max_cells)`** (legge a blocchi e si interrompe oltre `max_cells`), `arrow_to_pandas` (interi con nulli → `Int64`) |
| Console Python | 603–700 | `run_python(code, env) -> (stdout, value, error)`, `chart_kind`, `_browser_value`, `echarts_options`, `figure_image`, `to_display_frame` |
| Export | 651–705 | `_prepare_for_excel`, `to_xlsx_bytes`, `to_csv_bytes(italian=True)`, `open_in_excel` |

Punti chiave:
- Tutte le funzioni `run_*` ricevono `filters` (albero) e applicano `apply_filters` per prime, copiando solo le colonne che usano.
- `run_sql` registra ogni DataFrame in DuckDB tramite Arrow (cache per `id(df)`; se la conversione fallisce passa il DataFrame diretto). La cache si svuota dopo l'esecuzione Python (il codice può modificare `df` sul posto).
- **`run_sql` ritorna `.df()` di DuckDB: va bene per risultati piccoli, ma su milioni di righe è ~6 volte più lento di Arrow→pandas e tiene il GIL.** Per operazioni pesanti usare `run_sql_arrow` + `arrow_to_pandas` (come fa `combine.join`). Le tabelle Arrow vengono da `arrow_table(df)` (cache per `id(df)`); `run_sql_arrow` non tocca la cache.
- `run_python` esegue in un `env` persistente e restituisce il valore dell'ultima espressione come in Jupyter.

---

## 4b. `pivot.py` — pivot come cubo (senza UI)

Due fasi, come la cache delle pivot di Excel:
- **`build_cube(df, spec) -> Cube`** (lenta, in thread): filtri (`filter_mask`, posizioni tenute per il drill), campi di righe e colonne codificati con `charts.category_keys` (codici interi ordinati, date raggruppate per `spec["dates"]`, nulli = «(vuoto)»), poi **tutti i livelli** (dettaglio, subtotali, totali di righe e colonne) in `Cube.cells`: chiavi `r0…`, `c0…` (`TOTAL = -1` = tutti gli elementi), `_row_level`, `_column_level`, un valore `a<i>` per ogni coppia (colonna, funzione) di `aggregates_of`. Le funzioni in `ROLLUP` (somma, conteggio, min, max, nulli) si ricavano dal dettaglio; le altre si ricalcolano dai dati per ogni livello (`_level`, con `eng.aggregate`). Prima di calcolare: `_check_fields` e `eng.check_measures` (funzioni numeriche su testi/date → errore immediato).
- **`render(cube, values, layout) -> PivotTable`** (rapida, ma in thread anche lei): controlli delle misure (`_check_measure`), limite `MAX_COLUMNS` (500) colonne di valori = elementi delle colonne × misure (oltre → `ValueError` che suggerisce di filtrare o spostare il campo nelle righe), `show_values` per ogni misura, livelli visibili (`_visible_levels`), ordinamento (`_order`; per valore con `_value_ranks`: totale della misura dell'elemento dentro il padre), tabella in piano (una serie di colonne per misura, nomi con `eng.measure_name`), etichette con `_labels` (il primo campo di totale dice «Totale», i successivi vuoti). `PivotTable` tiene `frame`, `row_codes`, `column_codes` (nome colonna → codici) e il cubo.
- `Cube.covers(spec)`: stessi campi, filtri e date (`cube_signature`) e aggregazioni già calcolate → la UI ridisegna senza ricalcolare.
- «Mostra valori come»: `CALCULATIONS` (Nessun calcolo, % di, Differenza da, Differenza % da, Totale progressivo in, % progressiva in, Rango dal più grande / piccolo in, Indice) × `REFERENCES` (Totale complessivo, Totale di riga, Totale di colonna, Riga padre, Colonna padre, Totale padre del campo base, Elemento del campo base, Precedente, Successivo). Riferimenti come chiavi trasformate (`_reference_keys`) cercate nel cubo (`Cube.lookup`); progressivi e ranghi lungo il campo base per ogni combinazione degli altri campi (`_along_field`). Percentuali in 0–100; divisioni per zero → vuoto.
- Utilità: `needs_base_field`, `needs_base_item`, `show_as_text`, `field_items` (elementi di un campo per la scelta dell'elemento base), `run_pivot(df, spec, layout)` (cubo + tabella), `drill(table, row, column)` (righe del dataset intero), `selection_text`.

## 5. `columns.py` — colonne calcolate (senza UI)

| Sezione | Simboli |
|---------|---------|
| Cambio formato | `CONVERSIONS`, `NUMERIC_CONVERSIONS`, `DATE_PARTS`, `to_number`, `to_date`, `convert`, `lost_values`, `default_name` |
| Formula | `formula_query` (colonna `__row_order__` per mantenere l'ordine), `evaluate_formula(df, formula, tables)` (usa `eng.run_sql` con `df` + altre tabelle) |
| Se… allora | `case_when(df, rules, otherwise, current)`, helper `_typed_values`, `_uniform`, `_parse_number` |
| Applicazione | `with_column(df, name, values, after)`, `_values`, **`calculate(df, spec, tables)`** (punto d'ingresso usato da `app.tab_columns`) |

`calculate` restituisce una **copia** del DataFrame; la validazione di nome/sovrascrittura è lì. Per la `spec` vedi [§13](#13-strutture-dati-chiave).

---

## 5b. `combine.py` — join e union (senza UI)

| Sezione | Simboli |
|---------|---------|
| Parti comuni | `ORIGIN`, `filtered(df, tree)` (filtri della union; stesso oggetto se non ci sono condizioni), `unique_labels`, `_unique`, `_check_names`, `_same_kind` (tipi con `formula_help.column_kind`) |
| Join: tipi, chiavi | `JoinType` (`label, description, sql, sides, keyed`), **`JOIN_TYPES`**, `KEY_OPERATORS`, `KeyMatch`/`KEY_MATCHES`, `NAMING`, `new_key`, `suggest_keys` (sceglie la colonna con più valori distinti su un campione: `KEY_SAMPLE_ROWS`, `MIN_KEY_UNIQUENESS`), `_checked_keys`, `_condition` |
| Join: piano colonne (per passo) | **`plan_chain(frames, steps, names, overrides)`** (piani di tutti i passi: ogni passo parte dalle colonne tenute del precedente, usa solo nomi e tipi), `join_plan(left, right, spec, names, overrides, left_sources)`, `merged_away`, `_merged_keys`, `_name_columns`, `_origin_entry` (voce `side: origin`), `column_sources(plan)` |
| Join: esecuzione (catena) | `_select_items`, `join_query`, `_arrow_source`, `with_row`, `_in_row_order`, **`join_chain(frames, steps, names, filters, plans)`** (un passo alla volta su tabelle Arrow con `eng.run_sql_arrow`; limite `MIN_RESULT_CELLS` / `RESULT_GROWTH`), **`join(left, right, spec)`** (wrapper di un solo passo) |
| Union | `UNION_TYPES`, `APPENDING`, `ALIGNMENTS`, **`union_plan(frames, align)`**, `entry_kinds`, `type_conflicts`, `_aligned`, `_set_operation`, **`union(frames, spec, names)`** |

Punti chiave:
- Il **join gira in DuckDB** (semantica SQL: i nulli non si corrispondono salvo `null_keys`; semi/anti con `SEMI`/`ANTI JOIN`; per i semi/anti di destra le tabelle si scambiano). Nessun `ORDER BY` né `row_number()`: la query restituisce i numeri di riga originali (`ROW_ORDER`) e `_in_row_order` controlla in modo vettoriale se sono già ordinati (di solito sì) e ordina solo se serve. Ordine: sinistra, poi destra, senza corrispondenza in fondo.
- **Limite**: il risultato si legge a blocchi e, oltre `max(MIN_RESULT_CELLS, RESULT_GROWTH × celle di partenza)` celle, `join` solleva un `ValueError` (chiavi con valori ripetuti da entrambe le parti moltiplicano le righe: prima il join andava in swap su disco e bloccava l'app). Il risultato torna a pandas con `eng.arrow_to_pandas`. Con `match="exact"` chiavi di tipo diverso (numero/testo/data/booleano) danno un errore che suggerisce «come testo».
- La **union gira in pandas** (`concat`, `drop_duplicates`, `merge` con indicatore): ordine e tipi restano quelli dei dati. INTERSECT/EXCEPT confrontano righe intere (NaN uguale a NaN) e richiedono ogni colonna in ogni dataset. Colonne con tipi diversi tra dataset → tutto testo (`type_conflicts`).
- **Piano colonne**: l'interfaccia lo mostra e lo modifica, `join`/`union` lo ricevono in `spec["columns"]` (se manca lo ricalcolano). Voce del join: `side, source, column, name, keep, duplicate, merged_with`; voce della union: `name, keep, sources` (una colonna, o None, per ogni dataset).
- `merge_keys`: le chiavi «=» tra colonne dello stesso tipo diventano una sola colonna `COALESCE(sinistra, destra)`; la chiave di destra non compare (`merged_away`).
- Le funzioni non cambiano i DataFrame in ingresso.

---

## 6. `formula_help.py` — aiuto alle formule (senza UI)

- Nomi colonna: `reserved_words`, `quote_column`, `column_kind` (testo/numero/data/booleano → icone in `app.KIND_ICONS`).
- `Function` (NamedTuple) + liste `TEXT`, `NUMBERS`, `DATES`, `LOGIC`, `WINDOWS` → raccolte in `GUIDE`.
- Segnaposto negli esempi: `PLACEHOLDER`, `placeholders`, `first_placeholder`; ricerca: `search_text`; risultati degli esempi calcolati da DuckDB: `example_result`, `format_value`, `SAMPLE`.
- Aggiungere una funzione alla guida = aggiungere una voce `Function(...)` nella lista giusta; nessuna modifica a `app.py`.

---

## 7. `charts.py` — dati dei grafici (senza UI)

| Sezione | Righe | Contenuto |
|---------|------:|-----------|
| Costanti | 22–48 | limiti (`MAX_*`), `DATE_UNITS`, `SORT_OPTIONS`, `TOP/BOTTOM`, `OTHERS`, `EMPTY`, `BOX_STATS` |
| Catalogo | 51–137 | `ChartType` (dataclass frozen), **`CHART_TYPES`** (23 tipi in gruppi: Confronto, Andamento, Composizione, Distribuzione, Relazioni), `used_fields(chart)`, `TYPE_OPTIONS`, `ChartData` |
| Utilità e categorie | 157–272 | `_column`, `_drop_missing`, `_labels`, `_factorize`, `category_keys`, `_bins` |
| Top N, «altri», ordinamento | 274–336 | `_apply_top`, `_limit_series`, `_resolved_sort`, `_order` |
| Aggregazione | 338–426 | `measure_label`, `_measures`, `_aggregate`, `_ranking`, `_wide_table`, `_row_percent`, `_cumulative_percent` |
| Preparazione per famiglia | 427–556 | `_prepare_aggregate`, `_prepare_histogram`, `_prepare_boxplot`, `_prepare_scatter`; registro `PREPARERS` (chiave = `ChartType.kind`) |
| Punto d'ingresso | 565 | **`prepare(df, spec) -> ChartData`** |
| Pannelli | ~605–665 | `_panel_notes`, `_panel_table`, `_prepare_panels`, `_attach_source` |
| Drill-down | fine file | `drill_positions(data, item)` (posizioni nel dataset; usata anche dal filtro incrociato delle dashboard), `drill(data, item)` (chiave `"p"` = pannello), `selection_text` |

`ChartType.kind` decide la **preparazione dati** (aggregate/histogram/boxplot/scatter); `ChartType.draw` decide il **disegno** in `chart_options` (cartesian/pie/funnel/treemap/heatmap/radar/boxplot/scatter); `variant` distingue donut/rose/combo/pareto/bubble.

---

## 8. `chart_options.py` — opzioni ECharts (senza UI)

| Sezione | Righe | Contenuto |
|---------|------:|-----------|
| Costanti/etichette | 20–55 | `LABEL_MODES`, `DECIMALS`, `DEFAULT_LABEL_SIZE`, `LABEL_SIZE_RANGE`, `label_size()`, `PLAIN_TEXT` (etichette senza contorno né ombra; fuori dagli elementi nel colore `View.ink`), `AUTO_*`, `_num` |
| Funzioni JavaScript | 47–110 | `js_number` (formato italiano, compatto), `js_axis`, `js_label`, `js_box_tooltip`, `js_point_tooltip`, `js_cell_tooltip`, `js_bubble_size` (chiavi con prefisso `":"` = funzioni JS per NiceGUI) |
| `View` | 112–135 | dataclass con le scelte di visualizzazione (etichette, decimali, compatto, smooth, media, titolo, `locale` = stile dei numeri, colori) |
| Parti comuni | 138–250 | `_base`, `_category_axis`, `_value_axis`, zoom, `_shares`, `_items`, `_average_line` |
| Costruttori per famiglia | 252–527 | `_cartesian`, `_pie`, `_funnel`, `_treemap`, `_heatmap`, `_radar`, `_boxplot`, `_scatter` |
| Pannelli | fine file | `BY_CATEGORY`, `value_bounds`, `_colored`, `_same_colors`, **`build_panels(data, view, same_scale)`** (titolo del pannello «colonna: valore») |
| Ingresso | 528–542 | `BUILDERS` (chiave = `ChartType.draw`), **`build_options(data, view)`** (alla fine passa le opzioni da `number_format.localized(options, view.locale)`: le funzioni JavaScript sono scritte per `it-IT` e per lo stile americano si sostituisce il locale nelle chiamate `toLocaleString('it-IT', {…})`; le date restano com'erano). `stats_charts.build_options` fa lo stesso |

---

## 9b. `stats.py` e `stats_charts.py` — statistica (senza UI)

**`stats.py`** (sezioni nell'ordine del file): costanti e scelte (`CONFIDENCE_LEVELS`, `CORRELATION_METHODS`, `OUTLIER_METHODS`, `TIME_UNITS`, `TIME_AGGREGATIONS`, limiti `MAX_*`, `FIELD_LABELS`) · risultato (`Plot(kind, title, data, x_name, y_name)`, `StatResult(tables, plots, notes)`) · utilità (`is_numeric`, `numeric_columns`, `_numbers`, `_groups`, `_test`, `_table`, `_indices`, `_sample_index`) · riepiloghi e grafici comuni (`numeric_summary`, `category_summary`, `_box_plot`, `_histogram`, `_scatter`) · analisi: `describe`, `distribution` (+ `_normality_tests`), `frequencies`, `correlation`, `outliers` (+ `outlier_bounds`), `compare_groups` (+ `_tukey`), `one_sample`, `paired`, `independence`, `linear_regression`, `logistic_regression` (+ `_design`, `_coefficients`, `_binary`, `_roc`), `time_series` (+ `time_series_values`, `_stationarity`, `_autocorrelation`) · catalogo **`ANALYSES`** (`Analysis(label, group, description, fields, run, labels)`), `ANALYSIS_OPTIONS`, `field_label`, **`run(df, spec)`** (filtri prima, avvisi numerici soppressi).

`spec`: `{"analysis": chiave di ANALYSES, "filters": <albero>, <un valore per ogni campo di fields>}`. Risultati solo numerici (tabelle e grafici), nessun testo interpretativo; le note dicono solo cosa è stato campionato, interpolato o saltato.

**`stats_charts.py`**: `build_options(plot, view)` con `BUILDERS` per `kind` (`bars`, `boxplot`, `scatter`, `lines`, `heatmap`); riusa `_base`, `View`, `View.label_from`, `_average_line` di `chart_options`. `view` applica le scelte di aspetto della scheda Grafici (titolo, decimali, numeri compatti sempre; etichette (con dimensione `View.label_size`; nei box plot sulla mediana), linee smussate, media secondo `VIEW_FIELDS`; `view_fields(plots)` = scelte da mostrare). Contenuto di `plot.data` per tipo: docstring del modulo.

---

## 9c. `excel_writer.py`, `excel_split.py`, `excel_report.py` — file Excel (senza UI)

**`excel_writer.py`**: scelte (`TABLE_STYLES` nome → stile delle Tabelle di Excel; `PLAIN` → `None` = intervallo semplice: intestazione in grassetto centrata in orizzontale e in verticale, nessun filtro, nessuna riga bloccata (`freeze` vale solo con la Tabella); `CONDITIONAL`; formati `INTEGER`, `DECIMAL`, `DATE`, `DATE_TIME`, `NEGATIVE_RED`; `MAX_ROWS`), `ExcelStyle(table_style, totals, conditional, red_negatives, index, freeze)`, `Sheet(name, data, title, note)` · nomi (`sheet_name`, `file_name`, `_deduplicated`, `_unique_headers`) · scrittura (`_Formats`, `_kind`, `_cells` (nulli → celle vuote), `_width`, `_parts` (oltre `MAX_ROWS` → «nome (2)»), `_write_sheet` (titolo e nota sopra, Tabella con `total_function` sum, condizionali sulle colonne numeriche, riquadri bloccati), `_plain_totals` (`SUBTOTAL(109)`), `_write_index` (link interni)), **`workbook_bytes(sheets, style)`** · consegna (`zip_bytes`, `save_to_folder` senza sovrascrivere).

**`excel_split.py`**: spec nella docstring del modulo. `MODES` (`values` / `rows`), `ROWS_INTO`, segnaposto `{colonna}`, `{n}`, `{nome}` (`fill_pattern`, `default_pattern`, `key_text` per date e nulli), `_groups`, `_by_values`, `_by_rows`, **`split_plan(df, spec) -> list[FilePart]`** (nomi già validi e unici; limiti `MAX_FILES`, `MAX_SHEETS`), `plan_table` (anteprima), `output_columns`, `build_files`, **`split(df, spec, style) -> {nome file: bytes}`**.

**`excel_report.py`**: `ReportItem(name, data, source, title, note, id)`, `Report` (`add`, `rename`, `move`, `remove`, `clear`, `unique_name`, `sheets`, `workbook(style)`). Le tabelle sono istantanee del `ResultPanel.df` di origine (nessuna copia: i risultati non vengono mai modificati sul posto).

---

## 9g. `dashboard.py` — dashboard della Console (senza UI)

Il codice della Console **descrive** la dashboard, `app.DashboardView` la disegna:
```python
dash = Dashboard(df, "Vendite")          # df = dataset della finestra
dash.slicer("prodotto")                  # multi (testo), single, range (numeri e date, default)
dash.kpi("Fatturato", "importo", "somma", decimals=0, suffix=" €")   # senza colonna: numero di righe
dash.chart("line", x="data", value="importo", date_unit="Mese", series="regione", width=8, height=360)
dash.table(groups=["categoria"], value="importo", sort_by=..., limit=...)   # oppure columns=[...] (righe)
dash.pivot(rows="prodotto", columns="regione", value="importo")
dash.python(lambda d: ..., title=...)    # d = righe filtrate → tabella, grafico o valore
dash.text("## titolo"); dash.page("Dettaglio")
dash                                     # i metodi restituiscono la dashboard
```
- Misure: `value` + `fn` oppure `measures` (nomi di colonna, coppie (colonna, funzione), dict come nel Group By): `measures_of`. Tipo di grafico per chiave o etichetta (`chart_key`); opzioni del grafico: `CHART_FIELDS` (dati, a `charts.prepare`) e `VIEW_FIELDS` (aspetto, a `chart_options.View`); opzioni sconosciute, colonne o funzioni sbagliate → `ValueError` con i nomi disponibili (il codice è scritto da Claude a partire dai metadati).
- Griglia di `GRID_COLUMNS` = 12 colonne, `width` per visual (`DEFAULT_WIDTHS`); più pagine come schede; gli slicer valgono per tutta la dashboard.
- **`compute(page)`**: maschera degli slicer (`filter_tree` → `engine.filter_mask`), poi per ogni visual le sue righe e la funzione di `CALCULATIONS` (`_kpi`, `charts.prepare`, `_table`, `_pivot`, `_python`); un errore resta nel suo visual (`Output.error`). `Output.positions` = righe del dataset usate.
- **Filtro incrociato**: `select(visual, output, item)` (chiavi `DRILL_KEYS`) salva `CrossFilter`; `compute` calcola prima il grafico cliccato sui soli slicer e filtra gli altri visual sulle righe dell'elemento (`charts.drill_positions`). Stesso elemento di nuovo = tolto; cambio di slicer o di pagina lo toglie (`DashboardView`).
- UI: `DashboardView(dash, name, expandable)` — `build` (titolo, filtro incrociato con ✕, schermo intero, slicer, schede delle pagine, griglia), `refresh` (thread, `version` scarta i calcoli superati), `draw` + `DASHBOARD_DRAWERS`; tabelle e pivot in `ResultPanel(section="Console", fit_height=True)`; KPI e tabelle seguono il «Formato numeri» della Console. `full_screen`: stessa dashboard in un dialog massimizzato; alla chiusura la vista si ricostruisce (dialog eliminato per ultimo, §15.18).

---

## 9. `ai_assistant.py` e `chart_ai.py` — Claude (senza UI)

**`ai_assistant.py`**: `MODELS`, `DEFAULT_MODEL`, `EFFORTS`, `MAX_TOKENS`, `RULES` (SQL/Python), `SYSTEM_PROMPT`, `describe_tables` (solo nomi e tipi), `build_prompt`, `clean_code`, `validate_code` (sintassi, senza eseguire), `ask` (chiamata API generica, riusata da `chart_ai`), **`generate_code`**.

**`chart_ai.py`**: prompt (`SYSTEM_PROMPT`, `FIELDS`, `RULES`, `build_prompt`) → `parse_answer` (JSON) → `normalize` (valida tipo di grafico, colonne, funzioni; i campi non usati dal tipo restano di default) → **`generate_settings`**. `DEFAULTS` deve restare allineato ai controlli di `app.ChartTab.apply`.

Privacy: all'API vanno solo richiesta e metadati. Mantenere questa garanzia in ogni modifica.

---

## 9f. `number_format.py` — formato dei numeri (senza UI)

- `NumberFormat(mode, decimals, style)` (dataclass frozen, validata): `mode` ∈ `MODES` (`NO_FORMAT` «Nessun formato», `DECIMAL` «Formato decimale»), `decimals` in `DECIMALS_RANGE` (0–10; `clamp_decimals`), `style` ∈ `STYLES` (`ITALIAN` `1.234,56` → `it-IT`, `AMERICAN` `1,234.56` → `en-US`; ogni `Style` ha locale, separatore decimale, esempio). Predefinito: decimale, 2, italiano (`PRECISE_DECIMALS` = 4 per le tabelle della Statistica).
- `grid_formatter(fmt, integer, precise=False)` → `valueFormatter` JavaScript di una colonna numerica di AG Grid (un `Intl.NumberFormat` creato una volta per colonna, `useGrouping: 'always'` perché il locale italiano da solo non raggruppa i numeri a 4 cifre); `None` = valori com'erano. Gli **interi** hanno solo il separatore delle migliaia; con `precise` (Statistica) `decimals` è un massimo (i conteggi restano interi) e i valori < 0,001 o ≥ 1e12 vanno in notazione esponenziale (p-value).
- `localized(options, locale)` → opzioni ECharts con lo stile dei numeri scelto (vedi §8).
- `format_number(value, decimals, style)` → testo del numero lato Python (KPI delle dashboard).
- Test: `test_number_format.py` (la resa JavaScript è provata con Node, se installato).

---

## 9d. `workspace.py` e `notes.py` — database e note (senza UI)

`workspace.py` (un file DuckDB, una sola connessione protetta da `RLock`: si usa anche dai thread di `run.io_bound`):
- Tabelle di catalogo `folders (id, parent_id, name, created)` — `parent_id` NULL = progetto — e `datasets (id, folder_id, name, rows, cols, note, created)`; i dati di ogni dataset in `data_<id>` (`table_name`). Id da una sola sequenza `ids`.
- Nomi: `check_name` (vuoto, già usato tra fratelli, anche per maiuscole), `storable_columns` (colonne come testo e uniche senza maiuscole: in DuckDB «A» e «a» coincidono).
- Cartelle: `folders`, `folder`, `create_folder(name, parent_id)`, `rename_folder`, `subtree`, `delete_folder` (a cascata, in transazione, con le tabelle dei dataset), `folder_choices` (id → «Progetto › Sotto», ordinati), `folder_path`.
- Dataset: `datasets(folder_id)`, `dataset`, `find_dataset`, **`save_dataset(folder_id, name, df, note, replace)`** (Arrow con `eng.to_arrow`, in transazione; `replace` sostituisce il dataset con lo stesso nome), **`load_dataset`** (con `eng.arrow_to_pandas`), `rename_dataset`, `move_dataset`, `set_note`, `delete_dataset`.
- Albero per `ui.tree`: `tree()` (nodi `id`, `label`, `icon`, `children`; cartelle prima dei dataset), `node_id(kind, id)` / `parse_node` con `FOLDER`, `DATASET`.

`notes.py`: **`make_note(operation, sources, origins)`** — `sources` = `(nome dataset, albero filtri)` per ogni dataset usato (numerati se più di uno), `origins` = storia di ogni dataset riportata rientrata (`INDENT`). Storie: `file_origin`, `database_origin(path, created, note)` (la nota salvata diventa storia), `with_change` / `without_change` (modifiche di un dataset e loro annullamento).

Flusso: caricamento file → `S.origins[name] = file_origin`; ogni scheda passa `note=S.note_for(operazione, [(dataset, albero)])` a `ResultPanel.show`; «Salva come dataset» porta la nota in `S.origins`; `S.replace`/`revert` aggiungono/tolgono «Modificato: …»; «Apri» dal database → `database_origin`. Così la nota di un risultato ricavato da un dataset salvato contiene anche la storia precedente.

---

## 9e. `import_plan.py` — piano di importazione (senza UI)

- Piano = lista di voci `{"position", "name", "keep", "detected", "format"}` (le prime tre come `structure.column_items`).
- `FORMATS`: `KEEP` («Come rilevato»), Testo, Numero, Numero intero, Data, Data senza ora (conversioni di `columns.convert`).
- `detected_format(series)`: Sì/No, Numero intero, Numero, Data, Testo. Nessun formato proposto: il piano parte sempre da `KEEP` (scelta dell'utente).
- `suggest_formats(df, plan, decimal)`: proposta facoltativa dei formati per le colonne lette come testo (primi `SUGGEST_ROWS` valori).
- `column_plan(df)`, `apply_plan(df, plan, decimal)` (tiene, rinomina con `structure.apply_columns`, converte), `lost_counts(df, plan, converted)` (valori non vuoti persi per colonna convertita).
- Colonne lette come numeri o date ma scelte come «Testo»: `text_reload_needed(plan)` → rilette dal file con `engine.load_text_columns` e sostituite con `with_text_columns(df, texts)`.

`app.ImportDialog(file_label, df, text_loader)` (finestra `persistent`): righe colonna (marker `import-keep`, `import-name`, `import-format`), Tutte / Nessuna, separatore decimale, contatore; `preview` (prime `PREVIEW_ROWS` righe, avviso valori persi `import-lost`, errori `import-error`, griglia `import-preview`); `change_format` → `load_texts` (rilettura una sola volta per colonna, in `texts`), `source()`; `restore` (Ripristina al piano iniziale, `import-restore`), `confirm` (Carica, `import-ok`: piano sull'intero dataset, avviso valori persi), `ask()` → DataFrame o None (Annulla).

---

## 10. `style.py` — aspetto grafico

Sezioni: colori (`HEX`, `is_color`, `mix`, `brightness`, `is_readable`, `readable_on`, `ACCENTS`, `CHART_PALETTES`, `NEUTRALS`, `ADVANCED`, `RADIUS_RANGE`) · preferenze (**`Appearance`** dataclass frozen: `dark, accent, tint, charts, radius, canvas, surface, ink`; `PRESETS` = temi pronti, `DEFAULT`, `to_dict`/`from_dict` (valida e normalizza i valori salvati), `preset_name`) · colori derivati (`Palette` NamedTuple con `heat` ed `editor`; **`resolve(appearance) -> Palette`**: neutri + tinta dell'accento + colori avanzati, testo/bordi/sfondi secondari derivati) · CSS (`BASE_CSS` con variabili (anche finestre spostabili e ridimensionabili) e `DIALOG_SCRIPT` (trascinamento dalla prima riga della card, con il titolo sempre visibile) `--ink`, `--surface`, `--radius*`, `--on-primary`…; `css_variables`) · aspetto attivo (`apply(appearance)` a inizio pagina, `switch(appearance, dark_mode)` dal vivo via JavaScript) · grafici (`chart_theme(palette)`, `chart_view(palette)`).

Nuovo tema pronto = nuova voce in `PRESETS`; nuova serie di colori dei grafici = voce in `CHART_PALETTES`.

---

## 11. `app.py` — interfaccia NiceGUI

| Sezione | Righe | Contenuto |
|---------|------:|-----------|
| Costanti | 31–34 | `ROW_LIMITS`, `DEFAULT_PREVIEW_ROWS`, `DATA_PREVIEW_ROWS`, `FORM_GRID` |
| Stato globale | 82–210 | `AppState` (`datasets`, `active` (ultimo dataset caricato o scelto: predefinito delle nuove finestre e dei selettori, non più «dataset attivo» della pagina), `tab`, `lang`, `code`, `env`, `api_key`, `ai_model`, `ai_effort`, `appearance`, `themed`, `report`, `report_name`, `report_panel`, `excel_folder`, `pickers`, `panes` (finestre di lavoro), `watchers`, `number_formats` (sezione → `NumberFormat`, sopravvive a `page_content.refresh()`), `panels` (i `ResultPanel` registrati), `undo`, `origins`, `database_path`; proprietà `database` (apre `workspace.Workspace` al primo uso o se cambia il percorso); metodi `add(name, df, activate, origin)`, `add_panel`, `format_of(section, precise)`, `replace(name, df, description)`, `revert`, `rename`, `remove`, `note_for`, `add_themed`, `sql_tables(name)` (`df` = quel dataset), `palette`, `prepare_env(name)`, `live_panes`, `live_pickers`, `watch_datasets(element, update)` / `notify_watchers()`); istanza **`S`** |
| Utilità griglia | 94–159 | `_json_rows`, `_col_defs` (+ `_group_by_source`: intestazioni per dataset di origine), `_unique_columns`, `notify_error` |
| `NumberFormatMenu` / `ResultPanel` | 296–470 | `NumberFormatMenu(fmt, on_change)`: pulsante «Formato numeri» con menu (radio Nessun formato / Formato decimale, `ui.number` dei decimali, radio dello stile; decimali e stile disattivati senza formato; `show(fmt)` mostra una scelta fatta altrove, `updating` evita il rimbalzo degli eventi). `ResultPanel(..., *, section)`: `fmt` (da `S.format_of`), `change_format` (salva in `S.number_formats[section]`, aggiorna menu e colonne di tutti i pannelli della sezione), `redraw_columns` (solo `columnDefs`, senza toccare le righe). Griglia AG Grid (altezza `GRID_HEIGHT`, scorrimento interno) + pulsanti (`precise`: 4 decimali/esponenziale, `fit_height`: altezza sulle righe fino a `GRID_HEIGHT`), `on_cell_click(position, column)` facoltativo (evento `cellClicked`, posizione dalla chiave nascosta `ROW_ID`): `show(df, name, pinned, sources, note)` (`note` = origine e filtri; None → `current_note` usa il solo dataset attivo), menu «Esporta e salva» (`ui.dropdown_button`, marker `result-actions`; voci create con `result_action(label, icon, handler, marker)`: `dl-xlsx`, `dl-csv`, `open-excel`, `save-dataset`, `db-save`, `add-report`, `show-note`; `_enable` attiva menu e schermo intero), `full_screen` (copia della griglia in un dialog massimizzato, marker `full-screen`), `add_to_report` (dialog nome foglio + titolo → `S.report.add`, marker `add-report` / `report-ok`), `download_xlsx`, `download_csv`, `open_excel`, `save_as_dataset` |
| `FilterBuilder` | 269–382 | costruttore di filtri annidati (anche nelle regole Se… allora); valori: `_value` → `_text_value` / `_choice_value` (oltre `MAX_CHIPS` valori il campo ne mostra il numero, `count_text`); per «in lista»/«non in lista» pulsante `paste-values` → `_paste` (finestra `paste-text`, `paste-ok`: aggiunge ai valori scelti o li sostituisce); `filter_box(df, expanded)` lo avvolge in un expansion (chiuso di default; `on_render` aggiorna il conteggio nel titolo) |
| `AggregationList` | 584–660 | lista (colonna, funzione) per Group By, Pivot, Grafici; `on_change` (anche async: i gestori ne restituiscono il risultato) e `details(item)` per controlli sotto ogni misura; `df` facoltativo → `functions(col)` propone solo `eng.allowed_functions`, `adapt(item)` riporta a «conteggio» una funzione non ammessa, `set_col` cambia colonna e adatta subito (prima del refresh) |
| `ai_settings` | 870–880 | API key, modello, sforzo (condiviso Console e Grafici) |
| Finestre di lavoro | 855–975 | `MAX_WINDOWS`, `NEW_DATASET_SECTION`, `dataset_size`, `DatasetPane`, `PaneStack`, `refresh_dataset` (vedi §3) |
| Scheda **Dati** | 976–1030 | `tab_data(name)` |
| Scheda **Group By** | 536–570 | `tab_groupby` (filtri a scomparsa in alto, card dei controlli, pulsante «Esegui Group By», risultato) |
| Scheda **Pivot** | 900–1110 | `PivotTab`: filtri, Righe/Colonne (`pivot-rows`, `pivot-columns`), raggruppamento date (`date_controls`, `pivot-date`), `AggregationList` con `details=show_as_controls` (`show-as`, `relative-to`, `base-field`, `base-item`; `complete_measure` propone campo ed elemento base), card Layout (totali, subtotali, celle vuote, ordinamento `pivot-sort`, nomi), «Crea Pivot» (`run-pivot`, `self.button`: unico modo di aggiornare la pivot; `build` ricalcola il cubo solo se non `cube.covers`, poi `show`; `changed` aggiorna solo i controlli); `build_cube` e `render` passano entrambi da **`compute(funzione, *args)`** (thread con `run.io_bound`, pulsante in caricamento, `version` scarta i calcoli superati e i loro errori; a spec e misure si passa una `copy.deepcopy`, mai al dataset o al cubo); clic su una cella → `select` (testo della cella, pulsante attivo; `show` azzera la scelta con `clear_selection`), «Righe di origine» (`pivot-drill`) → `drill` nel `ResultPanel` «Righe di origine della cella» |
| Scheda **Join / Union** | 528–908 | `SourcePicker`, `PlanPanel` (base), `JoinPanel`, `UnionPanel`, `tab_combine` (vedi sotto) |
| Scheda **Colonne calcolate** | 911–1164 | `MODES`, `insert_text`, `column_picker`, `example_view`, `function_details`, `function_card`, `formula_guide`, `formula_editor`, `CaseRules`, `tab_columns` (`spec`, `compute`, `preview`, `apply`) |
| Scheda **Console** | 1167–1320 | `console_chart_example`, `show_console_chart`, `console_rows` (filtri della Console), `tab_console` (filtri a scomparsa in alto: cambiano solo `df` in Python e SQL, il dataset intero resta per nome; con filtri attivi df riassegnato non sostituisce il dataset; `execute`: dashboard (`DashboardView`), grafico se `engine.chart_kind` lo riconosce, altrimenti tabella o testo; `insert_example(make_example)` per «Esempio di grafico» e «Esempio di dashboard», `generate`, `change_language`) |
| **Dashboard della Console** | dopo la Console | `DASHBOARD_SECTION`, `DashboardView`, `DASHBOARD_DRAWERS` (vedi §9g) |
| Scheda **Grafici** | 1361–1585 | `ChartTab` (vedi sotto) |
| Scheda **Statistica** | 1633–1750 | `StatsTab` (vedi sotto) |
| Scheda **Excel** | 1753–1955 | `refresh_report`, `run_excel`, `OutputSettings`, `SplitPanel`, `ReportPanel`, `tab_excel` (vedi sotto) |
| Aspetto | — | `APPEARANCE_KEY`, `load_appearance`, `section_title`, `valid_color`, `AppearanceDialog` (`controls`, `presets`, `accents`, `warning` sono `refreshable_method`; `set` cambia alcune preferenze, `set_all` le sostituisce e ricostruisce i controlli; marker `appearance`, `preset-<nome>`) |
| Header e caricamento file | 3359–3460 | `DatasetMenu` (`render` refreshable, `rename` apre la finestra nello slot del pulsante dell'header), `refresh_menus`, `follow_datasets`, `update_header(renamed)` (dataset caricati/rinominati/rimossi: menu, finestre e selettori seguono, poi `S.notify_watchers()`; primo dataset o nessuno → `page_content.refresh()`), `show_new_dataset(name)` (la finestra di Dati mostra il dataset appena caricato o aperto e il menu delle sezioni della finestra del browser che l'ha caricato apre la scheda Dati: altrimenti da un'altra scheda il dataset non si vedeva), `rename_dialog(old_name)`, `remove_dataset(name)`, `registra_dataset`, `ask_sheet`, `load_dataset_file`, `handle_upload`, `upload_dialog` (upload di file e pulsante «Range Excel» → `ExcelRangeDialog`) |
| **Database** | — | `note_dialog(title, text, on_save)` (riquadro di testo, sola lettura o modificabile; marker `note-text`, `note-save`), `ask_text`, `confirm`, `save_to_database(df, name, folder_id, on_saved)` (cartella, nome, nota scritta a mano (vuota di default), sostituisci; `on_saved` riceve il dataset salvato; marker `db-folder`, `db-name`, `db-save-ok`), **`DatabaseDialog`** (finestra: albero `db-tree` a sinistra, `details` a destra; cartella: nuova sottocartella, «Salva qui» un dataset caricato scelto in un selettore (`save_loaded`; la finestra resta aperta e poi seleziona il nuovo dataset: una finestra aperta dentro un dialog che si chiude verrebbe eliminata con lui), rinomina, elimina; dataset: apri, nota modificabile, rinomina, sposta, elimina; `_run` esegue, notifica i `ValueError` e ricarica l'albero), `open_database` |
| Assemblaggio | 2084–2160 | `TABS` (nome → gruppo, icona, builder; ordine = ordine nel menu), `MULTI_WINDOW`, `OWN_SOURCES`, `sidebar()` (`ui.left_drawer` con pulsante «Database» in cima; sotto `SIDEBAR_BREAKPOINT` parte chiuso e si chiude dopo la scelta di una sezione, `hide_if_narrow`; al cambio di sezione la pagina torna in cima; marker `database`, poi `ui.tabs` verticali e titoli di gruppo; restituisce pannello e schede), `page_content()` (`@ui.refreshable`: titolo della sezione + `ui.tab_panels` legati al menu della finestra in cui si ricostruisce; ogni sezione = `PaneStack` oppure, per `OWN_SOURCES`, `builder()`; senza dataset disattiva le voci), `main_page()` (header con pulsante menu + sidebar + contenuto a tutta larghezza; registra menu delle sezioni e `DatasetMenu` in **`windows`**: id del client → (menu, menu dei dataset), tolti con `client.on_delete`), `main()` |

`ChartLook`: scelte di aspetto (etichette e loro dimensione in pixel con `ui.number`, decimali, «Separatori numeri» (`number_style` → `locale` in `settings`; non fa parte di `controls`, quindi l'AI non lo tocca), altezza, titolo, numeri compatti, linee smussate, media) con `on_change`, `controls` (quelle che compila anche l'AI), `show_only(fields)`, `settings(title)` (campi di `chart_options.View` + colori del tema). `ChartTab`: `ai_controls` → `data_controls` → `ChartLook` (`self.look`) → `data_table`; stato: `update_controls` (mostra solo i campi usati dal tipo), `spec()` (dati, richiede «Crea grafico»), aspetto da `self.look.settings()` (ridisegna subito), `apply(settings)` (compila dai valori dell'AI), `build` → `charts.prepare`, `draw` → `chart_options.build_options` + `ui.echart`, `select`/`show_table` (drill-down).

`StatsTab`: `build_controls` (un controllo per campo di `stats.FIELD_LABELS`), `update_controls` (visibilità ed etichette dai campi dell'analisi), «Aspetto dei grafici» con `ChartLook` (`self.look`; scelte facoltative da `stats_charts.view_fields`; titolo scritto = titolo del grafico, con più grafici «titolo — grafico»), `spec`, `compute` → `stats.run` in thread, `draw` (un `ui.echart` per `Plot`, altezza scelta, marker `stat-chart`), `show_tables` (una scheda per tabella, ognuna con un `ResultPanel`). Al cambio di tema le schede registrate con `S.add_themed` (Grafici, Statistica) vengono ridisegnate.

Scheda **Excel** (`tab_excel`): un toggle sceglie `SplitPanel` o `ReportPanel`; sotto, `OutputSettings` comune (stile della tabella — con l'intervallo semplice `update_freeze` nasconde «Blocca l'intestazione» —, condizionali, totali, negativi in rosso, indice, blocca intestazione; destinazione: download — un file diretto, più file in ZIP — o cartella del PC, `S.excel_folder`).
- `SplitPanel`: `SourcePicker` (dataset + filtri), modo per valori (file per / fogli per) o per righe (blocchi in file o fogli), nomi con segnaposto, colonne da scrivere, togli chiavi; `preview` (piano in un `ResultPanel`, marker `split-preview`), `execute` (marker `run-split`).
- `ReportPanel`: elenco di `S.report` (nome del foglio, titolo, nota, su/giù/togli), nome del file (`S.report_name`), «Genera report» (marker `run-report`). Si registra in `S.report_panel`; `refresh_report()` lo aggiorna quando un risultato viene aggiunto da un'altra scheda. Il report sopravvive a `page_content.refresh()` perché sta in `S`.

Scheda **Join / Union** (`tab_combine`): un toggle sceglie `JoinPanel` (catena di passi) o `UnionPanel` (costruita alla prima apertura); i due pannelli condividono un solo `ResultPanel`. Le colonne di ogni passo sono un expander per colonna, chiusi di default.
- `SourcePicker`: select del dataset + filtri («Filtri (applicati prima)», chiusi di default); il costruttore dei filtri si ricrea al cambio dataset. Si registra in `S.pickers` perché `update_header` ne aggiorni l'elenco dei dataset.
- `JoinStep`: un passo = una card con dataset da unire (`SourcePicker`), tipo, chiavi (`render_keys`), interruttori e, in `render_plan`, un expander per dataset di origine (titolo con colonne incluse/totali, aperto di default, stato in `open_sources`) che contiene i pulsanti Tutte/Nessuna/Senza duplicati e un expander per colonna (`plan_card`, chiuso di default, nota nell'intestazione).
- `JoinPanel`: catena di passi (`steps`), dataset di partenza (`start`), `overrides` keyed (passo, lato, colonna); `refresh()` ricalcola `plan_chain` e aggiorna ogni `JoinStep`; `execute()` chiama `combine.join_chain` con i piani calcolati da zero (`compute`) e mostra il risultato; `overwrite_start()` («Applica al dataset», marker `join-apply`) usa lo stesso `compute` e sostituisce il **dataset di partenza** con `S.replace` (annullabile dalla Struttura), poi `refresh_dataset`.
- `UnionPanel`: `new_picker`/`add_source`/`remove_source`, `change_type` (INTERSECT/EXCEPT passano a «solo colonne comuni»), `reset_plan`, `map_column`, `move`, `sort_plan`, `execute`.
- `column_card(status, entry, title)` e `mark_notes(plan, status, note)`: expander per colonna e note; `run_combination(button, title, function, *args)`: esecuzione in thread con pulsante in caricamento e notifica con righe, colonne e tempo.
- Le parti `@ui.refreshable_method`

Schema del flusso tipico di una scheda: **controlli → `spec`/parametri → `run.io_bound(funzione_engine, ...)` → `ResultPanel.show(df, name)`**; errori con `notify_error`.

Cambiare il dataset di una finestra ricostruisce solo quella finestra. Modificare un dataset (filtri, struttura, colonna calcolata, join, Console, annulla) richiama `refresh_dataset(name)`: si ricostruiscono solo le finestre e i `SourcePicker` su quel dataset (la Console che ha riassegnato `df` resta com'è, `keep`). `page_content.refresh()` serve solo al primo dataset caricato o quando non ne resta nessuno.

---

## 12. `launcher.py` — uso da Jupyter

`collect_datasets` → `to_parquet_bytes` → se la porta è occupata (`is_app_running`) → `send_to_running_app` (POST `/api/dataset?filename=<nome>.parquet`), altrimenti `start_app` (processo separato con file Parquet temporanei). Entry point: **`explore(*dfs, porta=8090, **named)`** (alias `esplora = explore` per i notebook esistenti).

---

## 13. Strutture dati chiave

**Albero filtri** (JSON, usato ovunque):
```python
{"logica": "AND" | "OR", "condizioni": [<condizione> | <gruppo>, ...]}
# condizione: {"col": str, "op": str (da OPERATORS), "val": str | list}
```
Creazione: `eng.new_group()`, `eng.new_condition()`.

**Aggregazioni** (Group By, Pivot, misure dei grafici): `[{"col": "importo", "fn": "somma", "name": ""}, ...]` (`name` facoltativo, solo Group By e Pivot), `fn` ∈ chiavi di `eng.AGG_FUNCTIONS`.

**`spec` colonna calcolata** (`columns.calculate`):
```python
{"mode": "convert" | "formula" | "case",
 "target": str, "overwrite": bool,
 "column": str, "to": str, "decimal": "," | ".",      # convert
 "formula": str,                                       # formula
 "rules": [{"filters": <albero>, "value": str}], "otherwise": str}   # case
```

**Catena di join** (`combine.join_chain`): `steps` = lista di passi, ognuno con `right` (indice del dataset in `frames`, 0 = partenza); `filters` = un albero per dataset; `plans` = piano di ogni passo (da `plan_chain`). Un passo:
```python
{"how": "left", "keys": [{"left": "id", "op": "=", "right": "cliente_id"}],   # op ∈ combine.KEY_OPERATORS; cross ignora le chiavi
 "match": "exact" | "text" | "text_loose", "null_keys": bool, "merge_keys": bool, "naming": "duplicates" | "right" | "all",
 "indicator": bool, "left_filters": <albero>, "right_filters": <albero>,
 "columns": [{"side": "left"|"right", "source": str, "column": str, "name": str, "keep": bool,
              "duplicate": bool, "merged_with": str | None}, ...]}            # piano: da combine.join_plan
```
**`spec` union** (`combine.union`):
```python
{"how": "union_all" | "union" | "intersect" | "except", "align": "by_name" | "common" | "first" | "position",
 "origin": bool, "filters": [<albero>, ...],                                   # un albero per dataset
 "columns": [{"name": str, "keep": bool, "sources": [str | None, ...]}, ...]}  # piano: da combine.union_plan
```

**`spec` grafico** (`charts.prepare`): `chart, x, series, y, size, measures, date_unit, bins, top_n, top_from, others, sort, panels, panel_date_unit, filters`. **`view`** (solo aspetto): `labels, decimals, compact, smooth, average, title` + `style.chart_view(theme)`.

**`ChartData`**: `chart, table` (disegnata; con i pannelli: tabelle dei pannelli una sotto l'altra, pannello in prima colonna), `panel_name`, `panels` (etichetta → `ChartData` del pannello), `prepared_rows` (righe durante la preparazione, poi None), `source` (dataset, stesso oggetto) + `positions` (posizioni delle righe di origine), `rows` / `rows_at(selector)` (righe di origine lette dal dataset, con il suo indice; colonna temporanea `ROW_KEY` solo durante `prepare`), `x_name, series_name, value_name, size_name, x_keys, series_keys, series_of, points, notes`.

**Tabelle SQL**: `S.sql_tables()` → `{sql_table_name(name): df, ..., "df": active_dataset}`.

---

## 14. Test

Tutti i test sono in `app/tests/` (da `app/`: `python -m pytest -q`; 831 passati, 3 saltati all'ultimo controllo).

| Modulo | File di test |
|--------|--------------|
| `engine.py` | `test_engine.py` |
| `pivot.py` | `test_pivot.py` |
| `columns.py` | `test_columns.py` |
| `combine.py` | `test_combine.py` |
| `formula_help.py` | `test_formula_help.py` |
| `charts.py` | `test_charts.py` |
| `chart_options.py` | `test_chart_options.py` |
| `ai_assistant.py` | `test_ai.py` |
| `chart_ai.py` | `test_chart_ai.py` |
| `stats.py`, `stats_charts.py` | `test_stats.py` |
| `dashboard.py` | `test_dashboard.py` (+ Console in `test_ui.py`: `test_console_dashboard_*`, `last_panel(DashboardView)`) |
| `structure.py` | `test_structure.py` |
| `excel_writer.py`, `excel_split.py`, `excel_report.py` | `test_excel.py` |
| `style.py` | `test_style.py` |
| `number_format.py` | `test_number_format.py` (+ stile dei grafici in `test_chart_options.py`; menu e condivisione per sezione in `test_ui.py`) |
| `workspace.py`, `notes.py` | `test_workspace.py` |
| `import_plan.py` (+ `engine.load_text_columns`) | `test_import_plan.py` |
| `excel_link.py` | `test_excel_link.py` (selezione finta; la prova con Excel reale non è nella suite) |
| `app.py` (UI, con `nicegui.testing.user_plugin`) | `test_ui.py` |

Marker UI usati dai test (`.mark(...)` in `app.py`): `run-dati`, `filter-apply`, `naming`, `naming-custom`, `measure-name`, `run-groupby`, `run-pivot`, `pivot-drill`, `pivot-rows`, `pivot-columns`, `pivot-date`, `pivot-sort`, `show-as`, `relative-to`, `base-field`, `base-item`, `run-join`, `join-apply`, `run-union`, `run-console`, `console-scope`, `rename-dataset`, `remove-dataset`, `datasets`, `pane-dataset`, `pane-count`, `pane-close`, `subpane-count`, `subpane-close`, `rename-name`, `rename-ok`, `structure-preview`, `structure-apply`, `structure-undo`, `column-name`, `sort-column`, `sort-direction`, `console-example`, `console-chart`, `console-column-chip`, `run-chart`, `chart-builder`, `db-note-text`, `run-stats`, `stat-chart`, `full-screen`, `add-report`, `report-ok`, `split-preview`, `run-split`, `run-report`, `appearance`, `preset-<nome>`, `result-actions`, `number-format`, `number-format-mode`, `number-format-decimals`, `number-format-style`, `paste-values`, `paste-text`, `paste-ok`, `dl-csv`, `open-excel`, `save-dataset`, `database`, `db-tree`, `db-new-project`, `db-new-folder`, `db-save-active`, `db-open`, `db-note`, `db-delete`, `db-confirm`, `db-text`, `db-text-ok`, `db-save`, `db-folder`, `db-name`, `db-save-ok`, `show-note`, `note-text`, `note-save`, `ai-genera`, `ai-chart`, `console-dashboard-example`, `dashboard-slicer`, `dashboard-pages`, `dashboard-visual`, `dashboard-kpi`, `dashboard-chart`, `dashboard-cross-clear`, `calc-preview`, `calc-apply`, `dl-xlsx`, `sheet-ok`, `sheet-range`, `excel-range`, `excel-range-ok`, `chart`. **Non rimuoverli né rinominarli** modificando l'interfaccia. I test UI usano `from data_explorer import app as A` e accedono a `A.S` (stato globale); `setup_state` dà a ogni test un database vuoto in una cartella temporanea (`A.S.database_path`) e al dataset `demo` l'origine «File: demo.parquet». Per Join / Union e Pivot `test_ui.py` registra i pannelli creati (`PANELS`, `last_panel(JoinPanel | UnionPanel | PivotTab)`; per la Pivot sostituisce il builder in `A.TABS`), come fa con i filtri (`CREATED`); `S.add(name, df)` rende **attivo** il dataset aggiunto, cioè quello di partenza delle finestre (usare `activate=False` per tenere `demo`). Le finestre si trovano con `A.S.live_panes()` (`pane.stack.section`, `pane.select`, `stack.count`, `pane.sub_stack` per le sotto-finestre; le `ChartTab` con `A.S.themed`). Fixture **`deleted_contexts_fail`**: fa leggere il contesto al `ui.notify` del simulatore, come il server vero (da usare nei test dei gestori che chiudono un dialog). Per verifiche sul comportamento reale c'è `playwright` (browser headless funzionante in questo ambiente: avviare `main.py --port N --db file.duckdb` nello **stesso comando** dello script, perché i processi non sopravvivono tra una chiamata e l'altra). `user.find(...).click()` clicca **solo l'elemento con l'id più basso**: per un pulsante preciso usare `UserInteraction(user, {elemento}, None).click()`. Ogni join/union riuscito notifica «Join: N righe × M colonne in X s»: i test lo aspettano in `user.notify.messages`. I download nei test si leggono con `await user.download.next()` (contenuto in `response.content`): `ui.download.content` è sostituito dal plugin di test, non va patchato.

---

## 15. Discrepanze e punti di attenzione

1. **`/api/dataset` non è definito in `app.py`.** `launcher.send_to_running_app` fa POST su quell'indirizzo e la docstring di `launcher.py` descrive l'invio all'app già aperta, ma nel codice dell'app non c'è nessuna rotta che lo riceva (nessun `@app.post`). Prima di lavorare su `launcher.py` o su "invia all'app aperta", verificare con `grep -rn "api/dataset" data_explorer/` (da `app/`) se la rotta è stata aggiunta altrove; altrimenti manca e va implementata in `app.py` (lettura Parquet → `S.add(name, df, activate=False)` per i nomi nuovi, aggiornamento in place per nome esistente, poi `update_header()`).
2. Lo stato `S` è **globale e a singolo utente**: ogni sessione del browser condivide gli stessi dataset.
3. `refresh_dataset(name)` ricostruisce le finestre su quel dataset (anche quella da cui parte l'azione): perdono i controlli non salvati in `S` (es. filtri impostati). Notificare **prima** di chiamarla. `page_content.refresh()` ricostruisce tutte le schede.
18. **Un gestore deve chiudere per ultimo il dialog che contiene il suo pulsante.** I dialog con `on_value_change(... dlg.delete())` (`DatabaseDialog`, `save_to_database`, `note_dialog`, `_paste` dei filtri) si eliminano alla chiusura: da quel momento il pulsante del gestore non ha più un contesto e `ui.notify` (che legge `ui.context.client`) lancia «The parent element this slot belongs to has been deleted», interrompendo il gestore. Sintomo reale: «Apri» dal database aggiungeva il dataset a `S.datasets` ma l'interfaccia non si aggiornava finché un altro `update_header` (es. un upload) non la rinfrescava. Quindi: notifiche e aggiornamenti **prima**, `dlg.close()` **per ultimo**. `refresh()` invece tollera slot eliminati. Il simulatore dei test NON lo mostra (tiene vivo l'elemento e `ui.notify` non legge il contesto): per questo i test dei flussi che chiudono un dialog usano la fixture `deleted_contexts_fail`.
17. **Elenchi che mostrano i dataset senza ricostruirsi** (es. «Tabelle disponibili» e box Colonne della Console): si registrano con `S.watch_datasets(elemento, aggiorna)`; `update_header` e `refresh_dataset` li richiamano dopo che le finestre hanno seguito i dataset. Il callback deve tollerare un dataset già rimosso. La lingua della Console (`S.lang`, `S.code`) è condivisa: ogni finestra ricorda la propria lingua corrente per salvare il codice nella voce giusta.
16. **Finestre di lavoro**: i `refresh()` di NiceGUI possono girare dopo il gestore che li chiede; un gestore asincrono partito da un elemento che viene ricostruito (es. le righe di `DatasetMenu`) deve aprire le sue finestre in uno slot che resta (`with self.button:`). Le variabili della Console Python (`S.env`) sono condivise tra le finestre: cambia solo `df`. Il codice scritto (`S.code`) è l'ultimo eseguito, in qualsiasi finestra. La nota «Carica un secondo dataset» di Join è registrata con `S.watch_datasets`: quando i dataset diventano almeno due lascia il posto al `JoinPanel`.
4. Le funzioni pesanti vanno eseguite con `run.io_bound(...)` per non bloccare l'interfaccia.
5. I nomi di aggregazione sono **in italiano** (`somma`, `media`, `conteggio`…) e sono usati anche dal prompt di `chart_ai`: cambiarli richiede di aggiornare i test.
6. Il `README.md` (nella radice del repository) è la guida per chi usa il programma per la prima volta; la descrizione tecnica delle funzioni è in §1 di questo file.
7. **`download(...)` e `download_zip(...)` non esistono nel codice.** Il prompt dell'AI per Python (`ai_assistant.RULES["Python"]`) li presenta come disponibili, ma `AppState.prepare_env` espone solo `pd`, `np`, `df`, `datasets`, `sql`. Il codice generato che le chiama darà errore. Prima di lavorare sulla console Python o su quel prompt: o si implementano in `app.py`/`engine.py` (usando `ui.download.content`, come in `ResultPanel.download_xlsx`) o si tolgono dal prompt.

10. **Avvio senza reload** (`reload=False`): dopo ogni modifica ai `.py` il server va fermato e riavviato. Se un vecchio processo occupa ancora la porta 8090, il browser continua a parlare con il codice vecchio (sintomo: i dataset caricati sono ancora lì).
11. **Più finestre del browser aperte**: menu delle sezioni e dei dataset sono per finestra (`app.windows`, id del client → elementi); `update_header` aggiorna tutte le finestre. Prima erano globali e puntavano all'ultima finestra collegata: con `show=True` (che apre da solo una scheda) o con una scheda vecchia aperta, dopo un caricamento l'header restava vuoto e la sidebar bloccata. Nei test UI i client non vengono eliminati: `setup_state` svuota `A.windows` come fa con `page_content.targets`.
12. **Test**: `pytest` e `pytest-asyncio` non sono in `requirements.txt` (installati a mano nel venv `NiceGuiApp`).
14. **Formato dei numeri**: cambia solo la visualizzazione (griglie e grafici), non i dati né Excel/CSV scaricati. Con il formato predefinito anche gli **interi** hanno il separatore delle migliaia (anni e codici numerici compaiono come «2.024»): per vederli senza separatori, «Nessun formato». I decimali valgono solo per i numeri con virgola; negli interi non compaiono mai. Il pulsante sta in ogni `ResultPanel`: nella scheda Statistica le tabelle esistono solo dopo «Calcola». L'anteprima della finestra di importazione (`ImportDialog`) usa il formato predefinito, senza menu. Le tabelle di «Struttura colonne» (scheda Dati) e i grafici scritti nella Console non seguono il formato.
13. **Range Excel**: `excel_link` usa COM (pywin32, Windows): legge l'Excel aperto solo mentre è in esecuzione. Si legge il file salvato su disco, quindi con modifiche non salvate l'importazione è rifiutata. Non c'è un test automatico con Excel reale: la verifica è stata fatta a mano su una cartella di prova.

9. **Database**: il file DuckDB si può aprire da un solo processo alla volta (una seconda istanza dell'app riceve un errore, notificato da `open_database` / `save_to_database`). I dataset si salvano come istantanee: nessun ricalcolo, la storia sta solo nella nota. I tipi tornano come li restituisce Arrow (interi con nulli → `Int64`, testo → `str`).

8. **Join / Union**: i selettori di dataset si aggiornano solo tramite `update_header()` (chiamata da salva/carica/rimuovi dataset): chi aggiunge un dataset senza passare da lì non li aggiorna. I filtri impostati si perdono al cambio di dataset di un selettore, la mappatura colonne della union si ricalcola cambiando dataset o allineamento. Il risultato del join è in memoria per intero (anche con righe moltiplicate da chiavi duplicate): i soli dati mostrati sono limitati da «Righe mostrate».

---

## 16. Procedura di consegna

1. Leggere solo i file coinvolti dalla tabella di [§3](#3-dove-intervenire-richiesta--codice).
2. Copiare i file da `/mnt/project/` in una cartella di lavoro (sono in sola lettura), modificarli lì.
3. Rispettare le [regole di §0](#0-regole-di-lavoro-dellutente): codice chiaro, nomi inglesi, nessuna ridondanza, logica nei moduli senza UI.
4. Se possibile eseguire `python -m pytest -q` sui test del modulo toccato; aggiornare o aggiungere test nel file corrispondente ([§14](#14-test)).
5. Se cambia una funzione visibile o la struttura, aggiornare **questo file** e, se cambia l'uso per l'utente, anche il `README.md`.
6. Salvare i file aggiornati in `/mnt/user-data/outputs/` e consegnarli con `present_files`. **Non mostrare mai nella chat i paragrafi modificati**: solo i file `.py` da scaricare, con al massimo una riga di sintesi.
