"""
Motore di analisi dati (nessuna dipendenza da UI).

Contiene:
  - caricamento file
  - filtri AND/OR annidati (albero JSON-serializzabile)
  - groupby con aggregazioni multiple
  - pivot table (con filtri e totali)
  - console SQL (DuckDB) e Python
  - export Excel / CSV e apertura in Excel
"""
from __future__ import annotations

import contextlib
import io
import os
import platform
import re
import subprocess
import tempfile
import traceback
from functools import reduce
from pathlib import Path

import duckdb
import numpy as np
import pandas as pd

# ---------------------------------------------------------------------------
# Caricamento
# ---------------------------------------------------------------------------

def load_file(path: str | Path) -> pd.DataFrame:
    p = Path(path)
    ext = p.suffix.lower()
    if ext in (".parquet", ".pq"):
        return pd.read_parquet(p)
    if ext in (".pkl", ".pickle"):
        return pd.read_pickle(p)
    if ext in (".xlsx", ".xlsm", ".xls"):
        return pd.read_excel(p)
    if ext in (".csv", ".txt", ".tsv"):
        return pd.read_csv(p, sep=None, engine="python")  # sniff separatore
    if ext == ".feather":
        return pd.read_feather(p)
    raise ValueError(f"Formato non supportato: {ext}")


def load_bytes(name: str, content: bytes) -> pd.DataFrame:
    """Carica da contenuto in memoria (upload dal browser)."""
    with tempfile.TemporaryDirectory() as tmp:
        p = Path(tmp) / name
        p.write_bytes(content)
        return load_file(p)


# ---------------------------------------------------------------------------
# Filtri AND/OR annidati
# ---------------------------------------------------------------------------
# Albero:
#   {"logica": "AND"|"OR", "condizioni": [ <condizione> | <gruppo>, ... ]}
#   condizione: {"col": str, "op": str, "val": str}

OPERATORI = [
    "=", "≠", ">", "<", ">=", "<=",
    "contiene", "non contiene", "inizia con", "finisce con",
    "in lista", "non in lista", "tra", "regex",
    "è nullo", "non è nullo",
]
OP_SENZA_VALORE = {"è nullo", "non è nullo"}


def nuovo_gruppo(logica: str = "AND") -> dict:
    return {"logica": logica, "condizioni": []}


def nuova_condizione(col: str = "", op: str = "=", val: str = "") -> dict:
    return {"col": col, "op": op, "val": val}


def _is_group(n: dict) -> bool:
    return "condizioni" in n


def _split_list(val: str) -> list[str]:
    return [v.strip() for v in str(val).split(",") if v.strip()]


def _to_num(x):
    try:
        return float(str(x).replace(",", ".")) if isinstance(x, str) else float(x)
    except (ValueError, TypeError):
        return None


def _mask_condizione(df: pd.DataFrame, c: dict) -> pd.Series | None:
    col, op, val = c.get("col"), c.get("op", "="), c.get("val", "")
    if col not in df.columns:
        return None
    s = df[col]
    vuoto = pd.Series(False, index=df.index)

    if op == "è nullo":
        return s.isna()
    if op == "non è nullo":
        return s.notna()

    if val is None or (str(val) == "" and op not in ("=", "≠")):
        return None  # condizione incompleta: ignorata

    st = s.astype(str)
    if op == "contiene":
        return st.str.contains(str(val), case=False, na=False, regex=False)
    if op == "non contiene":
        return ~st.str.contains(str(val), case=False, na=False, regex=False)
    if op == "inizia con":
        return st.str.lower().str.startswith(str(val).lower(), na=False)
    if op == "finisce con":
        return st.str.lower().str.endswith(str(val).lower(), na=False)
    if op == "regex":
        try:
            re.compile(str(val))  # con i dtype stringa di pandas 3 l'errore arriverebbe da pyarrow
        except re.error:
            return vuoto
        return st.str.contains(str(val), regex=True, na=False)
    if op in ("in lista", "non in lista"):
        valori = _split_list(val)
        m = st.isin(valori)
        if pd.api.types.is_numeric_dtype(s):
            nums = [n for n in (_to_num(v) for v in valori) if n is not None]
            m = s.isin(nums)
        return m if op == "in lista" else ~m
    if op == "tra":
        parti = _split_list(val)
        if len(parti) != 2:
            return vuoto
        if pd.api.types.is_datetime64_any_dtype(s):
            a, b = pd.to_datetime(parti[0], errors="coerce"), pd.to_datetime(parti[1], errors="coerce")
            return (s >= a) & (s <= b)
        a, b = _to_num(parti[0]), _to_num(parti[1])
        if a is None or b is None:
            return vuoto
        sn = pd.to_numeric(s, errors="coerce")
        return (sn >= a) & (sn <= b)

    # confronti = ≠ > < >= <=
    if pd.api.types.is_datetime64_any_dtype(s):
        v = pd.to_datetime(val, errors="coerce", dayfirst=True)
        if pd.isna(v):
            return vuoto
        cmp = {"=": s == v, "≠": s != v, ">": s > v, "<": s < v, ">=": s >= v, "<=": s <= v}
        return cmp.get(op, vuoto).fillna(False)

    vn = _to_num(val)
    if pd.api.types.is_numeric_dtype(s) and vn is not None:
        cmp = {"=": s == vn, "≠": s != vn, ">": s > vn, "<": s < vn, ">=": s >= vn, "<=": s <= vn}
        return cmp.get(op, vuoto).fillna(False)

    sv = str(val)
    if op == "=":
        return st == sv
    if op == "≠":
        return st != sv
    # confronto ordinale su stringhe
    cmp = {">": st > sv, "<": st < sv, ">=": st >= sv, "<=": st <= sv}
    return cmp.get(op, vuoto)


