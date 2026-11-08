import pytest

from data_explorer.ui import style
def test_presets_cover_light_and_dark():
    assert len(style.PRESETS) >= 5 and style.DEFAULT in style.PRESETS.values()
    assert {preset.dark for preset in style.PRESETS.values()} == {True, False}


@pytest.mark.parametrize("name", style.PRESETS)
def test_preset_palette_is_complete_and_readable(name):
    palette = style.resolve(style.PRESETS[name])
    for color in (palette.primary, palette.on_primary, palette.canvas, palette.surface, palette.ink,
                  palette.muted, palette.line, palette.soft, *palette.charts):
        assert style.is_color(color), color
    assert len(palette.heat) == 4 and all(map(style.is_color, palette.heat))
    assert palette.editor == ("basicDark" if palette.dark else "basicLight")
    assert style.is_readable(palette)
    assert (style.brightness(palette.surface) < 128) == palette.dark


@pytest.mark.parametrize("colors", style.CHART_PALETTES.values())
def test_chart_palettes(colors):
    assert len(colors) >= 8 and len(set(colors)) == len(colors) and all(map(style.is_color, colors))


def test_accent_and_text_on_accent():
    for accent in style.ACCENTS.values():
        palette = style.resolve(style.Appearance(accent=accent))
        assert palette.primary == accent
        assert abs(style.brightness(palette.on_primary) - style.brightness(accent)) > 60
    assert style.readable_on(style.ACCENTS["Giallo"]) == "#1d1d1f"


def test_tint_and_advanced_overrides():
    neutral = style.resolve(style.Appearance(accent="#ff0000"))
    tinted = style.resolve(style.Appearance(accent="#ff0000", tint=1))
    assert neutral.canvas == style.NEUTRALS[False][0] and tinted.canvas != neutral.canvas
    custom = style.resolve(style.Appearance(canvas="#123456", ink="#fafafa", surface="#000000"))
    assert (custom.canvas, custom.ink, custom.surface) == ("#123456", "#fafafa", "#000000")


def test_saved_preferences_round_trip_and_validation():
    appearance = style.Appearance(dark=True, accent="#8944ab", tint=0.3, charts=("#111111", "#222222"), radius=4,
                                  surface="#202020")
    assert style.from_dict(style.to_dict(appearance)) == appearance
    broken = style.from_dict({"accent": "rosso", "tint": 7, "radius": "x", "charts": [], "ink": "#ZZZZZZ", "other": 1})
    assert broken == style.DEFAULT
    assert style.from_dict(None) == style.DEFAULT
    assert style.from_dict({"accent": "#ABCDEF"}).accent == "#abcdef"


def test_css_variables():
    variables = style.css_variables(style.Appearance(radius=6))
    assert variables["--radius"] == "6px" and variables["--radius-sm"] == "4px" and variables["--radius-lg"] == "12px"
    assert {"--ink", "--surface", "--canvas", "--line", "--on-primary"} <= set(variables)


def test_mix():
    assert style.mix("#000000", "#ffffff", 0.5) == "#808080"
    assert style.mix("#102030", "#102030", 0.7) == "#102030"


@pytest.mark.parametrize("name", style.PRESETS)
def test_chart_theme_and_view(name):
    palette = style.resolve(style.PRESETS[name])
    theme = style.chart_theme(palette)
    assert theme["color"] == list(palette.charts) and theme["textStyle"]["color"] == palette.ink
    assert style.chart_view(palette) == {"surface": palette.surface, "heat": palette.heat, "ink": palette.ink,
                                         "colors": list(palette.charts)}


def test_button_text_color_overrides_filled_and_plain_buttons():
    default = style.css_variables(style.DEFAULT)
    assert default["--button-plain"] == style.DEFAULT.accent
    custom = style.css_variables(style.Appearance(button_text="#ff0000"))
    assert custom["--on-primary"] == custom["--button-plain"] == "#ff0000"
    assert style.from_dict({"button_text": "#FF0000"}).button_text == "#ff0000"
    assert style.from_dict({"button_text": "rosso"}).button_text is None
