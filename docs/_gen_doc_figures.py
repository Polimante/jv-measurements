"""Generate documentation figures (logo + simulated curves) for docs/help.html.

Run from the project root:  python docs/_gen_doc_figures.py

Produces PNGs in docs/figures/ using the project's own DummyCell physics engine
and analysis functions so every plotted curve is physically consistent with the
software. This script is a build helper for the documentation only — it is not
imported by the application.
"""
from __future__ import annotations

import os
import sys

import numpy as np
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

# Make the project importable when run from anywhere.
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from app.simulation.dummy_cell import DummyCell  # noqa: E402
from app.measurement.analysis import (  # noqa: E402
    calculate_jv_parameters,
    calculate_resistances,
    calculate_hysteresis_index,
    fit_single_diode,
    fit_dark_ideality,
    fit_light_ideality,
)

OUT = os.path.join(ROOT, "docs", "figures")
os.makedirs(OUT, exist_ok=True)

# House style — keep consistent with the slate/blue documentation palette.
plt.rcParams.update({
    "font.family": "DejaVu Sans",
    "font.size": 11,
    "axes.edgecolor": "#34495e",
    "axes.labelcolor": "#2c3e50",
    "axes.titlecolor": "#2c3e50",
    "axes.titleweight": "bold",
    "axes.grid": True,
    "grid.color": "#dfe4ea",
    "grid.linewidth": 0.7,
    "figure.dpi": 130,
})

C_FWD = "#e67e22"   # forward scan (orange)
C_REV = "#1565C0"   # reverse scan (blue)
C_PWR = "#27ae60"   # power / SPO (green)
C_FIT = "#c0392b"   # fit overlay (red)


def _save(fig, name):
    path = os.path.join(OUT, name)
    fig.savefig(path, bbox_inches="tight", facecolor="white")
    plt.close(fig)
    print("wrote", os.path.relpath(path, ROOT))


# ── 1. Logo from the application icon ──────────────────────────────────────────
def make_logo():
    from PIL import Image
    src = os.path.join(ROOT, "app", "assets", "icon.ico")
    img = Image.open(src)
    # Pick the largest embedded resolution.
    if hasattr(img, "ico"):
        try:
            img = img.ico.getimage(max(img.ico.sizes()))
        except Exception:
            pass
    img = img.convert("RGBA")
    img.save(os.path.join(OUT, "logo.png"))
    print("wrote", os.path.relpath(os.path.join(OUT, 'logo.png'), ROOT))


# ── 2. Illuminated J-V curve with hysteresis + marked parameters ───────────────
def make_jv_curve():
    cell = DummyCell(mode="with_hysteresis", j_ph=22.5, j0=2e-12, n=1.45,
                     rs=4.0, rsh=6000.0, hi_fraction=0.10)
    v = np.round(np.arange(-0.1, 1.15 + 1e-9, 0.02), 4)
    j_rev = cell.scan(v, "reverse")
    j_fwd = cell.scan(v, "forward")
    illum = 100.0

    Voc_r, Jsc_r, FF_r, PCE_r = calculate_jv_parameters(v, j_rev, illum)
    Voc_f, Jsc_f, FF_f, PCE_f = calculate_jv_parameters(v, j_fwd, illum)
    HI = calculate_hysteresis_index(PCE_f, PCE_r)

    p = v * j_rev
    imp = int(np.argmax(p))
    Vm, Jm = v[imp], j_rev[imp]

    fig, ax = plt.subplots(figsize=(6.6, 4.5))
    ax.axhline(0, color="#7f8c8d", lw=0.8)
    ax.axvline(0, color="#7f8c8d", lw=0.8)
    ax.plot(v, j_rev, "-o", ms=3, color=C_REV, label=f"Reverse scan  (PCE {PCE_r:.1f} %)")
    ax.plot(v, j_fwd, "-s", ms=3, color=C_FWD, label=f"Forward scan  (PCE {PCE_f:.1f} %)")

    # Maximum power rectangle (reverse).
    ax.add_patch(plt.Rectangle((0, 0), Vm, Jm, fill=True, color=C_PWR,
                               alpha=0.12, zorder=0))
    ax.plot([Vm], [Jm], "*", ms=14, color=C_PWR, zorder=5,
            label=f"MPP  ({Vm:.2f} V, {Jm:.1f} mA/cm²)")
    ax.annotate("$V_{oc}$", (Voc_r, 0), textcoords="offset points", xytext=(4, 8),
                color=C_REV, fontweight="bold")
    ax.annotate("$J_{sc}$", (0, Jsc_r), textcoords="offset points", xytext=(6, -2),
                color=C_REV, fontweight="bold")

    ax.set_xlim(-0.1, 1.2)
    ax.set_ylim(-3, 26)
    ax.set_xlabel("Voltage (V)")
    ax.set_ylabel("Current density J (mA/cm²)")
    ax.set_title("Illuminated J–V scan with hysteresis")
    ax.legend(loc="lower left", fontsize=9, framealpha=0.95)
    txt = (f"$V_{{oc}}$ = {Voc_r:.3f} V\n$J_{{sc}}$ = {Jsc_r:.1f} mA/cm²\n"
           f"FF = {FF_r:.1f} %\nHI = {HI:+.1f} %")
    ax.text(0.015, 0.97, txt, transform=ax.transAxes, va="top", ha="left",
            fontsize=9, bbox=dict(boxstyle="round", fc="#f7f8fa", ec="#bdc3c7"))
    _save(fig, "sim_jv_curve.png")


