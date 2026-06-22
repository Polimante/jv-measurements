"""Generate animated GIFs for the time-series measurements:
  - anim_stab.gif  : stabilized current J & PCE vs time
  - anim_mpp.gif   : MPP / SPO efficiency vs time
  - anim_isos.gif  : ISOS normalized-PCE degradation with T95 / T80 markers

Each trace is revealed point-by-point. Styling matches docs/_gen_doc_figures.py.
Run from the project root:  python docs/_gen_more_gifs.py
"""
from __future__ import annotations

import io
import os

import numpy as np
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from PIL import Image

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT = os.path.join(ROOT, "docs", "figures")
os.makedirs(OUT, exist_ok=True)

plt.rcParams.update({
    "font.family": "DejaVu Sans", "font.size": 11,
    "axes.edgecolor": "#34495e", "axes.labelcolor": "#2c3e50",
    "axes.titlecolor": "#2c3e50", "axes.titleweight": "bold",
    "axes.grid": True, "grid.color": "#dfe4ea", "grid.linewidth": 0.7,
})

C_REV = "#1565C0"
C_FWD = "#e67e22"
C_PWR = "#27ae60"
C_FIT = "#c0392b"


def _frame(fig):
    buf = io.BytesIO()
    fig.savefig(buf, format="png", dpi=96, facecolor="white")
    plt.close(fig)
    buf.seek(0)
    return Image.open(buf).convert("P", palette=Image.ADAPTIVE)


def _write(frames, durations, name):
    path = os.path.join(OUT, name)
    frames[0].save(path, save_all=True, append_images=frames[1:],
                   duration=durations, loop=0, optimize=True, disposal=2)
    print(f"wrote {os.path.relpath(path, ROOT)}  ({len(frames)} frames, "
          f"{os.path.getsize(path) // 1024} KB)")


# ── Stabilized current (dual axis, J + PCE) ────────────────────────────────────
def make_stab_gif():
    t = np.linspace(0, 120, 160)
    rng = np.random.default_rng(5)
    j = 20.5 * (1 - 0.12 * np.exp(-t / 12)) + rng.normal(0, 0.03, t.size)
    pce = 17.8 * (1 - 0.12 * np.exp(-t / 12)) + rng.normal(0, 0.03, t.size)
    t_stab = 55.0

    def render(n):
        fig, ax = plt.subplots(figsize=(6.6, 4.0))
        ax2 = ax.twinx()
        ax.set_xlim(0, 120); ax.set_ylim(18, 21)
        ax2.set_ylim(15, 18.5); ax2.grid(False)
        ax.set_xlabel("Time (s)"); ax.set_ylabel("J (mA/cm²)", color=C_REV)
        ax2.set_ylabel("PCE (%)", color=C_FWD)
        ax.set_title("Stabilized current / PCE at fixed bias")
        l1, = ax.plot(t[:n], j[:n], color=C_REV, lw=1.6, label="J")
        l2, = ax2.plot(t[:n], pce[:n], color=C_FWD, lw=1.6, label="PCE")
        if t[:n].size and t[n - 1] >= t_stab:
            ax.axvline(t_stab, ls="--", color=C_PWR, lw=1.4)
            ax.text(t_stab + 2, 18.2, "stabilization", color=C_PWR,
                    fontsize=9, fontweight="bold")
        ax.legend(handles=[l1, l2], loc="lower right", fontsize=9)
        fig.subplots_adjust(left=0.12, right=0.87, top=0.9, bottom=0.13)
        return _frame(fig)

    frames, dur = [], []
    for n in range(2, len(t) + 1, 2):
        frames.append(render(n)); dur.append(60)
    frames.append(render(len(t))); dur.append(2500)
    _write(frames, dur, "anim_stab.gif")


# ── MPP / SPO efficiency vs time ───────────────────────────────────────────────
def make_mpp_gif():
    t = np.linspace(0, 12, 180)
    rng = np.random.default_rng(3)
    pce = 18.5 * (1 - 0.18 * np.exp(-t / 1.2)) + rng.normal(0, 0.04, t.size)
    plateau = pce[-30:].mean()

    def render(n):
        fig, ax = plt.subplots(figsize=(6.6, 4.0))
        ax.set_xlim(0, 12); ax.set_ylim(14, 20)
        ax.set_xlabel("Time (min)"); ax.set_ylabel("PCE (%)")
        ax.set_title("Maximum power point tracking (SPO)")
        ax.plot(t[:n], pce[:n], "-", color=C_PWR, lw=1.7, label="SPO efficiency")
        if n >= len(t) - 30:
            ax.axhline(plateau, ls="--", color=C_FIT, lw=1.2,
                       label=f"stabilized PCE ≈ {plateau:.1f} %")
        ax.legend(loc="lower right", fontsize=9)
        fig.subplots_adjust(left=0.12, right=0.95, top=0.9, bottom=0.13)
        return _frame(fig)

    frames, dur = [], []
    for n in range(2, len(t) + 1, 2):
        frames.append(render(n)); dur.append(55)
    frames.append(render(len(t))); dur.append(2500)
    _write(frames, dur, "anim_mpp.gif")


# ── ISOS degradation with T95 / T80 markers appearing ──────────────────────────
def make_isos_gif():
    t = np.linspace(0, 1000, 260)
    rng = np.random.default_rng(11)
    pce = (1.0 - 0.00022 * t) + rng.normal(0, 0.003, t.size)

    def crossing(level):
        idx = np.where(pce <= level)[0]
        return float(t[idx[0]]) if len(idx) else None
    t95, t80 = crossing(0.95), crossing(0.80)

    def render(n):
        fig, ax = plt.subplots(figsize=(6.8, 4.3))
        ax.set_xlim(0, 1000); ax.set_ylim(0.7, 1.02)
        ax.set_xlabel("Stress time (h)"); ax.set_ylabel("PCE / PCE₀")
        ax.set_title("ISOS stability — degradation tracking")
        ax.plot(t[:n], pce[:n], "-", color=C_REV, lw=1.5, label="normalized PCE")
        tnow = t[n - 1]
        for lvl, tt, c, lab in [(0.95, t95, "#f39c12", "T95"),
                                (0.80, t80, C_FIT, "T80")]:
            ax.axhline(lvl, ls=":", color=c, lw=1)
            if tt is not None and tnow >= tt:
                ax.axvline(tt, ls="--", color=c, lw=1.2)
                ax.annotate(f"{lab} ≈ {tt:.0f} h", (tt, lvl),
                            textcoords="offset points", xytext=(6, 6),
                            color=c, fontweight="bold", fontsize=9)
        ax.legend(loc="lower left", fontsize=9)
        fig.subplots_adjust(left=0.11, right=0.96, top=0.9, bottom=0.13)
        return _frame(fig)

    frames, dur = [], []
    for n in range(2, len(t) + 1, 3):
        frames.append(render(n)); dur.append(55)
    frames.append(render(len(t))); dur.append(2500)
    _write(frames, dur, "anim_isos.gif")


if __name__ == "__main__":
    make_stab_gif()
    make_mpp_gif()
    make_isos_gif()
    print("done")
