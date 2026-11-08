"""Genera un CSV di vendite FITTIZIE (dati casuali, seed fisso) per provare l'app e le dashboard della Console.

Uso (da app/):  python tools/make_sample_sales.py [righe]      ->  data/esempi/vendite_fittizie.csv
"""
import sys
from pathlib import Path

import numpy as np
import pandas as pd

ROWS = int(sys.argv[1]) if len(sys.argv) > 1 else 5000
OUT = Path(__file__).resolve().parent.parent / "data" / "esempi" / "vendite_fittizie.csv"
rng = np.random.default_rng(42)

# category -> {product: (list price, unit cost as a share of the price)}
CATALOG = {
    "Elettronica": {"Smartphone": (599, .72), "Tablet": (349, .70), "Cuffie wireless": (89, .55), "Smartwatch": (199, .62)},
    "Informatica": {"Notebook": (899, .78), "Monitor 27\"": (229, .68), "Tastiera": (49, .50), "Mouse": (29, .45)},
    "Casa": {"Aspirapolvere": (159, .60), "Macchina caffe'": (119, .58), "Lampada LED": (25, .40), "Set pentole": (79, .52)},
    "Sport": {"Scarpe running": (95, .50), "Zaino trekking": (69, .48), "Tappetino yoga": (22, .38), "Bicicletta": (499, .70)},
    "Abbigliamento": {"Giacca": (129, .45), "Jeans": (59, .42), "Maglietta": (19, .35), "Sneakers": (79, .46)},
    "Giardino": {"Tagliaerba": (249, .66), "Barbecue": (139, .58), "Set attrezzi": (39, .44), "Sedia pieghevole": (35, .42)},
}
REGIONS = {"Lombardia": .20, "Lazio": .13, "Campania": .11, "Veneto": .10, "Piemonte": .09, "Emilia-Romagna": .09,
           "Toscana": .08, "Sicilia": .08, "Puglia": .06, "Sardegna": .06}
CHANNELS = {"Online": .45, "Negozio": .40, "Agenti": .15}
SEGMENTS = {"Privato": .60, "Piccola impresa": .28, "Grande azienda": .12}
CHANNEL_DISCOUNT = {"Online": .06, "Negozio": .02, "Agenti": .10}


def pick(options: dict, size: int) -> np.ndarray:
    return rng.choice(list(options), size=size, p=list(options.values()))


products = [(cat, name, price, cost) for cat, items in CATALOG.items() for name, (price, cost) in items.items()]
# cheaper products are sold more often
weights = np.array([1 / np.sqrt(p[2]) for p in products])
chosen = rng.choice(len(products), size=ROWS, p=weights / weights.sum())

# dates 2023-2025 drawn with seasonal weights: more sales in Nov-Dec, fewer in Aug
days = pd.date_range("2023-01-01", "2025-12-31")
day_weight = days.month.map({8: .55, 11: 1.4, 12: 1.6}).fillna(1.0).to_numpy(dtype=float)
dates = pd.Series(days[rng.choice(len(days), size=ROWS, p=day_weight / day_weight.sum())])

df = pd.DataFrame({
    "ID_Ordine": [f"ORD-{100000 + i}" for i in range(ROWS)],
    "Data": dates.dt.strftime("%Y-%m-%d"),
    "Regione": pick(REGIONS, ROWS),
    "Canale": pick(CHANNELS, ROWS),
    "Segmento_Cliente": pick(SEGMENTS, ROWS),
    "Categoria": [products[i][0] for i in chosen],
    "Prodotto": [products[i][1] for i in chosen],
})
list_price = np.array([products[i][2] for i in chosen], dtype=float)
unit_cost = list_price * np.array([products[i][3] for i in chosen])

year_growth = 1 + .06 * (pd.to_datetime(df["Data"]).dt.year - 2023).to_numpy()  # prices drift up
df["Prezzo_Unitario"] = (list_price * year_growth * rng.normal(1, .03, ROWS)).round(2)
df["Costo_Unitario"] = (unit_cost * rng.normal(1, .02, ROWS)).round(2)
business = df["Segmento_Cliente"].ne("Privato").to_numpy()
df["Quantita"] = np.where(business, rng.integers(2, 25, ROWS), rng.integers(1, 5, ROWS))
discount = df["Canale"].map(CHANNEL_DISCOUNT).to_numpy() + np.where(df["Quantita"] >= 10, .05, 0) + rng.choice([0, 0, 0, .05, .10], ROWS)
df["Sconto_Pct"] = (discount * 100).round(0).astype(int)
df["Spesa"] = (df["Prezzo_Unitario"] * df["Quantita"] * (1 - df["Sconto_Pct"] / 100)).round(2)
df["Margine"] = (df["Spesa"] - df["Costo_Unitario"] * df["Quantita"]).round(2)

df = df.sort_values(["Data", "ID_Ordine"]).reset_index(drop=True)
OUT.parent.mkdir(parents=True, exist_ok=True)
df.to_csv(OUT, index=False, encoding="utf-8")
print(f"{len(df)} righe -> {OUT}")
