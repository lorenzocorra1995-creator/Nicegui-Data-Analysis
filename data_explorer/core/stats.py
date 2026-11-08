"""
Statistical analyses (no UI dependency).

run(df, spec) -> StatResult: tables (name → DataFrame), charts (Plot) and notes of the chosen analysis.
The ANALYSES catalog says, for each analysis, which choices it uses (`fields`): the interface shows only those.
The filters (spec["filters"]) are applied before every analysis. The results are only numbers, tables and charts.
"""
from __future__ import annotations

import math
import warnings
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import NamedTuple

import numpy as np
import pandas as pd
import statsmodels.api as sm
from scipy import stats as sps
from statsmodels.stats.descriptivestats import sign_test
from statsmodels.stats.diagnostic import acorr_ljungbox, het_breuschpagan, lilliefors, normal_ad
from statsmodels.stats.multicomp import pairwise_tukeyhsd
from statsmodels.stats.oneway import anova_oneway
from statsmodels.stats.outliers_influence import variance_inflation_factor
from statsmodels.stats.stattools import durbin_watson
from statsmodels.tsa.seasonal import seasonal_decompose
from statsmodels.tsa.stattools import acf, adfuller, kpss, pacf

from data_explorer.core import engine as eng
# ---------------------------------------------------------------------------
# Constants and choices
# ---------------------------------------------------------------------------

CONFIDENCE_LEVELS = {0.90: "90%", 0.95: "95%", 0.99: "99%"}
DEFAULT_CONFIDENCE = 0.95
CORRELATION_METHODS = {"pearson": "Pearson", "spearman": "Spearman", "kendall": "Kendall"}


class OutlierMethod(NamedTuple):
    label: str
    threshold: float  # default threshold


OUTLIER_METHODS = {
    "iqr": OutlierMethod("IQR (Tukey): fuori da Q1 − k·IQR, Q3 + k·IQR", 1.5),
    "zscore": OutlierMethod("z-score: |x − media| > k·dev. std", 3.0),
    "mad": OutlierMethod("z-score robusto (MAD): |0,6745·(x − mediana)/MAD| > k", 3.5),
}


class TimeUnit(NamedTuple):
    rule: str  # pandas.resample rule
    period: int  # default seasonal period (0 = none)
    label: str  # label format ({q} = quarter)


TIME_UNITS = {
    "Giorno": TimeUnit("D", 7, "%Y-%m-%d"),
    "Settimana": TimeUnit("W", 52, "%Y-%m-%d"),
    "Mese": TimeUnit("MS", 12, "%Y-%m"),
    "Trimestre": TimeUnit("QS", 4, "%Y-T{q}"),
    "Anno": TimeUnit("YS", 0, "%Y"),
}
DEFAULT_TIME_UNIT = "Mese"
TIME_AGGREGATIONS = ("somma", "media", "mediana", "min", "max", "conteggio")  # names from engine.AGG_FUNCTIONS
COUNT = "conteggio"

DEFAULT_BINS = 20
MAX_BINS = 200
DEFAULT_LAGS = 24
PERCENTILES = (1, 5, 10, 25, 50, 75, 90, 95, 99)
MAX_GROUPS = 50
MAX_CATEGORIES = 50
MAX_CORRELATION_COLUMNS = 40
SHAPIRO_MAX_ROWS = 5000
MAX_PLOT_POINTS = 2000
INTERCEPT = "(intercetta)"
MISSING = "(vuoto)"

FIELD_LABELS = {  # default labels of the controls; an analysis can change them (Analysis.labels)
    "columns": "Colonne (vuoto = tutte)",
    "column": "Colonna numerica",
    "column2": "Seconda colonna numerica",
    "group": "Gruppi (colonna)",
    "category": "Colonna delle categorie",
    "category2": "Seconda colonna delle categorie",
    "y": "Variabile dipendente (y)",
    "x": "Variabili indipendenti (x)",
    "date": "Colonna delle date",
    "method": "Metodo",
    "outlier_method": "Metodo",
    "threshold": "Soglia k",
    "confidence": "Livello di confidenza",
    "mu": "Valore di riferimento (μ₀)",
    "bins": "Numero di classi",
    "time_unit": "Raggruppa le date per",
    "time_agg": "Aggregazione",
    "period": "Periodo stagionale (0 = automatico)",
    "lags": "Ritardi dell'autocorrelazione",
}


# ---------------------------------------------------------------------------
# Result
# ---------------------------------------------------------------------------

@dataclass
class Plot:
    """Data of a chart; stats_charts turns it into ECharts options.
    kind: "bars" | "boxplot" | "scatter" | "lines" | "heatmap" (see stats_charts for the content of `data`)."""
    kind: str
    title: str
    data: dict
    x_name: str = ""
    y_name: str = ""


@dataclass
class StatResult:
    tables: dict[str, pd.DataFrame] = field(default_factory=dict)
    plots: list[Plot] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Utilities
# ---------------------------------------------------------------------------

def is_numeric(values: pd.Series) -> bool:
    return pd.api.types.is_numeric_dtype(values) and not pd.api.types.is_bool_dtype(values)


def numeric_columns(df: pd.DataFrame) -> list[str]:
    return [str(name) for name in df.columns if is_numeric(df[name])]


def _floats(values: pd.Series) -> pd.Series:
    return values.astype("float64")


def _required(spec: dict, key: str, what: str):
    value = spec.get(key)
    if value is None or value == "" or value == []:
        raise ValueError(f"Scegli {what}.")
    return value


def _numbers(rows: pd.DataFrame, name: str) -> pd.Series:
    """Non-null numeric values of the column."""
    if not is_numeric(rows[name]):
        raise ValueError(f"La colonna «{name}» non è numerica: convertila dalla scheda Colonne calcolate.")
    return _floats(rows[name]).dropna()


def _enough(values, minimum: int, what: str) -> None:
    if len(values) < minimum:
        raise ValueError(f"Servono almeno {minimum} valori non nulli per {what} (ce ne sono {len(values)}).")


def _level(confidence: float) -> str:
    return CONFIDENCE_LEVELS.get(confidence, f"{confidence * 100:g}%")


def _confidence(spec: dict) -> float:
    return float(spec.get("confidence") or DEFAULT_CONFIDENCE)


def _interval_names(confidence: float) -> tuple[str, str]:
    level = _level(confidence)
    return f"IC {level} inf", f"IC {level} sup"


def _mean_interval(values: pd.Series, confidence: float) -> tuple[float, float]:
    n = len(values)
    if n < 2:
        return np.nan, np.nan
    return sps.t.interval(confidence, n - 1, loc=values.mean(), scale=values.std() / math.sqrt(n))


