"""Look of the app (Apple style): customizable preferences (light/dark, accent, tint, chart
colors, corners, advanced colors), derived colors, CSS and chart theme."""
import json
import re
from dataclasses import asdict, dataclass, fields, replace
from typing import NamedTuple

from nicegui import ui

# ---------------------------------------------------------------------------
# Colors
# ---------------------------------------------------------------------------

HEX = re.compile(r"#[0-9a-f]{6}")


def is_color(value) -> bool:
    return isinstance(value, str) and HEX.fullmatch(value.lower()) is not None


def mix(first: str, second: str, weight: float) -> str:
    """Color halfway between two hex colors (`weight` = weight of the second one)."""
    first_rgb, second_rgb = ([int(color[i:i + 2], 16) for i in (1, 3, 5)] for color in (first, second))
    return "#" + "".join(f"{round(a + (b - a) * weight):02x}" for a, b in zip(first_rgb, second_rgb))


def brightness(color: str) -> float:
    """Perceived brightness, from 0 (black) to 255 (white)."""
    red, green, blue = (int(color[i:i + 2], 16) for i in (1, 3, 5))
    return 0.299 * red + 0.587 * green + 0.114 * blue


def is_readable(palette: "Palette") -> bool:
    """Text and card different enough in brightness to be easy to read."""
    return abs(brightness(palette.ink) - brightness(palette.surface)) > 120


def readable_on(color: str) -> str:
    """Readable text on top of a color: almost black on light colors, white on the others."""
    return "#1d1d1f" if brightness(color) > 165 else "#ffffff"


# System accent colors (Apple)
ACCENTS = {
    "Blu": "#0071e3", "Viola": "#8944ab", "Rosa": "#e3348c", "Rosso": "#e0383e", "Arancione": "#f7821b",
    "Giallo": "#ffc600", "Verde": "#28a745", "Verde acqua": "#0aa5a5", "Grafite": "#8c8c8c",
}

CHART_PALETTES = {
    "Classica": ("#5470c6", "#91cc75", "#fac858", "#ee6666", "#73c0de", "#3ba272", "#fc8452", "#9a60b4", "#ea7ccc"),
    "Sistema": ("#0a84ff", "#30d158", "#ff9f0a", "#bf5af2", "#ff453a", "#64d2ff", "#ffd60a", "#ac8e68"),
    "Oceano": ("#0077b6", "#00b4d8", "#2a9d8f", "#90e0ef", "#023e8a", "#f4a261", "#e76f51", "#6a8caf"),
    "Foresta": ("#2d6a4f", "#74c69d", "#bc6c25", "#e9c46a", "#40916c", "#9c6644", "#8ab17d", "#5f7a61"),
    "Tramonto": ("#e8590c", "#f59f00", "#c92a2a", "#ae3ec9", "#f783ac", "#1098ad", "#74b816", "#5c7cfa"),
    "Neon": ("#8b9cff", "#5eead4", "#fbbf24", "#f472b6", "#60a5fa", "#a78bfa", "#fb7185", "#34d399"),
}

# Base neutrals (page background, surface, text) for light and dark
NEUTRALS = {False: ("#f5f5f7", "#ffffff", "#1d1d1f"), True: ("#1c1c1e", "#2c2c2e", "#f5f5f7")}
ADVANCED = {"canvas": "Sfondo della pagina", "surface": "Card e campi", "ink": "Testo"}  # replaceable colors
RADIUS_RANGE = (0, 20)

# ---------------------------------------------------------------------------
# Preferences
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Appearance:
    """User choices; all the other colors derive from them (see `resolve`)."""
    dark: bool = False
    accent: str = ACCENTS["Blu"]
    tint: float = 0.0  # 0 = neutral background, 1 = background tinted by the accent
    charts: tuple[str, ...] = CHART_PALETTES["Classica"]
    radius: int = 10   # corners of fields and buttons, in pixels
    canvas: str | None = None   # advanced colors: None = calculated
    surface: str | None = None
    ink: str | None = None
    button_text: str | None = None  # text of the buttons: None = calculated (readable on the accent / the accent itself)


