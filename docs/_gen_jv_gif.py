"""Generate an animated GIF of a J-V scan building up point-by-point.

The styling mirrors the application's own live J-V plot (app/gui/main_window.py
update_plot): per-measurement colour, forward = solid line + filled circles,
reverse = dashed line + hollow circles, power density on a twin axis, legend
upper-right, fixed 0–30 mA/cm² window. Output: docs/figures/anim_jv.gif

Run from the project root:  python docs/_gen_jv_gif.py
"""
from __future__ import annotations

import io
import os
import sys

import numpy as np
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from PIL import Image

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
from app.simulation.dummy_cell import DummyCell  # noqa: E402

OUT = os.path.join(ROOT, "docs", "figures")
os.makedirs(OUT, exist_ok=True)

COLOR = "#1565C0"   # PLOT_COLORS[0] — the app's first measurement colour
SAMPLE = "DemoCell_01"


def build_curves():
    cell = DummyCell(mode="with_hysteresis", j_ph=22.5, j0=2e-12, n=1.45,
                     rs=4.0, rsh=6000.0, hi_fraction=0.10)
    v = np.round(np.arange(-0.1, 1.15 + 1e-9, 0.02), 4)
    j_fwd = cell.scan(v, "forward")     # start → end (left to right)
    j_rev = cell.scan(v, "reverse")     # measured end → start; arrays stored ascending V
    p_fwd = v * j_fwd
    p_rev = v * j_rev
    return v, j_fwd, j_rev, p_fwd, p_rev


def style_axes(ax, ax2):
    ax.set_xlim(0, 1.2)
    ax.set_ylim(0, 30)
    ax2.set_ylim(0, 30)
    ax.set_xlabel("Voltage (V)", labelpad=8)
    ax.set_ylabel("Current Density (mA/cm²)", labelpad=8)
    ax2.set_ylabel("Power Density (mW/cm²)", labelpad=8)
    ax2.yaxis.set_label_position("right")
    ax.set_title(f"{SAMPLE} — J-V Characteristics", fontsize=12,
                 fontweight="bold", pad=12)
    ax.grid(True)


def render_frame(v, j_fwd, j_rev, p_fwd, p_rev, n_fwd, n_rev):
    """Render one frame: first n_fwd forward points and n_rev reverse points."""
    fig, ax = plt.subplots(figsize=(8, 5))
    ax2 = ax.twinx()
    style_axes(ax, ax2)
    fig.subplots_adjust(left=0.12, right=0.87, top=0.88, bottom=0.12)

    handles, labels = [], []
    if n_fwd > 0:
        lf, = ax.plot(v[:n_fwd], j_fwd[:n_fwd], color=COLOR, linestyle="-",
                      marker="o", markersize=5, markerfacecolor=COLOR,
                      markeredgecolor=COLOR, linewidth=1.8, zorder=3)
        ax2.plot(v[:n_fwd], p_fwd[:n_fwd], color=COLOR, linestyle="-",
                 marker="s", markersize=4, markerfacecolor=COLOR,
                 markeredgecolor=COLOR, linewidth=1.2, alpha=0.5, zorder=2)
        handles.append(lf); labels.append(f"{SAMPLE} — Fwd J-V")
    # Reverse is measured from high V to low V → reveal from the right.
    if n_rev > 0:
        lr, = ax.plot(v[len(v) - n_rev:], j_rev[len(v) - n_rev:], color=COLOR,
                      linestyle="--", marker="o", markersize=5,
                      markerfacecolor="white", markeredgecolor=COLOR,
                      markeredgewidth=1.5, linewidth=1.8, zorder=3)
        ax2.plot(v[len(v) - n_rev:], p_rev[len(v) - n_rev:], color=COLOR,
                 linestyle="--", marker="s", markersize=4, markerfacecolor="white",
                 markeredgecolor=COLOR, markeredgewidth=1.2, linewidth=1.2,
                 alpha=0.5, zorder=2)
        handles.append(lr); labels.append(f"{SAMPLE} — Rev J-V")
    if handles:
        ax.legend(handles, labels, loc="upper right", frameon=True,
                  framealpha=0.9, fontsize=9)

    # Phase caption (mirrors the app status bar wording).
    if n_rev == 0 and n_fwd < len(v):
        cap = "Measuring forward scan…"
    elif n_rev == 0:
        cap = "Forward scan complete — pre-bias / reverse next"
    elif n_rev < len(v):
        cap = "Measuring reverse scan…"
    else:
        cap = "Measurement complete"
    ax.text(0.015, 0.03, cap, transform=ax.transAxes, fontsize=9,
            color="#37474F", style="italic",
            bbox=dict(boxstyle="round,pad=0.3", fc="#ECEFF1", ec="#B0BEC5"))

    buf = io.BytesIO()
    fig.savefig(buf, format="png", dpi=96, facecolor="white")
    plt.close(fig)
    buf.seek(0)
    return Image.open(buf).convert("P", palette=Image.ADAPTIVE)


def main():
    v, j_fwd, j_rev, p_fwd, p_rev = build_curves()
    N = len(v)
    frames, durations = [], []
    step = 2  # reveal 2 points per frame to keep the file small

    # Phase 1: forward scan grows left→right.
    for n in range(1, N + 1, step):
        frames.append(render_frame(v, j_fwd, j_rev, p_fwd, p_rev, n, 0))
        durations.append(70)
    frames.append(render_frame(v, j_fwd, j_rev, p_fwd, p_rev, N, 0))
    durations.append(600)  # pause after forward

    # Phase 2: reverse scan grows right→left.
    for n in range(1, N + 1, step):
        frames.append(render_frame(v, j_fwd, j_rev, p_fwd, p_rev, N, n))
        durations.append(70)
    final = render_frame(v, j_fwd, j_rev, p_fwd, p_rev, N, N)
    frames.append(final)
    durations.append(2500)  # long hold on the complete curve

    path = os.path.join(OUT, "anim_jv.gif")
    frames[0].save(path, save_all=True, append_images=frames[1:],
                   duration=durations, loop=0, optimize=True, disposal=2)
    print(f"wrote {os.path.relpath(path, ROOT)}  ({len(frames)} frames, "
          f"{os.path.getsize(path) // 1024} KB)")


if __name__ == "__main__":
    main()