def mask_filtri(df: pd.DataFrame, nodo: dict | None) -> pd.Series:
    """Ritorna la maschera booleana di un albero di filtri (ricorsivo)."""
    tutti = pd.Series(True, index=df.index)
    if not nodo or not nodo.get("condizioni"):
        return tutti
    maschere = []
    for figlio in nodo["condizioni"]:
        m = mask_filtri(df, figlio) if _is_group(figlio) else _mask_condizione(df, figlio)
        if m is not None and not (_is_group(figlio) and not figlio.get("condizioni")):
            maschere.append(m)
    if not maschere:
        return tutti
    op = (lambda a, b: a & b) if nodo.get("logica", "AND") == "AND" else (lambda a, b: a | b)
    return reduce(op, maschere)


def apply_filters(df: pd.DataFrame, nodo: dict | None) -> pd.DataFrame:
    return df[mask_filtri(df, nodo)]


def descrivi_filtri(nodo: dict | None) -> str:
    """Rappresentazione leggibile dell'albero (per riepilogo)."""
    if not nodo or not nodo.get("condizioni"):
        return "(nessun filtro)"
    parti = []
    for f in nodo["condizioni"]:
        if _is_group(f):
            parti.append("(" + descrivi_filtri(f) + ")")
        elif f.get("col"):
            v = "" if f["op"] in OP_SENZA_VALORE else f' "{f.get("val", "")}"'
            parti.append(f'{f["col"]} {f["op"]}{v}')
    return f" {nodo.get('logica', 'AND')} ".join(parti) or "(nessun filtro)"


# ---------------------------------------------------------------------------
# GroupBy
# ---------------------------------------------------------------------------

def _mode(series: pd.Series):
    modes = series.mode()
    return modes.iloc[0] if not modes.empty else None


FUNZIONI_AGG = {
    "somma": "sum", "media": "mean", "mediana": "median",
    "conteggio": "count", "distinti": "nunique",
    "min": "min", "max": "max", "primo": "first", "ultimo": "last",
    "dev.std": "std", "varianza": "var",
    "range": lambda s: s.max() - s.min(),
    "q1": lambda s: s.quantile(0.25),
    "q3": lambda s: s.quantile(0.75),
    "moda": _mode,
    "nulli": lambda s: s.isna().sum(),
    "% nulli": lambda s: s.isna().mean() * 100,
}


def run_groupby(df: pd.DataFrame, by: list[str], aggs: list[dict],
                filtri: dict | None = None, ordina: bool = True) -> pd.DataFrame:
    """
    aggs: [{"col": "importo", "fn": "somma"}, ...]
    Se `by` è vuoto aggrega l'intero dataset in una riga.
    """
    d = apply_filters(df, filtri)
    aggs = [a for a in aggs if a.get("col") in d.columns]
    if not aggs:
        # senza aggregazioni: conteggio righe per gruppo
        if by:
            return d.groupby(by, dropna=False, sort=ordina).size().reset_index(name="n_righe")
        return pd.DataFrame({"n_righe": [len(d)]})

    named, usati = {}, set()
    for a in aggs:
        fn = FUNZIONI_AGG.get(a["fn"], a["fn"])
        nome = f'{a["fn"]}_{a["col"]}'
        i = 2
        while nome in usati:
            nome = f'{a["fn"]}_{a["col"]}_{i}'
            i += 1
        usati.add(nome)
        named[nome] = pd.NamedAgg(column=a["col"], aggfunc=fn)

    if by:
        out = d.groupby(by, dropna=False, sort=ordina).agg(**named).reset_index()
    else:
        # "first"/"last" esistono solo per groupby: senza chiavi si usa il primo/ultimo non nullo
        senza_gruppi = {"first": lambda s: s.dropna().iloc[0] if s.notna().any() else None,
                        "last": lambda s: s.dropna().iloc[-1] if s.notna().any() else None}
        res = {n: [d[na.column].agg(senza_gruppi.get(na.aggfunc, na.aggfunc))] for n, na in named.items()}
        out = pd.DataFrame(res)
    return out