# ── 3. Single-diode fit overlay + residuals ────────────────────────────────────
def make_diode_fit():
    rng = np.random.default_rng(7)
    # Slightly higher J0 / lower Rsh so Voc sits near ~1.1 V and the curve clearly
    # crosses J = 0 within the swept range.
    cell = DummyCell(mode="ideal_stable", j_ph=22.0, j0=4e-12, n=1.55,
                     rs=5.5, rsh=4500.0)
    v = np.round(np.arange(-0.1, 1.25 + 1e-9, 0.025), 4)
    j = cell.scan(v, "reverse") + rng.normal(0, 0.06, size=len(v))

    fit = fit_single_diode(v, j, T=298.15)
    pp = fit.parameters
    # Reconstruct the fitted curve through the same solver the cell uses.
    fitted_cell = DummyCell(mode="ideal_stable", j_ph=pp["J_ph"], j0=pp["J0"],
                            n=pp["n"], rs=pp["Rs"], rsh=pp["Rsh"])
    j_fit = fitted_cell.scan(v, "reverse")

    fig, (ax, axr) = plt.subplots(
        2, 1, figsize=(6.6, 5.4), sharex=True,
        gridspec_kw={"height_ratios": [3, 1], "hspace": 0.08})
    ax.axhline(0, color="#7f8c8d", lw=0.8)
    ax.plot(v, j, "o", ms=4, color=C_REV, label="Measured data", zorder=3)
    ax.plot(v, j_fit, "-", lw=2, color=C_FIT, label="Single-diode fit", zorder=4)
    ax.set_ylim(-3, 26)
    ax.set_ylabel("J (mA/cm²)")
    ax.set_title("Five-parameter single-diode fit")
    ax.legend(loc="lower left", fontsize=9)
    info = (f"$J_{{ph}}$ = {pp['J_ph']:.2f} mA/cm²\n"
            f"$J_0$ = {pp['J0']:.2e} mA/cm²\n"
            f"n = {pp['n']:.2f}\n$R_s$ = {pp['Rs']:.1f} Ω·cm²\n"
            f"$R_{{sh}}$ = {pp['Rsh']:.0f} Ω·cm²\n$R^2$ = {fit.r_squared:.4f}")
    ax.text(0.015, 0.97, info, transform=ax.transAxes, va="top", ha="left",
            fontsize=9, bbox=dict(boxstyle="round", fc="#f7f8fa", ec="#bdc3c7"))

    axr.axhline(0, color=C_FIT, lw=1)
    axr.plot(v, j - j_fit, "o", ms=3, color="#34495e")
    axr.set_ylabel("Resid.")
    axr.set_xlabel("Voltage (V)")
    axr.set_xlim(-0.1, 1.3)
    _save(fig, "sim_diode_fit.png")


