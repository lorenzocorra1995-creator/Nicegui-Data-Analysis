"""Dataset di esempio riproducibile, usato dai test."""
import numpy as np
import pandas as pd


def make_demo_df(n_rows: int = 1000, seed: int = 0) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    return pd.DataFrame({
        "regione": rng.choice(["Nord", "Sud", "Centro"], n_rows),
        "prodotto": rng.choice(["A", "B", "C", "D"], n_rows),
        "importo": rng.normal(1000, 300, n_rows).round(2),
        "qta": rng.integers(1, 20, n_rows),
        "data": pd.date_range("2024-01-01", periods=n_rows, freq="D"),
        "note": rng.choice(["ok", None, "urgente"], n_rows),
    })