PRESETS = {
    "Chiaro": Appearance(),
    "Scuro": Appearance(dark=True, accent="#0a84ff", charts=CHART_PALETTES["Sistema"]),
    "Oceano": Appearance(accent="#0077b6", tint=0.6, charts=CHART_PALETTES["Oceano"]),
    "Foresta": Appearance(accent="#2d6a4f", tint=0.5, charts=CHART_PALETTES["Foresta"]),
    "Tramonto": Appearance(accent="#d9480f", tint=0.6, charts=CHART_PALETTES["Tramonto"]),
    "Notte": Appearance(dark=True, accent="#6d7bff", tint=0.8, charts=CHART_PALETTES["Neon"]),
}
DEFAULT = PRESETS["Chiaro"]


def to_dict(appearance: Appearance) -> dict:
    """Form that can be saved as JSON."""
    return {**asdict(appearance), "charts": list(appearance.charts)}


def from_dict(data) -> Appearance:
    """Saved preferences → Appearance; missing or invalid values keep the defaults."""
    if not isinstance(data, dict):
        return DEFAULT
    checks = {
        "dark": lambda value: isinstance(value, bool),
        "accent": is_color,
        "tint": lambda value: isinstance(value, (int, float)) and 0 <= value <= 1,
        "charts": lambda value: isinstance(value, (list, tuple)) and value and all(map(is_color, value)),
        "radius": lambda value: isinstance(value, int) and RADIUS_RANGE[0] <= value <= RADIUS_RANGE[1],
        **{key: lambda value: value is None or is_color(value) for key in (*ADVANCED, "button_text")},
    }
    valid = {field.name: data[field.name] for field in fields(Appearance)
             if field.name in data and checks[field.name](data[field.name])}
    for key, value in valid.items():  # colors in lower case, series as a tuple
        if isinstance(value, str):
            valid[key] = value.lower()
        elif isinstance(value, (list, tuple)):
            valid[key] = tuple(color.lower() for color in value)
    return replace(DEFAULT, **valid)


def preset_name(appearance: Appearance) -> str | None:
    """Name of the preset equal to the preferences, if there is one."""
    return next((name for name, preset in PRESETS.items() if preset == appearance), None)


# ---------------------------------------------------------------------------
# Derived colors
# ---------------------------------------------------------------------------


class Palette(NamedTuple):
    dark: bool
    primary: str
    on_primary: str  # text on top of the primary color
    canvas: str    # page background
    surface: str   # cards, fields, tables
    ink: str       # main text
    muted: str     # secondary text
    line: str      # borders
    soft: str      # background of code, chips and secondary areas
    charts: tuple[str, ...]  # colors of the chart series

    @property
    def heat(self) -> list[str]:
        """Heatmap scale: from the background color to the primary color, up to the final accent."""
        return [mix(self.surface, self.primary, 0.12), mix(self.surface, self.primary, 0.5),
                self.primary, mix(self.primary, self.ink, 0.55)]

    @property
    def editor(self) -> str:
        """Theme of the code editor."""
        return "basicDark" if self.dark else "basicLight"


def resolve(appearance: Appearance) -> Palette:
    """All the interface colors, starting from the preferences."""
    canvas, surface, ink = NEUTRALS[appearance.dark]
    accent, tint = appearance.accent, appearance.tint
    canvas = appearance.canvas or mix(canvas, accent, 0.14 * tint)
    surface = appearance.surface or mix(surface, accent, (0.08 if appearance.dark else 0.03) * tint)
    ink = appearance.ink or mix(ink, accent, 0.25 * tint)
    return Palette(
        dark=appearance.dark, primary=accent, on_primary=readable_on(accent),
        canvas=canvas, surface=surface, ink=ink,
        muted=mix(ink, surface, 0.42), line=mix(surface, ink, 0.14 if appearance.dark else 0.09),
        soft=mix(surface, ink, 0.1 if appearance.dark else 0.05), charts=appearance.charts,
    )


# ---------------------------------------------------------------------------
# CSS
# ---------------------------------------------------------------------------