def run_value_counts(df: pd.DataFrame, by: list[str], columns: list[str],
                     filtri: dict | None = None, percent: bool = True,
                     ordina: bool = True) -> pd.DataFrame:
    """
    Conta le righe per ogni valore (o combinazione di valori) di `columns`,
    all'interno di ciascun gruppo `by`. La percentuale è calcolata sul totale
    del gruppo (se `by` è vuoto, sul totale del dataset).
    """
    columns = [c for c in columns if c not in by]
    if not columns:
        raise ValueError("Seleziona almeno una colonna da contare (diversa dalle chiavi di gruppo).")

    d = apply_filters(df, filtri)
    out = d.groupby(by + columns, dropna=False, sort=False, observed=True).size().reset_index(name="conteggio")

    if percent:
        totals = out.groupby(by, dropna=False)["conteggio"].transform("sum") if by else out["conteggio"].sum()
        out["percentuale"] = (out["conteggio"] / totals * 100).round(2)
    if ordina:
        out = out.sort_values(by + ["conteggio"], ascending=[True] * len(by) + [False], kind="stable")
    return out.reset_index(drop=True)


# ---------------------------------------------------------------------------
# Pivot
# ---------------------------------------------------------------------------

def run_pivot(df: pd.DataFrame, righe: list[str], colonne: list[str],
              valori: list[dict], filtri: dict | None = None,
              totali: bool = False, riempi_zero: bool = False) -> pd.DataFrame:
    """
    valori: [{"col": "importo", "fn": "somma"}, ...]
    Restituisce un DataFrame piatto (colonne leggibili).
    """
    d = apply_filters(df, filtri)
    valori = [v for v in valori if v.get("col") in d.columns]
    if not valori:
        raise ValueError("Seleziona almeno un valore da aggregare.")
    if not righe and not colonne:
        raise ValueError("Seleziona almeno una riga o una colonna.")

    parti = []
    visti = set()
    for v in valori:
        fn = FUNZIONI_AGG.get(v["fn"], v["fn"])
        if (v["col"], fn) in visti:
            continue
        visti.add((v["col"], fn))
        etichetta = f'{v["fn"]} di {v["col"]}'
        # una pivot per misura: i totali (margins) restano corretti anche per medie/min/max
        p = pd.pivot_table(
            d, index=righe or None, columns=colonne or None, values=v["col"],
            aggfunc=fn, margins=totali, margins_name="Totale",
            fill_value=0 if riempi_zero else None, observed=True,
        )
        if isinstance(p, pd.Series):
            p = p.to_frame(etichetta)
        elif colonne:
            if isinstance(p.columns, pd.MultiIndex):
                chiavi = [" / ".join(map(str, c)) for c in p.columns]
            else:
                chiavi = [str(c) for c in p.columns]
            p.columns = [f"{etichetta} | {k}" for k in chiavi]
        else:
            p.columns = [etichetta]
        parti.append(p)

    piv = pd.concat(parti, axis=1)
    if not righe:  # solo colonne: una riga unica
        piv = piv.reset_index(drop=True)
        return piv
    return piv.reset_index()


# ---------------------------------------------------------------------------
# Console SQL (DuckDB)
# ---------------------------------------------------------------------------

def run_sql(query: str, tabelle: dict[str, pd.DataFrame]) -> pd.DataFrame | None:
    """
    Esegue SQL con DuckDB. Ogni dataset è visibile come tabella con il suo nome
    (nomi non validi vengono sanificati) più l'alias `df` per il dataset attivo
    se passato come tabella 'df'.
    Ritorna il DataFrame del risultato (o None per istruzioni senza risultato).
    """
    con = duckdb.connect()
    try:
        for nome, d in tabelle.items():
            con.register(nome, d)
        rel = con.execute(query)
        if rel.description is None:
            return None
        return rel.df()
    finally:
        con.close()


