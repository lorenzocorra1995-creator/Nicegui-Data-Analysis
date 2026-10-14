"""
Data Explorer — strumento locale di analisi dati (NiceGUI + pandas + DuckDB).

Avvio:
    python app.py                       # poi carichi i file dall'interfaccia
    python app.py vendite.parquet clienti.csv
    python app.py --port 8123 vendite.parquet
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd
from nicegui import run, ui

import ai_assistant as ai
import engine as eng
import style

MAX_RIGHE_GRIGLIA = 10_000  # righe mostrate nella tabella (l'export è sempre completo)


# ===========================================================================
# Stato globale (app locale a singolo utente)
# ===========================================================================

class Stato:
    def __init__(self) -> None:
        self.datasets: dict[str, pd.DataFrame] = {}
        self.attivo: str | None = None
        self.tab = "Dati"
        self.lang = "SQL"
        self.codice = {
            "Python": "df.describe()",
            "SQL": "SELECT *\nFROM df\nLIMIT 100",
        }
        self.env: dict = {"pd": pd, "np": np}
        self.api_key = ""
        self.ai_model = ai.DEFAULT_MODEL
        self.ai_effort = ai.DEFAULT_EFFORT

    @property
    def df(self) -> pd.DataFrame:
        return self.datasets[self.attivo] if self.attivo else pd.DataFrame()

    def aggiungi(self, nome: str, df: pd.DataFrame, attiva: bool = True) -> str:
        base, i = nome, 2
        while nome in self.datasets:
            nome = f"{base}_{i}"
            i += 1
        self.datasets[nome] = df
        if attiva or self.attivo is None:
            self.attivo = nome
        return nome

    def tabelle_sql(self) -> dict[str, pd.DataFrame]:
        t = {eng.nome_tabella(n): d for n, d in self.datasets.items()}
        if self.attivo:
            t["df"] = self.df
        return t

    def prepara_env(self) -> dict:
        self.env["df"] = self.df
        self.env["datasets"] = self.datasets
        self.env["sql"] = lambda q: eng.run_sql(q, self.tabelle_sql())
        return self.env


S = Stato()


# ===========================================================================
# Utilità per la griglia
# ===========================================================================

def _righe_json(df: pd.DataFrame) -> list[dict]:
    return json.loads(df.to_json(orient="records", date_format="iso", date_unit="s",
                                 default_handler=str))


def _col_defs(df: pd.DataFrame) -> list[dict]:
    defs = []
    for c in df.columns:
        d = {"field": str(c), "headerName": str(c), "filter": "agTextColumnFilter"}
        if pd.api.types.is_bool_dtype(df[c]):
            pass
        elif pd.api.types.is_numeric_dtype(df[c]):
            d["type"] = "numericColumn"
            d["filter"] = "agNumberColumnFilter"
            if pd.api.types.is_float_dtype(df[c]):
                d[":valueFormatter"] = (
                    "params => params.value == null ? '' : "
                    "Number(params.value).toLocaleString('it-IT', {maximumFractionDigits: 2})"
                )
        elif pd.api.types.is_datetime64_any_dtype(df[c]):
            d["filter"] = "agDateColumnFilter"
        defs.append(d)
    return defs


def _nomi_unici(df: pd.DataFrame) -> pd.DataFrame:
    """La griglia richiede nomi colonna unici e stringa."""
    visti: dict[str, int] = {}
    nuovi = []
    for c in df.columns:
        c = str(c)
        if c in visti:
            visti[c] += 1
            c = f"{c}_{visti[c]}"
        else:
            visti[c] = 1
        nuovi.append(c)
    if nuovi != list(df.columns):
        df = df.copy()
        df.columns = nuovi
    return df


def notifica_errore(e: Exception | str) -> None:
    ui.notify(str(e), type="negative", multi_line=True, close_button=True, timeout=8000)


# ===========================================================================
# Pannello risultato: griglia + export (sempre presente sotto ogni vista)
# ===========================================================================

class Risultato:
    def __init__(self, nome_base: str = "risultato") -> None:
        self.df: pd.DataFrame | None = None
        self.nome = nome_base
        with ui.column().classes("w-full gap-2"):
            with ui.row().classes("w-full items-center gap-2"):
                self.info = ui.label("Nessun risultato").classes("text-sm text-gray-600")
                ui.space()
                self.b_xlsx = ui.button("Excel", icon="download", on_click=self.scarica_xlsx).props("dense outline").mark("dl-xlsx")
                self.b_csv = ui.button("CSV", icon="download", on_click=self.scarica_csv).props("dense outline")
                self.b_open = ui.button("Apri in Excel", icon="open_in_new", on_click=self.apri_excel).props("dense color=green")
                self.b_ds = ui.button("Salva come dataset", icon="playlist_add", on_click=self.salva_dataset).props("dense outline")
            self.grid = ui.aggrid({
                "columnDefs": [], "rowData": [],
                "defaultColDef": {"sortable": True, "resizable": True, "filter": True, "minWidth": 90},
                "suppressFieldDotNotation": True,
                "enableCellTextSelection": True,
                "pagination": True, "paginationPageSize": 100,
                "paginationPageSizeSelector": [50, 100, 500, 1000],
            }, theme="quartz").classes("w-full").style("height: 520px")
        self._abilita(False)

    def _abilita(self, on: bool) -> None:
        for b in (self.b_xlsx, self.b_csv, self.b_open, self.b_ds):
            b.set_enabled(on)

    def mostra(self, df: pd.DataFrame, nome: str | None = None) -> None:
        if nome:
            self.nome = nome
        self.df = df
        vis = _nomi_unici(df.head(MAX_RIGHE_GRIGLIA))
        self.grid.options["columnDefs"] = _col_defs(vis)
        self.grid.options["rowData"] = _righe_json(vis)
        self.grid.update()
        testo = f"{len(df):,} righe × {df.shape[1]} colonne".replace(",", ".")
        if len(df) > MAX_RIGHE_GRIGLIA:
            testo += f" — in tabella le prime {MAX_RIGHE_GRIGLIA:,}; l'export contiene tutto".replace(",", ".")
        self.info.set_text(testo)
        self._abilita(True)

    # --- azioni -----------------------------------------------------------
    def _check(self) -> bool:
        if self.df is None:
            ui.notify("Nessun dato da esportare", type="warning")
            return False
        return True

    async def scarica_xlsx(self) -> None:
        if self._check():
            try:
                data = await run.io_bound(eng.to_xlsx_bytes, self.df, self.nome)
                ui.download.content(data, f"{self.nome}.xlsx")
            except Exception as e:  # noqa: BLE001
                notifica_errore(e)

    async def scarica_csv(self) -> None:
        if self._check():
            try:
                data = await run.io_bound(eng.to_csv_bytes, self.df, True)
                ui.download.content(data, f"{self.nome}.csv")
            except Exception as e:  # noqa: BLE001
                notifica_errore(e)

    async def apri_excel(self) -> None:
        if self._check():
            try:
                path = await run.io_bound(eng.apri_in_excel, self.df, self.nome)
                ui.notify(f"Aperto in Excel: {path}", type="positive")
            except Exception as e:  # noqa: BLE001
                notifica_errore(e)

    def salva_dataset(self) -> None:
        if not self._check():
            return
        with ui.dialog() as dlg, ui.card():
            ui.label("Salva il risultato come nuovo dataset").classes("text-base font-medium")
            campo = ui.input("Nome", value=self.nome).props("autofocus")

            def ok() -> None:
                nome = S.aggiungi(campo.value.strip() or self.nome, self.df.copy(), attiva=False)
                dlg.close()
                aggiorna_header()
                ui.notify(f"Dataset '{nome}' aggiunto (usalo da selettore, SQL o datasets['{nome}'])",
                          type="positive")

            with ui.row().classes("w-full justify-end"):
                ui.button("Annulla", on_click=dlg.close).props("flat")
                ui.button("Salva", on_click=ok).mark("salva-ok")
        dlg.open()


# ===========================================================================
# Costruttore di filtri AND/OR annidati
# ===========================================================================

class FiltroBuilder:
    COLORI = ["border-blue-400", "border-emerald-400", "border-amber-400", "border-fuchsia-400"]

    def __init__(self, colonne: list[str]) -> None:
        self.colonne = colonne
        self.tree = eng.nuovo_gruppo("AND")
        self.render()

    @ui.refreshable_method
    def render(self) -> None:
        self._gruppo(self.tree, None, 0)

    def _gruppo(self, nodo: dict, padre: dict | None, livello: int) -> None:
        colore = self.COLORI[livello % len(self.COLORI)]
        with ui.column().classes(f"w-full gap-1 pl-3 py-1 border-l-4 {colore}"):
            with ui.row().classes("items-center gap-2"):
                ui.toggle(["AND", "OR"], value=nodo["logica"],
                          on_change=lambda e, n=nodo: n.update(logica=e.value)).props("dense no-caps")
                ui.button("Condizione", icon="add", on_click=lambda n=nodo: self._aggiungi(n, False)).props("dense flat size=sm")
                ui.button("Gruppo", icon="account_tree", on_click=lambda n=nodo: self._aggiungi(n, True)).props("dense flat size=sm")
                if padre is not None:
                    ui.button(icon="delete", on_click=lambda n=nodo, p=padre: self._rimuovi(p, n)).props("dense flat size=sm color=negative")
            for figlio in nodo["condizioni"]:
                if "condizioni" in figlio:
                    self._gruppo(figlio, nodo, livello + 1)
                else:
                    self._condizione(figlio, nodo)

    def _condizione(self, c: dict, padre: dict) -> None:
        with ui.row().classes("items-center gap-2 no-wrap"):
            ui.select(self.colonne, value=c["col"] or None, label="Colonna", with_input=True,
                      on_change=lambda e: c.update(col=e.value or "")).props("dense outlined").classes("w-52")

            def cambia_op(e) -> None:
                c["op"] = e.value
                self.render.refresh()

            ui.select(eng.OPERATORI, value=c["op"], on_change=cambia_op).props("dense outlined").classes("w-40")
            if c["op"] not in eng.OP_SENZA_VALORE:
                hint = {"in lista": "a, b, c", "non in lista": "a, b, c", "tra": "min, max"}.get(c["op"], "")
                ui.input(value=str(c["val"]), label="Valore", placeholder=hint,
                         on_change=lambda e: c.update(val=e.value)).props("dense outlined").classes("w-56")
            ui.button(icon="close", on_click=lambda: self._rimuovi(padre, c)).props("dense flat size=sm color=negative")

    def _aggiungi(self, nodo: dict, gruppo: bool) -> None:
        if gruppo:
            nodo["condizioni"].append(eng.nuovo_gruppo("OR" if nodo["logica"] == "AND" else "AND"))
        else:
            nodo["condizioni"].append(eng.nuova_condizione())
        self.render.refresh()

    def _rimuovi(self, padre: dict, figlio: dict) -> None:
        padre["condizioni"].remove(figlio)
        self.render.refresh()

    def azzera(self) -> None:
        self.tree = eng.nuovo_gruppo("AND")
        self.render.refresh()


def box_filtri(colonne: list[str], titolo: str = "Filtri") -> FiltroBuilder:
    with ui.expansion(titolo, icon="filter_alt", value=True).classes("w-full border rounded"):
        fb = FiltroBuilder(colonne)
        ui.button("Azzera filtri", icon="backspace", on_click=fb.azzera).props("dense flat size=sm")
    return fb


# ===========================================================================
# Lista di aggregazioni (colonna + funzione), riusata da GroupBy e Pivot
# ===========================================================================

class ListaAgg:
    def __init__(self, colonne: list[str], titolo: str = "Valori") -> None:
        self.colonne = colonne
        self.items: list[dict] = []
        self.titolo = titolo
        ui.label(titolo).classes("text-sm font-medium")
        self.render()
        ui.button("Aggiungi valore", icon="add", on_click=self.aggiungi).props("dense flat size=sm")

    @ui.refreshable_method
    def render(self) -> None:
        for it in self.items:
            with ui.row().classes("items-center gap-2 no-wrap"):
                ui.select(self.colonne, value=it["col"] or None, with_input=True,
                          on_change=lambda e, it=it: it.update(col=e.value or "")).props("dense outlined").classes("w-52")
                ui.select(list(eng.FUNZIONI_AGG), value=it["fn"],
                          on_change=lambda e, it=it: it.update(fn=e.value)).props("dense outlined").classes("w-36")
                ui.button(icon="close", on_click=lambda it=it: self.rimuovi(it)).props("dense flat size=sm color=negative")

    def aggiungi(self) -> None:
        self.items.append({"col": "", "fn": "somma"})
        self.render.refresh()

    def rimuovi(self, it: dict) -> None:
        self.items.remove(it)
        self.render.refresh()


# ===========================================================================
# Tab
# ===========================================================================

def tab_dati(df: pd.DataFrame) -> None:
    cols = [str(c) for c in df.columns]
    fb = box_filtri(cols, "Filtri sul dataset")
    ris = Risultato(S.attivo or "dati")

    def applica() -> None:
        try:
            out = eng.apply_filters(df, fb.tree)
            ris.mostra(out, f"{S.attivo}_filtrato" if fb.tree["condizioni"] else S.attivo)
        except Exception as e:  # noqa: BLE001
            notifica_errore(e)

    with ui.row().classes("items-center gap-3"):
        ui.button("Applica filtri", icon="play_arrow", on_click=applica).mark("run-dati")
        ui.label("Il risultato filtrato è esportabile e utilizzabile come nuovo dataset.").classes("text-xs text-gray-500")

    with ui.expansion("Struttura colonne", icon="view_column").classes("w-full border rounded"):
        info = pd.DataFrame({
            "colonna": df.columns.astype(str),
            "tipo": [str(t) for t in df.dtypes],
            "non nulli": df.notna().sum().values,
            "nulli": df.isna().sum().values,
            "distinti": [df[c].nunique(dropna=True) for c in df.columns],
        })
        ui.table(rows=info.to_dict("records"),
                 columns=[{"name": k, "label": k, "field": k, "align": "left"} for k in info.columns],
                 pagination=15).classes("w-full").props("dense flat")
    ris.mostra(df, S.attivo)


def tab_groupby(df: pd.DataFrame) -> None:
    cols = [str(c) for c in df.columns]
    with ui.card().classes("w-full"):
        by = ui.select(cols, multiple=True, label="Raggruppa per", with_input=True).props("use-chips outlined dense").classes("w-full")
        mode = ui.toggle(["Aggregazioni", "Value counts"], value="Aggregazioni").props("no-caps")

        with ui.column().classes("w-full").bind_visibility_from(mode, "value", value="Aggregazioni"):
            aggs = ListaAgg(cols, "Aggregazioni")
        with ui.column().classes("w-full gap-2").bind_visibility_from(mode, "value", value="Value counts"):
            count_cols = ui.select(cols, multiple=True, label="Colonne da contare", with_input=True).props("use-chips outlined dense").classes("w-full")
            percent = ui.switch("Mostra anche la percentuale (sul totale di ogni gruppo)", value=True)

        fb = box_filtri(cols, "Filtri (applicati prima del raggruppamento)")
        ordina = ui.switch("Ordina per chiavi", value=True)
    ris = Risultato("groupby")

    async def esegui() -> None:
        try:
            keys = list(by.value or [])
            if mode.value == "Value counts":
                out = await run.io_bound(eng.run_value_counts, df, keys, list(count_cols.value or []),
                                         fb.tree, percent.value, ordina.value)
            else:
                out = await run.io_bound(eng.run_groupby, df, keys, aggs.items, fb.tree, ordina.value)
            ris.mostra(out, f"groupby_{S.attivo}")
        except Exception as e:  # noqa: BLE001
            notifica_errore(e)

    ui.button("Esegui Group By", icon="play_arrow", on_click=esegui).mark("run-groupby")
    ui.label("Aggregazioni: senza valori ottieni il conteggio righe per gruppo. "
             "Value counts: frequenza (e %) dei valori delle colonne scelte, per ogni gruppo.").classes("text-xs text-gray-500")


def tab_pivot(df: pd.DataFrame) -> None:
    cols = [str(c) for c in df.columns]
    with ui.card().classes("w-full"):
        with ui.row().classes("w-full no-wrap gap-4"):
            righe = ui.select(cols, multiple=True, label="Righe", with_input=True).props("use-chips outlined dense").classes("w-1/2")
            colonne = ui.select(cols, multiple=True, label="Colonne", with_input=True).props("use-chips outlined dense").classes("w-1/2")
        valori = ListaAgg(cols, "Valori")
        fb = box_filtri(cols, "Filtri (applicati prima della pivot)")
        with ui.row().classes("gap-6"):
            totali = ui.switch("Totali", value=False)
            zero = ui.switch("Celle vuote = 0", value=False)
    ris = Risultato("pivot")

    async def esegui() -> None:
        try:
            out = await run.io_bound(eng.run_pivot, df, list(righe.value or []), list(colonne.value or []),
                                     valori.items, fb.tree, totali.value, zero.value)
            ris.mostra(out, f"pivot_{S.attivo}")
        except Exception as e:  # noqa: BLE001
            notifica_errore(e)

    ui.button("Crea Pivot", icon="play_arrow", on_click=esegui).mark("run-pivot")


def tab_console(df: pd.DataFrame) -> None:
    editor_ref: dict = {}

    with ui.row().classes("items-center gap-4"):
        lang = ui.toggle(["SQL", "Python"], value=S.lang).props("no-caps")
        ui.button("Esegui", icon="play_arrow", on_click=lambda: esegui()).props("color=primary").mark("run-console")
        ui.label("Ctrl+Invio per eseguire").classes("text-xs text-gray-500")
    hint = ui.label().classes("text-xs text-gray-600")

    def aggiorna_hint() -> None:
        if lang.value == "SQL":
            nomi = ", ".join(f"`{n}`" for n in S.tabelle_sql())
            hint.set_text(f"Tabelle disponibili: {nomi}   (df = dataset attivo)")
        else:
            hint.set_text("Fai riferimento a \"df\".")

    async def esegui(_=None) -> None:
        codice = editor_ref["ed"].value
        S.codice[lang.value] = codice
        out.set_text("")
        out.classes(remove="text-red-600")
        try:
            if lang.value == "SQL":
                res = await run.io_bound(eng.run_sql, codice, S.tabelle_sql())
                if res is None:
                    out.set_text("Eseguito (nessun risultato).")
                else:
                    ris.mostra(res, "query")
            else:
                env = S.prepara_env()
                stdout, val, err = await run.io_bound(eng.run_python, codice, env)
                testo = stdout
                if err:
                    out.classes(add="text-red-600")
                    testo += err
                elif (table := eng.to_display_frame(val)) is not None:
                    ris.mostra(table, "console")
                elif val is not None:
                    testo += repr(val)
                out.set_text(testo.rstrip())
                # se l'utente ha riassegnato df, aggiorna il dataset attivo
                nuovo = env.get("df")
                if isinstance(nuovo, pd.DataFrame) and nuovo is not S.df and S.attivo:
                    S.datasets[S.attivo] = nuovo
                    ui.notify("Dataset attivo aggiornato dalla console", type="info")
        except Exception as e:  # noqa: BLE001
            out.classes(add="text-red-600")
            out.set_text(str(e))

    with ui.expansion("Assistente AI (invia solo nomi e tipi delle colonne, mai i dati)", icon="auto_awesome").classes("w-full"):
        with ui.row().classes("w-full items-center gap-3"):
            ui.input("API key Anthropic", password=True, password_toggle_button=True).bind_value(S, "api_key").props("dense outlined").classes("w-96")
            ui.select(ai.MODELS, label="Modello").bind_value(S, "ai_model").props("dense outlined").classes("w-72")
            ui.select(ai.EFFORTS, label="Sforzo").bind_value(S, "ai_effort").props("dense outlined").classes("w-56")
        request = ui.textarea("Descrivi cosa vuoi ottenere", placeholder="es. totale importo per regione, solo per il 2024, in ordine decrescente").props("outlined autogrow").classes("w-full")
        generate_button = ui.button("Genera codice", icon="auto_awesome", on_click=lambda: genera()).mark("ai-genera")

    async def genera() -> None:
        if not S.api_key.strip() or not request.value.strip():
            ui.notify("Inserisci la API key e la richiesta", type="warning")
            return
        tables = S.tabelle_sql() if lang.value == "SQL" else {"df": S.df, **{f"datasets['{n}']": d for n, d in S.datasets.items()}}
        generate_button.props(add="loading")
        try:
            code = await run.io_bound(ai.generate_code, S.api_key.strip(), request.value, lang.value, tables,
                                        S.ai_model, S.ai_effort)
            editor_ref["ed"].value = code
        except Exception as e:  # noqa: BLE001
            notifica_errore(e)
        finally:
            generate_button.props(remove="loading")

    editor = ui.codemirror(S.codice[S.lang], language=S.lang, theme="basicLight",
                           line_wrapping=True, keymap={"Mod-Enter": esegui}).classes("w-full").style("min-height: 180px")
    editor_ref["ed"] = editor

    def cambia_lingua(e) -> None:
        S.codice[S.lang] = editor.value
        S.lang = e.value
        editor.language = e.value
        editor.value = S.codice[e.value]
        aggiorna_hint()

    lang.on_value_change(cambia_lingua)
    aggiorna_hint()

    out = ui.label().classes("w-full whitespace-pre-wrap font-mono text-sm bg-gray-50 rounded p-2")
    ris = Risultato("query")


# ===========================================================================
# Header e caricamento file
# ===========================================================================

def aggiorna_header() -> None:
    selettore.set_options(list(S.datasets), value=S.attivo)


def cambia_dataset(e) -> None:
    if e.value and e.value != S.attivo:
        S.attivo = e.value
        contenuto.refresh()


def rimuovi_dataset() -> None:
    if not S.attivo:
        return
    del S.datasets[S.attivo]
    S.attivo = next(iter(S.datasets), None)
    aggiorna_header()
    contenuto.refresh()


def carica_da_percorso(path: str) -> None:
    try:
        p = Path(path.strip().strip('"'))
        df = eng.load_file(p)
        S.aggiungi(p.stem, df)
        aggiorna_header()
        contenuto.refresh()
        ui.notify(f"Caricato {p.name}: {len(df):,} righe".replace(",", "."), type="positive")
    except Exception as e:  # noqa: BLE001
        notifica_errore(e)


async def carica_upload(e) -> None:
    try:
        # NiceGUI >= 3: e.file è un oggetto con name e read()
        f = e.file
        content = await f.read()
        df = await run.io_bound(eng.load_bytes, f.name, content)
        S.aggiungi(Path(f.name).stem, df)
        aggiorna_header()
        contenuto.refresh()
        ui.notify(f"Caricato {f.name}: {len(df):,} righe".replace(",", "."), type="positive")
    except Exception as ex:  # noqa: BLE001
        notifica_errore(ex)


def dialog_carica() -> None:
    with ui.dialog() as dlg, ui.card().classes("w-[520px]"):
        ui.label("Carica un dataset").classes("text-base font-medium")
        ui.label("Percorso locale (consigliato per file grandi)").classes("text-xs text-gray-500")
        with ui.row().classes("w-full no-wrap items-center"):
            campo = ui.input(placeholder=r"C:\dati\vendite.parquet").props("dense outlined").classes("grow")
            ui.button("Carica", on_click=lambda: (carica_da_percorso(campo.value), dlg.close()))
        ui.separator()
        ui.label("…oppure trascina un file (csv, xlsx, parquet, pkl, feather)").classes("text-xs text-gray-500")
        ui.upload(on_upload=carica_upload, auto_upload=True, multiple=True,
                  on_rejected=lambda: ui.notify("File rifiutato", type="warning")).props("flat bordered").classes("w-full")
        with ui.row().classes("w-full justify-end"):
            ui.button("Chiudi", on_click=dlg.close).props("flat")
    dlg.open()


TABS = {
    "Dati": ("table_chart", tab_dati),
    "Group By": ("functions", tab_groupby),
    "Pivot": ("pivot_table_chart", tab_pivot),
    "Console": ("terminal", tab_console),
}


@ui.refreshable
def contenuto() -> None:
    if not S.attivo:
        with ui.column().classes("w-full items-center mt-24 gap-3"):
            ui.icon("upload_file", size="64px").classes("text-gray-400")
            ui.label("Nessun dataset caricato").classes("text-lg text-gray-600")
            ui.button("Carica un file", icon="folder_open", on_click=dialog_carica)
        return

    df = S.df
    with ui.tabs(value=S.tab, on_change=lambda e: setattr(S, "tab", e.value)).classes("w-full") as tabs:
        for nome, (icona, _) in TABS.items():
            ui.tab(nome, icon=icona)
    with ui.tab_panels(tabs, value=S.tab, animated=False).classes("w-full"):
        for nome, (_, builder) in TABS.items():
            with ui.tab_panel(nome).classes("gap-3"):
                builder(df)


def pagina() -> None:
    global selettore
    style.apply()
    with ui.header().classes("app-header items-center gap-3 py-2"):
        ui.icon("analytics", size="28px").classes("text-primary")
        ui.label("Data Explorer").classes("text-lg font-semibold")
        ui.space()
        selettore = ui.select(list(S.datasets), value=S.attivo, label="Dataset attivo",
                              on_change=cambia_dataset).props("dense outlined").classes("w-64")
        ui.button(icon="add", on_click=dialog_carica).props("flat round dense").tooltip("Carica dataset")
        ui.button(icon="delete", on_click=rimuovi_dataset).props("flat round dense").tooltip("Rimuovi dataset attivo")
    with ui.column().classes("w-full max-w-[1600px] mx-auto p-2"):
        contenuto()


selettore: ui.select


def main() -> None:
    args = sys.argv[1:]
    port = 8090
    if "--port" in args:
        i = args.index("--port")
        port = int(args[i + 1])
        del args[i:i + 2]
    for arg in args:
        try:
            p = Path(arg)
            S.aggiungi(p.stem, eng.load_file(p))
            print(f"Caricato {p.name}")
        except Exception as e:  # noqa: BLE001
            print(f"Impossibile caricare {arg}: {e}")
    ui.page("/")(pagina)
    ui.run(title="Data Explorer", reload=False, show=True, port=port, favicon="📊")


if __name__ in {"__main__", "__mp_main__"}:
    main()