BASE_CSS = """
/* Base */
body, .q-field, .q-btn, .q-tab, .q-item {
    font-family: -apple-system, BlinkMacSystemFont, "SF Pro Text", "Inter", "Segoe UI", Roboto, "Helvetica Neue", Arial, sans-serif;
    -webkit-font-smoothing: antialiased;
}
body { background: var(--canvas) !important; color: var(--ink); }

/* Utility colors used in the app, tied to the palette */
.text-gray-400, .text-gray-500, .text-gray-600 { color: var(--muted) !important; }
.bg-gray-50, .bg-gray-100 { background: var(--soft) !important; }
.bg-amber-50 { background: color-mix(in srgb, var(--warn) 14%, var(--surface)) !important; }
.text-amber-700 { color: var(--warn) !important; }
.border { border-color: var(--line); }

/* Translucent header */
.q-header.app-header {
    background: color-mix(in srgb, var(--surface) 72%, transparent) !important;
    backdrop-filter: saturate(180%) blur(20px);
    -webkit-backdrop-filter: saturate(180%) blur(20px);
    color: var(--ink) !important;
    border-bottom: 1px solid var(--line);
}
.q-header .q-field { color: var(--ink); }

/* Dialogs: moved by dragging the first row (title), resized from the bottom-right corner.
   Full-screen (maximized) dialogs stay fixed. */
.q-dialog__inner--minimized > .q-card {
    resize: both;
    overflow: auto;
    max-width: 95vw !important;
    max-height: 95vh !important;
    min-width: 260px;
    min-height: 120px;
}
.q-dialog__inner--minimized > .q-card > :first-child { cursor: move; user-select: none; }
.q-dialog__inner--minimized > .q-card > :first-child :is(.q-btn, a) { cursor: pointer; }
.q-dialog__inner--minimized > .q-card > :first-child :is(input, textarea, .q-field) { cursor: text; user-select: text; }

/* Cards, expansion sections, menus */
.q-card, .q-expansion-item, .q-menu {
    background: var(--surface) !important;
    color: var(--ink) !important;
}
.q-card {
    border-radius: var(--radius-lg);
    border: 1px solid var(--line);
    box-shadow: var(--shadow);
}
.q-expansion-item {
    border: 1px solid var(--line) !important;
    border-radius: var(--radius-md) !important;
    overflow: hidden;
}

/* Sidebar */
.app-sidebar.q-drawer__content {
    padding: 0;
    background: color-mix(in srgb, var(--soft) 55%, var(--canvas)) !important;
    border-right: 1px solid var(--line);
}
.app-sidebar .q-tabs { border-bottom: none; padding: 12px 10px; }
.app-sidebar .q-tabs__content { width: 100%; }
.app-sidebar .q-tabs__content { align-items: stretch; gap: 2px; }
.app-sidebar .q-tab { width: 100%; }
.app-sidebar .sidebar-group {
    font-size: 11px; font-weight: 600; letter-spacing: 0.04em; text-transform: uppercase;
    color: var(--muted); padding: 14px 12px 4px;
}
.app-sidebar .sidebar-group:first-child { padding-top: 2px; }
.app-sidebar .q-tab {
    justify-content: flex-start; min-height: 34px; padding: 0 10px;
    border-radius: var(--radius-sm); color: var(--ink); transition: background 0.15s ease;
}
.app-sidebar .q-tab__content { justify-content: flex-start; gap: 4px; }
.app-sidebar .q-tab__icon { font-size: 20px; color: var(--q-primary); }
.app-sidebar .q-tab:hover { background: color-mix(in srgb, var(--ink) 6%, transparent); }
.app-sidebar .q-tab--active { background: color-mix(in srgb, var(--q-primary) 14%, transparent); font-weight: 600; }
.app-sidebar .q-focus-helper { display: none; }

/* Section content */
.page-title { font-size: 26px; font-weight: 700; letter-spacing: -0.02em; color: var(--ink); }
.app-panels > .q-panel > .q-tab-panel { padding: 0; }

/* Tabs */
.q-tabs { border-bottom: 1px solid var(--line); }
.q-tab { text-transform: none; font-weight: 500; letter-spacing: 0; }
.q-tab__indicator { height: 3px; border-radius: 3px 3px 0 0; }
.q-tab-panels, .q-panel { background: transparent; }

/* Buttons */
.q-btn { text-transform: none; font-weight: 500; letter-spacing: 0; }
.q-btn:not(.q-btn--round) { border-radius: var(--radius); }
/* Quasar's own !important colors sit in a CSS layer: unlayered rules would lose, the `overrides` layer wins */
@layer overrides {
    .bg-primary.text-white { color: var(--on-primary) !important; }
    .q-btn.text-primary { color: var(--button-plain) !important; }  /* outline and flat buttons */
}
.q-btn:before { box-shadow: none !important; }

/* Toggle "segmented control" */
.q-btn-group.q-btn-toggle {
    background: var(--soft);
    border-radius: var(--radius);
    padding: 2px;
    box-shadow: none;
}
.q-btn-toggle .q-btn { border-radius: var(--radius-sm) !important; color: var(--ink); }
.q-btn-toggle .q-btn.bg-primary {
    background: var(--surface) !important;
    color: var(--ink) !important;
    box-shadow: 0 1px 3px rgba(0, 0, 0, 0.18);
}

/* Fields */
.q-field--outlined .q-field__control { border-radius: var(--radius); background: var(--surface); }
.q-field__native, .q-field__input, .q-field__label, .q-field__marginal, .q-item { color: var(--ink); }

/* Tables, editors, notifications */
.nicegui-aggrid, .nicegui-codemirror, .q-table__container {
    border: 1px solid var(--line);
    border-radius: var(--radius-md);
    overflow: hidden;
    background: var(--surface);
}
.q-table, .q-table th, .q-table td { color: var(--ink); border-color: var(--line); }
.nicegui-aggrid, .nicegui-aggrid .ag-root-wrapper {
    --ag-background-color: var(--surface) !important;
    --ag-foreground-color: var(--ink) !important;
    --ag-header-background-color: var(--soft) !important;
    --ag-header-text-color: var(--ink) !important;
    --ag-border-color: var(--line) !important;
    --ag-odd-row-background-color: var(--surface) !important;
}
.nicegui-aggrid .dup-column { box-shadow: inset 0 -3px 0 var(--warn); }
.nicegui-aggrid .total-row { font-weight: 600; background: var(--soft) !important; }
.nicegui-codemirror .cm-editor, .nicegui-codemirror .cm-gutters {
    background: var(--surface) !important;
    color: var(--ink);
}
.q-notification { border-radius: var(--radius-md); }

/* File upload: no size and percentage before a file is chosen */
.upload-box .q-uploader__subtitle { display: none; }

/* Appearance dialog */
.swatch { width: 28px; height: 28px; min-height: 28px; border-radius: 50% !important; padding: 0;
          box-shadow: inset 0 0 0 1px rgba(0, 0, 0, 0.12); }
.swatch .q-icon { font-size: 16px; }
.swatch-dot { width: 14px; height: 14px; border-radius: 50%; }
.preset-card { cursor: pointer; border-radius: var(--radius-md); border: 1px solid var(--line); padding: 8px; }
.preset-card.selected { box-shadow: 0 0 0 2px var(--q-primary); }

/* Dashboards of the console: 12-column grid; on narrow screens every visual takes the whole width */
.dash-grid { display: grid; grid-template-columns: repeat(12, minmax(0, 1fr)); gap: 12px; }
.dash-card { min-width: 0; gap: 6px; }
.dash-kpi { font-size: 2rem; font-weight: 600; line-height: 1.2; color: var(--ink); }
@media (max-width: 900px) { .dash-grid > * { grid-column: 1 / -1 !important; } }
"""


