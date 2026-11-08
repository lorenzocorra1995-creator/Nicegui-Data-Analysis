"""
Number format of the result tables and of the charts (no UI dependency).

`NumberFormat` is the choice of the user for one section of the app (none, or decimal with a number of decimals,
in the Italian or American style); `grid_formatter` turns it into the JavaScript `valueFormatter` of a numeric
AG Grid column. The charts only use the style (`locale`): their decimals have a choice of their own.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import NamedTuple

NO_FORMAT, DECIMAL = "Nessun formato", "Formato decimale"
MODES = [NO_FORMAT, DECIMAL]
DECIMALS_RANGE = (0, 10)
DEFAULT_DECIMALS = 2
PRECISE_DECIMALS = 4  # statistics tables


class Style(NamedTuple):
    locale: str  # JavaScript locale
    decimal: str  # decimal separator
    example: str  # shown next to the name


ITALIAN, AMERICAN = "Italiano", "Americano"
STYLES = {
    ITALIAN: Style("it-IT", ",", "1.234,56"),
    AMERICAN: Style("en-US", ".", "1,234.56"),
}
ITALIAN_LOCALE = STYLES[ITALIAN].locale


@dataclass(frozen=True)
class NumberFormat:
    mode: str = DECIMAL
    decimals: int = DEFAULT_DECIMALS
    style: str = ITALIAN

    def __post_init__(self) -> None:
        low, high = DECIMALS_RANGE
        if self.mode not in MODES or self.style not in STYLES or not low <= self.decimals <= high:
            raise ValueError(f"Formato numeri non valido: {self}")

    @property
    def locale(self) -> str:
        return STYLES[self.style].locale


def clamp_decimals(value, default: int = DEFAULT_DECIMALS) -> int:
    """Number of decimals within DECIMALS_RANGE; `default` if the value is missing."""
    if value is None:
        return default
    low, high = DECIMALS_RANGE
    return min(max(int(value), low), high)


def grid_formatter(fmt: NumberFormat, integer: bool, precise: bool = False) -> str | None:
    """JavaScript `valueFormatter` of a numeric column; None = the values as they are.

    Integers get only the thousands separator. `precise` (statistics): `decimals` is a maximum, so that counts
    stay whole, and very small or very large values use the exponential notation (p-values).
    """
    if fmt.mode == NO_FORMAT:
        return None
    most = 0 if integer else fmt.decimals
    least = 0 if precise else most
    options = f"useGrouping: 'always', minimumFractionDigits: {least}, maximumFractionDigits: {most}"
    exponential = ""
    if precise and not integer:
        decimal = STYLES[fmt.style].decimal
        exponential = (f"const a = Math.abs(v); if (a !== 0 && (a < 0.001 || a >= 1e12)) "
                       f"return v.toExponential(3).replace('.', '{decimal}'); ")
    return (f"(() => {{ const f = new Intl.NumberFormat('{fmt.locale}', {{{options}}}); "
            f"return (params) => {{ const v = params.value; if (v == null) return ''; "
            f"{exponential}return f.format(v); }}; }})()")


def localized(options, locale: str):
    """ECharts options with the numbers formatted for `locale`.

    The JavaScript functions of the charts (keys with the ":" prefix) are written for the Italian locale and
    format numbers with `toLocaleString('it-IT', {...})`: dates, which have no options, stay as they are.
    """
    if locale == ITALIAN_LOCALE:
        return options
    if isinstance(options, dict):
        return {key: value.replace(f"'{ITALIAN_LOCALE}', {{", f"'{locale}', {{")
                if key.startswith(":") and isinstance(value, str) else localized(value, locale)
                for key, value in options.items()}
    if isinstance(options, list):
        return [localized(item, locale) for item in options]
    return options


def format_number(value: float, decimals: int = 0, style: str = ITALIAN) -> str:
    """Number as text with the thousands separator and `decimals` decimals, in the chosen style (Python side)."""
    text = f"{value:,.{decimals}f}"
    return text if style == AMERICAN else text.translate(str.maketrans(",.", ".,"))
