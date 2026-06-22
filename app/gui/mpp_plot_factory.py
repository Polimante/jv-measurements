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


# ── Layout 4: V-1 diagnostic panel (dark + illuminated J-V degradation) ─────────

def build_v1_diagnostic_panel(parent_frame) -> dict:
    """Build the rich ISOS-V-1 diagnostic panel (GridSpec 4×2).

      row 0, span:  PCE(t) markers (from illuminated scans)
      row 1, col 0: R_s_dark(t) and R_s_illum(t) overlaid
      row 1, col 1: R_sh_dark(t) and R_sh_illum(t) — log scale
      row 2, col 0: n_dark(t)
      row 2, col 1: J_s_dark(t) — log scale
      row 3, col 0: HI(t) and HI_dark(t) overlaid
      row 3, col 1: latest illuminated J-V (top) / latest dark J-V (bottom)
    """
    # A flat 8×2 GridSpec (no nested subgridspecs — those defeat
    # constrained_layout and cause overlapping titles/axes). Each diagnostic row
    # spans two grid rows; the bottom-right J-V cell is split into one grid row
    # each (illuminated on top, dark below) while HI spans both on the left.
    fig = plt.figure(figsize=(9, 12), constrained_layout=True)
    gs = GridSpec(8, 2, figure=fig)
    ax_pce = fig.add_subplot(gs[0:2, :])
    ax_rs  = fig.add_subplot(gs[2:4, 0])
    ax_rsh = fig.add_subplot(gs[2:4, 1])
    ax_n   = fig.add_subplot(gs[4:6, 0])
    ax_js  = fig.add_subplot(gs[4:6, 1])
    ax_hi  = fig.add_subplot(gs[6:8, 0])
    ax_jv      = fig.add_subplot(gs[6, 1])
    ax_jv_dark = fig.add_subplot(gs[7, 1])

    _blank(ax_pce, "PCE vs Time (illuminated scans)", "Time (h)", "PCE (%)")
    _blank(ax_rs,  "Series Resistance", "Time (h)", "R_s (Ω·cm²)")
    _blank(ax_rsh, "Shunt Resistance", "Time (h)", "R_sh (Ω·cm²)")
    _blank(ax_n,   "Dark Ideality Factor", "Time (h)", "n_dark")
    _blank(ax_js,  "Dark Saturation Current", "Time (h)", "J_s (mA/cm²)")
    _blank(ax_hi,  "Hysteresis Index", "Time (h)", "HI (%)")
    _blank(ax_jv,      "Latest Illuminated J-V", "Voltage (V)", "J (mA/cm²)")
    _blank(ax_jv_dark, "Latest Dark J-V", "Voltage (V)", "J (mA/cm²)")

    canvas = _make_canvas(fig, parent_frame)
    return {"fig": fig, "canvas": canvas,
            "ax_pce": ax_pce, "ax_rs": ax_rs, "ax_rsh": ax_rsh,
            "ax_n": ax_n, "ax_js": ax_js, "ax_hi": ax_hi,
            "ax_jv": ax_jv, "ax_jv_dark": ax_jv_dark}


def _v1_col(rows, key):
    """Extract a float column from log rows, preserving None for missing cells."""
    out = []
    for r in rows:
        val = r.get(key)
        try:
            out.append(float(val) if val not in (None, "") else None)
        except (TypeError, ValueError):
            out.append(None)
    return out


def _v1_pairs(t, y):
    """Zip two parallel columns, dropping entries where either is None."""
    return [(a, b) for a, b in zip(t, y) if a is not None and b is not None]


def _v1_scatter(ax, title, xl, yl, series, logy=False):
    """Redraw a marker time-series axis from a list of (pairs, color, label)."""
    ax.clear()
    any_data = False
    for pairs, color, label in series:
        if pairs:
            xs, ys = zip(*pairs)
            ax.plot(xs, ys, color=color, marker="o", markersize=4,
                    linewidth=1.0, label=label)
            any_data = True
    ax.set_title(title, fontsize=10, fontweight="bold")
    ax.set_xlabel(xl, fontsize=9); ax.set_ylabel(yl, fontsize=9)
    ax.grid(True, which="both")
    if logy and any_data:
        try:
            ax.set_yscale("log")
        except Exception:
            pass
    if any_data and len([s for s in series if s[2]]) > 1:
        ax.legend(fontsize=7)
    elif not any_data:
        ax.text(0.5, 0.5, "No data yet", transform=ax.transAxes,
                ha="center", va="center", color="gray", fontsize=9)


