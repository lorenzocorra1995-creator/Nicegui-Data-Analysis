import json
import shutil
import subprocess

import pytest

from data_explorer.core import number_format as nf
ITALIAN = nf.NumberFormat(nf.DECIMAL, 2, nf.ITALIAN)
AMERICAN = nf.NumberFormat(nf.DECIMAL, 2, nf.AMERICAN)


def run_formatter(code: str, values: list) -> list[str]:
    """Runs the JavaScript formatter on the values with Node (the browser engine would give the same result)."""
    script = f"const f = {code}; console.log(JSON.stringify({json.dumps(values)}.map((v) => f({{value: v}}))))"
    return json.loads(subprocess.run(["node", "-e", script], capture_output=True, text=True, check=True).stdout)


def test_default_format_and_validation():
    assert nf.NumberFormat() == ITALIAN
    for wrong in ({"mode": "x"}, {"style": "x"}, {"decimals": -1}, {"decimals": nf.DECIMALS_RANGE[1] + 1}):
        with pytest.raises(ValueError):
            nf.NumberFormat(**wrong)
    assert nf.NumberFormat(style=nf.AMERICAN).locale == "en-US"


def test_clamp_decimals():
    assert nf.clamp_decimals(None) == nf.DEFAULT_DECIMALS and nf.clamp_decimals(None, 4) == 4
    assert nf.clamp_decimals(-3) == 0 and nf.clamp_decimals(99) == nf.DECIMALS_RANGE[1] and nf.clamp_decimals(3.0) == 3


def test_no_format_leaves_values_as_they_are():
    none = nf.NumberFormat(nf.NO_FORMAT)
    assert nf.grid_formatter(none, integer=False) is None and nf.grid_formatter(none, integer=True) is None


def test_formatter_options():
    code = nf.grid_formatter(nf.NumberFormat(nf.DECIMAL, 3, nf.AMERICAN), integer=False)
    assert "'en-US'" in code and "minimumFractionDigits: 3, maximumFractionDigits: 3" in code
    assert "useGrouping: 'always'" in code  # also 1.234, which the Italian locale would leave without separator
    assert "maximumFractionDigits: 0" in nf.grid_formatter(ITALIAN, integer=True)  # whole numbers get no decimals
    precise = nf.grid_formatter(nf.NumberFormat(decimals=4), integer=False, precise=True)
    assert "minimumFractionDigits: 0, maximumFractionDigits: 4" in precise and "toExponential" in precise


@pytest.mark.skipif(shutil.which("node") is None, reason="Node is not installed")
def test_formatted_values():
    values = [1234.5, 1234567.891, 12, -0.005, None, 0]
    assert run_formatter(nf.grid_formatter(ITALIAN, False), values) == ["1.234,50", "1.234.567,89", "12,00", "-0,01", "", "0,00"]
    assert run_formatter(nf.grid_formatter(AMERICAN, False), values) == ["1,234.50", "1,234,567.89", "12.00", "-0.01", "", "0.00"]
    zero = nf.NumberFormat(nf.DECIMAL, 0, nf.ITALIAN)
    assert run_formatter(nf.grid_formatter(zero, False), [1234.5, 99.4]) == ["1.235", "99"]
    assert run_formatter(nf.grid_formatter(ITALIAN, True), [1234, 12345678, 5, 2024, None]) == [
        "1.234", "12.345.678", "5", "2.024", ""]
    assert run_formatter(nf.grid_formatter(nf.NumberFormat(nf.DECIMAL, 3, nf.ITALIAN), True), [1234]) == ["1.234"]


@pytest.mark.skipif(shutil.which("node") is None, reason="Node is not installed")
def test_precise_values_keep_counts_whole_and_small_values_exponential():
    italian = nf.grid_formatter(nf.NumberFormat(decimals=4), False, precise=True)
    american = nf.grid_formatter(nf.NumberFormat(decimals=4, style=nf.AMERICAN), False, precise=True)
    values = [30.0, 0.5, 1234.56789, 0.0000123, 0.0, 2e12]
    assert run_formatter(italian, values) == ["30", "0,5", "1.234,5679", "1,230e-5", "0", "2,000e+12"]
    assert run_formatter(american, values) == ["30", "0.5", "1,234.5679", "1.230e-5", "0", "2.000e+12"]


def test_localized_changes_numbers_but_not_dates():
    options = {"title": {"text": "x"}, "series": [{":formatter": "(v) => Number(v).toLocaleString('it-IT', {a: 1})"}],
               "tooltip": {":formatter": "(v) => new Date(v).toLocaleString('it-IT')"}, "text": "'it-IT', {"}
    out = nf.localized(options, "en-US")
    assert out["series"][0][":formatter"] == "(v) => Number(v).toLocaleString('en-US', {a: 1})"
    assert out["tooltip"][":formatter"] == options["tooltip"][":formatter"]  # date: no options
    assert out["text"] == options["text"]  # only the JavaScript functions are touched
    assert options["series"][0][":formatter"].count("it-IT") == 1  # the original stays as it was
    assert nf.localized(options, nf.ITALIAN_LOCALE) is options


def test_format_number_in_both_styles():
    assert nf.format_number(1234567.891, 2) == "1.234.567,89"
    assert nf.format_number(1234567.891, 0, nf.AMERICAN) == "1,234,568"
