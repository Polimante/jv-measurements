"""
Reusable Matplotlib panel factory for continuous-experiment plots.

Three layouts are provided, all returning a dict of the figure, canvas, and
named axes so the caller can drive incremental updates:

  * ``build_mpp_continuous_panel`` — PCE(t), J(t), Vmpp(t), latest J-V.
    Identical GridSpec(3,2) / height_ratios=[4,3,3] layout to the existing
    Pure-MPP panel, so refactoring that panel to this factory is visually
    transparent.
  * ``build_oc_continuous_panel`` — Voc(t) on top, latest J-V below (ISOS-L-1
    open-circuit stress).
  * ``build_scan_only_panel`` — sparse PCE(t) scatter on top, latest J-V and
    I_dark(t) below (ISOS-V-1, where PCE is sampled only at scan points).

When ``parent_frame`` is ``None`` the Tk canvas is skipped (``canvas`` is
``None``) so the layouts can be built and exercised headlessly in tests.

The ``update_*`` functions are defensive: they accept empty arrays without
raising, redrawing an empty grid with axis labels intact.
"""

from __future__ import annotations

import matplotlib.pyplot as plt
from matplotlib.gridspec import GridSpec

# Shared colours (match the existing Pure-MPP panel).
_C_PCE   = "#1565C0"
_C_J     = "#E65100"
_C_V     = "#4A148C"
_C_FWD   = "#1565C0"
_C_REV   = "#B71C1C"
_C_VMPP  = "#E65100"
_C_IDARK = "#37474F"


def _make_canvas(fig, parent_frame):
    """Embed *fig* in a Tk canvas under *parent_frame*; return the canvas.

    Returns ``None`` when *parent_frame* is ``None`` (headless / tests).
    """
    if parent_frame is None:
        return None
    from matplotlib.backends.backend_tkagg import FigureCanvasTkAgg
    canvas = FigureCanvasTkAgg(fig, master=parent_frame)
    canvas.get_tk_widget().pack(fill="both", expand=True)
    return canvas


def _blank(ax, title, xl, yl):
    ax.set_title(title, fontsize=10, fontweight="bold")
    ax.set_xlabel(xl, fontsize=9)
    ax.set_ylabel(yl, fontsize=9)
    ax.grid(True)
    ax.text(0.5, 0.5, "No data yet", transform=ax.transAxes,
            ha="center", va="center", color="gray", fontsize=10)


# ── Layout 1: MPP continuous (PCE / J / Vmpp / J-V) ─────────────────────────────

def build_mpp_continuous_panel(parent_frame) -> dict:
    """Build the 4-axis MPP continuous panel. See module docstring."""
    fig = plt.figure(figsize=(8, 9), constrained_layout=True)
    gs = GridSpec(3, 2, figure=fig, height_ratios=[4, 3, 3])
    ax_pce = fig.add_subplot(gs[0, :])
    ax_j   = fig.add_subplot(gs[1, :])
    ax_v   = fig.add_subplot(gs[2, 0])
    ax_jv  = fig.add_subplot(gs[2, 1])

    _blank(ax_pce, "PCE(t) — Pure MPP Tracking (P&O)", "Time (min)", "PCE (%)")
    _blank(ax_j,   "Current Density at Vmpp", "Time (min)", "J (mA/cm²)")
    _blank(ax_v,   "Tracked Vmpp", "Time (min)", "Vmpp (V)")
    _blank(ax_jv,  "Initial J-V Scan", "Voltage (V)", "J (mA/cm²)")

    canvas = _make_canvas(fig, parent_frame)
    return {"fig": fig, "canvas": canvas, "ax_pce": ax_pce,
            "ax_j": ax_j, "ax_v": ax_v, "ax_jv": ax_jv}


def update_mpp_plot(axes, time_min, v, j, pce, jv_curves=None):
    """Redraw the MPP panel from full traces.

    ``time_min`` / ``v`` / ``j`` / ``pce`` are parallel sequences. ``jv_curves``
    is an optional ``{"v": [...], "j": [...], "vmpp": float}`` for the J-V axis.
    """
    ax_pce, ax_j, ax_v = axes["ax_pce"], axes["ax_j"], axes["ax_v"]
    ax_pce.clear()
    ax_pce.plot(time_min, pce, color=_C_PCE, linewidth=1.2)
    ax_pce.set_title("PCE(t) — Pure MPP Tracking (P&O)", fontweight="bold")
    ax_pce.set_xlabel("Time (min)"); ax_pce.set_ylabel("PCE (%)")
    ax_pce.grid(True)

    ax_j.clear()
    ax_j.plot(time_min, j, color=_C_J, linewidth=1.2)
    ax_j.set_title("Current Density at Vmpp")
    ax_j.set_xlabel("Time (min)"); ax_j.set_ylabel("J (mA/cm²)")
    ax_j.grid(True)

    ax_v.clear()
    ax_v.plot(time_min, v, color=_C_V, linewidth=1.0)
    ax_v.set_title("Tracked Vmpp")
    ax_v.set_xlabel("Time (min)"); ax_v.set_ylabel("Vmpp (V)")
    ax_v.grid(True)

    if jv_curves:
        _draw_jv(axes["ax_jv"], jv_curves)
    _redraw(axes)


# ── Layout 2: OC continuous (Voc / J-V) ─────────────────────────────────────────

