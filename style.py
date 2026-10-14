"""Aspetto grafico dell'app (stile Apple): colori, font, angoli arrotondati, ombre leggere."""
from nicegui import ui

CSS = """
:root {
    --ink: #1d1d1f;
    --muted: #6e6e73;
    --surface: #ffffff;
    --canvas: #f5f5f7;
    --line: rgba(0, 0, 0, 0.08);
    --shadow: 0 1px 2px rgba(0, 0, 0, 0.04), 0 6px 20px rgba(0, 0, 0, 0.06);
}

/* Base */
body, .q-field, .q-btn, .q-tab, .q-item {
    font-family: -apple-system, BlinkMacSystemFont, "SF Pro Text", "Inter", "Segoe UI", Roboto, "Helvetica Neue", Arial, sans-serif;
    -webkit-font-smoothing: antialiased;
}
body.body--light { background: var(--canvas); color: var(--ink); }
.text-gray-500, .text-gray-600 { color: var(--muted) !important; }

/* Header traslucido */
.q-header.app-header {
    background: rgba(255, 255, 255, 0.72) !important;
    backdrop-filter: saturate(180%) blur(20px);
    -webkit-backdrop-filter: saturate(180%) blur(20px);
    color: var(--ink) !important;
    border-bottom: 1px solid var(--line);
}
.q-header .q-field { color: var(--ink); }

/* Card e sezioni comprimibili */
.q-card {
    border-radius: 16px;
    border: 1px solid var(--line);
    box-shadow: var(--shadow);
}
.q-expansion-item {
    background: var(--surface);
    border: 1px solid var(--line) !important;
    border-radius: 12px !important;
    overflow: hidden;
}

/* Schede */
.q-tabs { border-bottom: 1px solid var(--line); }
.q-tab { text-transform: none; font-weight: 500; letter-spacing: 0; }
.q-tab__indicator { height: 3px; border-radius: 3px 3px 0 0; }
.q-tab-panels, .q-panel { background: transparent; }

/* Pulsanti */
.q-btn { text-transform: none; font-weight: 500; letter-spacing: 0; }
.q-btn:not(.q-btn--round) { border-radius: 10px; }
.q-btn:before { box-shadow: none !important; }

/* Toggle "segmented control" */
.q-btn-group.q-btn-toggle {
    background: rgba(120, 120, 128, 0.12);
    border-radius: 10px;
    padding: 2px;
    box-shadow: none;
}
.q-btn-toggle .q-btn { border-radius: 8px !important; color: var(--ink); }
.q-btn-toggle .q-btn.bg-primary {
    background: var(--surface) !important;
    color: var(--ink) !important;
    box-shadow: 0 1px 3px rgba(0, 0, 0, 0.18);
}

/* Campi */
.q-field--outlined .q-field__control { border-radius: 10px; background: var(--surface); }

/* Tabelle, editor, notifiche */
.nicegui-aggrid, .nicegui-codemirror, .q-table__container {
    border: 1px solid var(--line);
    border-radius: 12px;
    overflow: hidden;
    background: var(--surface);
}
.q-notification { border-radius: 12px; }
"""


def apply() -> None:
    """Da chiamare all'inizio di ogni pagina."""
    ui.colors(primary="#0071e3", positive="#34c759", negative="#ff3b30", warning="#ff9f0a")
    ui.add_css(CSS)