def _table(rows: list[dict]) -> pd.DataFrame:
    """Table from rows; columns that are always empty are dropped."""
    return pd.DataFrame(rows).dropna(axis=1, how="all")


def _indices(values: dict[str, float]) -> pd.DataFrame:
    return pd.DataFrame({"indice": list(values), "valore": [float(value) for value in values.values()]})


def _test(name: str, statistic, p_value, dof=None, effect=None, effect_name: str | None = None, **extra) -> dict:
    """Row of the test table; `dof` can be a pair (degrees of freedom of numerator and denominator)."""
    first, second = dof if isinstance(dof, tuple) else (dof, None)
    return {"test": name, "statistica": statistic, "gl": first, "gl 2": second, "p-value": p_value,
            "effetto": effect, "misura dell'effetto": effect_name, **extra}


def _sample_index(n: int, limit: int = MAX_PLOT_POINTS) -> np.ndarray:
    """Positions of at most `limit` elements, always the same ones (charts do not draw millions of points)."""
    if n <= limit:
        return np.arange(n)
    return np.sort(np.random.default_rng(0).choice(n, limit, replace=False))


def _label(value: float) -> str:
    return f"{value:.4g}".replace(".", ",")


def _check_count(count: int, limit: int, what: str) -> None:
    if count > limit:
        raise ValueError(f"Troppi valori distinti in {what}: {count} (massimo {limit}).")


def _groups(rows: pd.DataFrame, value: str, group: str) -> dict[str, np.ndarray]:
    """Numeric values per group (rows with a null value or group are dropped)."""
    numbers = _numbers(rows, value)
    labels = rows.loc[numbers.index, group]
    keep = labels.notna()
    numbers, labels = numbers[keep], labels[keep]
    _check_count(labels.nunique(), MAX_GROUPS, f"«{group}»")
    return {str(key): part.to_numpy() for key, part in numbers.groupby(labels, sort=True, observed=True)}


def _rank_biserial(differences: np.ndarray) -> float:
    """Rank-biserial correlation for paired data: (sum of positive − negative ranks) / sum of ranks."""
    differences = differences[differences != 0]
    if not differences.size:
        return np.nan
    ranks = sps.rankdata(np.abs(differences))
    return float((ranks[differences > 0].sum() - ranks[differences < 0].sum()) / ranks.sum())


def _cohen_d(a: np.ndarray, b: np.ndarray) -> float:
    pooled = math.sqrt(((len(a) - 1) * a.var(ddof=1) + (len(b) - 1) * b.var(ddof=1)) / (len(a) + len(b) - 2))
    return float((a.mean() - b.mean()) / pooled) if pooled else np.nan


# ---------------------------------------------------------------------------
# Shared summaries and charts
# ---------------------------------------------------------------------------

def numeric_summary(values: pd.Series, confidence: float = DEFAULT_CONFIDENCE) -> dict:
    data = _floats(values)
    clean = data.dropna()
    n = len(clean)
    mean, std = clean.mean(), clean.std()
    low, high = _mean_interval(clean, confidence)
    q1, median, q3 = clean.quantile([0.25, 0.5, 0.75]).tolist()
    low_name, high_name = _interval_names(confidence)
    return {"n": n, "mancanti": int(data.isna().sum()), "media": mean, "dev. std": std,
            "errore std": std / math.sqrt(n) if n > 1 else np.nan, low_name: low, high_name: high,
            "min": clean.min(), "q1": q1, "mediana": median, "q3": q3, "max": clean.max(), "IQR": q3 - q1,
            "CV %": std / abs(mean) * 100 if mean else np.nan, "asimmetria": clean.skew(), "curtosi": clean.kurt(),
            "somma": clean.sum()}


def category_summary(values: pd.Series) -> dict:
    clean = values.dropna()
    counts = clean.value_counts()
    mode_count = int(counts.iloc[0]) if len(counts) else 0
    return {"n": len(clean), "mancanti": int(values.isna().sum()), "distinti": len(counts),
            "moda": str(counts.index[0]) if len(counts) else None, "frequenza moda": mode_count,
            "% moda": mode_count / len(clean) * 100 if len(clean) else np.nan}


def _box(values: np.ndarray, lower: float | None = None, upper: float | None = None) -> tuple[list[float], np.ndarray]:
    """Five numbers of the box (whiskers within the limits) and values outside the limits (default: 1.5 IQR)."""
    q1, median, q3 = np.quantile(values, [0.25, 0.5, 0.75])
    spread = q3 - q1
    lower = q1 - 1.5 * spread if lower is None else lower
    upper = q3 + 1.5 * spread if upper is None else upper
    inside = values[(values >= lower) & (values <= upper)]
    whiskers = (inside.min(), inside.max()) if inside.size else (q1, q3)
    return [whiskers[0], q1, median, q3, whiskers[1]], values[(values < lower) | (values > upper)]