def build_oc_continuous_panel(parent_frame) -> dict:
    """Build the 2-axis open-circuit continuous panel."""
    fig = plt.figure(figsize=(8, 8), constrained_layout=True)
    gs = GridSpec(2, 1, figure=fig, height_ratios=[3, 3])
    ax_voc = fig.add_subplot(gs[0, 0])
    ax_jv  = fig.add_subplot(gs[1, 0])

    _blank(ax_voc, "Voc(t) — Open-Circuit Stress", "Time (h)", "Voc (V)")
    _blank(ax_jv,  "Latest J-V Scan", "Voltage (V)", "J (mA/cm²)")

    canvas = _make_canvas(fig, parent_frame)
    return {"fig": fig, "canvas": canvas, "ax_voc": ax_voc, "ax_jv": ax_jv}


def update_oc_plot(axes, time_h, voc, jv_curves=None):
    """Redraw the OC panel from full Voc(t) traces."""
    ax_voc = axes["ax_voc"]
    ax_voc.clear()
    ax_voc.plot(time_h, voc, color=_C_V, linewidth=1.0)
    ax_voc.set_title("Voc(t) — Open-Circuit Stress", fontweight="bold")
    ax_voc.set_xlabel("Time (h)"); ax_voc.set_ylabel("Voc (V)")
    ax_voc.grid(True)

    if jv_curves:
        _draw_jv(axes["ax_jv"], jv_curves)
    _redraw(axes)


# ── Layout 3: scan-only (sparse PCE / J-V / I_dark) ─────────────────────────────

def build_scan_only_panel(parent_frame) -> dict:
    """Build the 3-axis scan-only panel for sparse-PCE protocols (V-1)."""
    fig = plt.figure(figsize=(8, 8), constrained_layout=True)
    gs = GridSpec(2, 2, figure=fig, height_ratios=[3, 3])
    ax_pce   = fig.add_subplot(gs[0, :])
    ax_jv    = fig.add_subplot(gs[1, 0])
    ax_idark = fig.add_subplot(gs[1, 1])

    _blank(ax_pce,   "PCE vs Time (scan points)", "Time (h)", "PCE (%)")
    _blank(ax_jv,    "Latest J-V Scan", "Voltage (V)", "J (mA/cm²)")
    _blank(ax_idark, "Dark Current During Stress", "Time (h)", "I_dark (mA/cm²)")

    canvas = _make_canvas(fig, parent_frame)
    return {"fig": fig, "canvas": canvas, "ax_pce": ax_pce,
            "ax_jv": ax_jv, "ax_idark": ax_idark}


def update_scan_only_plot(axes, log_rows, latest_jv=None, i_dark_trace=None):
    """Redraw the scan-only panel.

    ``log_rows`` — list of dicts with ``Elapsed_h``, ``PCE_rev (%)``,
    ``PCE_fwd (%)`` keys (as produced by ``read_isos_log``).
    ``latest_jv`` — optional ``{"v","j","vmpp"}`` for the J-V axis.
    ``i_dark_trace`` — optional list of ``(elapsed_s, j)`` tuples.
    """
    ax_pce = axes["ax_pce"]
    ax_pce.clear()
    rows = log_rows or []

    def _col(key):
        out = []
        for r in rows:
            val = r.get(key)
            if val is not None:
                try:
                    out.append(float(val))
                except (TypeError, ValueError):
                    out.append(None)
            else:
                out.append(None)
        return out

    t   = _col("Elapsed_h")
    rev = _col("PCE_rev (%)")
    fwd = _col("PCE_fwd (%)")
    # Filter out paired Nones so matplotlib does not choke.
    tr = [(a, b) for a, b in zip(t, rev) if a is not None and b is not None]
    tf = [(a, b) for a, b in zip(t, fwd) if a is not None and b is not None]
    if tr:
        xs, ys = zip(*tr)
        ax_pce.plot(xs, ys, color=_C_REV, marker="o", markersize=4,
                    linewidth=1.0, label="PCE reverse")
    if tf:
        xs, ys = zip(*tf)
        ax_pce.plot(xs, ys, color=_C_FWD, marker="s", markersize=4,
                    linewidth=1.0, label="PCE forward")
    ax_pce.set_title("PCE vs Time (scan points)", fontweight="bold")
    ax_pce.set_xlabel("Time (h)"); ax_pce.set_ylabel("PCE (%)")
    ax_pce.grid(True)
    if tr or tf:
        ax_pce.legend(fontsize=7)

    if latest_jv:
        _draw_jv(axes["ax_jv"], latest_jv)

    ax_id = axes["ax_idark"]
    ax_id.clear()
    if i_dark_trace:
        ts = [p[0] / 3600.0 for p in i_dark_trace]
        js = [p[1] for p in i_dark_trace]
        ax_id.plot(ts, js, color=_C_IDARK, linewidth=1.0)
    ax_id.set_title("Dark Current During Stress")
    ax_id.set_xlabel("Time (h)"); ax_id.set_ylabel("I_dark (mA/cm²)")
    ax_id.grid(True)

    _redraw(axes)


# ── Internal drawing helpers ────────────────────────────────────────────────────

def _draw_jv(ax, jv):
    ax.clear()
    v = jv.get("v"); j = jv.get("j")
    if v is not None and j is not None and len(v):
        ax.plot(v, j, color="#1B5E20", linewidth=1.5,
                marker="o", markersize=2, markerfacecolor="#1B5E20")
    vmpp = jv.get("vmpp")
    if vmpp is not None:
        ax.axvline(x=vmpp, color=_C_VMPP, linewidth=1.0, linestyle="--",
                   label=f"Vmpp = {vmpp:.3f} V")
        ax.legend(fontsize=7)
    ax.set_title("Latest J-V Scan")
    ax.set_xlabel("Voltage (V)"); ax.set_ylabel("J (mA/cm²)")
    ax.grid(True)


def _redraw(axes):
    canvas = axes.get("canvas")
    if canvas is not None:
        canvas.draw_idle()
