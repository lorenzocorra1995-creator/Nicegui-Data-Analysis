"""
Uso da Jupyter:

    from launcher import esplora
    esplora(df)                         # un dataset
    esplora(vendite=df1, clienti=df2)   # più dataset, ognuno con il suo nome (= nome tabella SQL)

Salva i DataFrame in una cartella temporanea e avvia Data Explorer in un processo
separato, quindi il notebook resta libero e puoi lanciare `esplora()` più volte.
"""
from __future__ import annotations

import subprocess
import sys
import tempfile
from pathlib import Path

import pandas as pd

_AQUI = Path(__file__).resolve().parent


def esplora(*args: pd.DataFrame, porta: int = 8090, **dataset: pd.DataFrame) -> subprocess.Popen:
    if len(args) == 1 and not dataset:
        dataset = {"df": args[0]}
    else:
        dataset.update({f"df{i + 1}": d for i, d in enumerate(args)})
    if not dataset:
        raise ValueError("Passa almeno un DataFrame: esplora(df) oppure esplora(vendite=df)")

    cartella = Path(tempfile.mkdtemp(prefix="data_explorer_"))
    percorsi = []
    for nome, d in dataset.items():
        try:
            p = cartella / f"{nome}.parquet"
            d.to_parquet(p)
        except Exception:  # colonne object a tipi misti: ripiega su pickle
            p = cartella / f"{nome}.pkl"
            d.to_pickle(p)
        percorsi.append(str(p))

    return subprocess.Popen([sys.executable, str(_AQUI / "app.py"), "--port", str(porta), *percorsi])