def css_variables(appearance: Appearance) -> dict[str, str]:
    """CSS variables of the look (colors, shadow, corners)."""
    palette = resolve(appearance)
    radius = appearance.radius
    shadow = ("0 1px 2px rgba(0, 0, 0, 0.3), 0 6px 20px rgba(0, 0, 0, 0.35)" if palette.dark
              else "0 1px 2px rgba(0, 0, 0, 0.04), 0 6px 20px rgba(0, 0, 0, 0.06)")
    return {
        "--ink": palette.ink, "--muted": palette.muted, "--surface": palette.surface, "--canvas": palette.canvas,
        "--line": palette.line, "--soft": palette.soft, "--on-primary": appearance.button_text or palette.on_primary,
        "--button-plain": appearance.button_text or palette.primary,
        "--warn": "#fbbf24" if palette.dark else "#b45309", "--shadow": shadow,
        "--radius": f"{radius}px", "--radius-sm": f"{max(radius - 2, 0)}px",
        "--radius-md": f"{radius + 2}px", "--radius-lg": f"{radius + 6}px",
    }


# ---------------------------------------------------------------------------
# Active look
# ---------------------------------------------------------------------------


DIALOG_SCRIPT = """
document.addEventListener("pointerdown", event => {
    const card = event.target.closest(".q-dialog__inner--minimized > .q-card");
    const header = card && card.firstElementChild;
    if (event.button !== 0 || !header || !header.contains(event.target)) return;
    if (event.target.closest("input, textarea, button, a, .q-btn, .q-field, .q-toggle, .q-checkbox")) return;
    const [baseX, baseY] = (card.dataset.offset || "0 0").split(" ").map(Number);
    const startX = event.clientX, startY = event.clientY, startTop = card.getBoundingClientRect().top;
    const move = e => {
        const x = baseX + e.clientX - startX;
        const y = baseY + Math.max(e.clientY - startY, -startTop);  // the title always stays visible
        card.style.translate = `${x}px ${y}px`;
        card.dataset.offset = `${x} ${y}`;
    };
    const stop = () => {
        document.removeEventListener("pointermove", move);
        document.removeEventListener("pointerup", stop);
    };
    document.addEventListener("pointermove", move);
    document.addEventListener("pointerup", stop);
    event.preventDefault();
});
"""


