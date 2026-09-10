"""Centralised GUI theme: the single source of truth for colours, fonts and
spacing used across the Tkinter widgets and the Matplotlib panels.

Before this module the 10-colour plot palette was duplicated in three files and
button/status/semantic colours were hard-coded inline throughout. Import from
here instead of re-declaring values, so a visual tweak is a one-line change.

Nothing here touches Tk at import time — these are plain constants, safe to
import from any module (GUI or headless plot factory).
"""

from __future__ import annotations

# ── Plot trace palette ────────────────────────────────────────────────────────
# Per-measurement series colours, cycled by index. Canonical home for what used
# to be ``main_window.PLOT_COLORS`` and ``data_viewer._COLORS``.
PLOT_COLORS: list[str] = [
    "#1565C0",  # deep blue
    "#B71C1C",  # deep red
    "#1B5E20",  # deep green
    "#E65100",  # deep orange
    "#4A148C",  # deep purple
    "#006064",  # deep cyan
    "#3E2723",  # deep brown
    "#37474F",  # blue-grey
    "#880E4F",  # deep pink
    "#004D40",  # deep teal
]

# ── Semantic plot-quantity colours ────────────────────────────────────────────
# Used by the continuous-experiment plot factories (MPP / Pure-MPP / ISOS).
COLOR_PCE   = "#1565C0"
COLOR_J     = "#E65100"
COLOR_V     = "#4A148C"
COLOR_FWD   = "#1565C0"
COLOR_REV   = "#B71C1C"
COLOR_VMPP  = "#E65100"
COLOR_IDARK = "#37474F"

# ── Semantic UI colours ───────────────────────────────────────────────────────
COLOR_START      = "#388E3C"   # green start/run buttons
COLOR_STOP       = "#C62828"   # red stop/abort buttons
COLOR_STATUS_BG  = "#37474F"   # dark status-bar background
COLOR_STATUS_FG  = "#CFD8DC"   # status-bar text
COLOR_MUTED      = "#666666"   # secondary / hint text
COLOR_PANEL_BG   = "#ececec"   # neutral panel background

# ── Fonts ─────────────────────────────────────────────────────────────────────
FONT_BODY    = ("Arial", 9)
FONT_HEADING = ("Arial", 10, "bold")
FONT_SMALL   = ("Arial", 8)
FONT_MONO    = ("Consolas", 9)

# ── Spacing scale (px) ────────────────────────────────────────────────────────
PAD_S = 4
PAD_M = 6
PAD_L = 10