# ── 4. Dark J-V semilog with ideality fit window ───────────────────────────────
def make_dark_jv():
    # High Rsh + larger J0 so the diode exponential clearly dominates the
    # selected window (otherwise the shunt term flattens the log-linear slope
    # and the extracted ideality factor is meaningless).
    cell = DummyCell(mode="ideal_stable", j_ph=0.0, j0=1.0e-9, n=1.55,
                     rs=4.0, rsh=1.0e6)
    v = np.round(np.arange(-0.2, 1.1 + 1e-9, 0.025), 4)
    j = cell.scan(v, "reverse")  # dark: photocurrent positive convention → forward inj. negative

    v_min, v_max = 0.55, 0.85
    fit = fit_dark_ideality(v, j, v_min, v_max, T=298.15)

    # The fit dataclass exposes n_idd and J_s; recover the log-linear line:
    #   slope = 1/(Vt·ln10·n_idd),  intercept = log10(J_s).
    vt = 1.380649e-23 * 298.15 / 1.602176634e-19
    slope = 1.0 / (vt * np.log(10) * fit.n_idd)
    intercept = np.log10(fit.J_s)

    fig, ax = plt.subplots(figsize=(6.6, 4.5))
    ax.semilogy(v, np.abs(j), "-o", ms=3, color=C_REV, label="|J| dark")
    ax.axvspan(v_min, v_max, color=C_PWR, alpha=0.13, label="Ideality fit window")
    # Fitted line in the window.
    vv = np.linspace(v_min, v_max, 30)
    jj = 10 ** (intercept + slope * vv)
    ax.semilogy(vv, jj, "--", lw=2, color=C_FIT,
                label=f"fit:  n = {fit.n_idd:.2f}")
    ax.set_xlabel("Voltage (V)")
    ax.set_ylabel("|J| (mA/cm²)  —  log scale")
    ax.set_title("Dark J–V — ideality factor extraction")
    ax.legend(loc="lower right", fontsize=9)
    info = (f"n = {fit.n_idd:.2f} ± {fit.n_idd_err:.2f}\n"
            f"$J_s$ = {fit.J_s:.2e} mA/cm²\n$R^2$ = {fit.r_squared:.4f}")
    ax.text(0.015, 0.97, info, transform=ax.transAxes, va="top", ha="left",
            fontsize=9, bbox=dict(boxstyle="round", fc="#f7f8fa", ec="#bdc3c7"))
    _save(fig, "sim_dark_jv.png")


# ── 5. Light-intensity: Voc vs ln(L) light ideality ────────────────────────────
def make_light_intensity():
    suns = np.array([0.1, 0.25, 0.5, 0.75, 1.0])
    cell = DummyCell(mode="ideal_stable", j_ph=22.0, j0=1e-12, n=1.45,
                     rs=4.0, rsh=6000.0)
    v = np.round(np.arange(-0.1, 1.2 + 1e-9, 0.01), 4)
    voc = []
    for s in suns:
        cell.set_illumination_factor(float(s))
        j = cell.scan(v, "reverse")
        cr = np.where(np.diff(np.sign(j)))[0][0]
        voc.append(float(v[cr]))
    voc = np.array(voc)
    fit = fit_light_ideality(suns, voc, T=298.15)

    fig, ax = plt.subplots(figsize=(6.6, 4.3))
    lnL = np.log(suns / suns.max())
    ax.plot(lnL, voc, "o", ms=7, color=C_REV, label="measured $V_{oc}$")
    xs = np.linspace(lnL.min(), 0, 20)
    ax.plot(xs, fit.intercept + fit.slope * xs, "--", lw=2, color=C_FIT,
            label=f"fit:  $n_{{idL}}$ = {fit.n_idL:.2f}")
    ax.set_xlabel("ln(L / L$_{max}$)")
    ax.set_ylabel("$V_{oc}$ (V)")
    ax.set_title("Light ideality factor from $V_{oc}$ vs ln(L)")
    ax.legend(loc="lower right", fontsize=9)
    ax.text(0.015, 0.97, f"$n_{{idL}}$ = {fit.n_idL:.2f}\n$R^2$ = {fit.r_squared:.4f}",
            transform=ax.transAxes, va="top", ha="left", fontsize=9,
            bbox=dict(boxstyle="round", fc="#f7f8fa", ec="#bdc3c7"))
    _save(fig, "sim_light_intensity.png")


