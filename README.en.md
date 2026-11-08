# Data Explorer

[Italiano](README.md) · **English**

A **local** tool for exploring and analysing tabular data (CSV, Excel, Parquet…) right in your browser, without writing code and without sending your data to any external service.

Built with Python, [NiceGUI](https://nicegui.io), pandas, DuckDB and ECharts.

> **Note on language.** The user interface and its messages are in **Italian**. In this guide, button and menu names are written as they appear on screen, followed by an English translation in parentheses the first time they appear, e.g. **Carica** (Load).

With Data Explorer you can, all in the same application:

- load one or more files and inspect their structure;
- filter, sort, rename and compute new columns;
- combine tables (join and union);
- summarise data with Group By, Pivot tables, charts and statistical analysis;
- export results to formatted Excel (optionally split into several files) or CSV;
- save datasets in a local database organised into projects and folders;
- use SQL or Python for anything the buttons don't cover, with an optional AI assistant.

---

## Contents

1. [Requirements](#1-requirements)
2. [Installation](#2-installation)
3. [Starting the app](#3-starting-the-app)
4. [First steps in 10 minutes](#4-first-steps-in-10-minutes)
5. [Loading data](#5-loading-data)
6. [Screen layout](#6-screen-layout)
7. [Key concepts](#7-key-concepts)
8. [Section guide](#8-section-guide)
9. [Exporting and saving results](#9-exporting-and-saving-results)
10. [Dataset database](#10-dataset-database)
11. [AI assistant (optional)](#11-ai-assistant-optional)
12. [Advanced start-up options](#12-advanced-start-up-options)
13. [Troubleshooting](#13-troubleshooting)
14. [For developers](#14-for-developers)
15. [License](#15-license)

---

## 1. Requirements

| Requirement | Details |
|-------------|---------|
| Operating system | **Windows 10 or 11.** The program uses `pywin32` (to read the range selected in Excel and for «Apri in Excel» / Open in Excel), which only exists on Windows. |
| Python | **3.10 or later** (developed and tested with 3.12). |
| Browser | A modern browser (Chrome, Edge, Firefox). The app opens automatically in your default browser. |
| Excel | Optional. Only needed for «Apri in Excel» (Open in Excel) and «Range Excel». Reading and writing `.xlsx` files does **not** require Excel. |
| Anthropic API key | Optional. Only needed for the AI assistant (see [section 11](#11-ai-assistant-optional)). |
| Internet | Only needed for the initial installation and, if you use it, for the AI assistant. All data analysis happens on your computer. |

---

## 2. Installation

Open **Command Prompt** (or PowerShell) and follow these steps.

**1. Download the project**

With Git:

```
git clone https://github.com/lorenzocorra1995-creator/Nicegui-Data-Analysis.git
cd Nicegui-Data-Analysis
```

Or, on the GitHub page, click **Code → Download ZIP**, extract the folder and open it in Command Prompt with `cd`.

**2. Create a virtual environment** (recommended: it keeps the program's libraries separate from the rest of your Python)

```
python -m venv NiceGuiApp
NiceGuiApp\Scripts\activate
```

Once activated, you will see `(NiceGuiApp)` at the start of the line.

**3. Install the libraries**

```
python -m pip install -r requirements.txt
```

This takes a few minutes: it installs NiceGUI, pandas, DuckDB, PyArrow, openpyxl, xlsxwriter, scipy, statsmodels, anthropic, numpy and pywin32.

**4. (Optional) Check that everything works**

```
python -m pip install pytest pytest-asyncio
python -m pytest -q
```

No errors should appear at the end (some tests may be reported as «skipped»: that is normal).

---

## 3. Starting the app

From the project folder, with the virtual environment active:

```
python main.py
```

The browser opens automatically at **http://localhost:8090**. If it doesn't, paste that address into your browser's address bar.

- **To close the program**, go back to Command Prompt and press `Ctrl+C`.
- **To reopen the app** after closing only the browser tab, go to http://localhost:8090 again (as long as Command Prompt is still running).
- If you change the Python code, stop the program with `Ctrl+C` and restart it: it does not reload by itself.

You can also pass files to load straight away:

```
python main.py sales.csv customers.parquet
```

More options (a different port, a database in another location) are in [section 12](#12-advanced-start-up-options).

> **Double-click start (optional).** `tools/build_exe.ps1` creates `DataExplorer.exe`, a launcher without a terminal window. It is **not** a standalone program: it only works if the program folder is named `app`, contains the `NiceGuiApp` virtual environment, and the exe sits next to the `app` folder. Error messages go to `logs/app.log`. If you don't need it, ignore it.

---

## 4. First steps in 10 minutes

This walkthrough lets you try the main features with sample data.

**1. Generate the sample data.** From a second Command Prompt (with the environment active):

```
python tools/make_sample_sales.py
```

It creates `data/esempi/vendite_fittizie.csv`: 5,000 rows of made-up sales (order, date, region, channel, category, product, price, cost, quantity, discount, revenue, margin). Column names are in Italian. You can pass a different number of rows: `python tools/make_sample_sales.py 20000`.

**2. Load the file.** At the top right, click the **+** button («Carica dataset» / Load dataset), then drag the CSV into the window. The import window opens: leave everything as it is and click **Carica** (Load).

**3. Look at the data.** The **Dati** (Data) section shows the first rows and, if you open «Struttura colonne» (Column structure), the type of each column, how many values are missing and how many are distinct.

**4. Filter.** Open **Filtri** (Filters), add a condition (for example *Regione = Lombardia*) and click **Applica filtri** (Apply filters).

**5. Summarise.** Go to **Group By** (left menu, *Analizza* / Analyse group). Choose *Categoria* as the key and *Margine* with the *somma* (sum) function as the measure, then click **Esegui Group By** (Run Group By).

**6. Draw a chart.** Go to **Grafici** (Charts), choose «Barre verticali» (Vertical bars), *Categoria* on the X axis and *Spesa* with *somma* as the value, then click **Crea grafico** (Create chart).

**7. Try a Pivot.** In **Pivot**, put *Categoria* in the rows, *Canale* in the columns, *Spesa* with *somma* in the values and click **Crea Pivot** (Create Pivot). Click a cell and then **Righe di origine** (Source rows) to see the rows that produced it.

**8. Export.** Below any result, open the **Esporta e salva** (Export and save) menu and choose *Excel*, *CSV*, *Apri in Excel* (Open in Excel), *Salva come dataset* (Save as dataset), *Salva nel database* (Save to database) or *Aggiungi al report* (Add to report).

**9. A dashboard in one click.** In **Console**, choose *Python*, click **Esempio di dashboard** (Dashboard example) and then **Esegui** (Run): you get an interactive dashboard with side filters, indicators and linked charts.

---

## 5. Loading data

### Supported formats

| Type | Extensions |
|------|------------|
| Text | `.csv`, `.tsv`, `.txt` |
| Excel | `.xlsx`, `.xlsm`, `.xls` |
| Parquet | `.parquet`, `.pq` |
| Feather | `.feather` |
| pandas pickle | `.pkl`, `.pickle` |

For text files the separator (`;`, `,`, tab…) and the character encoding are detected automatically.

### How to load

- **From the interface:** the **+** button at the top right, then drag one or more files into the window (or click it to pick files).
- **From the command line:** `python main.py file1.csv file2.xlsx` (see [section 12](#12-advanced-start-up-options)). In this case the import window does not appear.
- **From the database:** the **Database** button at the top of the side menu ([section 10](#10-dataset-database)).
- **From Jupyter:** see [section 12](#12-advanced-start-up-options).

Each dataset has a **name**, which by default is the file name without its extension. That name is also the table name in the Console's SQL queries.

### Excel: sheet and range

When you load an Excel file, a window always appears where you choose the **sheet** and, optionally, the **cell range** to read (for example `B3:F120`). Leaving the range empty reads the whole sheet. The first row of the range is used as the column header.

### «Range Excel»: reading the selection made in Excel

In the load window, the **Range Excel** button reads the range currently selected in an Excel workbook open on your PC (Windows only). The file must **already be saved**: the program reads the file from disk, so if there are unsaved changes the import is refused with a message.

### The import window

After reading the file, a window appears where, before loading, you can:

- **keep or drop** columns (*Tutte* / All, *Nessuna* / None buttons);
- **rename** columns;
- choose the **format** of each column: *Come rilevato* (As detected, the default), *Testo* (Text), *Numero* (Number), *Numero intero* (Integer), *Data* (Date), *Data senza ora* (Date without time);
- set the **decimal separator** used in the file (comma or point) for columns converted to numbers;
- see a **preview** with a warning about values that would be lost in the conversion;
- click **Proponi formati** (Suggest formats) to get suggestions for columns read as text whose values are all numbers or dates (codes with leading zeros, such as `00123`, are excluded so the zeros are not lost);
- click **Ripristina** (Reset) to go back to the initial choices.

If you choose *Testo* (Text) for a column the program had read as a number or date, the file is re-read for that column, so leading zeros and original formats are preserved.

---

## 6. Screen layout

**Top bar**

- Menu button: shows or hides the side menu.
- **Aspetto** (Appearance): light/dark theme, preset themes, accent colour, corners, chart colours and advanced colours. Your choices are remembered between sessions.
- **Dataset (N)**: list of loaded datasets with their sizes; for each one you can **rename** or **remove** it, and from here you can **load** more.
- **+**: load a dataset.

**Side menu (left)**

At the top is the **Database** button. Below it, the sections in four groups:

| Group | Sections |
|-------|----------|
| Prepara (Prepare) | Dati (Data) · Struttura (Structure) · Colonne calcolate (Computed columns) · Join / Union |
| Analizza (Analyse) | Group By · Pivot · Grafici (Charts) · Statistica (Statistics) |
| Esporta (Export) | Excel |
| Strumenti (Tools) | Console |

On narrow screens the menu closes by itself after each choice.

**Work area**

Fills the rest of the page and shows the chosen section. Each section is made of **work panels** (see below).

---

## 7. Key concepts

### Datasets

A dataset is a table loaded in memory. You can have as many as you like. **There is no global "active dataset"**: in each section you choose which dataset to work on.

### Work panels

At the top of every panel is the **Dataset** selector: that is where you decide which table that panel works on. In the Data, Structure, Computed columns, Group By, Pivot, Charts, Statistics and Console sections, the **Finestre** (Panels) selector (1 to 6) lets you **stack several panels**, each with its own dataset, filters and settings. This is handy for comparing two tables or two analyses without losing anything.

In **Group By**, **Pivot** and **Charts** each panel also has **sub-panels**: filters are set once at the top and several analyses (for example three different charts) read the same filtered rows.

### Filters

The filter builder is the same throughout the program (titled **Filtri (N)**, where N is the number of active conditions). You can create:

- **conditions** (column + operator + value);
- nested **groups** combined with **AND** («all true») or **OR** («at least one true»), including groups inside groups.

Available operators: `=` · `≠` · `>` · `<` · `>=` · `<=` · contains · does not contain · starts with · ends with · in list · not in list · between · regex · is null · is not null.

Tips:

- For `=`, `≠`, «in list» and «not in list» on text columns, the program suggests the values found in the column: you can search and pick them.
- With «in list» and «not in list», the clipboard-icon button lets you paste a list copied from Excel (one cell per row, or values separated by `;`).
- Numbers accept both `1.234,56` and `1,234.56`; dates are written `YYYY-MM-DD`.
- In **Group By**, **Pivot**, **Charts**, **Statistics** and **Console**, filters are applied **before** the calculation.

### Results

Every result appears in a table with resizable columns, sortable by clicking the header, and filterable. Above and below the table you will find:

- **Righe mostrate** (Rows shown): how many rows to display (50, 100, 500, 1,000, 5,000, 10,000). This only limits the **display**: exports always contain everything.
- **Formato numeri** (Number format): see below.
- **Esporta e salva** (Export and save) and **Schermo intero** (Full screen): see [section 9](#9-exporting-and-saving-results).
- **Nota** (Note): shows where the result comes from (source file, dataset, applied filters, operation performed).

### Number format

The **Formato numeri** (Number format) button above each table lets you choose between:

- *Nessun formato* (No format): values are shown as they are;
- *Formato decimale* (Decimal format): choose the number of decimals and the style, **Italiano** (`1.234,56`) or **Americano** (`1,234.56`).

The default is «decimal, 2 digits, Italian style». It applies to all tables in the same section and changes **only the display**: downloaded Excel and CSV files are unaffected.

> With the default format, **integers** also show the thousands separator: a year such as 2024 is shown as «2.024». To see it without the dot, choose *Nessun formato* (No format).

In charts, the number style is set in the **Separatori numeri** (Number separators) field.

### Dialogs

All dialogs can be **moved** by dragging their first row and **resized** from the bottom-right corner.

---

## 8. Section guide

### Dati (Data)

Shows the chosen dataset, with filters.

- **Applica filtri** (Apply filters) shows only the rows that meet the conditions. The dataset **does not change**.
- **Applica al dataset** (Apply to dataset) actually replaces the dataset with only the filtered rows. This can be undone from the Structure section.
- **Azzera filtri** (Clear filters) removes all conditions.
- **Struttura colonne** (Column structure, collapsible panel): for each column shows type, missing values and distinct values.

### Struttura (Structure)

To tidy up the dataset:

- **rename** the dataset and its columns;
- **reorder** columns and **drop** some of them;
- **sort rows** on several levels (for example first by Region A → Z, then by Revenue Z → A).

Click **Anteprima** (Preview) to see the result and **Applica al dataset** (Apply to dataset) to confirm. **Annulla ultima modifica** (Undo last change) restores the dataset to its previous state (the version before the last change is kept).

### Colonne calcolate (Computed columns)

Create a new column, or overwrite an existing one, in three ways:

| Mode | What it is for |
|------|----------------|
| **Cambio formato** (Change format) | Converts a column to *Number*, *Integer*, *Text*, *Date*, *Date without time*, or extracts *Year*, *Quarter*, *Month*, *Day*, *Year-month*. For numbers written in files you can specify whether the decimal separator is a comma or a point. |
| **Formula** | Write an expression (DuckDB SQL syntax) using the columns, for example `"Spesa" - "Costo_Unitario" * "Quantita"`. Clicking column names inserts them into the formula. The **Guida alle formule** (Formula guide) panel lists functions for text, numbers, dates, logic and windows, with pre-computed examples. |
| **Se… allora** (If… then) | Assigns a value based on rules, each with its own filters («if Region = Lombardia then North»), plus an «otherwise» value. |

Choose where to insert the new column (after which column), check the preview (**Anteprima**) and click **Applica al dataset** (Apply to dataset).

### Join / Union

A switch at the top chooses between **Join** and **Union**.

**Join** (combine two or more tables on shared keys)

1. Choose the starting dataset and the dataset to join.
2. Choose the **join type**: *Inner* (only matching rows), *Left*, *Right*, *Full outer*, *Cross* (all combinations), *Left semi* / *Right semi* (keep rows that have a match, without adding columns), *Left anti* / *Right anti* (keep rows that have **no** match).
3. Set the **keys**: left column, operator and right column. The program suggests one automatically.
4. To join a third dataset click **Aggiungi join (+ dataset)** (Add join): the steps follow one another like a chain.
5. In **Colonne del risultato** (Result columns) decide which columns to keep and what to call them; columns with the same name are flagged.
6. **Esegui join** (Run join) shows the result; **Applica al dataset** (Apply to dataset) replaces the starting dataset with the result.

Options let you match keys of different types «as text», treat empty values as equal, add a column indicating which side each row comes from, and merge identical keys into a single column.

To avoid freezing your computer, if the keys have repeated values on both sides and the result would explode in size, the join stops with a message explaining why.

**Union** (stack tables with the same columns)

- **Append all rows** (UNION ALL), **without duplicate rows** (UNION), **only rows present in all datasets** (INTERSECT), **rows of the first dataset missing from the others** (EXCEPT).
- Columns can be aligned by name, common columns only, as in the first dataset, or by position.
- You can add a column with each row's source dataset, and filter each dataset before combining it.
- **Esegui union** (Run union) shows the result.

### Group By

Groups rows and computes summaries.

1. Choose one or more **key columns** (the groups).
2. Add **measures**: one column and one function each. Functions: sum, mean, median, count, distinct, min, max, first, last, std. dev., variance, range, q1, q3, mode, nulls, % nulls. The program only offers functions that make sense for the column type (for example, no sum of text).
3. Choose how to **name the result columns** (for example «somma Spesa» or «Spesa (somma)») or give each measure a custom name.
4. Click **Esegui Group By** (Run Group By).

At the top a switch chooses between **Aggregazioni** (Aggregations, as described above) and **Value counts**, which counts how often each value of the chosen columns appears, with the percentage, for each group.

### Pivot

A pivot table like Excel's, with more options.

1. Choose the **Rows** and **Columns** fields (more than one is allowed, with subtotals).
2. For date fields, choose how to group them (year, quarter, month…).
3. Add the **Values**: column + function, as in Group By.
4. Set the **Layout**: totals and subtotals, empty cells, sorting (by label or by value) and column names.
5. For each value you can choose **«Mostra valori come»** (Show values as): % of (total, row, column…), difference from, % difference from, running total, running %, rank, index.
6. Click **Crea Pivot** (Create Pivot).

Clicking a cell and then **Righe di origine** (Source rows) shows exactly the dataset rows that produced that number. If the pivot would have too many columns (over 500), the program says so and suggests filtering or moving a field to the rows.

### Grafici (Charts)

23 chart types in five families:

| Family | Types |
|--------|-------|
| Comparison | Vertical bars, horizontal bars, stacked, 100% stacked, Bars + lines (dual axis), Pareto |
| Trend | Lines, Step lines, Area, Stacked area |
| Composition | Pie, Doughnut, Rose (Nightingale), Treemap, Funnel |
| Distribution | Histogram, Box plot, Scatter, Bubbles |
| Relationships | Heat map, Radar |

In the **Costruzione del grafico** (Chart builder) panel:

- **Data**: choose the type, columns (X axis, series, values, bubble size…) and functions; for dates choose the unit (day, month…); with **Top N** you can keep only the highest or lowest values and group the rest into «Altri» (Others); you can sort by value or by label.
- **Panels**: create a small chart for each value of a column, with a shared scale and colours.
- **Appearance**: title, value labels, decimals, compact numbers (1.2 K · 3.4 M · 5 B), smoothed lines, average line, height, number style.
- Click **Crea grafico** (Create chart).

**Click a bar, slice or point** to see the dataset rows behind it. The chart's data table is in the panel below.

At the top you can also use **Costruisci il grafico con l'AI** (Build the chart with AI): describe the chart you want and the assistant fills in the controls for you (requires an API key, [section 11](#11-ai-assistant-optional)).

### Statistica (Statistics)

Choose an analysis and the columns involved, then click **Calcola** (Calculate). Results are numeric tables and charts (no interpretive text).

| Group | Analyses |
|-------|----------|
| Descriptive | Descriptive statistics · Distribution and normality · Frequencies · Outliers |
| Relationships | Correlations |
| Hypothesis tests | Group comparison · One-sample test · Paired samples · Independence between categories |
| Models | Linear regression · Logistic regression · Time series |

Each analysis only shows the fields it needs (for example the confidence level, the correlation method or the time unit).

### Excel

Generates formatted Excel files. A switch chooses between **Split** and **Report**.

**Split** divides a dataset into several files or sheets:

- *By column values*: one file (or sheet) per value, for example one per region;
- *By number of rows*: blocks of N rows, each in a file or sheet.

Names are built with placeholders such as `{Regione}`, `{n}` and `{nome}`. The **Anteprima** (Preview) button shows the plan (how many files, which names, how many rows) before anything is created; **Genera file** (Generate files) creates them. Limits: at most 1,000 files and 250 sheets per file.

**Report** gathers several tables you produced in the other sections into a single Excel file. From any result use **Esporta e salva → Aggiungi al report** (Export and save → Add to report), choosing a sheet name and title; then in Report you can reorder, rename and remove sheets and click **Genera report** (Generate report).

**Shared settings** (apply to both Split and Report): Excel table style (or a plain range), totals row, conditional highlighting on numeric columns, negative numbers in red, index sheet with links, frozen header. The destination can be a **download** (a single file, or a ZIP if there is more than one file) or a **folder on your PC** of your choice. If a sheet exceeds Excel's row limit (1,048,575 data rows), it automatically continues on the next sheet.

### Console

For anything the buttons don't cover. A switch chooses **SQL** or **Python**; `Ctrl+Enter` (or **Esegui** / Run) runs the code.

**SQL** (DuckDB)

- Every loaded dataset is a table with its own name.
- `df` is the panel's dataset.

**Python**

- Available variables: `df` (the panel's dataset), `datasets` (all datasets, by name), `pd` (pandas), `np` (numpy), `sql("SELECT …")` (runs an SQL query and returns a table), `Dashboard` (see below).
- The last line is displayed as in Jupyter. It can be a **table**, a **chart** (ECharts options, a matplotlib or plotly figure, if installed) or a **dashboard**.
- Variables persist between runs.

**Filters in the Console:** the filters at the top only change the contents of `df`; the other SQL tables remain complete. A notice reminds you when `df` is filtered.

**Dashboard.** The **Esempio di dashboard** (Dashboard example) button inserts complete code ready to edit. In short:

```python
dash = Dashboard(df, "Vendite")
dash.slicer("Regione")                                    # side filter
dash.kpi("Fatturato", "Spesa", "somma", decimals=0, suffix=" €")
dash.chart("line", x="Data", value="Spesa", date_unit="Mese", series="Canale", width=8)
dash.table(groups=["Categoria"], value="Spesa")
dash.pivot(rows="Prodotto", columns="Regione", value="Spesa")
dash.page("Dettaglio")                                    # a new page (tab)
dash                                                      # last line: shows the dashboard
```

Function names (`"somma"`) and date units (`"Mese"`) are written in Italian, as in the interface.

Side filters apply to the whole dashboard; clicking an element of a chart filters the other panels accordingly (cross-filtering). A button switches the dashboard to full screen.

**Esempio di grafico** (Chart example) inserts an example of a chart written in the Console.

**AI assistant:** see [section 11](#11-ai-assistant-optional).

---

## 9. Exporting and saving results

Below every result, the **Esporta e salva** (Export and save) menu offers:

| Item | What it does |
|------|--------------|
| **Excel** | Downloads an `.xlsx` file with all rows. |
| **CSV** | Downloads a CSV in **Italian format**: `;` separator and decimal comma. |
| **Apri in Excel** (Open in Excel) | Saves a temporary file and opens it in Excel (requires Excel on the PC). |
| **Salva come dataset** (Save as dataset) | The result becomes a new dataset you can filter again, query with SQL, etc. |
| **Salva nel database** (Save to database) | Stores it in the local database ([section 10](#10-dataset-database)). |
| **Aggiungi al report** (Add to report) | Adds it to the Excel report ([Excel section](#excel)). |
| **Nota** (Note) | Shows or edits the source note. |

**Schermo intero** (Full screen) opens the table in a maximised window.

> Exports always contain **all** rows, regardless of the «Righe mostrate» (Rows shown) limit.

---

## 10. Dataset database

The **Database** button (at the top of the side menu) opens a local store where you can keep datasets between sessions: otherwise, data loaded from files disappears when the program closes.

- Datasets are organised into **projects** and nestable **subfolders** (**Nuovo progetto** / New project, **Nuova sottocartella** / New subfolder).
- On a folder: **Salva qui** (Save here) saves a loaded dataset, which can then be renamed or deleted.
- On a dataset: **Apri** (Open) loads it into the app; you can write or edit **notes**, rename it, move it to another folder, or delete it.
- Every saved dataset keeps a **source note** (source file, applied filters, operations performed), so you always know where it comes from; you can also add your own handwritten note.
- Datasets are saved **as they are** at that moment (snapshots): they are not recalculated.

The database is a single DuckDB file, by default `data/workspace.duckdb` inside the program folder. To use a different one: `python main.py --db path\my_database.duckdb`.

> The file can be opened by **only one program at a time**: do not run two copies of Data Explorer on the same database.

---

## 11. AI assistant (optional)

The AI assistant is a convenience; you don't need it to use the program.

**What it does**

- In the **Console** (*Assistente AI* panel): describe what you want and **Genera codice** (Generate code) writes the SQL or Python code in the editor. The code is **not run**: you review it, edit it and run it yourself.
- In **Charts** (*Costruisci il grafico con l'AI* / Build the chart with AI): describe the chart and the controls are filled in for you.

**How to enable it**

1. Create an API key on the Anthropic website (console.anthropic.com).
2. Paste it into the **API key Anthropic** field.
3. Choose the **model** (Fable 5.1, Opus 5.5, Sonnet 5.5, Haiku 5.5: from the most capable to the fastest and cheapest) and the reasoning **effort** (model default, low, medium…).

API usage is billed at Anthropic's rates. The key is not saved to disk: you need to enter it again at each start.

**Privacy**

**Only** your request and the data **metadata** are sent to the API: table and column names with their types. **The values in your data are never sent.**

---

## 12. Advanced start-up options

```
python main.py [file ...] [--port N] [--db path.duckdb]
```

| Option | Meaning | Default |
|--------|---------|---------|
| `file ...` | Files to load at start-up (one or more). The dataset name is the file name without its extension. | none |
| `--port N` | Server port. Useful if 8090 is taken, or to run two separate instances (each with its own `--db`). | `8090` |
| `--db path.duckdb` | Dataset database file. | `data/workspace.duckdb` |

Examples:

```
python main.py sales.csv --port 8123
python main.py --db D:\Analysis\project1.duckdb
```

### From Jupyter

Inside a notebook, with the project folder on the Python path:

```python
from data_explorer.launcher import explore
explore(sales=df_sales, customers=df_customers)   # the names become dataset and SQL table names
explore(df)                                       # a single DataFrame: it is called «df»
```

The app starts in a separate process and the notebook stays free. If the app is already open on the same port, close it and run `explore` again: sending DataFrames to an already running app is not yet fully supported.

---

## 13. Troubleshooting

**The browser doesn't open / the page doesn't load.**
Check that Command Prompt is still running and go to http://localhost:8090 (or the port set with `--port`).

**«Address already in use» / the port is taken.**
Another copy of the program is already running. Close it (`Ctrl+C` in its Command Prompt) or start this one on another port: `python main.py --port 8123`.

**I changed the code but nothing changes.**
The app does not reload by itself: stop it with `Ctrl+C` and run `python main.py` again. If an old process is still holding the port, the browser keeps talking to the old code (a clue: the loaded datasets are still there).

**The database gives an error when opening.**
A DuckDB file can be opened by only one program at a time. Close the other copies of Data Explorer (or other programs using it).

**«Range Excel» refuses the import.**
Save the workbook in Excel first: the program reads the file from disk, not unsaved data. Also check that you selected a cell range (not a chart or an object).

**The numbers shown differ from those in the file (for example «2.024» instead of 2024).**
That is the display *Formato numeri* (Number format). Choose *Nessun formato* (No format) above the table; downloaded files are never affected by the format.

**Columns with codes such as `00123` have lost their zeros.**
At import time, set the format of that column to *Testo* (Text).

**The table shows fewer rows than the file.**
Change **Righe mostrate** (Rows shown, up to 10,000). Exports always contain all rows.

**The join stops with a message about size.**
The chosen keys have many repeated values on both sides, so the result would be huge. Check the keys or filter the datasets first.

**The pivot has too many columns.**
Filter the data or move a field from the columns to the rows.

**The app is slow on the loaded file.**
Reduce the work with filters or with a narrower choice of columns at import time; heavy operations (pivot, join, statistics) run in the background, but still work in memory.

---

## 14. For developers

### Project structure

```
.
├─ main.py                  entry point (python main.py)
├─ launcher.py              compatibility with older notebooks
├─ requirements.txt
├─ pytest.ini
├─ data_explorer/
│  ├─ app.py                NiceGUI interface (state, sections, components)
│  ├─ launcher.py           explore(): start from Jupyter
│  ├─ core/                 data logic, no UI
│  │                        (engine, pivot, columns, combine, stats, structure,
│  │                         import_plan, number_format, workspace, notes, formula_help)
│  ├─ plotting/             chart data and options
│  │                        (charts, chart_options, chart_ai, stats_charts)
│  ├─ excel_io/             Excel writing, split, report, Excel link
│  ├─ ai/                   AI assistant (ai_assistant)
│  └─ ui/                   style (style) and Console dashboard (dashboard)
├─ tests/                   automated tests
├─ tools/                   build_exe.ps1, exe_launcher.py, make_sample_sales.py
└─ docs/PROJECT_MAP.md      technical map of the code (in Italian)
```

Core rule: **logic lives in UI-free modules** (`core`, `plotting`, `excel_io`, `ai`, `ui/dashboard.py`), which can be tested on their own; `app.py` only glues the controls to those functions.

### Tests

```
python -m pip install pytest pytest-asyncio
python -m pytest -q
```

UI tests use the NiceGUI simulator (`nicegui.testing.user_plugin`).

### Technical documentation

`docs/PROJECT_MAP.md` (in Italian) describes the role of each file, a table of «if the request is about X, work in Y», the main data structures and points to watch out for. It should be updated whenever the code structure changes.

### Conventions

- Simple, readable Python code, with variable and function names in English.
- No redundant code: before writing a helper, check that it doesn't already exist.
- Code is organised by topic: functions, logic and architecture kept separate.
- Only user-facing text and some stored or exchanged keys (described in `PROJECT_MAP.md`) stay in Italian.

---

## 15. License

Released under the **MIT** license: you may use, copy, modify and redistribute the code, including for commercial purposes, provided you keep the copyright notice. The full text is in the [LICENSE](LICENSE) file.