def nome_tabella(nome: str) -> str:
    n = re.sub(r"\W", "_", Path(nome).stem)
    if not n or n[0].isdigit():
        n = "t_" + n
    return n


# ---------------------------------------------------------------------------
# Console Python
# ---------------------------------------------------------------------------

def run_python(code: str, env: dict) -> tuple[str, object | None, str | None]:
    """
    Esegue codice Python in `env` (persistente tra le esecuzioni).
    Se l'ultima istruzione è un'espressione ne restituisce il valore (come Jupyter).
    Ritorna (stdout, valore_ultima_espressione, errore).
    """
    import ast

    buf = io.StringIO()
    valore = None
    err = None
    try:
        tree = ast.parse(code)
        ultima = None
        if tree.body and isinstance(tree.body[-1], ast.Expr):
            ultima = ast.Expression(tree.body.pop().value)
        with contextlib.redirect_stdout(buf), contextlib.redirect_stderr(buf):
            exec(compile(tree, "<console>", "exec"), env)
            if ultima is not None:
                valore = eval(compile(ultima, "<console>", "eval"), env)
    except Exception:
        err = traceback.format_exc(limit=4)
    return buf.getvalue(), valore, err


def to_display_frame(value) -> pd.DataFrame | None:
    """
    Converte il risultato di una console (DataFrame o Series) in una tabella piatta
    con l'indice visibile come colonna. Per altri tipi ritorna None.
    """
    if isinstance(value, pd.Series):
        value = value.to_frame(name=value.name if value.name is not None else "valore")
    if not isinstance(value, pd.DataFrame):
        return None
    if isinstance(value.columns, pd.MultiIndex):
        value = value.copy()
        value.columns = [" | ".join(map(str, c)) for c in value.columns]
    if not (isinstance(value.index, pd.RangeIndex) and value.index.name is None):
        value = value.reset_index()
    return value


# ---------------------------------------------------------------------------
# Export
# ---------------------------------------------------------------------------

def _prepara_per_excel(df: pd.DataFrame) -> pd.DataFrame:
    """Excel non supporta datetime con timezone."""
    d = df.copy()
    for c in d.columns:
        if isinstance(d[c].dtype, pd.DatetimeTZDtype):
            d[c] = d[c].dt.tz_localize(None)
    if isinstance(d.columns, pd.MultiIndex):
        d.columns = [" | ".join(map(str, c)) for c in d.columns]
    return d


def to_xlsx_bytes(df: pd.DataFrame, sheet: str = "Dati") -> bytes:
    from openpyxl.utils import get_column_letter

    buf = io.BytesIO()
    d = _prepara_per_excel(df)
    with pd.ExcelWriter(buf, engine="openpyxl") as w:
        d.to_excel(w, sheet_name=sheet[:31], index=False)
        ws = w.sheets[sheet[:31]]
        ws.freeze_panes = "A2"
        ws.auto_filter.ref = ws.dimensions
        for i, col in enumerate(d.columns, start=1):
            sample = d.iloc[:200, i - 1].astype(str).fillna("")  # NaN resta NaN con i dtype stringa di pandas 3
            larg = max([len(str(col))] + sample.str.len().tolist()) + 2
            ws.column_dimensions[get_column_letter(i)].width = min(larg, 50)
    return buf.getvalue()


def to_csv_bytes(df: pd.DataFrame, italiano: bool = True) -> bytes:
    """CSV italiano: separatore ';', decimale ',', BOM per Excel."""
    d = _prepara_per_excel(df)
    if italiano:
        return d.to_csv(index=False, sep=";", decimal=",").encode("utf-8-sig")
    return d.to_csv(index=False).encode("utf-8")


def apri_in_excel(df: pd.DataFrame, nome: str = "export") -> str:
    """
    Salva un .xlsx temporaneo e lo apre con l'applicazione predefinita (Excel).
    Ritorna il percorso del file.
    """
    nome = re.sub(r"[^\w\-]+", "_", nome) or "export"
    path = Path(tempfile.gettempdir()) / f"{nome}_{pd.Timestamp.now():%H%M%S}.xlsx"
    path.write_bytes(to_xlsx_bytes(df))
    sistema = platform.system()
    if sistema == "Windows":
        os.startfile(str(path))  # type: ignore[attr-defined]
    elif sistema == "Darwin":
        subprocess.Popen(["open", str(path)])
    else:
        subprocess.Popen(["xdg-open", str(path)])
    return str(path)