def update_v1_plot(axes, log_rows, latest_illum_jv=None, latest_dark_jv=None):
    """Redraw the V-1 diagnostic panel from cumulative log rows + latest curves.

    ``log_rows`` — list of dicts as produced by ``read_isos_log``.
    ``latest_illum_jv`` / ``latest_dark_jv`` — optional ``{"v","j","vmpp"}``.
    """
    rows = log_rows or []
    t = _v1_col(rows, "Elapsed_h")

    _v1_scatter(
        axes["ax_pce"], "PCE vs Time (illuminated scans)", "Time (h)", "PCE (%)",
        [(_v1_pairs(t, _v1_col(rows, "PCE_rev (%)")), _C_REV, "PCE reverse"),
         (_v1_pairs(t, _v1_col(rows, "PCE_fwd (%)")), _C_FWD, "PCE forward")])

    _v1_scatter(
        axes["ax_rs"], "Series Resistance", "Time (h)", "R_s (Ω·cm²)",
        [(_v1_pairs(t, _v1_col(rows, "R_s_dark_rev")), _C_IDARK, "R_s dark"),
         (_v1_pairs(t, _v1_col(rows, "Rs_rev (Ω·cm²)")), _C_J, "R_s illum")])

    _v1_scatter(
        axes["ax_rsh"], "Shunt Resistance", "Time (h)", "R_sh (Ω·cm²)",
        [(_v1_pairs(t, _v1_col(rows, "R_sh_dark_rev")), _C_IDARK, "R_sh dark"),
         (_v1_pairs(t, _v1_col(rows, "Rsh_rev (Ω·cm²)")), _C_J, "R_sh illum")],
        logy=True)

    _v1_scatter(
        axes["ax_n"], "Dark Ideality Factor", "Time (h)", "n_dark",
        [(_v1_pairs(t, _v1_col(rows, "n_dark_rev")), _C_V, "")])

    _v1_scatter(
        axes["ax_js"], "Dark Saturation Current", "Time (h)", "J_s (mA/cm²)",
        [(_v1_pairs(t, _v1_col(rows, "J_s_dark_rev")), _C_PCE, "")],
        logy=True)

    _v1_scatter(
        axes["ax_hi"], "Hysteresis Index", "Time (h)", "HI (%)",
        [(_v1_pairs(t, _v1_col(rows, "HI (%)")), _C_REV, "HI illum"),
         (_v1_pairs(t, _v1_col(rows, "HI_dark")), _C_IDARK, "HI dark")])

    _draw_jv(axes["ax_jv"], latest_illum_jv or {})
    axes["ax_jv"].set_title("Latest Illuminated J-V")
    _draw_jv(axes["ax_jv_dark"], latest_dark_jv or {})
    axes["ax_jv_dark"].set_title("Latest Dark J-V")

    _redraw(axes)


# ── Stability-metric overlays ───────────────────────────────────────────────────

# Overlay colours (kept distinct from the trace colours above).
_C_T80   = "#C62828"   # red
_C_T95   = "#4FC3F7"   # light blue
_C_TS80  = "#FB8C00"   # orange
_C_TS    = "#9E9E9E"   # gray (burn-in end)


def overlay_stability_metrics(ax_pce, metrics, total_duration_h):
    """Draw stability-metric reference lines + annotations on a PCE axis.

    The axis is assumed to be showing the *normalized* PCE trend (ratio to t₀),
    so the 0.80 / 0.95 horizontal thresholds line up with the data. Works for the
    L-1 MPP, L-1 OC, and V-1 sparse-marker panels.

    Draws:
      * horizontal dashed lines at 0.80 (red) and 0.95 (light blue);
      * a horizontal dashed line at 0.80·PCE_S_norm (orange) — the Ts80 reference;
      * a vertical gray dashed line at T_S (burn-in end);
      * vertical lines at T80 (red) and T95 (light blue) when reached;
      * a vertical orange line at T_S + Ts80 when reached;
      * short text annotations near each marker.

    ``metrics`` may be ``None`` (no-op). ``total_duration_h`` bounds annotation
    placement.
    """
    if metrics is None or ax_pce is None:
        return

    ax_pce.axhline(0.80, color=_C_T80, linestyle="--", linewidth=0.9, alpha=0.6)
    ax_pce.axhline(0.95, color=_C_T95, linestyle="--", linewidth=0.9, alpha=0.6)

    pce_0 = getattr(metrics, "pce_0", None)
    pce_s = getattr(metrics, "pce_s", None)
    if pce_s is not None and pce_0:
        ts_ref = 0.80 * (pce_s / pce_0)
        ax_pce.axhline(ts_ref, color=_C_TS80, linestyle="--",
                       linewidth=0.9, alpha=0.6)

    def _vline(x, color, label):
        if x is None:
            return
        ax_pce.axvline(x, color=color, linestyle="--", linewidth=1.1)
        try:
            y_top = ax_pce.get_ylim()[1]
        except Exception:
            y_top = 1.0
        ax_pce.annotate(label, xy=(x, y_top), xytext=(2, -2),
                        textcoords="offset points", rotation=90,
                        va="top", ha="left", fontsize=7, color=color)

    t_s = getattr(metrics, "t_s_h", None)
    if t_s is not None:
        _vline(t_s, _C_TS, f"T_S = {t_s:.1f} h")

    if getattr(metrics, "t80_reached", False):
        t80 = getattr(metrics, "t80_h", None)
        _vline(t80, _C_T80, f"T80 = {t80:.1f} h" if t80 is not None else "T80")

    if getattr(metrics, "t95_reached", False):
        t95 = getattr(metrics, "t95_h", None)
        _vline(t95, _C_T95, f"T95 = {t95:.1f} h" if t95 is not None else "T95")

    if getattr(metrics, "ts80_reached", False):
        ts80 = getattr(metrics, "ts80_h", None)
        if ts80 is not None and t_s is not None:
            _vline(t_s + ts80, _C_TS80, f"Ts80 = {ts80:.1f} h")


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