# ── 6. MPP / SPO tracking trace ────────────────────────────────────────────────
def make_mpp():
    t = np.linspace(0, 12, 240)  # minutes
    rng = np.random.default_rng(3)
    pce = 18.5 * (1 - 0.18 * np.exp(-t / 1.2)) + rng.normal(0, 0.04, t.size)
    fig, ax = plt.subplots(figsize=(6.6, 4.0))
    ax.plot(t, pce, "-", color=C_PWR, lw=1.6, label="SPO efficiency")
    ax.axhline(pce[-30:].mean(), ls="--", color=C_FIT, lw=1.2,
               label=f"stabilized PCE ≈ {pce[-30:].mean():.1f} %")
    ax.set_xlabel("Time (min)")
    ax.set_ylabel("PCE (%)")
    ax.set_title("Maximum power point tracking (SPO)")
    ax.set_ylim(14, 20)
    ax.legend(loc="lower right", fontsize=9)
    _save(fig, "sim_mpp.png")


# ── 7. Stabilized current dual-axis ────────────────────────────────────────────
def make_stab():
    t = np.linspace(0, 120, 240)  # seconds
    rng = np.random.default_rng(5)
    j = 20.5 * (1 - 0.12 * np.exp(-t / 12)) + rng.normal(0, 0.03, t.size)
    pce = j * 0.85 / 100 * 100 * (0.92)  # arbitrary consistent PCE trace
    pce = 17.8 * (1 - 0.12 * np.exp(-t / 12)) + rng.normal(0, 0.03, t.size)
    fig, ax = plt.subplots(figsize=(6.6, 4.0))
    ax2 = ax.twinx()
    l1, = ax.plot(t, j, color=C_REV, lw=1.6, label="J")
    l2, = ax2.plot(t, pce, color=C_FWD, lw=1.6, label="PCE")
    t_stab = 55
    ax.axvline(t_stab, ls="--", color=C_PWR, lw=1.4)
    ax.text(t_stab + 2, ax.get_ylim()[0] + 0.4, "stabilization", color=C_PWR,
            fontsize=9, fontweight="bold")
    ax.set_xlabel("Time (s)")
    ax.set_ylabel("J (mA/cm²)", color=C_REV)
    ax2.set_ylabel("PCE (%)", color=C_FWD)
    ax2.grid(False)
    ax.set_title("Stabilized current / PCE at fixed bias")
    ax.legend(handles=[l1, l2], loc="lower right", fontsize=9)
    _save(fig, "sim_stab.png")


# ── 8. ISOS stability degradation with T80 / T95 ───────────────────────────────
def make_isos_stability():
    t = np.linspace(0, 1000, 400)  # hours
    # Normalized to 1.0 at t0; gentle decay that crosses 0.95 (~T95) mid-run and
    # approaches 0.80 (~T80) near the end so both markers land on the curve.
    rng = np.random.default_rng(11)
    pce_norm = (1.0 - 0.00022 * t) + rng.normal(0, 0.003, t.size)

    def crossing(level):
        idx = np.where(pce_norm <= level)[0]
        return float(t[idx[0]]) if len(idx) else None
    t95 = crossing(0.95)
    t80 = crossing(0.80)

    fig, ax = plt.subplots(figsize=(6.8, 4.3))
    ax.plot(t, pce_norm, "-", color=C_REV, lw=1.5, label="normalized PCE")
    for lvl, tt, c, lab in [(0.95, t95, "#f39c12", "T95"), (0.80, t80, C_FIT, "T80")]:
        ax.axhline(lvl, ls=":", color=c, lw=1)
        if tt is not None:
            ax.axvline(tt, ls="--", color=c, lw=1.2)
            ax.annotate(f"{lab} ≈ {tt:.0f} h", (tt, lvl),
                        textcoords="offset points", xytext=(6, 6),
                        color=c, fontweight="bold", fontsize=9)
    ax.set_xlabel("Stress time (h)")
    ax.set_ylabel("PCE / PCE₀")
    ax.set_ylim(0.7, 1.02)
    ax.set_title("ISOS stability — degradation tracking")
    ax.legend(loc="lower left", fontsize=9)
    _save(fig, "sim_isos_stability.png")


if __name__ == "__main__":
    make_logo()
    make_jv_curve()
    make_diode_fit()
    make_dark_jv()
    make_light_intensity()
    make_mpp()
    make_stab()
    make_isos_stability()
    print("All figures written to", os.path.relpath(OUT, ROOT))
