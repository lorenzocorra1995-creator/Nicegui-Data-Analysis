"""
Use from Jupyter:

    from data_explorer.launcher import explore
    explore(df)                         # one dataset
    explore(sales=df1, customers=df2)   # several datasets, each with its own name (= SQL table name)

`esplora` is kept as an alias of `explore`, for older notebooks.

If Data Explorer is already open on the given port, the DataFrames are sent to the running app
(work in progress is not lost; a dataset with the same name is updated).
Otherwise the app starts in a separate process and the notebook stays free.
"""
from __future__ import annotations

import socket
import subprocess
import sys
import tempfile
import urllib.parse
import urllib.request
from pathlib import Path

import pandas as pd

APP_PATH = Path(__file__).resolve().parent.parent / "main.py"
HOST = "127.0.0.1"
CONNECT_TIMEOUT = 0.5
UPLOAD_TIMEOUT = 300


# --- input -----------------------------------------------------------------

def collect_datasets(args: tuple[pd.DataFrame, ...], named: dict[str, pd.DataFrame]) -> dict[str, pd.DataFrame]:
    if len(args) == 1 and not named:
        return {"df": args[0]}
    datasets = {**named, **{f"df{i + 1}": df for i, df in enumerate(args)}}
    if not datasets:
        raise ValueError("Passa almeno un DataFrame: esplora(df) oppure esplora(vendite=df)")
    return datasets


# --- serialization ---------------------------------------------------------

def to_parquet_bytes(df: pd.DataFrame) -> bytes:
    try:
        return df.to_parquet()
    except Exception:  # object columns with mixed types: convert them to text
        text_df = df.copy()
        for column in text_df.select_dtypes(include=["object", "string"]):
            text_df[column] = text_df[column].astype(str).where(text_df[column].notna())
        return text_df.to_parquet()


# --- app already open ------------------------------------------------------

def is_app_running(port: int) -> bool:
    try:
        with socket.create_connection((HOST, port), timeout=CONNECT_TIMEOUT):
            return True
    except OSError:
        return False


def send_to_running_app(name: str, payload: bytes, port: int) -> None:
    query = urllib.parse.urlencode({"filename": f"{name}.parquet"})
    request = urllib.request.Request(f"http://{HOST}:{port}/api/dataset?{query}", data=payload, method="POST",
                                     headers={"Content-Type": "application/octet-stream"})
    urllib.request.urlopen(request, timeout=UPLOAD_TIMEOUT).close()


# --- new app ---------------------------------------------------------------

def start_app(payloads: dict[str, bytes], port: int) -> subprocess.Popen:
    folder = Path(tempfile.mkdtemp(prefix="data_explorer_"))
    paths = []
    for name, payload in payloads.items():
        path = folder / f"{name}.parquet"
        path.write_bytes(payload)
        paths.append(str(path))
    return subprocess.Popen([sys.executable, str(APP_PATH), "--port", str(port), *paths])


# --- entry point -----------------------------------------------------------

def explore(*args: pd.DataFrame, porta: int = 8090, **dataset: pd.DataFrame) -> subprocess.Popen | None:
    payloads = {name: to_parquet_bytes(df) for name, df in collect_datasets(args, dataset).items()}
    if not is_app_running(porta):
        return start_app(payloads, porta)

    for name, payload in payloads.items():
        send_to_running_app(name, payload, porta)
    print(f"Inviato all'app già aperta (porta {porta}): {', '.join(payloads)}")
    return None


# old Italian name, kept so that existing notebooks keep working
esplora = explore