def apply(appearance: Appearance) -> ui.dark_mode:
    """To be called at the start of every page; returns the dark/light switch to pass to `switch`."""
    palette = resolve(appearance)
    variables = "; ".join(f"{name}: {value}" for name, value in css_variables(appearance).items())
    ui.add_css(f":root {{ {variables}; }}\n" + BASE_CSS)
    ui.add_body_html(f"<script>{DIALOG_SCRIPT}</script>")  # movable windows
    ui.colors(primary=palette.primary, positive="#34c759", negative="#ff3b30", warning="#ff9f0a")
    return ui.dark_mode(palette.dark)


def switch(appearance: Appearance, dark_mode: ui.dark_mode) -> None:
    """Applies the look to the open page, without reloading it."""
    palette = resolve(appearance)
    ui.run_javascript(f"""
        const variables = {json.dumps(css_variables(appearance))};
        for (const [name, value] of Object.entries(variables)) document.documentElement.style.setProperty(name, value);
        document.body.style.setProperty("--q-primary", {json.dumps(palette.primary)});
    """)
    dark_mode.value = palette.dark
    for element in ui.context.client.elements.values():
        if isinstance(element, ui.codemirror):
            element.theme = palette.editor


# ---------------------------------------------------------------------------
# Charts (ECharts)
# ---------------------------------------------------------------------------


def chart_theme(palette: Palette) -> dict:
    """ECharts theme: series colors, texts, axes and tooltips from the palette."""
    axis = {"axisLine": {"lineStyle": {"color": palette.line}}, "axisTick": {"lineStyle": {"color": palette.line}},
            "axisLabel": {"color": palette.muted}, "splitLine": {"lineStyle": {"color": palette.line}}}
    return {
        "color": list(palette.charts),
        "backgroundColor": "transparent",
        "textStyle": {"color": palette.ink},
        "title": {"textStyle": {"color": palette.ink}},
        "legend": {"textStyle": {"color": palette.ink}},
        "categoryAxis": axis, "valueAxis": axis, "timeAxis": axis, "logAxis": axis,
        "tooltip": {"backgroundColor": palette.surface, "borderColor": palette.line, "textStyle": {"color": palette.ink}},
        "toolbox": {"iconStyle": {"borderColor": palette.muted}},
    }


def chart_view(palette: Palette) -> dict:
    """Display choices (see chart_options.View) that depend on the palette."""
    return {"surface": palette.surface, "heat": palette.heat, "ink": palette.ink, "colors": list(palette.charts)}