def _box_plot(title: str, groups: dict[str, np.ndarray], y_name: str,
              bounds: dict[str, tuple[float, float]] | None = None) -> Plot:
    boxes, outliers = [], []
    for position, (name, values) in enumerate(groups.items()):
        box, outside = _box(values, *(bounds or {}).get(name, (None, None)))
        boxes.append(box)
        outliers += [[position, value] for value in outside[_sample_index(len(outside), MAX_PLOT_POINTS // 4)]]
    return Plot("boxplot", title, {"categories": list(groups), "boxes": boxes, "outliers": outliers}, y_name=y_name)


def _histogram(values: pd.Series, bins: int, name: str, normal: bool) -> tuple[pd.DataFrame, Plot]:
    counts, edges = np.histogram(values, bins=bins)
    table = pd.DataFrame({"da": edges[:-1], "a": edges[1:], "frequenza": counts, "%": counts / len(values) * 100})
    lines = {}
    std = values.std()
    if normal and std > 0:
        expected = len(values) * np.diff(sps.norm.cdf(edges, values.mean(), std))
        table["attesa (normale)"] = expected
        lines["Normale attesa"] = expected.tolist()
    labels = [f"{_label(low)} – {_label(high)}" for low, high in zip(edges[:-1], edges[1:])]
    plot = Plot("bars", "Istogramma", {"categories": labels, "bars": {"Frequenza": counts.tolist()}, "lines": lines},
                x_name=name, y_name="frequenza")
    return table, plot


def _scatter(title: str, x: np.ndarray, y: np.ndarray, x_name: str, y_name: str,
             lines: dict[str, list[list[float]]] | None = None, name: str = "valori") -> Plot:
    index = _sample_index(len(x))
    points = np.column_stack([x[index], y[index]]).tolist()
    return Plot("scatter", title, {"points": {name: points}, "lines": lines or {}}, x_name=x_name, y_name=y_name)


def _diagonal(values: np.ndarray) -> list[list[float]]:
    low, high = float(np.nanmin(values)), float(np.nanmax(values))
    return [[low, low], [high, high]]


# ---------------------------------------------------------------------------
# Descriptive statistics
# ---------------------------------------------------------------------------

def describe(rows: pd.DataFrame, spec: dict) -> StatResult:
    group = spec.get("group")
    columns = [name for name in (spec.get("columns") or [str(c) for c in rows.columns]) if name != group]
    confidence = _confidence(spec)
    numeric = [name for name in columns if is_numeric(rows[name])]
    others = [name for name in columns if name not in numeric]
    if group:
        _check_count(rows[group].nunique(dropna=False), MAX_GROUPS, f"«{group}»")
        parts = [({group: MISSING if pd.isna(key) else key}, part)
                 for key, part in rows.groupby(group, dropna=False, sort=True, observed=True)]
    else:
        parts = [({}, rows)]
    numbers = [{**key, "colonna": name, **numeric_summary(part[name], confidence)} for key, part in parts for name in numeric]
    texts = [{**key, "colonna": name, **category_summary(part[name])} for key, part in parts for name in others]
    result = StatResult()
    if numbers:
        result.tables["Colonne numeriche"] = pd.DataFrame(numbers)
    if texts:
        result.tables["Altre colonne"] = pd.DataFrame(texts)
    if not result.tables:
        raise ValueError("Nessuna colonna da descrivere.")
    if numeric and group:
        boxes = {str(key[group]): _floats(part[numeric[0]]).dropna().to_numpy() for key, part in parts}
        boxes = {name: values for name, values in boxes.items() if values.size}
        result.plots.append(_box_plot(f"{numeric[0]} per {group}", boxes, numeric[0]))
    elif numeric:
        boxes = {name: _floats(rows[name]).dropna().to_numpy() for name in numeric}
        result.plots.append(_box_plot("Box plot", {name: values for name, values in boxes.items() if values.size}, ""))
    return result


def distribution(rows: pd.DataFrame, spec: dict) -> StatResult:
    name = _required(spec, "column", "la colonna")
    values = _numbers(rows, name)
    _enough(values, 3, "la distribuzione")
    bins = int(min(max(spec.get("bins") or DEFAULT_BINS, 2), MAX_BINS))
    result = StatResult()
    result.tables["Forma"] = _indices({"n": len(values), "media": values.mean(), "dev. std": values.std(),
                                       "asimmetria": values.skew(), "curtosi": values.kurt()})
    result.tables["Percentili"] = pd.DataFrame({"percentile": PERCENTILES,
                                                "valore": np.percentile(values, PERCENTILES)})
    result.tables["Test di normalità"] = _table(_normality_tests(values, result.notes))
    histogram, plot = _histogram(values, bins, name, normal=True)
    result.tables["Istogramma"] = histogram
    (theoretical, observed), (slope, intercept, _) = sps.probplot(values.to_numpy(), dist="norm")
    ends = np.array([theoretical.min(), theoretical.max()])
    result.plots += [plot, _scatter("Q-Q plot (normale)", theoretical, observed, "quantili teorici", "quantili osservati",
                                    {"retta": np.column_stack([ends, slope * ends + intercept]).tolist()})]
    return result


def _normality_tests(values: pd.Series, notes: list[str]) -> list[dict]:
    data = values.to_numpy()
    n = len(data)
    sample = data[_sample_index(n, SHAPIRO_MAX_ROWS)]
    if n > SHAPIRO_MAX_ROWS:
        notes.append(f"Shapiro-Wilk calcolato su {SHAPIRO_MAX_ROWS:,} valori estratti su {n:,}".replace(",", "."))
    tests = [_test("Shapiro-Wilk", *sps.shapiro(sample), n=len(sample))]
    if n >= 8:
        tests.append(_test("D'Agostino-Pearson K²", *sps.normaltest(data), dof=2, n=n))
    tests.append(_test("Jarque-Bera", *sps.jarque_bera(data), dof=2, n=n))
    tests.append(_test("Kolmogorov-Smirnov (Lilliefors)", *lilliefors(data, dist="norm"), n=n))
    tests.append(_test("Anderson-Darling", *normal_ad(data), n=n))
    return tests


def frequencies(rows: pd.DataFrame, spec: dict) -> StatResult:
    name = _required(spec, "category", "la colonna delle categorie")
    counts = rows[name].astype("object").fillna(MISSING).value_counts()
    table = pd.DataFrame({"valore": counts.index.astype(str), "frequenza": counts.to_numpy()})
    table["%"] = table["frequenza"] / table["frequenza"].sum() * 100
    table["% cumulata"] = table["%"].cumsum()
    result = StatResult(tables={"Frequenze": table})
    if len(counts) > 1:
        statistic, p_value = sps.chisquare(counts.to_numpy())
        result.tables["Test"] = _table([_test("Chi-quadro di adattamento (distribuzione uniforme)", statistic, p_value,
                                              dof=len(counts) - 1, n=int(counts.sum()))])
    top = table.head(MAX_CATEGORIES)
    if len(table) > MAX_CATEGORIES:
        result.notes.append(f"Il grafico mostra le prime {MAX_CATEGORIES} categorie su {len(table)}")
    result.plots.append(Plot("bars", "Frequenze", {"categories": top["valore"].tolist(),
                                                   "bars": {"Frequenza": top["frequenza"].tolist()}, "lines": {}},
                             x_name=name, y_name="frequenza"))
    return result


# ---------------------------------------------------------------------------
# Correlations and outliers
# ---------------------------------------------------------------------------

CORRELATION_TESTS = {"pearson": sps.pearsonr, "spearman": sps.spearmanr, "kendall": sps.kendalltau}


def _varies(values: pd.Series) -> bool:
    """True if the values (not empty, without nulls) are not all equal: like nunique() > 1, without counting them."""
    numbers = values.to_numpy()
    return bool((numbers != numbers[0]).any())


def correlation(rows: pd.DataFrame, spec: dict) -> StatResult:
    columns = spec.get("columns") or numeric_columns(rows)
    for name in columns:
        if not is_numeric(rows[name]):
            raise ValueError(f"La colonna «{name}» non è numerica.")
    if len(columns) < 2:
        raise ValueError("Servono almeno due colonne numeriche.")
    _check_count(len(columns), MAX_CORRELATION_COLUMNS, "colonne scelte")
    method = spec.get("method") or "pearson"
    confidence = _confidence(spec)
    low_name, high_name = _interval_names(confidence)
    data = rows[columns].astype("float64")
    matrix = pd.DataFrame(np.eye(len(columns)), index=columns, columns=columns)
    p_values = pd.DataFrame(0.0, index=columns, columns=columns)
    pairs = []
    for i, first in enumerate(columns):
        for second in columns[i + 1:]:
            pair = data[[first, second]].dropna()
            row = {"colonna 1": first, "colonna 2": second, "n": len(pair), "r": np.nan, "p-value": np.nan}
            if len(pair) >= 3 and _varies(pair[first]) and _varies(pair[second]):
                outcome = CORRELATION_TESTS[method](pair[first], pair[second])
                row.update({"r": float(outcome.statistic), "p-value": float(outcome.pvalue)})
                if method == "pearson":
                    interval = outcome.confidence_interval(confidence)
                    row.update({low_name: interval.low, high_name: interval.high})
            matrix.loc[first, second] = matrix.loc[second, first] = row["r"]
            p_values.loc[first, second] = p_values.loc[second, first] = row["p-value"]
            pairs.append(row)
    ordered = pd.DataFrame(pairs).sort_values("r", key=lambda r: r.abs(), ascending=False, na_position="last")
    result = StatResult(tables={"Matrice": matrix.rename_axis("colonna").reset_index(),
                                "p-value": p_values.rename_axis("colonna").reset_index(),
                                "Coppie": ordered.reset_index(drop=True)})
    result.plots.append(Plot("heatmap", f"Correlazione di {CORRELATION_METHODS[method]}",
                             {"x": columns, "y": columns, "values": matrix.to_numpy().tolist(), "min": -1, "max": 1,
                              "diverging": True}))
    return result


def outlier_bounds(values: pd.Series, method: str, threshold: float) -> tuple[float, float]:
    if method == "iqr":
        q1, q3 = values.quantile([0.25, 0.75])
        return q1 - threshold * (q3 - q1), q3 + threshold * (q3 - q1)
    if method == "zscore":
        return values.mean() - threshold * values.std(), values.mean() + threshold * values.std()
    median = values.median()
    spread = threshold * (values - median).abs().median() / 0.6745
    return median - spread, median + spread


def _flag_labels(hits: pd.DataFrame) -> pd.Series:
    """For each row, the names of the columns where it is an outlier, joined by «, » (column by column, not row by row).
    Without rows the Series stays float64, as the row-by-row apply returned it."""
    text = np.full(len(hits), "", dtype=object)
    for name in hits.columns:
        hit = hits[name].to_numpy()
        current = text[hit]
        text[hit] = np.where(current == "", name, current + ", " + name)
    return pd.Series(text, index=hits.index, dtype="str" if len(hits) else "float64")


def outliers(rows: pd.DataFrame, spec: dict) -> StatResult:
    columns = spec.get("columns") or numeric_columns(rows)
    if not columns:
        raise ValueError("Nessuna colonna numerica.")
    method = spec.get("outlier_method") or "iqr"
    threshold = float(spec.get("threshold") or OUTLIER_METHODS[method].threshold)
    summary, flags, bounds, boxes = [], {}, {}, {}
    for name in columns:
        values = _numbers(rows, name)
        _enough(values, 2, f"«{name}»")
        lower, upper = outlier_bounds(values, method, threshold)
        below, above = values < lower, values > upper
        flags[name] = (below | above).reindex(rows.index, fill_value=False)
        bounds[name], boxes[name] = (lower, upper), values.to_numpy()
        summary.append({"colonna": name, "limite inferiore": lower, "limite superiore": upper, "n": len(values),
                        "anomali": int(below.sum() + above.sum()), "% anomali": (below.sum() + above.sum()) / len(values) * 100,
                        "sotto": int(below.sum()), "sopra": int(above.sum())})
    marks = pd.DataFrame(flags)
    flagged = marks.any(axis=1)
    found = _flag_labels(marks[flagged])
    outlier_rows = rows[flagged].copy()
    outlier_rows.insert(0, "anomalo in", found)
    result = StatResult(tables={"Riepilogo": pd.DataFrame(summary), "Righe anomale": outlier_rows})
    result.plots.append(_box_plot("Valori anomali", boxes, "", bounds))
    return result


# ---------------------------------------------------------------------------
# Hypothesis tests
# ---------------------------------------------------------------------------

def compare_groups(rows: pd.DataFrame, spec: dict) -> StatResult:
    value = _required(spec, "column", "la colonna dei valori")
    group = _required(spec, "group", "la colonna dei gruppi")
    confidence = _confidence(spec)
    groups = {name: values for name, values in _groups(rows, value, group).items() if len(values) >= 2}
    if len(groups) < 2:
        raise ValueError("Servono almeno due gruppi con almeno due valori ciascuno.")
    samples = list(groups.values())
    total = sum(map(len, samples))
    tests = []
    if len(groups) == 2:
        first, second = samples
        effect = _cohen_d(first, second)
        low_name, high_name = _interval_names(confidence)
        for name, equal in (("t di Welch (varianze diverse)", False), ("t di Student (varianze uguali)", True)):
            outcome = sps.ttest_ind(first, second, equal_var=equal)
            interval = outcome.confidence_interval(confidence)
            tests.append(_test(name, outcome.statistic, outcome.pvalue, outcome.df, effect, "d di Cohen",
                               differenza=first.mean() - second.mean(), **{low_name: interval.low, high_name: interval.high}))
        outcome = sps.mannwhitneyu(first, second, alternative="two-sided")
        tests.append(_test("Mann-Whitney U", outcome.statistic, outcome.pvalue,
                           effect=1 - 2 * outcome.statistic / (len(first) * len(second)), effect_name="r rango-biseriale"))
    values = np.concatenate(samples)
    between = sum(len(sample) * (sample.mean() - values.mean()) ** 2 for sample in samples)
    within = ((values - values.mean()) ** 2).sum()
    outcome = sps.f_oneway(*samples)
    tests.append(_test("ANOVA a una via", outcome.statistic, outcome.pvalue, (len(samples) - 1, total - len(samples)),
                       between / within if within else np.nan, "η²"))
    welch = anova_oneway(samples, use_var="unequal")
    tests.append(_test("ANOVA di Welch", welch.statistic, welch.pvalue, (welch.df_num, welch.df_denom)))
    outcome = sps.kruskal(*samples)
    tests.append(_test("Kruskal-Wallis H", outcome.statistic, outcome.pvalue, len(samples) - 1,
                       outcome.statistic * (total + 1) / (total ** 2 - 1), "ε²"))
    tests.append(_test("Levene (Brown-Forsythe, mediana)", *sps.levene(*samples, center="median"),
                       (len(samples) - 1, total - len(samples))))
    tests.append(_test("Bartlett", *sps.bartlett(*samples), len(samples) - 1))

    summaries = [{"gruppo": name, **numeric_summary(pd.Series(sample), confidence)} for name, sample in groups.items()]
    keep = ["gruppo", "n", "media", "dev. std", "errore std", *_interval_names(confidence), "mediana", "q1", "q3", "min", "max"]
    result = StatResult(tables={"Test": _table(tests), "Gruppi": pd.DataFrame(summaries)[keep]})
    if len(groups) > 2:
        result.tables["Tukey HSD"] = _tukey(groups, confidence)
    result.plots.append(_box_plot(f"{value} per {group}", groups, value))
    return result


def _tukey(groups: dict[str, np.ndarray], confidence: float) -> pd.DataFrame:
    values = np.concatenate(list(groups.values()))
    labels = np.concatenate([[name] * len(sample) for name, sample in groups.items()])
    outcome = pairwise_tukeyhsd(values, labels, alpha=1 - confidence)
    header, *body = outcome.summary().data
    table = pd.DataFrame(body, columns=header)
    low_name, high_name = _interval_names(confidence)
    return table.rename(columns={"group1": "gruppo 1", "group2": "gruppo 2", "meandiff": "differenza medie",
                                 "p-adj": "p-value corretto", "lower": low_name, "upper": high_name,
                                 "reject": "rifiuta H₀"})


def one_sample(rows: pd.DataFrame, spec: dict) -> StatResult:
    name = _required(spec, "column", "la colonna")
    reference = float(spec.get("mu") or 0)
    confidence = _confidence(spec)
    values = _numbers(rows, name)
    _enough(values, 3, "il test")
    data = values.to_numpy()
    low_name, high_name = _interval_names(confidence)
    outcome = sps.ttest_1samp(data, reference)
    interval = outcome.confidence_interval(confidence)
    std = data.std(ddof=1)
    tests = [_test("t a un campione", outcome.statistic, outcome.pvalue, outcome.df,
                   (data.mean() - reference) / std if std else np.nan, "d di Cohen",
                   differenza=data.mean() - reference, **{low_name: interval.low, high_name: interval.high})]
    if np.any(data != reference):
        outcome = sps.wilcoxon(data - reference)
        tests.append(_test("Wilcoxon dei ranghi con segno", outcome.statistic, outcome.pvalue,
                           effect=_rank_biserial(data - reference), effect_name="r rango-biseriale"))
    statistic, p_value = sign_test(data, reference)
    tests.append(_test("Test del segno", statistic, p_value))
    summary = {"n": len(data), "media": data.mean(), "mediana": np.median(data), "μ₀": reference,
               "differenza dalla media": data.mean() - reference, "dev. std": std}
    histogram, plot = _histogram(values, DEFAULT_BINS, name, normal=False)
    return StatResult(tables={"Test": _table(tests), "Campione": _indices(summary), "Istogramma": histogram},
                      plots=[plot])


def paired(rows: pd.DataFrame, spec: dict) -> StatResult:
    first_name = _required(spec, "column", "la prima colonna")
    second_name = _required(spec, "column2", "la seconda colonna")
    confidence = _confidence(spec)
    for name in (first_name, second_name):
        _numbers(rows, name)  # checks that they are numeric
    pair = rows[[first_name, second_name]].astype("float64").dropna()
    _enough(pair, 3, "il test")
    first, second = pair[first_name].to_numpy(), pair[second_name].to_numpy()
    differences = first - second
    low_name, high_name = _interval_names(confidence)
    outcome = sps.ttest_rel(first, second)
    interval = outcome.confidence_interval(confidence)
    std = differences.std(ddof=1)
    tests = [_test("t per campioni appaiati", outcome.statistic, outcome.pvalue, outcome.df,
                   differences.mean() / std if std else np.nan, "d di Cohen (dz)", differenza=differences.mean(),
                   **{low_name: interval.low, high_name: interval.high})]
    if np.any(differences != 0):
        outcome = sps.wilcoxon(first, second)
        tests.append(_test("Wilcoxon dei ranghi con segno", outcome.statistic, outcome.pvalue,
                           effect=_rank_biserial(differences), effect_name="r rango-biseriale"))
    statistic, p_value = sign_test(differences, 0)
    tests.append(_test("Test del segno", statistic, p_value))
    if np.std(first) and np.std(second):
        outcome = sps.pearsonr(first, second)
        tests.append(_test("Correlazione di Pearson tra le colonne", outcome.statistic, outcome.pvalue, len(first) - 2))
    summaries = [{"colonna": name, **numeric_summary(pd.Series(values), confidence)}
                 for name, values in ((first_name, first), (second_name, second), ("differenza", differences))]
    plot = _scatter(f"{first_name} e {second_name}", first, second, first_name, second_name,
                    {"y = x": _diagonal(np.concatenate([first, second]))})
    return StatResult(tables={"Test": _table(tests), "Descrittive": pd.DataFrame(summaries)}, plots=[plot])


def independence(rows: pd.DataFrame, spec: dict) -> StatResult:
    first = _required(spec, "category", "la prima colonna delle categorie")
    second = _required(spec, "category2", "la seconda colonna delle categorie")
    if first == second:
        raise ValueError("Scegli due colonne diverse.")
    data = rows[[first, second]].dropna()
    for name in (first, second):
        _check_count(data[name].nunique(), MAX_CATEGORIES, f"«{name}»")
    observed = pd.crosstab(data[first].astype(str), data[second].astype(str))
    if min(observed.shape) < 2:
        raise ValueError("Servono almeno due categorie in ogni colonna.")
    counts = observed.to_numpy()
    n = counts.sum()
    statistic, p_value, dof, expected = sps.chi2_contingency(counts, correction=False)
    cramer = math.sqrt(statistic / (n * (min(counts.shape) - 1)))
    tests = [_test("Chi-quadro di Pearson", statistic, p_value, dof, cramer, "V di Cramér", n=n)]
    if counts.shape == (2, 2):
        yates, yates_p, *_ = sps.chi2_contingency(counts, correction=True)
        tests.append(_test("Chi-quadro con correzione di Yates", yates, yates_p, dof, n=n))
        odds, fisher_p = sps.fisher_exact(counts)
        tests.append(_test("Fisher esatto", odds, fisher_p, effect=odds, effect_name="odds ratio", n=n))
    likelihood, likelihood_p, *_ = sps.chi2_contingency(counts, correction=False, lambda_="log-likelihood")
    tests.append(_test("Rapporto di verosimiglianza (G)", likelihood, likelihood_p, dof, n=n))
    small = (expected < 5).mean() * 100
    if small:
        tests[0]["% celle attese < 5"] = small
    row_share = counts.sum(axis=1, keepdims=True) / n
    column_share = counts.sum(axis=0, keepdims=True) / n
    adjusted = (counts - expected) / np.sqrt(expected * (1 - row_share) * (1 - column_share))

    def framed(values) -> pd.DataFrame:
        return pd.DataFrame(values, index=observed.index, columns=observed.columns).rename_axis(first).reset_index()

    with_totals = observed.copy()
    with_totals["Totale"] = with_totals.sum(axis=1)
    with_totals.loc["Totale"] = with_totals.sum()
    result = StatResult(tables={"Test": _table(tests), "Osservate": with_totals.rename_axis(first).reset_index(),
                                "Attese": framed(expected), "Residui standardizzati corretti": framed(adjusted)})
    result.plots.append(Plot("heatmap", f"{first} × {second}",
                             {"x": observed.index.tolist(), "y": observed.columns.tolist(), "values": counts.T.tolist()},
                             x_name=first, y_name=second))
    return result


# ---------------------------------------------------------------------------
# Regression
# ---------------------------------------------------------------------------

def _design(rows: pd.DataFrame, spec: dict) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Complete rows and predictor matrix: numbers as they are, categories as 0/1 variables."""
    target = _required(spec, "y", "la variabile dipendente")
    predictors = [name for name in _required(spec, "x", "almeno una variabile indipendente") if name != target]
    if not predictors:
        raise ValueError("Le variabili indipendenti devono essere diverse dalla dipendente.")
    data = rows[[target, *predictors]].dropna()
    parts = []
    for name in predictors:
        column = data[name]
        if is_numeric(column) or pd.api.types.is_bool_dtype(column):
            parts.append(_floats(column).rename(name))
        else:
            _check_count(column.nunique(), MAX_CATEGORIES, f"«{name}»")
            parts.append(pd.get_dummies(column.astype(str), prefix=name, prefix_sep=" = ", drop_first=True, dtype=float))
    design = sm.add_constant(pd.concat(parts, axis=1), has_constant="add").rename(columns={"const": INTERCEPT})
    if len(data) <= design.shape[1]:
        raise ValueError(f"Servono più righe complete ({len(data)}) che coefficienti ({design.shape[1]}).")
    return data, design


def _coefficients(model, confidence: float, statistic: str) -> pd.DataFrame:
    interval = model.conf_int(1 - confidence)
    low_name, high_name = _interval_names(confidence)
    return pd.DataFrame({"variabile": model.params.index, "coefficiente": model.params.to_numpy(),
                         "errore std": model.bse.to_numpy(), statistic: model.tvalues.to_numpy(),
                         "p-value": model.pvalues.to_numpy(), low_name: interval[0].to_numpy(),
                         high_name: interval[1].to_numpy()})


def linear_regression(rows: pd.DataFrame, spec: dict) -> StatResult:
    data, design = _design(rows, spec)
    target = spec["y"]
    observed = _numbers(data, target)
    model = sm.OLS(observed, design).fit()
    confidence = _confidence(spec)
    coefficients = _coefficients(model, confidence, "t")
    predictors = design.drop(columns=INTERCEPT)
    coefficients["coefficiente standardizzato"] = [
        np.nan if name == INTERCEPT else model.params[name] * predictors[name].std() / observed.std()
        for name in design.columns]
    if predictors.shape[1] > 1:
        coefficients["VIF"] = [np.nan if name == INTERCEPT else variance_inflation_factor(design.to_numpy(), position)
                               for position, name in enumerate(design.columns)]
    residuals, fitted = model.resid, model.fittedvalues
    fit = {"n": model.nobs, "coefficienti": design.shape[1], "R²": model.rsquared, "R² corretto": model.rsquared_adj,
           "F": model.fvalue, "p-value F": model.f_pvalue, "RMSE": math.sqrt(model.mse_resid), "AIC": model.aic,
           "BIC": model.bic, "log-verosimiglianza": model.llf, "Durbin-Watson": durbin_watson(residuals),
           "p-value Jarque-Bera (residui)": sps.jarque_bera(residuals).pvalue,
           "p-value Breusch-Pagan": het_breuschpagan(residuals, design)[1]}
    predictions = data.copy()
    predictions["previsto"] = fitted
    predictions["residuo"] = residuals
    predictions["residuo standardizzato"] = residuals / math.sqrt(model.mse_resid)
    plots = []
    if predictors.shape[1] == 1:
        name = predictors.columns[0]
        x = predictors[name].to_numpy()
        ends = np.array([x.min(), x.max()])
        line = model.params[INTERCEPT] + model.params[name] * ends
        plots.append(_scatter(f"{target} rispetto a {name}", x, observed.to_numpy(), name, target,
                              {"retta di regressione": np.column_stack([ends, line]).tolist()}))
    plots.append(_scatter("Osservati e previsti", fitted.to_numpy(), observed.to_numpy(), "previsto", target,
                          {"y = x": _diagonal(np.concatenate([fitted, observed]))}))
    plots.append(_scatter("Residui", fitted.to_numpy(), residuals.to_numpy(), "previsto", "residuo",
                          {"zero": [[float(fitted.min()), 0.0], [float(fitted.max()), 0.0]]}))
    return StatResult(tables={"Coefficienti": coefficients, "Bontà del modello": _indices(fit),
                              "Previsioni": predictions}, plots=plots)


def _binary(values: pd.Series) -> tuple[pd.Series, object]:
    """0/1 variable and the value taken as «1» (the greatest: True, the highest number, the last in alphabetical order)."""
    distinct = values.unique()
    if len(distinct) != 2:
        raise ValueError(f"La variabile dipendente deve avere esattamente due valori (ne ha {len(distinct)}).")
    try:
        positive = sorted(distinct)[-1]
    except TypeError:
        positive = sorted(distinct, key=str)[-1]
    return (values == positive).astype("float64"), positive


def _roc(observed: np.ndarray, probability: np.ndarray) -> np.ndarray:
    order = np.argsort(-probability, kind="stable")
    hits = observed[order]
    true_rate = np.concatenate([[0], np.cumsum(hits) / hits.sum()])
    false_rate = np.concatenate([[0], np.cumsum(1 - hits) / (1 - hits).sum()])
    index = np.unique(np.concatenate([[0], _sample_index(len(true_rate), 500), [len(true_rate) - 1]]))
    return np.column_stack([false_rate[index], true_rate[index]])


def logistic_regression(rows: pd.DataFrame, spec: dict) -> StatResult:
    data, design = _design(rows, spec)
    target = spec["y"]
    observed, positive = _binary(data[target])
    try:
        model = sm.Logit(observed, design).fit(disp=0, maxiter=200)
    except Exception as error:  # noqa: BLE001 (perfect separation, singular matrix, ...)
        raise ValueError(f"Il modello logistico non si può stimare: {error}") from error
    confidence = _confidence(spec)
    coefficients = _coefficients(model, confidence, "z")
    low_name, high_name = _interval_names(confidence)
    coefficients["odds ratio"] = np.exp(coefficients["coefficiente"])
    coefficients[f"OR {low_name}"] = np.exp(coefficients[low_name])
    coefficients[f"OR {high_name}"] = np.exp(coefficients[high_name])
    probability = model.predict(design).to_numpy()
    actual = observed.to_numpy()
    predicted = probability >= 0.5
    true_positive = int((predicted & (actual == 1)).sum())
    true_negative = int((~predicted & (actual == 0)).sum())
    positives, negatives = int(actual.sum()), int(len(actual) - actual.sum())
    auc = sps.mannwhitneyu(probability[actual == 1], probability[actual == 0]).statistic / (positives * negatives)
    fit = {"n": model.nobs, f"positivi (y = {positive})": positives, "pseudo R² (McFadden)": model.prsquared,
           "log-verosimiglianza": model.llf, "log-verosimiglianza nulla": model.llnull, "LR χ²": model.llr,
           "p-value LR": model.llr_pvalue, "AIC": model.aic, "BIC": model.bic, "AUC": auc,
           "accuratezza (soglia 0,5)": (true_positive + true_negative) / len(actual),
           "sensibilità": true_positive / positives, "specificità": true_negative / negatives}
    others = [value for value in data[target].unique() if value != positive]
    confusion = pd.DataFrame({"osservato": [f"{target} = {others[0]}", f"{target} = {positive}"],
                              f"previsto {others[0]}": [true_negative, positives - true_positive],
                              f"previsto {positive}": [negatives - true_negative, true_positive]})
    predictions = data.copy()
    predictions[f"probabilità {target} = {positive}"] = probability
    curve = _roc(actual, probability)
    plot = Plot("scatter", f"Curva ROC (AUC {_label(auc)})",
                {"points": {}, "lines": {"ROC": curve.tolist(), "casuale": [[0.0, 0.0], [1.0, 1.0]]}},
                x_name="1 − specificità", y_name="sensibilità")
    return StatResult(tables={"Coefficienti": coefficients, "Bontà del modello": _indices(fit),
                              "Matrice di confusione": confusion, "Previsioni": predictions}, plots=[plot])


# ---------------------------------------------------------------------------
# Time series
# ---------------------------------------------------------------------------

def _period_labels(index: pd.DatetimeIndex, unit: TimeUnit) -> list[str]:
    return [stamp.strftime(unit.label).replace("{q}", str(stamp.quarter)) for stamp in index]


def time_series_values(rows: pd.DataFrame, spec: dict) -> pd.Series:
    """Series aggregated by period (null or invalid dates are dropped)."""
    date_name = _required(spec, "date", "la colonna delle date")
    unit = TIME_UNITS[spec.get("time_unit") or DEFAULT_TIME_UNIT]
    aggregation = spec.get("time_agg") or "somma"
    value = spec.get("column")
    dates = pd.to_datetime(rows[date_name], errors="coerce")
    if aggregation == COUNT:
        values = rows[value].notna() if value else pd.Series(True, index=rows.index)
        aggregation = "somma"
    elif value:
        _numbers(rows, value)  # checks that it is numeric
        values = _floats(rows[value])
    else:
        raise ValueError("Scegli la colonna dei valori (o l'aggregazione «conteggio»).")
    keep = dates.notna()
    if not keep.any():
        raise ValueError(f"La colonna «{date_name}» non contiene date valide.")
    series = pd.Series(values[keep].to_numpy(dtype="float64"), index=pd.DatetimeIndex(dates[keep])).sort_index()
    return series.resample(unit.rule).agg(eng.AGG_FUNCTIONS[aggregation])


def time_series(rows: pd.DataFrame, spec: dict) -> StatResult:
    unit = TIME_UNITS[spec.get("time_unit") or DEFAULT_TIME_UNIT]
    series = time_series_values(rows, spec)
    _enough(series, 4, "la serie temporale")
    result = StatResult()
    missing = int(series.isna().sum())
    filled = series.interpolate(limit_direction="both")
    if missing:
        result.notes.append(f"{missing} periodi senza valori: interpolati per le analisi")
    n = len(filled)
    labels = _period_labels(filled.index, unit)
    steps = np.arange(n)
    trend_line = sps.linregress(steps, filled.to_numpy())
    tau = sps.kendalltau(steps, filled.to_numpy())
    trend = {"periodi": n, "pendenza per periodo": trend_line.slope, "intercetta": trend_line.intercept,
             "R²": trend_line.rvalue ** 2, "p-value pendenza": trend_line.pvalue, "tau di Kendall (Mann-Kendall)":
             tau.statistic, "p-value Mann-Kendall": tau.pvalue}
    table = pd.DataFrame({"periodo": filled.index, "valore": series.to_numpy(),
                          "retta di tendenza": trend_line.intercept + trend_line.slope * steps})
    period = int(spec.get("period") or unit.period)
    lines_main = {"valore": filled.tolist(), "retta di tendenza": table["retta di tendenza"].tolist()}
    if period >= 2 and n >= 2 * period:
        parts = seasonal_decompose(filled, model="additive", period=period)
        table["trend (media mobile)"] = parts.trend.to_numpy()
        table["stagionale"] = parts.seasonal.to_numpy()
        table["residuo"] = parts.resid.to_numpy()
        residual = parts.resid.dropna()
        trend["forza del trend"] = max(0.0, 1 - residual.var() / (parts.trend + parts.resid).dropna().var())
        trend["forza della stagionalità"] = max(0.0, 1 - residual.var() / (parts.seasonal + parts.resid).dropna().var())
        positions = steps % period + 1
        result.tables["Indici stagionali"] = pd.DataFrame({"posizione nel ciclo": range(1, period + 1),
                                                           "effetto stagionale": parts.seasonal.groupby(positions).first().to_numpy()})
        lines_main["trend (media mobile)"] = parts.trend.tolist()
        result.plots.append(Plot("lines", "Stagionalità e residui", {"x": labels, "series": {
            "stagionale": parts.seasonal.tolist(), "residuo": parts.resid.tolist()}}))
    elif period >= 2:
        result.notes.append(f"Scomposizione non calcolata: servono almeno {2 * period} periodi (ce ne sono {n})")
    result.plots.insert(0, Plot("lines", "Serie e trend", {"x": labels, "series": lines_main}))
    result.tables = {"Serie": table, "Trend": _indices(trend), **result.tables}
    result.tables["Stazionarietà"] = _table(_stationarity(filled, result.notes))
    nlags = min(int(spec.get("lags") or DEFAULT_LAGS), n // 2 - 1)
    if nlags >= 1 and filled.std() > 0:
        result.tables["Autocorrelazione"], plot = _autocorrelation(filled, nlags)
        result.plots.append(plot)
    return result


def _stationarity(values: pd.Series, notes: list[str]) -> list[dict]:
    tests = []
    for name, function, keyword in (("ADF (Dickey-Fuller aumentato)", adfuller, {"autolag": "AIC"}),
                                    ("KPSS", kpss, {"regression": "c", "nlags": "auto"})):
        try:
            outcome = function(values, **keyword)
        except (ValueError, np.linalg.LinAlgError) as error:
            notes.append(f"{name} non calcolato: {error}")
            continue
        critical = outcome[4] if function is adfuller else outcome[3]
        lags = outcome[2]
        tests.append({"test": name, "statistica": outcome[0], "p-value": outcome[1], "ritardi": lags,
                      "valore critico 5%": critical["5%"]})
    return tests


def _autocorrelation(values: pd.Series, nlags: int) -> tuple[pd.DataFrame, Plot]:
    correlations = acf(values, nlags=nlags, fft=True)[1:]
    partial = pacf(values, nlags=nlags)[1:]
    ljung = acorr_ljungbox(values, lags=nlags)
    band = 1.96 / math.sqrt(len(values))
    lags = list(range(1, nlags + 1))
    table = pd.DataFrame({"ritardo": lags, "ACF": correlations, "PACF": partial,
                          "Ljung-Box": ljung["lb_stat"].to_numpy(), "p-value Ljung-Box": ljung["lb_pvalue"].to_numpy(),
                          "banda 95%": band})
    plot = Plot("bars", "Autocorrelazione", {"categories": [str(lag) for lag in lags],
                                             "bars": {"ACF": correlations.tolist(), "PACF": partial.tolist()},
                                             "lines": {"+ banda 95%": [band] * nlags, "− banda 95%": [-band] * nlags}},
                x_name="ritardo", y_name="")
    return table, plot


# ---------------------------------------------------------------------------
# Catalog and entry point
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class Analysis:
    label: str
    group: str
    description: str
    fields: tuple[str, ...]
    run: Callable[[pd.DataFrame, dict], StatResult]
    labels: dict[str, str] = field(default_factory=dict)  # control labels that differ from FIELD_LABELS
    all_columns: bool = False  # the result shows whole rows (all the columns are needed)


ANALYSES = {
    "describe": Analysis("Statistiche descrittive", "Descrittive",
                         "Numeriche: n, media, dev. std, IC della media, quartili, asimmetria, curtosi. "
                         "Altre: distinti, moda.", ("columns", "group", "confidence"), describe,
                         {"group": "Gruppo (facoltativo)"}),
    "distribution": Analysis("Distribuzione e normalità", "Descrittive",
                             "Percentili, istogramma con normale attesa, Q-Q plot, test di normalità.",
                             ("column", "bins"), distribution),
    "frequencies": Analysis("Frequenze", "Descrittive",
                            "Frequenze, %, % cumulata, chi-quadro rispetto alla distribuzione uniforme.",
                            ("category",), frequencies),
    "outliers": Analysis("Valori anomali", "Descrittive", "Limiti, conteggi e righe dei valori anomali per colonna.",
                         ("columns", "outlier_method", "threshold"), outliers,
                         {"columns": "Colonne numeriche (vuoto = tutte)"}, all_columns=True),
    "correlation": Analysis("Correlazioni", "Relazioni", "Matrice delle correlazioni, p-value e coppie ordinate per |r|.",
                            ("columns", "method", "confidence"), correlation,
                            {"columns": "Colonne numeriche (vuoto = tutte)"}),
    "compare_groups": Analysis("Confronto tra gruppi", "Test d'ipotesi",
                               "t di Welch e di Student, Mann-Whitney (2 gruppi), ANOVA, ANOVA di Welch, "
                               "Kruskal-Wallis, Levene, Bartlett, Tukey HSD (più di 2 gruppi).",
                               ("column", "group", "confidence"), compare_groups),
    "one_sample": Analysis("Test su un campione", "Test d'ipotesi",
                           "t a un campione, Wilcoxon, test del segno rispetto a μ₀.",
                           ("column", "mu", "confidence"), one_sample),
    "paired": Analysis("Campioni appaiati", "Test d'ipotesi",
                       "t per dati appaiati, Wilcoxon, test del segno sulla differenza tra due colonne.",
                       ("column", "column2", "confidence"), paired, {"column": "Prima colonna numerica"}),
    "independence": Analysis("Indipendenza tra categorie", "Test d'ipotesi",
                             "Tabella di contingenza, chi-quadro, G, Fisher (2×2), V di Cramér, residui.",
                             ("category", "category2"), independence,
                             {"category": "Prima colonna delle categorie"}),
    "linear": Analysis("Regressione lineare", "Modelli",
                       "Minimi quadrati: coefficienti, IC, VIF, R², F, AIC/BIC, diagnostica dei residui.",
                       ("y", "x", "confidence"), linear_regression),
    "logistic": Analysis("Regressione logistica", "Modelli",
                         "y con due valori: coefficienti, odds ratio, pseudo R², AUC, matrice di confusione, ROC.",
                         ("y", "x", "confidence"), logistic_regression),
    "time_series": Analysis("Serie temporale", "Modelli",
                            "Serie per periodo, trend (regressione, Mann-Kendall), scomposizione stagionale, "
                            "ADF/KPSS, ACF/PACF, Ljung-Box.",
                            ("date", "column", "time_unit", "time_agg", "period", "lags"), time_series,
                            {"column": "Valori (vuoto con «conteggio» = numero di righe)"}),
}
ANALYSIS_OPTIONS = {key: f"{analysis.group} · {analysis.label}" for key, analysis in ANALYSES.items()}


def field_label(analysis: str, name: str) -> str:
    return ANALYSES[analysis].labels.get(name, FIELD_LABELS[name])


COLUMN_FIELDS = ("columns", "column", "column2", "group", "category", "category2", "y", "x", "date")


def used_columns(analysis: Analysis, spec: dict) -> list[str] | None:
    """Columns the analysis reads; None = all (whole rows in the result, or «Colonne» left empty)."""
    if analysis.all_columns or ("columns" in analysis.fields and not spec.get("columns")):
        return None
    names = []
    for name in COLUMN_FIELDS:
        value = spec.get(name) if name in analysis.fields else None
        names += value if isinstance(value, list) else [value] if value else []
    return names


def run(df: pd.DataFrame, spec: dict) -> StatResult:
    """Runs the analysis spec["analysis"] on the filtered rows (only the columns it uses)."""
    analysis = ANALYSES[spec["analysis"]]
    rows = eng.apply_filters(df, spec.get("filters"), used_columns(analysis, spec))
    if not len(rows):
        raise ValueError("Nessuna riga dopo i filtri.")
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")  # numeric warnings from scipy/statsmodels: the results stay in the tables
        return analysis.run(rows, spec)
